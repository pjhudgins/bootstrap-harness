"""Serve the governor UI offline, for a look at the page: fake Claude agents play a scripted scenario,
and nothing calls a model.

    python tests/ui_demo.py [port]      (default 8790)

Send any message. The governor dispatches owner.1, which reads onboarding, adds two numbers and asks
to promote a draft script. The governor forwards the request to you, and an approval card appears
under "Waiting for you". Approve or deny it, and the owner finishes. The fake nimoi tree and the
ledger are in a temporary folder, deleted when the server stops (End session, or Ctrl+C).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from tests.support import HARNESS, make_tree, scribe  # noqa: E402
from tests.fake_client import FakeFactory  # noqa: E402
from tests.test_governance import CEILING, GOV, OWNER  # noqa: E402

from events import EventBus  # noqa: E402
from harness import build_institution  # noqa: E402
from ledgerlog import LedgerLog  # noqa: E402
from session import AgentSession  # noqa: E402
from web import create_app  # noqa: E402


def scenario(root: Path) -> list[list[tuple]]:
    onboarding = json.dumps({"path": str(root / "origins" / "onboarding_1.02.md")})
    request = json.dumps({"kind": "promote_script", "justification": "A one-line probe I drafted; running it would "
                          "confirm scripts can report back.", "details": {"source": "/work/probe2.py",
                                                                           "name": "probe2.py"}})
    owner = [f"tool: mcp__fs__read {onboarding}", 'tool: mcp__calc__add {"a": 19.5, "b": 22.75}',
             f"tool: mcp__gov__request {request}", "say: The sum is 42.25. Promotion decided; see the request result."]
    return [
        [("tool", "mcp__ledger__write", {"name": "gov/assignments/demo", "body": "\n".join(owner)}),
         ("tool", "mcp__gov__dispatch", {"model": "claude-fable-5-1", "bounds": OWNER,
                                          "assignment": "gov/assignments/demo"}),
         ("text", "I dispatched owner.1 on gov/assignments/demo. It runs in the background; I will tell you "
                  "when it reports or asks for something.")],
        [("tool", "mcp__gov__resolve", {"request": "req.1", "decision": "approve",
                                         "text": "One print statement, no file or network access. Within the "
                                                 "rules; I recommend approval."}),
         ("text", "owner.1 asks to promote probe2.py. I read it: one print statement. I have forwarded it to you "
                  "with my assessment; please decide under 'Waiting for you'.")],
        [("text", "Your decision reached owner.1.")],
        [("text", "owner.1 has finished its turn.")],
    ]


def main(port: int = 8790) -> None:
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        root = tmp / "nimoi"
        make_tree(root)
        (root / "work" / "probe2.py").write_bytes(b"print('hello from a promoted probe')\n")
        ledger = scribe.Scribe.open(tmp, "demo", session_author=HARNESS, create=True)
        bus = EventBus(LedgerLog(scribe, ledger, HARNESS))
        env, governor, institution = build_institution(
            scribe, ledger, bus, governor_bounds=GOV, ceiling=CEILING, budget_usd=5.0, max_turns=10, root=root,
            workspace=root / "work", scripts=root / "scripts",
            client_factory=FakeFactory({"governor": scenario(root)}))
        session = AgentSession(governor, institution)
        server: uvicorn.Server | None = None

        def stop() -> None:
            if server is not None:
                server.should_exit = True

        app = create_app(session, bus, port=port, conversation_id=ledger.session, on_shutdown=stop)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        print(f"ui demo: http://127.0.0.1:{port}/  (send any message to start the scenario)", flush=True)
        try:
            asyncio.run(server.serve())
        finally:
            ledger.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8790)
