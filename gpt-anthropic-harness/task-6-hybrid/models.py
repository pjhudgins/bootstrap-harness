"""Explicit role allowlists; runtime availability is checked separately."""
from bounds import Denied

CLAUDE = {"opus": "claude-opus-5-5", "fable": "claude-fable-5-1", "sonnet": "claude-sonnet-5"}
OWNER_MODELS = (CLAUDE["opus"], CLAUDE["fable"], "gpt-6-astra", "gpt-6-sol", "gpt-5.6-sol")
WORKER_MODELS = (*OWNER_MODELS, CLAUDE["sonnet"], "gpt-5.6-terra")


def select_model(role, model):
    model = CLAUDE.get(model, model)
    allowed = (CLAUDE["opus"],) if role == "governor" else OWNER_MODELS if role == "owner" else WORKER_MODELS if role == "worker" else ()
    if model not in allowed:
        raise Denied(f"Model {model!r} is not allowed for {role}; choose {list(allowed)}.")
    return model
