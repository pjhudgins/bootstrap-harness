"""Track a contiguous, full read of the selected onboarding revision per agent."""


class OnboardingGate:
    def __init__(self, root, journal, actor):
        files = sorted((root / 'origins').glob('onboarding_*.md'))
        if not files:
            raise ValueError('No onboarding_*.md found in origins.')
        self.path = files[-1].relative_to(root).as_posix()
        self.journal, self.actor = journal, actor
        self.offset, self.digest, self.complete = 0, None, False

    def invoke(self, name, handler, arguments):
        if not self.complete and name not in {'fs_read', 'fs_list', 'ledger_read', 'ledger_list'}:
            raise ValueError(f'Read every page of nimoi:/{self.path} in order before substantive tools.')
        result = handler(arguments)
        if name == 'fs_read' and result['path'] == self.path and not self.complete:
            digest = result['sha256']
            if digest != self.digest:
                self.digest, self.offset = digest, 0
            if result['offset'] == self.offset:
                self.offset += len(result['text'])
                if result['next_offset'] is None:
                    self.journal.write('onboarding_complete', {'path': self.path, 'sha256': digest}, actor=self.actor)
                    self.complete = True
        return result


class ToolRegistry(dict):
    """A schema/handler map with one common gate, also inspectable by offline tests."""
    def __init__(self, tools, gate):
        self.gate = gate
        super().__init__((name, (spec, self.wrap(name, handler)))
                         for name, (spec, handler) in tools.items())

    def wrap(self, name, handler):
        def invoke(arguments):
            return self.gate.invoke(name, handler, arguments)
        return invoke
