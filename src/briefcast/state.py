from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

STATE_PATH = ROOT / "state.json"


def load_state(path: Path = STATE_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"processed_guids": [], "published": []}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    # Keep the guid list bounded; feeds only look back days, not years.
    state["processed_guids"] = state["processed_guids"][-1000:]
    path.write_text(json.dumps(state, indent=1))
