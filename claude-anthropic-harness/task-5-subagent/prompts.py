"""System prompts for top-level agents and subagents, from the templates in prompts/.

    top.md       the test pilot, directed by the user
    subagent.md  a subagent, directed by its parent's pinned instructions
    toolkit.md   bounds, fixed rules, limits and tools: the same text for every agent (rules.md 5e)
    spawn.md     how to spawn; included only for agents allowed to

The tools section is generated from the agent's actual tools (agent.planned_tools), so an
agent reads about exactly the tools it has. Placeholders are {name}. fill() replaces only
the names it is given, so literal braces elsewhere survive. The filled prompt is recorded
in full when each agent starts.
"""

from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
SPAWN_TOOL = "mcp__agents__spawn"

# One bullet per tool, in display order. {placeholders} are filled like the templates.
TOOL_DOCS = {
    "mcp__fs__list": "**`mcp__fs__list`:** list one directory within `fs.read`. Entries you may not read are "
                     "hidden and only counted.",
    "mcp__fs__read": "**`mcp__fs__read`:** read a text file within `fs.read`, by line. It returns "
                     "`next_offset` (null at the end) and the file's sha256.",
    "mcp__fs__search": "**`mcp__fs__search`:** search text files under a directory within `fs.read` for a "
                       "regular expression. Excluded directories are skipped, and listed as pruned.",
    "mcp__fs__write": "**`mcp__fs__write`:** write one exact ledger revision (`entry_id`) to a file within "
                      "`fs.write`. Write the entry first with `mcp__ledger__write`, which returns its id. "
                      "`expected_sha256` null creates a new file; to replace a file, pass its current sha256 "
                      "(from `mcp__fs__read`). The harness records which revision each file came from.",
    "mcp__exec__python": "**`mcp__exec__python`:** run a script within `fs.exec`. The harness records the run "
                         "first, then the output (exit code, stdout, stderr), and returns the output and a "
                         "`[[link]]`. Scripts run isolated, with a timeout, no stdin, and the workspace as "
                         "their working directory. `safe_probe.py` in {scripts} is a harmless test script.",
    "mcp__calc__add": "**`mcp__calc__add`:** adds two numbers.",
    "mcp__ledger__read": "**`mcp__ledger__read`:** read a ledger name within `ledger.read`, or one exact "
                         "revision by `id`.",
    "mcp__ledger__list": "**`mcp__ledger__list`:** list ledger names within `ledger.read`, by prefix or label.",
    "mcp__ledger__write": "**`mcp__ledger__write`:** write your own entries within `ledger.write`. To update "
                          "one, pass `prev` = its current id. You cannot delete.",
    SPAWN_TOOL: "**`mcp__agents__spawn`:** start a subagent; see Subagents below.",
}


def fill(template: str, **values: Any) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def latest_onboarding(root: Path) -> str:
    """The latest onboarding as a bounds target. Minors are zero-padded, so name order is version order."""
    files = sorted(p.name for p in (Path(root) / "origins").glob("onboarding_*.md"))
    if not files:
        raise FileNotFoundError(f"no onboarding_*.md in {Path(root) / 'origins'}")
    return f"/origins/{files[-1]}"


def tool_section(tools: list[str], **values: Any) -> str:
    return "\n".join("- " + fill(doc, **values) for name, doc in TOOL_DOCS.items() if name in tools)


def build_prompt(kind: str, *, tools: list[str], **values: Any) -> str:
    """kind is "top" or "subagent". `tools` is the agent's tool list. values fill every placeholder."""
    read = lambda name: (PROMPT_DIR / name).read_text(encoding="utf-8")  # noqa: E731
    spawn = fill(read("spawn.md"), **values) if SPAWN_TOOL in tools else (
        "## Subagents\n\nYou cannot spawn subagents: you are at the depth limit, or spawning is not enabled.")
    toolkit = fill(read("toolkit.md"), **values, tools=tool_section(tools, **values), spawn=spawn)
    return fill(read(f"{kind}.md"), **values, toolkit=toolkit)
