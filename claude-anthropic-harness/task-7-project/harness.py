"""The harness's configuration and wiring, for one project (task 7; from task 6's harness.py).

For one launch over a project (project.Project) and its open ledger:
  build_institution()  the shared environment (agent.HarnessEnv), the spawner, the institution
                       (governance.Institution) and the governor, with its prompt. Task owners are
                       made later, by dispatch.
  build_harness()      task 5's single top-level agent (the "pilot"), used by live_turn.py and by
                       the tests of everything below the governor.
Everything project-specific comes from the Project:
  - "/" of the notation is its read root;
  - its folder is scripts' working directory, and where drafts are promoted from;
  - its scripts/ is never writable, and its ledger/ is never reachable by the file tools;
  - its onboarding is the fixed file every agent reads first;
  - the bounds (project.py) derive from it, unless a caller passes its own (the tests do).
Tests call these over a temporary tree, with a fake client factory (and, for GPT agents, Codex
settings with a fake model). app.py calls them for one configured project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent import AgentCore, BudgetPool, HarnessEnv, agent_author, default_client_factory, planned_tools
from bounds import Bounds, fs_path
from governance import (DEFAULT_ALLOWANCE_USD, GOVERNOR_MODELS, MAX_OWNERS, OWNER_MAX_TURNS, OWNER_MODELS,
                        Institution, role_tool_names)
from project import Project
from prompts import build_prompt, lineage_values
from scribe_import import NIMOI_ROOT
from subagents import MODELS, Spawner

TASK_DIR = Path(__file__).resolve().parent
RUNTIME = TASK_DIR / ".runtime"  # gitignored: Codex's per-agent state (per project) and the isolated test CODEX_HOME
# The harness's author names where it is installed, from the nimoi root: the swimlane's task folder,
# or a packaged copy (nimoi/harness/prod gives "harness:harness/prod").
HARNESS_AUTHOR = "harness:" + (TASK_DIR.relative_to(NIMOI_ROOT).as_posix() if TASK_DIR.is_relative_to(NIMOI_ROOT)
                               else TASK_DIR.name)
# Scripted live runs (live_turn.py, live_institution.py): the developer agent writes the message, under
# the founder's authorization, so the ledger must not say a human typed it (task 6 finding, 2026-09-29).
DRIVER_AUTHOR = "developer:claude-code-session"
MODEL = "claude-sonnet-5"  # the pilot's pin, as in tasks 1-4 (founder decision, 2026-09-23)
GOVERNOR_MODEL = GOVERNOR_MODELS[0]  # rules.md 6c b1: claude opus only
MAX_TURNS = 20  # model rounds per message for the pilot, the governor and subagents; reading onboarding takes several


def codex_state_dir(project: Project) -> Path:
    """Where this project's GPT agents keep their Codex state (gitignored)."""
    return RUNTIME / project.name


def _environment(scribe: Any, ledger: Any, bus: Any, *, project: Project, budget_usd: float | None, max_turns: int,
                 client_factory: Callable[..., Any], codex: Any) -> tuple[HarnessEnv, Callable[..., str]]:
    """The shared environment, the spawner, and the prompt builder every agent's prompt comes from."""
    root = project.read_root
    facts = {"ledger": project.ledger_name, "ledger_session": ledger.session, "ledger_sessions": len(ledger.sessions),
             "scribe_id": scribe.SCRIBE_ID, "ledger_version": scribe.LEDGER_VERSION, "harness_author": HARNESS_AUTHOR,
             **project.facts()}
    env = HarnessEnv(scribe=scribe, ledger=ledger, bus=bus, root=root, workspace=project.project_dir,
                     never_writable=project.never_writable(), never_accessible=project.never_accessible(),
                     onboarding=project.onboarding_target, write_root=project.project_target,
                     budget=BudgetPool(project.budget_usd if budget_usd is None else budget_usd),
                     max_turns=max_turns, harness_author=HARNESS_AUTHOR, ledger_facts=facts,
                     client_factory=client_factory, codex=codex)
    common = dict(read_root=str(root), ledger=project.ledger_name, session=ledger.session, project=project.name,
                  project_dir=str(project.project_dir), project_target=project.project_target,
                  ledger_target=project.ledger_target, scripts=project.scripts_target,
                  onboarding_target=project.onboarding_target,
                  onboarding=str(fs_path(project.onboarding_target, root)),
                  models=", ".join(f"`{m}`" for m in MODELS))

    def prompt(kind: str, *, agent_id: str, model: str, bounds: Bounds, depth: int, can_spawn: bool, role: str,
               tool_bounds: Bounds | None = None, **extra: Any) -> str:
        tools = planned_tools(tool_bounds or bounds, can_spawn, role_tool_names(role) if env.institution else [])
        return build_prompt(kind, tools=tools, **{**common, **extra}, **lineage_values(model), agent_id=agent_id,
                            model=model, agent_author=agent_author(model, agent_id, env.session),
                            bounds=bounds.render(), depth=depth, max_depth=env.spawner.max_depth, role=role)

    env.spawner = Spawner(env, make_prompt=lambda **kw: prompt("subagent", **kw))
    return env, prompt


def build_harness(scribe: Any, ledger: Any, bus: Any, *, project: Project, model: str, max_turns: int,
                  top_bounds: Bounds | None = None, budget_usd: float | None = None,
                  client_factory: Callable[..., Any] = default_client_factory,
                  codex: Any = None) -> tuple[HarnessEnv, AgentCore]:
    """Task 5's single top-level agent, over one project. `codex`: backend_codex settings, or None when
    no GPT agent may run."""
    env, prompt = _environment(scribe, ledger, bus, project=project, budget_usd=budget_usd, max_turns=max_turns,
                               client_factory=client_factory, codex=codex)
    bounds = top_bounds or project.pilot_bounds()
    top = AgentCore(env, agent_id="pilot", model=model, bounds=bounds, role="pilot",
                    system_prompt=prompt("top", agent_id="pilot", model=model, bounds=bounds, depth=0,
                                         can_spawn=True, role="pilot"))
    return env, top


def build_institution(scribe: Any, ledger: Any, bus: Any, *, project: Project, max_turns: int,
                      governor_bounds: Bounds | None = None, ceiling: Bounds | None = None,
                      budget_usd: float | None = None, model: str = GOVERNOR_MODEL,
                      client_factory: Callable[..., Any] = default_client_factory, codex: Any = None,
                      owner_models: tuple[str, ...] = OWNER_MODELS, max_owners: int = MAX_OWNERS,
                      owner_max_turns: int = OWNER_MAX_TURNS) -> tuple[HarnessEnv, AgentCore, Institution]:
    """Task 6's governor and the institution it runs, over one project (task 7). Owners come from dispatch."""
    if model not in GOVERNOR_MODELS:
        raise ValueError(f"the governor must be one of {', '.join(GOVERNOR_MODELS)} (rules.md 6c)")
    governor_bounds = governor_bounds or project.governor_bounds()
    ceiling = ceiling or project.owner_ceiling()
    env, prompt = _environment(scribe, ledger, bus, project=project, budget_usd=budget_usd, max_turns=max_turns,
                               client_factory=client_factory, codex=codex)
    institution = Institution(env, ceiling=ceiling, scripts_dir=project.scripts_dir, owner_models=owner_models,
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
