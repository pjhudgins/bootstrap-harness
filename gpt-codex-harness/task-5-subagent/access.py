"""Ledger permission checks, including revision-pinned references."""

from dataclasses import asdict
from ledger_store import AGENT_NAME, HARNESS_AUTHOR


class LedgerAccess:
    def __init__(self, journal, bounds, author):
        self.journal, self.bounds, self.author = journal, bounds, author

    def list_entries(self, tag=None, prefix='', offset=0, limit=50):
        with self.journal.lock:
            names = self.journal.scribe.labelled(tag) if tag else self.journal.scribe.names(deleted=True)
            names = [n for n in names if n.startswith(prefix) and self.bounds.allows('ledger', 'read', n)]
            entries = [self.journal._entry(n) or {'name': n, 'id': None, 'tags': sorted(self.journal.scribe.labels(n))}
                       for n in names[offset:offset + limit]]
            return {'ledger': self.journal.scribe.ledger,
                    'entries': [{k: e[k] for k in ('name', 'id', 'author', 'tags') if k in e} for e in entries],
                    'next_offset': offset + limit if offset + limit < len(names) else None}

    def read_entry(self, name, history=False):
        self.bounds.require('ledger', 'read', name)
        return self.journal.read_entry(name, history)

    def agent_write(self, name, body, prev, tags):
        self.bounds.require('ledger', 'write', name)
        return self.journal.agent_write(name, body, prev, tags, author=self.author)

    def resolve(self, reference):
        if not isinstance(reference, dict) or set(reference) != {'name', 'id'} or not all(isinstance(v, str) for v in reference.values()):
            raise ValueError('Use a ledger reference with exactly name and id.')
        self.bounds.require('ledger', 'read', reference['name'])
        with self.journal.lock:
            for entry in self.journal.scribe.history(reference['name']):
                if entry.id == reference['id']:
                    if not isinstance(entry.body, str):
                        raise ValueError('The referenced body must be text.')
                    return entry
        raise ValueError('No such revision of this readable ledger name.')

    def reserve_output(self, name):
        if not isinstance(name, str) or len(name) > 256 or not AGENT_NAME.fullmatch(name) or any(p in ('.', '..') for p in name.split('/')):
            raise ValueError('Output name must be a simple name under agent/.')
        for action in ('read', 'write'):
            self.bounds.require('ledger', action, name)
        with self.journal.lock:
            if self.journal.scribe.current(name) or self.journal.scribe.labels(name):
                raise ValueError('Output name is already used or reserved; choose a fresh name.')
            for tag in ('protected', 'script-output'):
                self.journal._call(self.journal.scribe.tag, name, tag, author=HARNESS_AUTHOR)

    def output(self, name, body):
        with self.journal.lock:
            entry_id = self.journal._call(self.journal.scribe.write, name, body, author=HARNESS_AUTHOR)
        return {'name': name, 'id': entry_id, 'text': f'[[{name}]]', 'author': HARNESS_AUTHOR}
