"""Prompt one Claude agent to say hello world, print its response, and exit."""

import argparse
import asyncio
from pathlib import Path
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)


async def run(model: str) -> int:
    options = ClaudeAgentOptions(
        model=model,
        cwd=str(Path(__file__).resolve().parent),
        tools=[],
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=1,
        extra_args={"no-session-persistence": None},
    )
    parts: list[str] = []
    result: ResultMessage | None = None
    async for message in query(prompt="Say hello world.", options=options):
        if isinstance(message, AssistantMessage):
            parts.extend(block.text for block in message.content if isinstance(block, TextBlock))
        elif isinstance(message, ResultMessage):
            result = message

    response = "\n".join(parts).strip()
    if response:
        print(response)
    if result is None or result.is_error or not response:
        print("Hello run failed: missing response or unsuccessful SDK result.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="sonnet", help="Claude model ID or alias (default: sonnet)")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.model))
    except Exception as exc:
        # Exception strings can contain subprocess diagnostics; don't expose credentials.
        print(f"Hello run failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
