"""Audit one ledger session's records. Read-only: it loads the ledger and never writes.

    python audit.py                    # the newest session
    python audit.py 20260925T195145Z   # one session
    python audit.py --all              # every session

(Idea from gpt-anthropic-harness's inspect_ledger.py; peer review 2026-09-25.) It turns the
ledger into an audit artifact, by checking what the harness claims to guarantee:
  1. the scribe loads the ledger with no findings (an active session is expected to be "unclosed");
  2. every allowed tool call ends: pre -> post or failure (matched by tool_use_id);
  3. every file_write_started ends in file_write or file_write_failed, and each file_write's
     sha256 matches the ledger revision it names. Every written file is therefore
     reproducible from the ledger;
  4. every exec_started ends in exec;
  5. every subagent_start ends in subagent_end, and the child's recorded bounds are
     re-validated as a subset of its parent's recorded bounds, with fs.write and fs.exec apart;
  6. every message link resolves: text to its text entry, authored by the agent that sent
     it (Claude: AssistantMessage blocks; GPT: agentMessage items in codex_rpc records); a
     child's first message to the pinned instruction revision;
  7. every entry an agent wrote in the session outside log/ lies within that agent's
     recorded ledger.write;
  8. (task 6) every owner_start ends in owner_end. The owner's recorded bounds, and any bounds
     it was later given (bounds_changed), are re-validated within the recorded ceiling, with
     fs.write and fs.exec apart. The dispatched assignment revision carries no `Bar:` line;
  9. (task 6) every request_open ends in request_resolved;
 10. (task 6) every script_promoted matches an approved promote_script request: the same
     sha256, which is also the sha256 of the text recorded with the request. Every promoted
     script is therefore reproducible from the ledger.
Exit 0 with no problems, 1 with problems, 2 on a usage error.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any

from bounds import Bounds, BoundsError
from governance import BAR_LINE
from ledgerlog import LOG_PREFIX
from subagents import instruction_text

LINK_RE = re.compile(r"^\[\[([^\]]+)\]\]$")


def written_bytes(body: Any) -> bytes:
    """What files.FileTools.write writes for a body (keep the two in step)."""
    return body.encode("utf-8") if isinstance(body, str) else \
        (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def audit(view: Any, session: str) -> list[str]:
    problems: list[str] = []
    prefix = f"{LOG_PREFIX}{session}/"
    records = [(n, view.current(n)) for n in view.names() if n.startswith(prefix)]
    events = [(n, e.body) for n, e in records if isinstance(e.body, dict)]

    # Who the agents were, from their start records.
    agents: dict[str, dict[str, Any]] = {}
    for n, b in events:
        if b.get("kind") in ("session_start", "subagent_session", "owner_session"):
            try:
                agents[b["agent"]] = {"author": b["agent_author"], "bounds": Bounds.parse(b["bounds"]),
                                      "parent": b.get("parent")}
            except (KeyError, BoundsError) as e:
                problems.append(f"{n}: unreadable agent start record: {e}")
    by_author = {a["author"]: aid for aid, a in agents.items()}

    # 2. Tool calls end.
    allowed, ended = {}, set()
    for n, b in events:
        if b.get("kind") == "tool_call":
            if b.get("phase") == "pre" and b.get("policy_allow"):
                allowed[b.get("tool_use_id")] = n
            elif b.get("phase") in ("post", "failure"):
                ended.add(b.get("tool_use_id"))
    problems += [f"{n}: tool call {t} was allowed but has no post or failure record"
                 for t, n in allowed.items() if t not in ended]

    # 3, 4. Started side effects end; file writes match their pinned revision.
    starts = {e.id: (n, e.body["kind"]) for n, e in records
              if isinstance(e.body, dict) and e.body.get("kind") in ("file_write_started", "exec_started")}
    finished = {b.get("started") for _, b in events if b.get("kind") in ("file_write", "file_write_failed", "exec")}
    problems += [f"{n}: {kind} has no matching end record" for rid, (n, kind) in starts.items() if rid not in finished]
    for n, b in events:
        if b.get("kind") == "file_write":
            src = view.line(b.get("entry_id") or "")
            if not src or "body" not in src:
                problems.append(f"{n}: file_write names {b.get('entry_id')}, which is not a body revision")
            elif hashlib.sha256(written_bytes(src["body"])).hexdigest() != b.get("sha256"):
                problems.append(f"{n}: file_write sha256 does not match revision {b.get('entry_id')}")

    # 5. Spawns end, and children stay within their parents.
    spawned = {b.get("child"): (n, b) for n, b in events if b.get("kind") == "subagent_start"}
    ended_children = {b.get("child") for _, b in events if b.get("kind") == "subagent_end"}
    for child, (n, b) in spawned.items():
        if child not in ended_children:
            problems.append(f"{n}: subagent {child} has no subagent_end")
        parent = agents.get(b.get("agent"))
        if parent is None:
            problems.append(f"{n}: parent {b.get('agent')} has no start record")
            continue
        try:
            Bounds.parse(b.get("bounds", "")).within(parent["bounds"])
        except BoundsError as e:
            problems.append(f"{n}: {child}'s recorded bounds are not within {b.get('agent')}'s: {e}")

    # 6. Links resolve.
    for n, b in events:
        if b.get("kind") == "codex_rpc":  # task 6: a GPT agent's replies arrive as agentMessage items
            item = ((b.get("message") or {}).get("params") or {}).get("item") or {}
            if b.get("rpc") == "item/completed" and item.get("type") == "agentMessage" and item.get("text"):
                link = LINK_RE.match(str(item["text"]))
                target = view.current(link.group(1)) if link else None
                author = agents.get(b.get("agent"), {}).get("author")
                if target is None:
                    problems.append(f"{n}: agent text is not a resolvable link")
                elif author and target.author != author:
                    problems.append(f"{n}: [[{link.group(1)}]] is authored by {target.author}, not {author}")
            continue
        if b.get("kind") != "message":
            continue
        m = b.get("message") or {}
        if m.get("_type") == "prompt":
            link = LINK_RE.match(str(m.get("text", "")))
            if link and b.get("text_id"):
                line = view.line(b["text_id"])
                if not line or line.get("name") != link.group(1):
                    problems.append(f"{n}: prompt link {m['text']} does not match revision {b['text_id']}")
            elif link and view.current(link.group(1)) is None:
                problems.append(f"{n}: prompt link {m['text']} does not resolve")
        elif m.get("_type") == "AssistantMessage":
            author = agents.get(b.get("agent"), {}).get("author")
            for block in m.get("content") or []:
                link = LINK_RE.match(str(block.get("text", ""))) if block.get("_type") == "TextBlock" else None
                if not link:
                    continue
                target = view.current(link.group(1))
                if target is None:
                    problems.append(f"{n}: text link [[{link.group(1)}]] does not resolve")
                elif author and target.author != author:
                    problems.append(f"{n}: [[{link.group(1)}]] is authored by {target.author}, not {author}")

    # 8. Owners end, stay within the ceiling, and got no bar.
    owners = {b.get("owner"): (n, b) for n, b in events if b.get("kind") == "owner_start"}
    ended_owners = {b.get("owner") for _, b in events if b.get("kind") == "owner_end"}
    for owner, (n, b) in owners.items():
        if owner not in ended_owners:
            problems.append(f"{n}: owner {owner} has no owner_end")
        try:
            ceiling = Bounds.parse(b.get("ceiling", ""))
            Bounds.parse(b.get("bounds", "")).within(ceiling)
        except BoundsError as e:
            problems.append(f"{n}: {owner}'s recorded bounds are not within the recorded ceiling: {e}")
            continue
        for m, c in events:
            if c.get("kind") == "bounds_changed" and c.get("owner") == owner:
                try:
                    Bounds.parse(c.get("new", "")).within(ceiling)
                except BoundsError as e:
                    problems.append(f"{m}: {owner}'s new bounds are not within the ceiling: {e}")
        line = view.line(b.get("assignment_id") or "")
        if not line or "body" not in line:
            problems.append(f"{n}: {owner}'s assignment {b.get('assignment_id')} is not a body revision")
        elif BAR_LINE.search(instruction_text(line["body"])):
            problems.append(f"{n}: {owner}'s assignment carries a Bar: line (the governor sets rules, not the bar)")

    # 9. Requests are resolved.
    opened = {b.get("request"): (n, b) for n, b in events if b.get("kind") == "request_open"}
    resolved = {b.get("request"): b for _, b in events if b.get("kind") == "request_resolved"}
    problems += [f"{n}: request {r} has no request_resolved" for r, (n, _) in opened.items() if r not in resolved]

    # 10. Promoted scripts are the approved, recorded bytes.
    for n, b in events:
        if b.get("kind") != "script_promoted":
            continue
        req = opened.get(b.get("request"))
        details = (req[1].get("details") or {}) if req else {}
        if req is None or req[1].get("request_kind") != "promote_script":
            problems.append(f"{n}: script_promoted names {b.get('request')}, which is not a promote_script request")
        elif (resolved.get(b.get("request")) or {}).get("decision") != "approved":
            problems.append(f"{n}: {b.get('request')} was not approved")
        elif details.get("sha256") != b.get("sha256") or hashlib.sha256(
                str(details.get("text", "")).encode("utf-8")).hexdigest() != b.get("sha256"):
            problems.append(f"{n}: the promoted sha256 does not match the text recorded with {b.get('request')}")

    # 7. Agents wrote only within their ledger.write.
    for name in view.names(deleted=True):
        if name.startswith(LOG_PREFIX):
            continue
        for e in view.history(name):
            aid = by_author.get(e.author)
            if e.id.startswith(session + ":") and aid and not agents[aid]["bounds"].permits("ledger.write", name):
                problems.append(f"{e.id}: {aid} wrote {name!r}, outside its ledger.write")

    return problems


def main(argv: list[str]) -> int:
    from harness import LEDGER_NAME, LEDGER_ROOT
    from scribe_import import import_scribe

    scribe = import_scribe()
    try:
        view = scribe.load(LEDGER_ROOT, LEDGER_NAME)
    except scribe.ScribeError as e:
        print(f"cannot load {LEDGER_ROOT / LEDGER_NAME}: {e}", file=sys.stderr)
        return 2
    if len(argv) > 1:
        print("usage: python audit.py [SESSION | --all]", file=sys.stderr)
        return 2
    sessions = view.sessions if argv == ["--all"] else (argv or view.sessions[-1:])
    unknown = [s for s in sessions if s not in view.sessions]
    if unknown:
        print(f"no such session: {', '.join(unknown)}", file=sys.stderr)
        return 2
    findings = [str(f) for f in view.findings]
    if findings:
        print("scribe findings:\n  " + "\n  ".join(findings))
    total = len(findings) - (1 if view.is_leased() and any("unclosed" in f for f in findings) else 0)
    for s in sessions:
        problems = audit(view, s)
        total += len(problems)
        print(f"{s}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
        for p in problems:
            print(f"  {p}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main(sys.argv[1:]))
