# From ../task-5-subagent/check_ledger.py. Task 6: the governor's bounds, grants,
# requests and task owners.
"""Check one session of this harness's ledger after a run. Exit 0 when it is clean.

    python check_ledger.py                        # the newest session in ledgers/
    python check_ledger.py --root .runtime/fake-ledgers --session 20260928T185523Z --closed

Read-only: it loads the ledger with scribe.load (no lease, never writes) and prints
metadata only, never a body. It checks:
  - the scribe's own findings (an open newest session is allowed unless --closed);
  - every harness record: named harness/<session>/<seq>.<kind>, by the harness, labelled
    exactly log.<kind>; every transcript text labelled transcript.<role>, every script
    output exec, every request request;
  - every text link (message, python_exec, request, message_stream): the linked version
    exists, has the linked name, the recorded author and a text body; every transcript/,
    exec/ and requests/ text of the session is linked from exactly one record;
  - intent before effect, and every start its end: fs_write_started -> fs_write,
    python_exec_started -> python_exec, subagent_spawn -> subagent_finished, owner_start ->
    owner_closed, request -> request_decided;
  - bounds: the governor's are the run's; each other agent's lie inside its parent's as
    they stood when it started (grants included), and keep fs.write out of fs.exec
    folders; a grant by the governor lies inside the governor's bounds (a grant beyond
    them must be the human's);
  - message streams: the fragments added up to the final text;
  - the run ended with a summary in which no agent made an unreviewed model tool call.
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
PAIRS = {"fs_write_started": ("fs_write", "path"), "python_exec_started": ("python_exec", "script"),
         "subagent_spawn": ("subagent_finished", "subagent"), "owner_start": ("owner_closed", "owner"),
         "request": ("request_decided", "request")}
TEXT_KINDS = ("message", "python_exec", "request")  # records whose text_id is their own text
TEXT_LABELS = {ledger_log.EXEC_PREFIX: ledger_log.EXEC_LABEL,
               ledger_log.REQUEST_PREFIX: ledger_log.REQUEST_LABEL}


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
    records, labels, texts = [], collections.defaultdict(set), {}
    text_prefixes = tuple(f"{p}{session}/" for p in (ledger_log.TRANSCRIPT_PREFIX,
                                                     ledger_log.EXEC_PREFIX,
                                                     ledger_log.REQUEST_PREFIX))
    for entry_id, line in lines:
        name = line.get("name", "")
        if "tag" in line:
            labels[name].add(line["tag"])
        elif "untag" in line:
            labels[name].discard(line["untag"])
        elif "body" in line and name.startswith(f"{ledger_log.HARNESS_PREFIX}{session}/"):
            records.append((entry_id, line))
        elif "body" in line and name.startswith(text_prefixes):
            texts[entry_id] = line
    bodies = [line["body"] for _, line in records]
    runs = [b for b in bodies if b.get("kind") == "run"]
    facts = {"session": session, "lines": len(lines), "records": len(records),
             "record_format": runs[0].get("record_format") if runs else None,
             "kinds": dict(collections.Counter(b.get("kind") for b in bodies))}
    if not runs:
        problems.append("no run record")
        return problems, facts
    problems += _check_records(records, labels)
    problems += _check_texts(view, records, texts, labels)
    problems += _check_pairs(records)
    problems += _check_bounds(runs[0], records)
    streams = [b for b in bodies if b["kind"] == "message_stream"]
    problems += [f"message_stream {s.get('item_id')}: fragments do not add up to the text"
                 for s in streams if s.get("matches_text") is False]
    summary = [b for b in bodies if b["kind"] == "summary"]
    if not summary:
        problems.append("no summary record: the run did not end cleanly")
    else:
        for agent, stats in summary[-1].get("agents", {}).items():
            if stats.get("unreviewed_calls"):
                problems.append(f"{agent} made unreviewed model tool calls: "
                                f"{stats['unreviewed_calls']}")
    kinds = [b["kind"] for b in bodies]
    facts.update(agents=sorted(summary[-1].get("agents", {})) if summary else [],
                 messages_streamed=len(streams),
                 fragments=sum(s.get("fragments", 0) for s in streams),
                 tool_calls=kinds.count("tool_call"), files_written=kinds.count("fs_write"),
                 scripts_run=kinds.count("python_exec"), owners=kinds.count("owner_start"),
                 subagents=kinds.count("subagent_spawn"), requests=kinds.count("request"),
                 grants=kinds.count("bounds_granted"))
    return problems, facts


def _check_records(records, labels):
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
        if "unrecordable" in body:  # the scribe refused it; only a repr() survived
            problems.append(f"{entry_id} {name}: recorded only as text ({body['unrecordable']})")
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
        name = line["name"]
        if linked[entry_id] != 1:
            problems.append(f"{entry_id} {name}: linked from {linked[entry_id]} records, not 1")
        want = next(({label} for prefix, label in TEXT_LABELS.items() if name.startswith(prefix)),
                    {f"{ledger_log.TRANSCRIPT_LABEL}.{name.rsplit('-', 1)[-1]}"})
        if labels.get(name) != want:
            problems.append(f"{entry_id} {name}: labels {sorted(labels.get(name, ()))}, "
                            f"not {sorted(want)}")
    return problems


def _check_pairs(records):
    """Each intent is followed by its result, and each start by its end."""
    problems, open_ = [], []
    ends = {end: (start, key) for start, (end, key) in PAIRS.items()}
    for entry_id, line in records:
        body = line["body"]
        kind = body["kind"]
        if kind in PAIRS:
            open_.append((entry_id, kind, body.get(PAIRS[kind][1]), body.get("agent")))
        elif kind in ends:
            start, key = ends[kind]
            match = next((o for o in open_ if o[1] == start and o[2] == body.get(key)
                          and (start not in ("fs_write_started", "python_exec_started")
                               or o[3] == body.get("agent"))), None)
            if match is None:
                problems.append(f"{entry_id}: a {kind} record with no {start} before it")
            else:
                open_.remove(match)
    problems += [f"{entry_id}: {kind} {what!r} has no {PAIRS[kind][0]}"
                 for entry_id, kind, what, _ in open_]
    return problems


def _check_bounds(run, records):
    problems, held, governor = [], {}, None
    for entry_id, line in records:
        body = line["body"]
        kind, agent = body["kind"], body.get("agent")
        try:
            if kind == "agent_started":
                bounds = body.get("bounds")
                if body.get("parent") is None:
                    governor = agent
                    if bounds != run.get("governor_bounds"):
                        problems.append(f"{entry_id}: {agent}'s bounds are not the run's")
                else:
                    bd.child_bounds(bd.Bounds(held.get(body["parent"])), bounds)
                held[agent] = bounds
                problems += [f"{entry_id}: {agent}: {p}"
                             for p in bd.Bounds(bounds).invariant_problems()]
            elif kind == "bounds_granted":
                if body.get("by") != ledger_log.HUMAN_AUTHOR:
                    beyond = bd.Bounds(body.get("granted")).problems_as_child_of(
                        bd.Bounds(held.get(governor)))
                    if beyond:
                        problems.append(f"{entry_id}: a grant beyond the governor's bounds, "
                                        f"not by the human: {beyond}")
                held[agent] = body.get("after")
                problems += [f"{entry_id}: {agent}: {p}"
                             for p in bd.Bounds(body.get("after")).invariant_problems()]
        except bd.BoundsError as error:
            problems.append(f"{entry_id}: {agent}'s bounds: {error}".replace("\n", "; "))
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
