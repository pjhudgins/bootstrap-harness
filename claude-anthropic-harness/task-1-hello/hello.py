"""task-1-hello: prompt a Claude agent to say hello world, print its response, exit.

Swimlane: claude-anthropic-harness (Claude Agent SDK, Claude models only).

The agent is deliberately isolated from its surroundings:
- no tools (tools=[]), one turn;
- no filesystem settings (setting_sources=[]), so it does not pick up the
  NIMOI CLAUDE.md, user memory, hooks or plugins from wherever it is run;
- no session persistence, so the run writes no transcript under ~/.claude.

Usage:
    python hello.py [--model MODEL]   # default claude-sonnet-5

Auth is whatever the Claude Code CLI already uses (ANTHROPIC_API_KEY if set,
otherwise the logged-in CLI account). This script never reads or prints a key.
"""

import argparse
import asyncio
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

PROMPT = "Say hello world."
MODEL = "claude-sonnet-5"  # pinned by founder decision, 2026-09-23


async def run(model: str) -> int:
    options = ClaudeAgentOptions(
        tools=[],
        setting_sources=[],
        max_turns=1,
        model=model,
        extra_args={"no-session-persistence": None},
    )

    text_parts: list[str] = []
    models_seen: set[str] = set()
    result: ResultMessage | None = None

    async for message in query(prompt=PROMPT, options=options):
        if isinstance(message, AssistantMessage):
            models_seen.add(message.model)
            text_parts.extend(b.text for b in message.content if isinstance(b, TextBlock))
        elif isinstance(message, ResultMessage):
            result = message

    print("".join(text_parts).strip())

    # Provenance goes to stderr so stdout stays exactly the agent's reply.
    if result is not None:
        cost = f"${result.total_cost_usd:.6f}" if result.total_cost_usd is not None else "n/a"
        print(
            f"[model={','.join(sorted(models_seen)) or '?'} "
            f"turns={result.num_turns} cost={cost} is_error={result.is_error}]",
            file=sys.stderr,
        )
        return 1 if result.is_error else 0

    print("[no ResultMessage received]", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=MODEL, help=f"Claude model id (default {MODEL})")
    args = parser.parse_args()
    return asyncio.run(run(args.model))


if __name__ == "__main__":
    sys.exit(main())
