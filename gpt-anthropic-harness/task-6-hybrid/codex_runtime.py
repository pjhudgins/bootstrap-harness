"""Codex App Server adapter sharing the same bounds, tools and ledger as Claude.

Transport/restrictions adapted from GPT/Codex task 5; executable discovery from
Claude/Codex task 5. Protocol: https://learn.chatgpt.com/docs/app-server
"""
import asyncio
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import shutil
import threading
from uuid import uuid4

from codex_protocol import Client, RpcError
from codex_policy import RESTRICTIONS, model_catalog, needs_exec
from ledger import NIMOI
from prompts import build_prompt
from tools import ToolService, TOOLS


def find_codex():
    found = shutil.which("codex")
    if found:
        return found
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
    copies = sorted(root.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime)
    if not copies:
        raise FileNotFoundError("Codex CLI not found.")
    return str(copies[-1])


class Stop:
    def __init__(self, shared):
        self.shared, self.local = shared, threading.Event()

    def is_set(self):
        return self.shared.is_set() or self.local.is_set()

    def set(self):
        self.local.set()


class Journal:
    """Provider events, prose provenance and UI observations for one actor."""
    def __init__(self, session):
        self.session = session
        self.refs = {}

    @property
    def failed(self):
        return self.session.log.failed

    def flush_pending(self, **kwargs):
        pass

    def text(self, text):
        if text not in self.refs:
            log = self.session.log
            with log.lock:
                self.refs[text] = log._message_text(text, "assistant", self.session.context.author)
        return self.refs[text]

    def write(self, kind, payload, **metadata):
        s = self.session
        message = copy.deepcopy(payload)
        refs = []
        if isinstance(message, dict):
            method = message.get("method")
            params = message.get("params") or {}
            if kind == "send" and method == "turn/start":
                params["input"] = [{"type": "text", "text": s.prompt_ref.get("text", "[pinned instruction]"),
                                    "reference": s.prompt_ref}]
            item = params.get("item") or {}
            if method == "item/completed" and item.get("type") == "agentMessage":
                text = item["text"]
                ref = self.text(text)
                refs.append(ref)
                item["text"] = ref["link"]
                s.state.assistant_text(s.turn, text, actor=s.context.agent_id, label=s.context.agent_id)
            elif method in {"item/started", "item/completed"} and item.get("type") == "userMessage":
                # App Server echoes the task under its transport's user role;
                # preserve the actual governor/owner author via the pinned note.
                item["content"] = [{"type": "text", "text": s.prompt_ref["text"], "reference": s.prompt_ref}]
            elif method == "item/agentMessage/delta":
                ref = self.text(params.get("delta", ""))
                refs.append(ref)
                params["delta"] = ref["link"]
            elif method == "rawResponseItem/completed" and item.get("type") == "message":
                for block in item.get("content", []):
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        ref = self.text(block["text"])
                        refs.append(ref)
                        block["text"] = ref["link"]
            if method == "turn/completed":
                for completed in (params.get("turn") or {}).get("items", []):
                    if completed.get("type") == "agentMessage" and completed.get("text"):
                        ref = self.text(completed["text"])
                        refs.append(ref)
                        completed["text"] = ref["link"]
                    elif completed.get("type") == "userMessage":
                        completed["content"] = [{"type":"text", "text":s.prompt_ref["text"], "reference":s.prompt_ref}]
            if method == "thread/tokenUsage/updated":
                tokens = params.get("tokenUsage") or {}
                last = tokens.get("last") or {}
                usage = {"last_prompt": {"input_tokens": last.get("inputTokens"),
                         "output_tokens": last.get("outputTokens"), "cache_read_input_tokens": last.get("cachedInputTokens"),
                         "cache_creation_input_tokens": None}, "total_cost_usd": None,
                         "token_usage": tokens, "provider": "codex"}
                s.state.record_usage(s.context, usage)
        s.log.write("codex_" + kind, actor=s.context.author, turn=s.turn,
                    payload=message, text_entries=refs)


