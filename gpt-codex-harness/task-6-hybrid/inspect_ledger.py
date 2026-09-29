"""Read-only surviving-record checks. Accept a launch name; never read lease contents."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from bounds import Bounds
from ledger_store import HARNESS_AUTHOR, HUMAN_AUTHOR, scribe


def inspect(root, name, closed=False):
    view = scribe.load(Path(root), name)
    problems = [str(f) for f in view.findings if closed or f.code != 'unclosed']
    if closed and (not view.head_closed or view.is_leased()):
        problems.append('Expected closed, unleased ledger')
    events = []
    for name in view.names():
        entry = view.current(name)
        if entry is None or not name.startswith('harness/events/'):
            continue
        if entry.author != HARNESS_AUTHOR or 'harness' not in view.labels(name):
            problems.append('Harness author/protection mismatch: ' + name)
        events.append(entry.body)
    events.sort(key=lambda event: event['sequence'])
    kinds = Counter(event['kind'] for event in events)
    starts, ends, limits = {}, {}, {}
    messages, calls, usage, instructions = [], [], {}, {}
    actor_bounds = {}
    authors = {}
    parents, roles, requests, human_requests = {}, {}, {}, set()
    mounts = {'nimoi': Path('nimoi'), 'workspace': Path('nimoi/workspace'), 'scripts': Path('nimoi/scripts')}
    # These symbolic mounts compare the notation without touching recorded files.
    for event in events:
        kind, data, actor = event['kind'], event['data'], event.get('actor', 'main')
        if kind == 'session_start' and data.get('task') == 'task-6-hybrid':
            actor_bounds['main'] = Bounds(data['ceiling'], mounts)
            authors['main'] = data['governor']['author']
            roles['main'] = 'governor'
        if kind == 'session_start' and 'bounds' in data:
            actor_bounds['main'] = Bounds(data['bounds'], mounts)
        if kind == 'agent_start':
            if actor != 'main' or actor not in roles:
                actor_bounds[actor] = Bounds(data['bounds'], mounts)
            authors[actor] = data['author']
        if kind == 'agent_spawned':
            child, parent = data['id'], data['parent']
            starts[child], instructions[child] = event['sequence'], data['instructions']
            parents[child], authors[child], roles[child] = parent, data['author'], data['role']
            brief = view.line(data['instructions']['id'])
            if not brief or brief.get('name') != data['instructions']['name'] or brief.get('author') != authors.get(parent):
                problems.append('Worker brief is not a pinned supervisor-authored entry: ' + child)
            if parent not in actor_bounds or roles.get(parent) == 'subagent':
                problems.append('Invalid supervisor: ' + child)
            else:
                try:
                    actor_bounds[child] = actor_bounds[parent].child(data['bounds'])
                except ValueError as error:
                    problems.append(f'Worker {child} bounds: {error}')
        if kind == 'governance_requested':
            requests[data['id']] = 'pending'
            if data['needs_human']:
                human_requests.add(data['id'])
        elif kind == 'governance_decided':
            if data['id'] not in requests:
                problems.append('Decision without an owner request')
            if data['outcome'] == 'approved' and data['id'] in human_requests:
                problems.append('Governor bypassed required human approval')
            requests[data['id']] = data['outcome']
        elif kind == 'human_decision':
            if requests.get(data['id']) != 'needs_human':
                problems.append('Human decision without a concrete governor proposal')
            line = view.line(data['decision']['id'])
            if not line or line.get('author') != HUMAN_AUTHOR:
                problems.append('Human decision lacks human-authored text')
            requests[data['id']] = data['outcome']
        elif kind == 'governance_cancelled':
            requests[data['id']] = 'cancelled'
        if kind == 'subagent_started':
            starts[data['id']] = event['sequence']
            instructions[data['id']] = data['instructions']
            if 'author' in data:
                authors[data['id']] = data['author']
            brief = view.line(data['instructions']['id'])
            if 'main' in authors and (not brief or brief.get('author') != authors['main']):
                problems.append('Child brief is not parent-authored')
            if 'main' in actor_bounds:
                try:
                    actor_bounds['main'].child(data['bounds'])
                except ValueError as error:
                    problems.append(f"Child {data['id']} bounds: {error}")
        elif kind in ('subagent_finished', 'agent_finished'):
            ends[data['id']] = event['sequence']
            if data['id'] not in starts:
                problems.append('Finished child has no start: ' + data['id'])
            for ref in data.get('results', []):
                line = view.line(ref['id'])
                if not line or line.get('name') != ref['name'] or line.get('author') != ref['author']:
                    problems.append('Invalid child result reference')
        elif kind in ('message', 'message_checkpoint'):
            ref = view.line(data['text_id'])
            if not ref or ref.get('name') != data['text'][2:-2] or ref.get('author') != data['author'] or not isinstance(ref.get('body'), str):
                problems.append('Invalid text reference: ' + str(event['sequence']))
            if kind == 'message':
                messages.append({'actor': actor, 'direction': data['direction'], 'text_id': data['text_id']})
                if data['direction'] == 'from-user' and data['author'] != HUMAN_AUTHOR:
                    problems.append('Human message has the wrong author')
                if data['direction'] in ('to-user', 'to-parent') and actor in authors and data['author'] != authors[actor]:
                    problems.append('Agent message has the wrong author')
                if data['direction'] == 'to-agent' and actor in instructions and data['text_id'] != instructions[actor]['id']:
                    problems.append('Child did not receive its pinned brief')
        elif kind == 'python_tool':
            calls.append({'actor': actor, 'tool': data['tool'], 'success': data['success'], 'sequence': event['sequence']})
            if roles.get(actor) == 'governor' and data['tool'] in ('add', 'fs_write', 'python_execute', 'subagent_start'):
                problems.append('Governor invoked a task-work tool')
        elif kind == 'usage':
            usage[actor] = data
        elif kind == 'receive' and isinstance(data, dict):
            if data.get('method') == 'thread/tokenUsage/updated':
                usage[actor] = data['params']['tokenUsage']
        elif kind == 'restriction_evidence':
            limits[actor] = data
        elif kind == 'file_write_completed':
            source = view.line(data['source']['id'])
            if not source or not isinstance(source.get('body'), str):
                problems.append('Missing materialization source')
            else:
                body = source['body'].encode('utf-8')
                if hashlib.sha256(body).hexdigest() != data['sha256'] or len(body) != data['bytes']:
                    problems.append('Materialization metadata differs from its source body')
        elif kind == 'script_completed':
            ref = data['output']
            output = view.line(ref['id'])
            if (not output or output.get('name') != ref['name'] or output.get('author') != HARNESS_AUTHOR
                    or not isinstance(output.get('body'), str) or 'protected' not in view.labels(ref['name'])):
                problems.append('Invalid protected script output')
    if closed:
        for child in starts.keys() - ends.keys():
            problems.append('Accepted child has no terminal record: ' + child)
        for rid, state in requests.items():
            if state in ('pending', 'needs_human'):
                problems.append('Closed session has unresolved governance request: ' + rid)
    overlap = any(call['actor'] == 'main' and begin < call['sequence'] < ends.get(child, begin)
                  for child, begin in starts.items() for call in calls)
    return {'ledger': view.ledger, 'closed': view.head_closed, 'leased': view.is_leased(),
            'event_counts': dict(kinds), 'messages': messages, 'children_started': sorted(starts),
            'children_finished': sorted(ends), 'parent_work_during_child': overlap,
            'successful_tools': sorted({c['tool'] for c in calls if c['success']}),
            'refused_tools': [c for c in calls if not c['success']], 'usage': usage,
            'restrictions': limits, 'roles': roles, 'requests': requests, 'problems': problems}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', help='Launch directory name')
    parser.add_argument('--root', type=Path, default=Path(__file__).parent / 'ledgers')
    parser.add_argument('--closed', action='store_true')
    args = parser.parse_args()
    result = inspect(args.root, args.ledger, args.closed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['problems']))
