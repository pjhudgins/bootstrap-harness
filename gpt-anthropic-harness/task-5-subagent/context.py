"""Identity and role are independent of delegation and presentation."""
from dataclasses import dataclass
from bounds import Bounds


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    author: str
    model: str
    bounds: Bounds
    role: str = "child"
    can_delegate: bool = False

    @property
    def is_parent(self):
        return self.role == "parent"
