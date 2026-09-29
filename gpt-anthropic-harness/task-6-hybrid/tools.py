"""One definition per tool: schema, description, dispatch and availability."""
import asyncio
from dataclasses import dataclass
import inspect
import json

from bounds import KEYS, Denied
from capabilities import Capabilities
from ledger import NIMOI
from policy import add_numbers

SERVER = "nimoi"


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


@dataclass(frozen=True)
class Tool:
    description: str
    schema: dict
    handler: object
    grants: tuple = ()
    delegation: bool = False
    onboarding: bool = True


S, I = {"type": "string"}, {"type": "integer"}
TOOLS = {
    "add": Tool("Add two finite numbers using Python.",
        schema({"a": {"type": "number"}, "b": {"type": "number"}}, ("a", "b")),
        lambda s, a: {"sum": add_numbers(a)}),
    "fs_list": Tool("List readable direct children; filtering precedes pagination. Mount paths or NIMOI-relative paths.",
        schema({"path": S, "offset": I, "limit": I}), lambda s, a: s.cap.list(**a), ("fs.read",), onboarding=False),
    "fs_read": Tool("Read bounded UTF-8 text; follow next_line. max_lines <=300; file <=2MB.",
        schema({"path": S, "start_line": I, "max_lines": I}, ("path",)),
        lambda s, a: s.read_file(**a), ("fs.read",), onboarding=False),
    "fs_write": Tool("Create a NEW workspace file from the exact readable ledger body ID. No overwrite or directory creation.",
        schema({"entry_id": S, "path": S}, ("entry_id", "path")),
        lambda s, a: s.cap.write(**a), ("fs.write", "ledger.read")),
    "python_execute": Tool("Run approved Python, no arguments. Reserve fresh readable/writable pilot/ output_name first. 10s, 64KiB merged output.",
        schema({"path": S, "output_name": S}, ("path", "output_name")),
        lambda s, a: s.execute(**a), ("fs.execute", "ledger.read", "ledger.write")),
    "ledger_read": Tool("Read this chat's entries within ledger.read; exact name or filtered paginated listing.",
        schema({"name": S, "tag": S, "offset": I, "limit": I, "body_offset": I}),
        lambda s, a: s.access.read(**a), ("ledger.read",), onboarding=False),
    "ledger_write": Tool("Write your own pilot/ text note with allowed tags; revisions require exact current prev.",
        schema({"name": S, "body": S, "tags": {"type": "array", "items": S}, "prev": S}, ("name", "body", "tags")),
        lambda s, a: s.access.write(**a), ("ledger.read", "ledger.write", "ledger.tags")),
    "agent_start": Tool("Dispatch the next layer: governor -> owner, owner -> worker. Pinned instructions with Bar, narrower bounds, fresh readable result_name.",
        schema({"model": S, "instructions_id": S, "result_name": S,
            "bounds": {"type": "object", "additionalProperties": False,
                "properties": {k: {"type": "array", "items": S} for k in KEYS}, "required": list(KEYS)}},
            ("model", "instructions_id", "bounds", "result_name")),
        lambda s, a: s.manager.start(s, **a), ("ledger.read", "ledger.write"), delegation=True),
    "agent_status": Tool("Inspect your owned agents; governor may inspect all actors. wait_seconds 0..10.",
        schema({"agent_id": S, "wait_seconds": {"type": "number"}}, ("agent_id",)),
        lambda s, a: s.manager.status(s.context, **a), delegation=True),
    "governor_request": Tool("Owner only. Suspend yourself awaiting governor guidance. Pin your own justification note and choose a fresh readable response_name. No permissions expand.",
        schema({"request_id": S, "response_name": S}, ("request_id", "response_name")),
        lambda s, a: s.manager.request(s, **a), ("ledger.read", "ledger.write")),
    "governor_resolve": Tool("Governor only. Respond to a pending owner request with your pinned readable response note. Decision: approve, deny, or needs_human. Approval never expands grants; needs_human creates an explicit human decision card.",
        schema({"request_id": S, "response_id": S, "decision": {"type":"string", "enum":["approve","deny","needs_human"]}},
               ("request_id", "response_id", "decision")),
        lambda s, a: s.manager.resolve(s, **a), ("ledger.read", "ledger.write")),

}


