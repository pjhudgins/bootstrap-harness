"""Import scribe.py in place from bootstrap-ledger (founder decision, 2026-09-24).

bootstrap-ledger is outside this swimlane, and rules.md forbids writing there, so
bytecode writing is switched off for the import: Python would otherwise refresh
bootstrap-ledger/python-scribe/__pycache__ whenever scribe.py changes.
The version actually imported (SCRIBE_ID, LEDGER_VERSION) is logged at session start.
"""

import sys
from pathlib import Path
from types import ModuleType

NIMOI_ROOT = Path(__file__).resolve().parents[3]
SCRIBE_DIR = NIMOI_ROOT / "bootstrap-ledger" / "python-scribe"


def import_scribe() -> ModuleType:
    if "scribe" in sys.modules:
        return sys.modules["scribe"]
    if not (SCRIBE_DIR / "scribe.py").is_file():
        raise ImportError(f"scribe.py not found in {SCRIBE_DIR}")
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SCRIBE_DIR))
    try:
        import scribe
    finally:
        sys.path.remove(str(SCRIBE_DIR))
        sys.dont_write_bytecode = previous
    return scribe
