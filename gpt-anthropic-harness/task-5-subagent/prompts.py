"""Readable, versioned prompt text with explicit substitutions."""
from pathlib import Path
from string import Template

PROMPTS = Path(__file__).with_name("prompts")


def build_prompt(context, service):
    values = {"author": context.author, "agent_id": context.agent_id,
              "onboarding": service.onboarding_path, "bounds": context.bounds.notation(),
              "tools": ", ".join(sorted(service.operations)),
              "delegation": "enabled" if context.can_delegate else "disabled"}
    names = ["common.md", "parent.md" if context.is_parent else "child.md"]
    return "\n\n".join(Template((PROMPTS / name).read_text(encoding="utf-8")).substitute(values) for name in names)
