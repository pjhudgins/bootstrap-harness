"""System prompts for top-level agents and subagents, from the templates in prompts/.

    top.md       the test pilot, directed by the user (task 5's single agent; live_turn.py)
    governor.md  task 6: the governor, which talks with the human and dispatches task owners
    owner.md     task 6: a task owner, directed by the governor's pinned assignment
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
    "mcp__gov__dispatch": "**`mcp__gov__dispatch`:** start a task owner in the background; see Dispatching above.",
    "mcp__gov__status": "**`mcp__gov__status`:** the owners, open requests, approvals waiting for the human, and "
                        "the budget.",
    "mcp__gov__message_owner": "**`mcp__gov__message_owner`:** give an idle task owner a new instruction.",
    "mcp__gov__stop_owner": "**`mcp__gov__stop_owner`:** end a task owner (its turn, its open requests, its session).",
    "mcp__gov__resolve": "**`mcp__gov__resolve`:** resolve an owner's request; see Requests above.",
    "mcp__gov__request": "**`mcp__gov__request`:** ask the governor for a decision; your work waits until it is "
                         "resolved (see Your role above).",
}
NO_SPAWN = {  # the Subagents section for agents without the spawn tool
    "governor": "## Subagents\n\nYou do not spawn subagents: task owners do, within their own bounds. You dispatch "
                "task owners instead (above).",
    "subagent": "## Subagents\n\nYou cannot spawn subagents: in this harness only task owners do.",
}
NO_SPAWN_DEFAULT = "## Subagents\n\nYou cannot spawn subagents: you are at the depth limit, or spawning is not enabled."


CLAUDE_NOTES = (
    "**CLI text is not a tool list.** The Claude Code CLI underneath this harness may add generic text of its own, "
    "such as an environment block naming Bash or PowerShell, or budget reminders. A tool named there but missing "
    "from the list above is not available to you. Report such mismatches as observations.")
GPT_NOTES = (  # task 6: what Codex 0.158 offers under backend_codex.RESTRICTIONS (tests/test_codex.py)
    "**How to call your tools.** You run on the Codex App Server, which offers the tools above under its own "
    "names. This prompt uses the harness names, `mcp__<server>__<name>`, which is also how every call is recorded.\n"
    "- If Codex gives you a JavaScript `exec` tool, the harness tools are reachable only inside it, as "
    "`await tools.<server>__<name>({...})`. For example, `mcp__fs__read` is "
    "`await tools.fs__read({\"path\": \"/origins/...\"})`. Pass `text(result)` to see a result; results are text, "
    "often JSON. `wait` collects the output of an `exec` call that is still running.\n"
    "- Otherwise they appear directly as `<server>.<name>`, for example `fs.read`.\n\n"
    "**Codex text is not a tool list.** Codex may add text and helpers of its own. Its user-input tools are not "
    "connected to anyone: `request_user_input` is refused, and `request_user_input_async` only posts your question as "
    "a message. Ask in your reply instead. Any other tool Codex may name (shell "
    "commands, patches, native subagents, web search, images) is not part of your toolkit. Calling one stops you, "
    "and the stop is recorded. Report such mismatches as observations.")


def lineage_values(model: str) -> dict[str, str]:
    """Placeholders that differ by backend: {session_kind} and {runtime_notes}."""
    if model.startswith("gpt-"):
        return {"session_kind": "GPT (Codex)", "runtime_notes": GPT_NOTES}
    return {"session_kind": "Claude", "runtime_notes": CLAUDE_NOTES}


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
    """kind is "top", "governor", "owner" or "subagent". `tools` is the agent's tool list (for task
    owners, built from the ceiling). values fill every placeholder."""
    read = lambda name: (PROMPT_DIR / name).read_text(encoding="utf-8")  # noqa: E731
    spawn = fill(read("spawn.md"), **values) if SPAWN_TOOL in tools else \
        NO_SPAWN.get(values.get("role"), NO_SPAWN_DEFAULT)
    toolkit = fill(read("toolkit.md"), **values, tools=tool_section(tools, **values), spawn=spawn)
    return fill(read(f"{kind}.md"), **values, toolkit=toolkit)
