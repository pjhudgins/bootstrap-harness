"""Check one session of this harness's ledger after a run. Exit 0 when it is clean.

    python check_ledger.py                        # the newest session in ledgers/
    python check_ledger.py --root .runtime/fake-ledgers --session 20260925T192816Z --closed

Read-only: it loads the ledger with scribe.load (no lease, never writes) and prints
metadata only, never a body. For a session in record format 2 (its run record says so)
it checks:
  - the scribe's own findings (an open newest session is allowed unless --closed);
  - every harness record: named harness/<session>/<seq>.<kind>, by the harness, labelled
    exactly log.<kind>; every transcript text labelled transcript.<role>, every script
    output labelled exec;
  - every text link (message, python_exec, message_stream): the linked version exists,
    has the linked name, the recorded author and a text body; every transcript/ and exec/
    text of the session is linked from exactly one message or python_exec record;
  - intent before effect: each fs_write_started and python_exec_started is followed by
    its result, each subagent_spawn by a subagent_finished;
  - bounds: each agent's recorded bounds lie inside its parent's recorded bounds (the
    root's are the run's), and keep fs.write out of fs.exec folders;
  - message streams: the fragments added up to the final text;
  - the run ended with a summary in which no agent made an unreviewed model tool call.
Older sessions (record format 1) get the scribe's findings only.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

import bounds as bd
import ledger_log
from ledger_log import scribe

SUFFIX = ".ledger"
INTENTS = {"fs_write_started": "fs_write", "python_exec_started": "python_exec"}
TEXT_KINDS = ("message", "python_exec")  # the records whose text_id is their own text


def session_lines(root, ledger, session):
    """[(id, decoded line)] of one session file, in order."""
    path = Path(root) / ledger / f"{session}{SUFFIX}"
    lines = [line for line in path.read_bytes().split(b"\n") if line.strip()]
    return [(f"{session}:{n}", json.loads(line)) for n, line in enumerate(lines, 1)]


def check(root, ledger=ledger_log.LEDGER_NAME, session=None, closed=False):
    """(problems, facts) for one session: problems are strings; facts a dict of counts."""
    view = scribe.load(root, ledger)
    session = session or view.head
    problems = [f"scribe: {f}" for f in view.findings
                if not (f.code == "unclosed" and not closed and session == view.head
                        and session in str(f.where))]
    if closed and session == view.head and not view.head_closed:
        problems.append(f"session {session} has no trailer")
    if closed and view.is_leased():
        problems.append("lease.json is present: a harness holds the ledger or died holding it")
    lines = session_lines(root, ledger, session)
    records, labels = [], collections.defaultdict(set)
    texts = {}
    for entry_id, line in lines:
        name = line.get("name", "")
        if "tag" in line:
            labels[name].add(line["tag"])
        elif "untag" in line:
            labels[name].discard(line["untag"])
        elif "body" in line and name.startswith(f"{ledger_log.HARNESS_PREFIX}{session}/"):
            records.append((entry_id, line))
        elif "body" in line and name.startswith((f"{ledger_log.TRANSCRIPT_PREFIX}{session}/",
                                                 f"{ledger_log.EXEC_PREFIX}{session}/")):
            texts[entry_id] = line
    runs = [line["body"] for _, line in records if line["body"].get("kind") == "run"]
    fmt = runs[0].get("record_format", 1) if runs else None
    facts = {"session": session, "lines": len(lines), "records": len(records),
             "record_format": fmt,
             "kinds": dict(collections.Counter(line["body"].get("kind") for _, line in records))}
    if fmt != ledger_log.RECORD_FORMAT:
        facts["note"] = f"record format {fmt}: only the scribe's findings are checked"
        return problems, facts
    kinds = [line["body"]["kind"] for _, line in records]
    problems += _check_records(session, records, labels)
    problems += _check_texts(view, records, texts, labels)
    problems += _check_order(records)
    problems += _check_bounds(runs[0], records)
    streams = [line["body"] for _, line in records if line["body"]["kind"] == "message_stream"]
    problems += [f"message_stream {s.get('item_id')}: fragments do not add up to the text"
                 for s in streams if s.get("matches_text") is False]
    summary = [line["body"] for _, line in records if line["body"]["kind"] == "summary"]
    if not summary:
        problems.append("no summary record: the run did not end cleanly")
    else:
        for agent, stats in summary[-1].get("agents", {}).items():
            if stats.get("unreviewed_calls"):
                problems.append(f"{agent} made unreviewed model tool calls: "
                                f"{stats['unreviewed_calls']}")
    facts.update(agents=sorted(summary[-1].get("agents", {})) if summary else [],
                 messages_streamed=len(streams),
                 fragments=sum(s.get("fragments", 0) for s in streams),
                 tool_calls=kinds.count("tool_call"), files_written=kinds.count("fs_write"),
                 scripts_run=kinds.count("python_exec"), spawns=kinds.count("subagent_spawn"))
    return problems, facts


def _check_records(session, records, labels):
    problems = []
    for entry_id, line in records:
        name, body = line["name"], line["body"]
        kind = body.get("kind")
        if line.get("author") != ledger_log.HARNESS_AUTHOR:
            problems.append(f"{entry_id} {name}: authored {line.get('author')!r}")
        if not name.endswith(f".{kind}"):
            problems.append(f"{entry_id} {name}: its kind is {kind!r}")
        if labels.get(name) != {ledger_log.LOG_LABEL_PREFIX + str(kind)}:
            problems.append(f"{entry_id} {name}: labels {sorted(labels.get(name, ()))}")
    return problems


def _check_texts(view, records, texts, labels):
    problems, linked = [], collections.Counter()
    for entry_id, line in records:
        body = line["body"]
        if "text_id" not in body:
            continue
        target = view.line(body["text_id"]) or {}
        link = str(body.get("text", ""))
        if f"[[{target.get('name')}]]" != link:
            problems.append(f"{entry_id}: {link} does not name version {body['text_id']}")
        if "text_author" in body and target.get("author") != body["text_author"]:
            problems.append(f"{entry_id}: {body['text_id']} is by {target.get('author')!r}, "
                            f"recorded as {body['text_author']!r}")
        if not isinstance(target.get("body"), str):
            problems.append(f"{entry_id}: {body['text_id']} has no text body")
        if body["kind"] in TEXT_KINDS:
            linked[body["text_id"]] += 1
    for entry_id, line in texts.items():
        if linked[entry_id] != 1:
            problems.append(f"{entry_id} {line['name']}: linked from {linked[entry_id]} "
                            f"message or python_exec records, not 1")
        name = line["name"]
        want = ({ledger_log.EXEC_LABEL} if name.startswith(ledger_log.EXEC_PREFIX)
                else {f"{ledger_log.TRANSCRIPT_LABEL}.{name.rsplit('-', 1)[-1]}"})
        if labels.get(name) != want:
            problems.append(f"{entry_id} {name}: labels {sorted(labels.get(name, ()))}, "
                            f"not {sorted(want)}")
    return problems


def _check_order(records):
    """Each intent is followed by its result; each spawn by the subagent's finish."""
    problems, open_intents, spawned = [], [], {}
    for entry_id, line in records:
        body = line["body"]
        kind = body["kind"]
        if kind in INTENTS:
            open_intents.append((entry_id, kind, body.get("agent")))
        elif kind in INTENTS.values():
            match = next((i for i in open_intents
                          if INTENTS[i[1]] == kind and i[2] == body.get("agent")), None)
            if match is None:
                problems.append(f"{entry_id}: a {kind} record with no {kind}_started before it")
            else:
                open_intents.remove(match)
        elif kind == "subagent_spawn":
            spawned[body.get("subagent")] = entry_id
        elif kind == "subagent_finished":
            spawned.pop(body.get("subagent"), None)
    problems += [f"{entry_id}: {kind} has no result record" for entry_id, kind, _ in open_intents]
    problems += [f"{entry_id}: subagent {agent} never finished" for agent, entry_id in spawned.items()]
    return problems


