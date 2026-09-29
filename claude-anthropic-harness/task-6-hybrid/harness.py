"""The harness's configuration and wiring (split out of app.py, 2026-09-25).

For one launch over an open ledger:
  build_institution()  task 6: the shared environment (agent.HarnessEnv), the spawner, the
                       institution (governance.Institution) and the governor, with its prompt;
                       task owners are made later, by dispatch
  build_harness()      task 5's single top-level agent (the "pilot"), still used by
                       live_turn.py and by the tests of everything below the governor
Tests call these over a temporary tree, with a fake client factory (and, for GPT agents, Codex
settings with a fake model). app.py calls them over the real nimoi.

Bounds files, human-edited (notation: bounds.py):
  governor_bounds.txt  the governor's: read, its own ledger area; no write, exec or spawn
  owner_ceiling.txt    the most the governor may grant a task owner
  bounds.txt           the pilot's (task 5)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent import AgentCore, BudgetPool, HarnessEnv, agent_author, default_client_factory, planned_tools
from bounds import Bounds, BoundsError, fs_target
from governance import (DEFAULT_ALLOWANCE_USD, GOVERNOR_MODELS, MAX_OWNERS, OWNER_MAX_TURNS, OWNER_MODELS,
                        Institution, role_tool_names)
from prompts import build_prompt, lineage_values
from scribe_import import NIMOI_ROOT
from subagents import MODELS, Spawner

TASK_DIR = Path(__file__).resolve().parent
LEDGER_ROOT = TASK_DIR / "ledger"
LEDGER_NAME = "claude-anthropic-harness-t6"  # distinct from tasks 4-5's, so cross-ledger links are unambiguous
BOUNDS_FILE = TASK_DIR / "bounds.txt"  # the pilot's permissions (task 5); human-edited
GOVERNOR_BOUNDS_FILE = TASK_DIR / "governor_bounds.txt"
CEILING_FILE = TASK_DIR / "owner_ceiling.txt"
WORKSPACE = TASK_DIR / "workspace"  # where agents write files, and scripts' cwd
SCRIPTS = TASK_DIR / "scripts"  # what agents may run; written only by humans ("promotion")
RUNTIME = TASK_DIR / ".runtime"  # gitignored: Codex's per-agent state and the isolated test CODEX_HOME
HARNESS_AUTHOR = "harness:claude-anthropic-harness/task-6-hybrid"
# Scripted live runs (live_turn.py, live_institution.py): the developer agent writes the message, under
# the founder's authorization, so the ledger must not say a human typed it (task 6 finding, 2026-09-29).
DRIVER_AUTHOR = "developer:claude-code-session"
MODEL = "claude-sonnet-5"  # the pilot's pin, as in tasks 1-4 (founder decision, 2026-09-23)
GOVERNOR_MODEL = GOVERNOR_MODELS[0]  # rules.md 6c b1: claude opus only
BUDGET_USD = 5.00  # per launch (founder decision, 2026-09-23)
MAX_TURNS = 20  # model rounds per message for the pilot, the governor and subagents; reading onboarding takes several


def load_bounds(path: Path) -> Bounds:
    """Bounds from a human-edited file, refused if they break the invariant."""
    bounds = Bounds.parse(path.read_text(encoding="utf-8"))
    problems = bounds.invariant_problems()
    if problems:
        raise BoundsError(f"{path.name}: " + "; ".join(problems))
    return bounds


def load_top_bounds(path: Path = BOUNDS_FILE) -> Bounds:
    return load_bounds(path)


def _environment(scribe: Any, ledger: Any, bus: Any, *, budget_usd: float, max_turns: int, root: Path,
                 workspace: Path, scripts: Path, client_factory: Callable[..., Any],
                 codex: Any) -> tuple[HarnessEnv, Callable[..., str]]:
    """The shared environment, the spawner, and the prompt builder every agent's prompt comes from."""
    facts = {"ledger": LEDGER_NAME, "ledger_session": ledger.session, "ledger_sessions": len(ledger.sessions),
             "scribe_id": scribe.SCRIBE_ID, "ledger_version": scribe.LEDGER_VERSION,
             "harness_author": HARNESS_AUTHOR}
    env = HarnessEnv(scribe=scribe, ledger=ledger, bus=bus, root=root, workspace=workspace,
                     never_writable=[fs_target(scripts, root)], budget=BudgetPool(budget_usd),
                     max_turns=max_turns, harness_author=HARNESS_AUTHOR, ledger_facts=facts,
                     client_factory=client_factory, codex=codex)
    common = dict(nimoi_root=str(root), ledger=LEDGER_NAME, session=ledger.session,
                  workspace=fs_target(workspace, root), scripts=fs_target(scripts, root),
                  models=", ".join(f"`{m}`" for m in MODELS))

    def prompt(kind: str, *, agent_id: str, model: str, bounds: Bounds, depth: int, can_spawn: bool, role: str,
               tool_bounds: Bounds | None = None, **extra: Any) -> str:
        tools = planned_tools(tool_bounds or bounds, can_spawn, role_tool_names(role) if env.institution else [])
        return build_prompt(kind, tools=tools, **common, **lineage_values(model), agent_id=agent_id, model=model,
                            agent_author=agent_author(model, agent_id, env.session), bounds=bounds.render(),
                            depth=depth, max_depth=env.spawner.max_depth, role=role, **extra)

    env.spawner = Spawner(env, make_prompt=lambda **kw: prompt("subagent", **kw))
    return env, prompt


