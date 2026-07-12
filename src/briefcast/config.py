from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Podcast:
    name: str
    feed: str
    priority: int = 2
    active: bool = True

    @property
    def slug(self) -> str:
        return "".join(c if c.isalnum() else "-" for c in self.name.lower()).strip("-")


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path = ROOT) -> "Config":
        with open(root / "config.yaml") as f:
            return cls(raw=yaml.safe_load(f))

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, *path: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur


def load_roster(root: Path = ROOT) -> list[Podcast]:
    with open(root / "podcasts.yaml") as f:
        data = yaml.safe_load(f)
    roster = [Podcast(**p) for p in data["podcasts"]]
    return [p for p in roster if p.active]


def is_mock() -> bool:
    return os.environ.get("BRIEFCAST_MOCK", "") == "1"