def _check_bounds(run, records):
    problems, held = [], {}
    for entry_id, line in records:
        body = line["body"]
        if body["kind"] != "agent_started":
            continue
        agent, parent = body.get("agent"), body.get("parent")
        held[agent] = body.get("bounds")
        try:
            if parent is None:
                if body.get("bounds") != run.get("root_bounds"):
                    problems.append(f"{entry_id}: {agent}'s bounds are not the run's root bounds")
                bd.Bounds(body.get("bounds"))
            else:
                bd.child_bounds(bd.Bounds(held.get(parent)), body.get("bounds"))
        except bd.BoundsError as error:
            problems.append(f"{entry_id}: {agent}'s bounds: {error}".replace("\n", "; "))
        invariant = bd.Bounds(body.get("bounds")).invariant_problems() \
            if isinstance(body.get("bounds"), dict) else []
        problems += [f"{entry_id}: {agent}: {p}" for p in invariant]
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ledger_log.LEDGER_ROOT), help="folder of ledgers")
    parser.add_argument("--ledger", default=ledger_log.LEDGER_NAME)
    parser.add_argument("--session", help="session stamp (default: the newest)")
    parser.add_argument("--closed", action="store_true",
                        help="also require a trailer on the session and no lease")
    args = parser.parse_args(argv)
    problems, facts = check(args.root, args.ledger, args.session, args.closed)
    for key, value in facts.items():
        print(f"{key}: {json.dumps(value) if isinstance(value, (dict, list)) else value}")
    for problem in problems:
        print(f"PROBLEM {problem}")
    print("clean" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
