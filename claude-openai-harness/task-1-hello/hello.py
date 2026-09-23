"""task-1-hello: prompt a GPT agent to say hello world, print its response, exit.

Swimlane: claude-openai-harness (OpenAI Agents SDK, OpenAI models only).

STATUS: draft. The imports and the no-key failure path are checked against
openai-agents 0.22.3. It has never reached a model because no key is available
yet. See ../notebook.md.

The agent is kept isolated from its surroundings:
- no tools and no handoffs, one turn;
- tracing export to OpenAI is off (see ../mem/task-1-decisions.md);
- the prompt and instructions are fixed in this file.

Usage:
    python hello.py [--model MODEL]

Auth: OPENAI_API_KEY from the environment, or from <swimlane>/.env if that
file exists. An existing environment variable wins. This script never prints
a key.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from agents import Agent, Runner, set_tracing_disabled
from agents.models import get_default_model
from dotenv import load_dotenv

PROMPT = "Say hello world."
MODEL = None  # not pinned yet: waiting on founder decision

SWIMLANE = Path(__file__).resolve().parent.parent


async def run(model: str | None) -> int:
    set_tracing_disabled(True)

    agent_kwargs = {"name": "hello", "instructions": "You are a helpful assistant."}
    if model:
        agent_kwargs["model"] = model
    agent = Agent(**agent_kwargs)

    try:
        result = await Runner.run(agent, PROMPT, max_turns=1)
    except Exception as exc:  # report the failure type, not the payload
        print(f"[error={type(exc).__name__}: {exc}]", file=sys.stderr)
        return 1

    print(str(result.final_output).strip())

    # Provenance goes to stderr so stdout stays exactly the agent's reply.
    usage = [r.usage for r in result.raw_responses]
    ids = [r.response_id for r in result.raw_responses]
    print(
        f"[model={agent.model or get_default_model() + ' (sdk default)'} responses={len(result.raw_responses)} "
        f"response_ids={','.join(i or '?' for i in ids)} "
        f"input_tokens={sum(u.input_tokens for u in usage)} "
        f"output_tokens={sum(u.output_tokens for u in usage)}]",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=MODEL, help="OpenAI model id")
    args = parser.parse_args()
    load_dotenv(SWIMLANE / ".env", override=False)
    return asyncio.run(run(args.model))


if __name__ == "__main__":
    sys.exit(main())