class ToolService:
    def __init__(self, log, bounds, author, *, root=NIMOI, manager=None, state=None, context=None):
        self.log, self.bounds, self.author = log, bounds, author
        self.manager, self.state, self.context = manager, state, context
        self.cap = Capabilities(root, log, bounds, author)
        self.access = self.cap.access
        self.onboarding = max((self.cap.root / "origins").glob("onboarding_*.md"), key=lambda p: p.name)
        self.onboarding_path = self.onboarding.relative_to(self.cap.root).as_posix()
        self.onboarding_next, self.onboarded = 1, False
        if context is None:
            raise ValueError("Explicit agent context is required.")
        self.deadline = None
        can_delegate = context.can_delegate
        self.operations = {name for name, spec in TOOLS.items()
                           if all(bounds.get(k) for k in spec.grants) and (not spec.delegation or can_delegate)}
        if context.role == "governor":
            self.operations -= {"add", "fs_write", "python_execute", "governor_request"}
        else:
            self.operations.discard("governor_resolve")
            if context.role != "owner":
                self.operations.discard("governor_request")
        self.full_names = {f"mcp__{SERVER}__{name}" for name in self.operations}

    def validate(self, operation, args):
        if operation not in self.operations:
            raise Denied("Tool is not available with these capabilities.")
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object.")
        spec = TOOLS[operation].schema
        if set(args) - set(spec["properties"]) or set(spec["required"]) - set(args):
            raise ValueError("Unexpected or missing tool arguments.")
        types = {"string": (str,), "integer": (int,), "number": (int, float), "array": (list,), "object": (dict,)}
        for key, value in args.items():
            field = spec["properties"][key]
            if type(value) not in types[field["type"]]:
                raise ValueError(f"{key}: tool argument type mismatch.")
            if "enum" in field and value not in field["enum"]:
                raise ValueError(f"{key}: choose from {field['enum']}.")

    def allowed(self, name, args):
        """Dispatch validation only; capability checks run in the handler."""
        if name not in self.full_names:
            return False
        try:
            self.validate(name.split("__")[-1], args)
            return True
        except ValueError:
            return False

    def read_file(self, **args):
        result = self.cap.read(**args)
        if result["path"] == self.onboarding_path and result["start_line"] == self.onboarding_next:
            self.onboarding_next = result["next_line"]
            if self.onboarding_next is None:
                self.onboarded = True
                self.log.write("onboarding_complete", actor=self.author, path=self.onboarding_path)
        return result

    async def execute(self, **args):
        # Shield the worker from coroutine cancellation and await its cleanup.
        # A per-agent signal handles turn timeout; the shared signal handles shutdown.
        task = asyncio.create_task(asyncio.to_thread(self.cap.execute, **args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.cap.cancel_event.set()
            await asyncio.shield(task)
            raise

    async def invoke(self, operation, args):
        self.log.check()
        if self.log.stop_event.is_set():
            raise Denied("Launch is stopping.")
        self.validate(operation, args)
        if self.manager and self.manager.is_blocked(self.context.agent_id):
            raise Denied("Owner is blocked awaiting the governor; no further tools until a decision.")
        if TOOLS[operation].onboarding and not self.onboarded:
            raise Denied("Read the latest onboarding fully, in line order, before substantive tools.")
        result = TOOLS[operation].handler(self, args)
        return await result if inspect.isawaitable(result) else result

    async def call(self, operation, args, turn):
        self.log.write("python_tool_start", actor=self.author, turn=turn, tool=operation, arguments=args)
        try:
            result = self.log.clean(await self.invoke(operation, args))
        except (ValueError, OSError) as exc:
            code = "capability_denied" if isinstance(exc, Denied) else "invalid_arguments" if isinstance(exc, ValueError) else "execution_failed"
            kind = "tool_denied" if isinstance(exc, Denied) else "tool_invalid" if isinstance(exc, ValueError) else "tool_failed"
            error = self.log.clean(str(exc))
            self.log.write(kind, actor=self.author, turn=turn, tool=operation, code=code, error=error)
            if self.state:
                self.state.activity("Tool refused" if kind != "tool_failed" else "Tool failed",
                                    summary=f"{self.author}: {operation}: {error}")
            return {"content": [{"type": "text", "text": json.dumps({"code": code, "error": error})}], "isError": True}
        self.log.write("python_tool_result", actor=self.author, turn=turn, tool=operation, result=result)
        if self.state:
            self.state.activity(operation.replace("_", " "), summary=f"{self.author}: {args.get('path', args.get('name', args.get('agent_id', 'completed')))}")
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

    def build_server(self, get_turn):
        from claude_agent_sdk import create_sdk_mcp_server, tool
        registered = []
        for operation in sorted(self.operations):
            async def call(args, operation=operation):
                return await self.call(operation, args, get_turn())
            spec = TOOLS[operation]
            registered.append(tool(operation, spec.description, spec.schema)(call))
        return create_sdk_mcp_server(name=SERVER, version="0.3.0", tools=registered)
