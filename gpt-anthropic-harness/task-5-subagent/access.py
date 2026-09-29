"""An agent's ledger interface: fixed identity, explicit grants, pinned bodies."""
from bounds import Denied


class LedgerAccess:
    def __init__(self, log, bounds, author):
        self.log, self.bounds, self.author = log, bounds, author

    def read(self, **args):
        if args.get("name") is not None:
            self.bounds.require("ledger.read", args["name"])
        return self.log.read(**args, visible=lambda name: self.bounds.permits("ledger.read", name))

    def write(self, name, body, tags, prev=None):
        self.bounds.require("ledger.read", name)
        self.bounds.require("ledger.write", name)
        if not all(isinstance(tag, str) for tag in tags) or not set(tags) <= set(self.bounds.get("ledger.tags")):
            raise Denied("Outside ledger.tags bounds.")
        return self.log.agent_write(name, body, tags, prev, author=self.author)

    def resolve(self, entry_id):
        entry = self.log.body_at(entry_id)
        self.bounds.require("ledger.read", entry["name"])
        return entry

    def reserve_output(self, name):
        """Caller supplies a fresh readable/writable name; authorship stays harness-owned."""
        self.bounds.require("ledger.read", name)
        self.bounds.require("ledger.write", name)
        return self.log.reserve_output(name)
