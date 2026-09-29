# From ../task-5-subagent/prompt.py. Task 6: one set of instructions per layer.
"""Instructions for the governor, task owners and subagents (rules.md tasks 4e-4f, 5e,
6c), filled from the Markdown templates in prompts/. Sent as each agent's system prompt
(Claude) or developerInstructions (Codex), on top of the engine's own base prompt.

  prompts/common.md     every agent: NIMOI, onboarding, test pilot, the Bar, the ledger,
                        its bounds
  prompts/bounds.md     the bounds notation, the agent's bounds in it, and the fixed
                        rules: the one place they are stated, the same for every agent
  prompts/governor.md   the governor (6c1)
  prompts/owner.md      a task owner (6c2), with prompts/delegate.md
  prompts/subagent.md   a subagent (6c3)

A placeholder is {{name}}. Filling fails on a placeholder without a value, and never
touches placeholder-like text inside the values it fills in.
"""

import re
from pathlib import Path

import policy

PROMPTS = Path(__file__).resolve().parent / "prompts"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
OWN_OUTPUT = "the entries your own tool calls produce (script output)"


def template(name):
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


def fill(text, **values):
    def value(match):
        key = match.group(1)
        if key not in values:
            raise KeyError(f"no value for the placeholder {{{{{key}}}}}")
        return str(values[key])
    return PLACEHOLDER.sub(value, text)


def bounds_block(bounds, always_readable):
    return fill(template("bounds"), bounds=bounds.render(), always_readable=always_readable)


def _common(*, bounds, author, onboarding, agent_id, always_readable):
    return fill(template("common"), onboarding=onboarding, author=author,
                notebook=f"agent/{agent_id}/notebook",
                bounds_block=bounds_block(bounds, always_readable))


def governor_instructions(*, bounds, author, onboarding, agent_id="gov"):
    readable = f"the onboarding file ({onboarding}) and {OWN_OUTPUT}"
    return fill(template("governor"), agent_id=agent_id,
                owner_models=", ".join(policy.LAYER_MODELS["task-owner"]),
                common=_common(bounds=bounds, author=author, onboarding=onboarding,
                               agent_id=agent_id, always_readable=readable))


def owner_instructions(*, bounds, author, onboarding, agent_id, governor, ticket, ticket_id):
    readable = (f"the onboarding file ({onboarding}), your ticket ({ticket}), and "
                f"{OWN_OUTPUT}")
    return fill(template("owner"), agent_id=agent_id, governor=governor, ticket=ticket,
                ticket_id=ticket_id,
                delegation=fill(template("delegate"),
                                subagent_models=", ".join(policy.LAYER_MODELS["subagent"])),
                common=_common(bounds=bounds, author=author, onboarding=onboarding,
                               agent_id=agent_id, always_readable=readable))


def subagent_instructions(*, bounds, author, onboarding, agent_id, parent, instructions,
                          instructions_id):
    readable = (f"the onboarding file ({onboarding}), your instructions entry "
                f"({instructions}), and {OWN_OUTPUT}")
    return fill(template("subagent"), agent_id=agent_id, parent=parent,
                instructions=instructions, instructions_id=instructions_id,
                common=_common(bounds=bounds, author=author, onboarding=onboarding,
                               agent_id=agent_id, always_readable=readable))
