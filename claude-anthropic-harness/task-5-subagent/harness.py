"""The harness's configuration and wiring (split out of app.py, 2026-09-25).

build_harness() makes, for one launch over an open ledger:
  - the shared environment (agent.HarnessEnv);
  - the spawner (subagents.Spawner);
  - the top-level agent (agent.AgentCore), with its prompt.
Tests call it over a temporary tree, with a fake client factory. app.py calls it over the
real nimoi.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent import AgentCore, BudgetPool, HarnessEnv, agent_author, default_client_factory, planned_tools
from bounds import Bounds, BoundsError, fs_target
from prompts import build_prompt
from scribe_import import NIMOI_ROOT
from subagents import MODELS, Spawner

TASK_DIR = Path(__file__).resolve().parent
LEDGER_ROOT = TASK_DIR / "ledger"
LEDGER_NAME = "claude-anthropic-harness-t5"  # distinct from task 4's, so cross-ledger links are unambiguous
BOUNDS_FILE = TASK_DIR / "bounds.txt"  # the top-level agent's permissions; human-edited
WORKSPACE = TASK_DIR / "workspace"  # where agents write files, and scripts' cwd
SCRIPTS = TASK_DIR / "scripts"  # what agents may run; written only by humans ("promotion")
HARNESS_AUTHOR = "harness:claude-anthropic-harness/task-5-subagent"
MODEL = "claude-sonnet-5"  # same pin as tasks 1-4 (founder decision, 2026-09-23)
BUDGET_USD = 5.00  # per launch (founder decision, 2026-09-23)
MAX_TURNS = 20  # agent turns per user message; reading onboarding takes several


def load_top_bounds(path: Path = BOUNDS_FILE) -> Bounds:
    """The top-level agent's bounds from bounds.txt, refused if they break the invariant."""
    bounds = Bounds.parse(path.read_text(encoding="utf-8"))
    problems = bounds.invariant_problems()
    if problems:
        raise BoundsError("; ".join(problems))
    return bounds


def build_harness(scribe: Any, ledger: Any, bus: Any, *, model: str, top_bounds: Bounds, budget_usd: float,
                  max_turns: int, root: Path = NIMOI_ROOT, workspace: Path = WORKSPACE, scripts: Path = SCRIPTS,
                  client_factory: Callable[..., Any] = default_client_factory) -> tuple[HarnessEnv, AgentCore]:
    facts = {"ledger": LEDGER_NAME, "ledger_session": ledger.session, "ledger_sessions": len(ledger.sessions),
             "scribe_id": scribe.SCRIBE_ID, "ledger_version": scribe.LEDGER_VERSION,
             "harness_author": HARNESS_AUTHOR}
    env = HarnessEnv(scribe=scribe, ledger=ledger, bus=bus, root=root, workspace=workspace,
                     never_writable=[fs_target(scripts, root)], budget=BudgetPool(budget_usd),
                     max_turns=max_turns, harness_author=HARNESS_AUTHOR, ledger_facts=facts,
                     client_factory=client_factory)
    common = dict(nimoi_root=str(root), ledger=LEDGER_NAME, session=ledger.session,
                  workspace=fs_target(workspace, root), scripts=fs_target(scripts, root),
                  models=", ".join(f"`{m}`" for m in MODELS))

    def prompt(kind: str, *, agent_id: str, model: str, bounds: Bounds, depth: int, can_spawn: bool,
               **extra: Any) -> str:
        return build_prompt(kind, tools=planned_tools(bounds, can_spawn), **common, agent_id=agent_id,
                            model=model, agent_author=agent_author(model, agent_id), bounds=bounds.render(),
                            depth=depth, max_depth=env.spawner.max_depth, **extra)

    env.spawner = Spawner(env, make_prompt=lambda **kw: prompt("subagent", **kw))
    top = AgentCore(env, agent_id="pilot", model=model, bounds=top_bounds,
                    system_prompt=prompt("top", agent_id="pilot", model=model, bounds=top_bounds, depth=0,
                                         can_spawn=env.spawner.can_spawn(0)))
    return env, top
