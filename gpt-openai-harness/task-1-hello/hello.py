"""Ask one OpenAI agent to say hello world, print its response, and exit."""

import asyncio
import os
from pathlib import Path

from agents import Agent, Runner, RunConfig
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "Set OPENAI_API_KEY in the environment or gpt-openai-harness/.env."
        )

    agent = Agent(
        name="Hello",
        instructions="Follow the user's greeting request exactly.",
        model=os.environ.get("OPENAI_MODEL", "gpt-6-astra"),
    )
    result = await Runner.run(
        agent,
        "Say hello world. Reply with only: hello world",
        max_turns=1,
        run_config=RunConfig(tracing_disabled=True),
    )
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