class CodexSession:
    def __init__(self, log, task_dir, state, context, manager, *, client_factory=Client, root=NIMOI):
        self.log, self.task_dir, self.state, self.context = log, Path(task_dir), state, context
        self.service = ToolService(log, context.bounds, context.author, root=root, manager=manager,
                                   state=state, context=context)
        self.stop = Stop(log.stop_event)
        self.factory, self.client, self.turn, self.prompt_ref = client_factory, None, None, {}
        self.journal = Journal(self)
        self.tool_tasks = set()

    async def _call_tool(self, name, args):
        task = asyncio.current_task()
        self.tool_tasks.add(task)
        try:
            return await self.service.call(name, args, self.turn)
        finally:
            self.tool_tasks.discard(task)

    def registry(self):
        registry = {}
        for name in sorted(self.service.operations):
            spec = TOOLS[name]
            def call(args, name=name):
                future = asyncio.run_coroutine_threadsafe(self._call_tool(name, args), self.loop)
                while True:
                    try:
                        return future.result(timeout=0.1)
                    except concurrent.futures.TimeoutError:
                        if self.stop.is_set():
                            future.cancel()
                            raise RuntimeError("Codex actor stopping.")
            registry[name] = ({"name": name, "description": spec.description, "inputSchema": spec.schema}, call)
        return registry

    def _start(self):
        env = os.environ.copy()
        env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
        env.pop("CODEX_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
        runtime = self.task_dir / ".runtime" / self.log.name / self.context.agent_id
        runtime.mkdir(parents=True, exist_ok=True)
        command = [find_codex(), "app-server", "--listen", "stdio://"]
        for key, value in {**RESTRICTIONS, "sqlite_home": str(runtime)}.items():
            command.extend(["-c", f"{key}={json.dumps(value)}"])
        registry = self.registry()
        self.client = self.factory(self.journal, command, env, self.stop, registry)
        try:
            self.client.request("initialize", {"clientInfo": {"name": "nimoi_hybrid", "version": "0.1"},
                "capabilities": {"experimentalApi": True, "optOutNotificationMethods":
                    ["item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded", "item/reasoning/textDelta", "item/plan/delta"]}})
            self.client.send({"method": "initialized", "params": {}})
            account = self.client.request("account/read", {"refreshToken": False}).get("account")
            if not account or account.get("type") != "chatgpt":
                raise RuntimeError("Codex requires existing ChatGPT sign-in; no API-key fallback.")
            config = self.client.request("config/read", {"includeLayers": False, "cwd": str(self.task_dir)})["config"]
            models, cursor = [], None
            while True:
                page = self.client.request("model/list", {"limit": 100, "cursor": cursor, "includeHidden": False})
                models += [m["model"] for m in page["data"]]
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            if self.context.model not in models:
                raise ValueError(f"Requested model unavailable: {self.context.model}; catalog: {models}")
            metadata = model_catalog(env["CODEX_HOME"]).get(self.context.model)
            exec_enabled = needs_exec(metadata)
            overrides = {**RESTRICTIONS, "features.code_mode_host": exec_enabled}
            for group in ("mcp_servers", "plugins"):
                for name in config.get(group) or {}:
                    overrides[f"{group}.{name}.enabled"] = False
            prompt = build_prompt(self.context, self.service)
            prompt += "\nCodex limitation: JavaScript exec may be required to dispatch these harness tools. Do not use native delegation, shell, patch, external MCP or browser tools. Use ONLY the supplied tools for work. Declined runtime approvals cannot be bypassed."
            self.log.write("configuration", actor=self.context.author, model=self.context.model,
                           provider="codex", role=self.context.role, bounds=self.context.bounds.data(),
                           mounts=self.context.bounds.mounts, system_prompt=prompt, custom_tools=sorted(registry),
                           exec_host_enabled=exec_enabled, catalog_metadata=metadata)
            started = self.client.request("thread/start", {"cwd": str(self.task_dir), "model": self.context.model,
                "sandbox": "read-only", "approvalPolicy": "untrusted", "approvalsReviewer": "user",
                "ephemeral": True, "experimentalRawEvents": True, "environments": [], "config": overrides,
                "baseInstructions": prompt, "developerInstructions": "Follow the exact institutional role, pinned task and bounded harness tools.",
                "dynamicTools": [spec for spec, handler in registry.values()]})
            self.client.thread_id = started["thread"]["id"]
            if started.get("model") != self.context.model:
                raise RuntimeError("Codex selected a different model; stopped without a turn.")
            features, cursor = {}, None
            while True:
                page = self.client.request("experimentalFeature/list", {"threadId": self.client.thread_id, "limit": 100, "cursor": cursor})
                features.update({f["name"]: f["enabled"] for f in page["data"]})
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            for name in ("shell_tool", "js_repl", "code_mode", "multi_agent", "apps", "hooks", "plugins", "browser_use", "computer_use"):
                if features.get(name) is not False:
                    raise RuntimeError(f"Restriction not confirmed disabled: {name}")
            if not exec_enabled and features.get("code_mode_host") is not False:
                raise RuntimeError("Direct-tool model exec host was not disabled.")
            self.log.write("restriction_evidence", actor=self.context.author, features=features,
                note="Requested flags and observed calls, not a pre-execution isolation proof. Exec dispatcher allowed by rule 5g; native delegation forbidden.")
            self._limits()
        except BaseException:
            self.client.close()
            self.client = None
            raise

    def _limits(self):
        try:
            limits = self.client.request("account/rateLimits/read", {})
            self.log.write("account_limits", actor=self.context.author, provider="codex", limits=limits)
        except RpcError as exc:
            self.log.write("account_limits_unavailable", actor=self.context.author, error=str(exc))

    async def _blocking(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.stop.set()
            self.service.cap.cancel_event.set()
            try:
                await asyncio.shield(task)
            except Exception:
                pass
            raise

    async def __aenter__(self):
        self.loop = asyncio.get_running_loop()
        await self._blocking(self._start)
        return self

    async def query(self, text, turn=None, *, text_ref):
        self.turn, self.prompt_ref = turn or uuid4().hex, text_ref
        self.log.write("prompt_send_attempt", actor=self.context.author, turn=self.turn, **text_ref)
        turn_id, replies = await self._blocking(self.client.run_turn, text)
        if not self.service.onboarded:
            raise RuntimeError("Codex reply completed without onboarding.")
        await self._blocking(self._limits)
        self.log.write("turn_complete", actor=self.context.author, turn=self.turn, provider="codex",
                       session_id=self.client.thread_id, provider_turn_id=turn_id)
        return "\n\n".join(replies)

    async def __aexit__(self, *args):
        self.stop.set()
        pending = list(self.tool_tasks)
        for task in pending:
            if not task.cancelling():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self.client:
            await asyncio.to_thread(self.client.close)
            self.client = None
