"""Checkpointed state and the append-only ledger.

Two rules, both learned from V3.2's actual failures.

Machine state is JSON in a JSON file. The previous master state lived in a
Google Doc named `.json` and needed a missing-brace repair; rich text is not a
serialisation format.

Checkpoints are written at sub-step granularity, not stage boundaries, so a
killed session resumes from the last checkpoint rather than the last stage.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .models import utcnow

STATE_SCHEMA_VERSION = 1


class StateError(RuntimeError):
    pass


@dataclass
class EngineState:
    path: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "EngineState":
        if not os.path.exists(path):
            # A STATE SCHEMA version, versioned independently of the package.
            # It was a hardcoded "4.0.0" that read as an engine version and was
            # two releases stale; the schema does not change every release, so
            # deriving it from __version__ would be equally wrong.
            return cls(path=path, data={"state_schema_version": STATE_SCHEMA_VERSION,
                                        "engine_version": __version__,
                                        "created_at": utcnow(), "checkpoints": []})
        with open(path, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError as exc:
                raise StateError(f"{path} is not valid JSON: {exc}") from exc
        return cls(path=path, data=data)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def checkpoint(self, stage: str, step: str, **detail: Any) -> None:
        self.data.setdefault("checkpoints", []).append(
            {"at": utcnow(), "stage": stage, "step": step, **detail}
        )
        self.data["stage"] = stage
        self.data["step"] = step
        self.data["updated_at"] = utcnow()
        self.save()

    def last_checkpoint(self) -> dict[str, Any] | None:
        cps = self.data.get("checkpoints") or []
        return cps[-1] if cps else None

    def resume_point(self) -> tuple[str | None, str | None]:
        cp = self.last_checkpoint()
        return (cp["stage"], cp["step"]) if cp else (None, None)


class Ledger:
    """Append-only. Never rewrite an entry; supersede it with a new one.

    Written as JSON Lines so an append is one line and a partial write can
    never corrupt earlier history.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def append(self, entry_type: str, object_id: str, **fields: Any) -> dict[str, Any]:
        entry = {"at": utcnow(), "type": entry_type, "object": object_id, **fields}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def read(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
