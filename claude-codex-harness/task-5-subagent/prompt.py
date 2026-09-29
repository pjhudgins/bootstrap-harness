"""Instructions for the root test pilot and for subagents (rules.md tasks 4e-4f, 5e),
filled from the Markdown templates in prompts/ and sent as each thread's
developerInstructions, on top of Codex's own base prompt.

  prompts/common.md     every agent: NIMOI, onboarding, the Bar, the ledger, its bounds
  prompts/bounds.md     the bounds notation, the agent's bounds in it, and the fixed
                        rules: the one place they are stated, the same for every agent
  prompts/root.md       the test pilot
  prompts/subagent.md   a subagent: its task entry and its report
  prompts/delegate.md   how to use subagents, for agents below the nesting cap

A placeholder is {{name}}. Filling fails on a placeholder without a value, and never
touches placeholder-like text inside the values it fills in.
"""

import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
NO_DELEGATION = "You cannot start subagents: you are at the harness's nesting limit."


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


def _delegation(can_delegate, when):
    return fill(template("delegate"), when=when) if can_delegate else NO_DELEGATION


def root_instructions(*, bounds, author, onboarding, agent_id, can_delegate):
    readable = (f"the onboarding file ({onboarding}) and the entries your own tool calls "
                f"produce (script output)")
    return fill(template("root"),
                common=_common(bounds=bounds, author=author, onboarding=onboarding,
                               agent_id=agent_id, always_readable=readable),
                delegation=_delegation(can_delegate, "the user asks you to"))


def subagent_instructions(*, bounds, author, onboarding, agent_id, parent, instructions,
                          instructions_id, can_delegate):
    readable = (f"the onboarding file ({onboarding}), your instructions entry "
                f"({instructions}), and the entries your own tool calls produce (script "
                f"output)")
    return fill(template("subagent"),
                common=_common(bounds=bounds, author=author, onboarding=onboarding,
                               agent_id=agent_id, always_readable=readable),
                agent_id=agent_id, parent=parent, instructions=instructions,
                instructions_id=instructions_id,
                delegation=_delegation(can_delegate, "your instructions ask you to"))
