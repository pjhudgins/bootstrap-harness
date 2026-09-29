"""Identity and role are independent of delegation and presentation."""
from dataclasses import dataclass
from bounds import Bounds


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    author: str
    model: str
    bounds: Bounds
    role: str = "worker"
    can_delegate: bool = False
    parent_id: str | None = None

    @property
    def is_parent(self):
        return self.role == "governor"

    @property
    def provider(self):
        return "codex" if self.model.startswith("gpt-") else "claude"
