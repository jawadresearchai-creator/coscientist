"""The source registry: a first-class, self-describing artifact.

This exists because of a real failure. A credentials list was once read as a
census of sources, and every OPEN-class source -- which by definition needs no
credential -- was invisible to it. The registry is therefore the single source
of truth about what data the engine can reach. A secrets list is a derived
view of one subset of it and is never authoritative.

Every source declares, explicitly:
  * its access class (may the engine depend on it at all?)
  * its redistribution rights (may the lake archive it?)
  * whether it needs a secret, and which one
  * what concepts it measures (used by the rescope search)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from .models import AccessClass


class RegistryError(RuntimeError):
    pass


@dataclass
class Source:
    id: str
    name: str
    access_class: AccessClass
    redistributable: bool
    url: str | None = None
    secret_names: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    granularity: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    licence: str | None = None
    platform: bool = False       # a delivery platform, not a single-licence source
    notes: str | None = None

    @property
    def admissible(self) -> bool:
        """Admissible for a frozen confirmatory design."""
        return self.access_class.admissible

    def archivable(self) -> bool:
        """May the lake store and redistribute this?

        A platform answers False. BigQuery carries many datasets under many
        licences, so it cannot speak for any of them -- and reporting
        archivable=True here while archive_policy() returned PROVENANCE_ONLY
        gave two contradictory machine answers to the same question.
        """
        if self.platform:
            return False
        return self.admissible and self.redistributable

    def archive_status(self) -> str:
        """What the doctor report should say, which is not always yes/no."""
        if self.platform:
            return "DATASET_SPECIFIC"
        return "yes" if self.archivable() else "no"

    def secrets_present(self, env: dict[str, str] | None = None) -> tuple[bool, list[str]]:
        env = os.environ if env is None else env
        missing = [s for s in self.secret_names if not env.get(s)]
        return (not missing), missing


@dataclass
class SourceRegistry:
    sources: dict[str, Source] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "SourceRegistry":
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        entries = raw.get("sources") or []
        if not entries:
            raise RegistryError(f"registry {path} declares no sources")
        sources: dict[str, Source] = {}
        for e in entries:
            try:
                sid = e["id"]
                src = Source(
                    id=sid,
                    name=e["name"],
                    access_class=AccessClass(e["access_class"]),
                    redistributable=bool(e["redistributable"]),
                    url=e.get("url"),
                    secret_names=list(e.get("secret_names") or []),
                    concepts=[c.lower() for c in (e.get("concepts") or [])],
                    granularity=e.get("granularity"),
                    coverage_start=e.get("coverage_start"),
                    coverage_end=e.get("coverage_end"),
                    licence=e.get("licence"),
                    platform=bool(e.get("platform", False)),
                    notes=e.get("notes"),
                )
            except KeyError as exc:
                raise RegistryError(f"source entry missing required field {exc}") from exc
            except ValueError as exc:
                raise RegistryError(f"source {e.get('id')!r}: {exc}") from exc
            if sid in sources:
                raise RegistryError(f"duplicate source id {sid!r}")
            sources[sid] = src
        return cls(sources=sources)

    def get(self, source_id: str) -> Source:
        try:
            return self.sources[source_id]
        except KeyError as exc:
            raise RegistryError(f"unknown source {source_id!r}") from exc

    def admissible(self) -> list[Source]:
        return [s for s in self.sources.values() if s.admissible]

    def by_concept(self, concepts: list[str], admissible_only: bool = True) -> list[Source]:
        """Sources measuring any of these concepts, best overlap first."""
        want = {c.lower() for c in concepts}
        hits: list[tuple[int, Source]] = []
        for s in self.sources.values():
            if admissible_only and not s.admissible:
                continue
            overlap = len(want & set(s.concepts))
            if overlap:
                hits.append((overlap, s))
        hits.sort(key=lambda t: (-t[0], t[1].id))
        return [s for _, s in hits]

    def audit(self, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """What the weekly doctor job reports on."""
        rows = []
        for s in self.sources.values():
            ok, missing = s.secrets_present(env)
            rows.append(
                {
                    "id": s.id,
                    "access_class": s.access_class.value,
                    "admissible": s.admissible,
                    "archivable": s.archivable(),
                    "archive_status": s.archive_status(),
                    "secrets_ok": ok,
                    "missing_secrets": missing,
                }
            )
        return sorted(rows, key=lambda r: r["id"])