def build_harness(scribe: Any, ledger: Any, bus: Any, *, model: str, top_bounds: Bounds, budget_usd: float,
                  max_turns: int, root: Path = NIMOI_ROOT, workspace: Path = WORKSPACE, scripts: Path = SCRIPTS,
                  client_factory: Callable[..., Any] = default_client_factory,
                  codex: Any = None) -> tuple[HarnessEnv, AgentCore]:
    """Task 5's single top-level agent. `codex`: backend_codex settings, or None when no GPT agent may run."""
    env, prompt = _environment(scribe, ledger, bus, budget_usd=budget_usd, max_turns=max_turns, root=root,
                               workspace=workspace, scripts=scripts, client_factory=client_factory, codex=codex)
    top = AgentCore(env, agent_id="pilot", model=model, bounds=top_bounds, role="pilot",
                    system_prompt=prompt("top", agent_id="pilot", model=model, bounds=top_bounds, depth=0,
                                         can_spawn=True, role="pilot"))
    return env, top


def build_institution(scribe: Any, ledger: Any, bus: Any, *, governor_bounds: Bounds, ceiling: Bounds,
                      budget_usd: float, max_turns: int, model: str = GOVERNOR_MODEL, root: Path = NIMOI_ROOT,
                      workspace: Path = WORKSPACE, scripts: Path = SCRIPTS,
                      client_factory: Callable[..., Any] = default_client_factory, codex: Any = None,
                      owner_models: tuple[str, ...] = OWNER_MODELS, max_owners: int = MAX_OWNERS,
                      owner_max_turns: int = OWNER_MAX_TURNS) -> tuple[HarnessEnv, AgentCore, Institution]:
    """Task 6: the governor and the institution it runs. Task owners are made by dispatch."""
    if model not in GOVERNOR_MODELS:
        raise ValueError(f"the governor must be one of {', '.join(GOVERNOR_MODELS)} (rules.md 6c)")
    env, prompt = _environment(scribe, ledger, bus, budget_usd=budget_usd, max_turns=max_turns, root=root,
                               workspace=workspace, scripts=scripts, client_factory=client_factory, codex=codex)
    institution = Institution(env, ceiling=ceiling, scripts_dir=scripts, owner_models=owner_models,
                              max_owners=max_owners, owner_max_turns=owner_max_turns,
                              make_owner_prompt=lambda **kw: prompt("owner", depth=1, can_spawn=True, role="owner",
                                                                    **kw))
    env.institution = institution
    governor = AgentCore(env, agent_id="governor", model=model, bounds=governor_bounds, role="governor",
                         system_prompt=prompt("governor", agent_id="governor", model=model, bounds=governor_bounds,
                                              depth=0, can_spawn=False, role="governor",
                                              owner_models=", ".join(f"`{m}`" for m in owner_models),
                                              default_allowance=f"{DEFAULT_ALLOWANCE_USD:.2f}",
                                              owner_max_turns=owner_max_turns, max_owners=max_owners,
                                              ceiling=ceiling.render()))
    institution.governor = governor
    return env, governor, institution
