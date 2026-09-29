"""Readable, versioned prompt text with explicit substitutions."""
from pathlib import Path
from string import Template
from models import OWNER_MODELS, WORKER_MODELS

PROMPTS = Path(__file__).with_name("prompts")


def build_prompt(context, service):
    values = {"author": context.author, "agent_id": context.agent_id,
              "onboarding": service.onboarding_path, "bounds": context.bounds.notation(),
              "tools": ", ".join(sorted(service.operations)),
              "delegation": "enabled" if context.can_delegate else "disabled",
              "owner_models": ", ".join(OWNER_MODELS),
              "worker_models": ", ".join(WORKER_MODELS)}
    names = ["common.md", context.role + ".md"]
    return "\n\n".join(Template((PROMPTS / name).read_text(encoding="utf-8")).substitute(values) for name in names)
