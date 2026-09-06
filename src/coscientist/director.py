"""Single-paper Research Director for Management Science.

This module does not call an LLM. It turns the canonical single-paper state into
one persistent, bounded research action at a time. A reasoning plane may answer
that action through ``director_answer.json``; the deterministic core validates
and applies the answer, then advances the same paper.

The design intentionally separates:

* ``single_paper.json`` -- scientific lifecycle and the one active paper;
* ``director.json`` -- the current orchestration action and compact records;
* ``director_answer.json`` -- overwriteable reasoning-plane handoff.

No candidate portfolio, reserve queue, historical-candidate revival or repeated
broad discovery exists here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .freeze import FreezeManifest, FreezeViolation
from .gms_lake import AnalysisRoute, Availability, GMSCatalog
from .models import utcnow
from .single_paper import (
    GenuineBlocker,
    PaperStage,
    SinglePaperError,
    SinglePaperState,
    TopicCharter,
)

DIRECTOR_FILENAME = "director.json"
ANSWER_FILENAME = "director_answer.json"
DIRECTOR_MODE = "SINGLE_PAPER_DIRECTOR"
DIRECTOR_SCHEMA_VERSION = 1
MAX_DISCOVERY_SHORTLIST = 3
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class DirectorError(RuntimeError):
    pass


class ActionKind(str, Enum):
    DISCOVER_TOPIC = "DISCOVER_TOPIC"
    DEVELOP_LITERATURE_THEORY = "DEVELOP_LITERATURE_THEORY"
    DATA_FEASIBILITY = "DATA_FEASIBILITY"
    DESIGN_CLOSURE = "DESIGN_CLOSURE"
    PRE_FREEZE_AUDIT = "PRE_FREEZE_AUDIT"
    CREATE_FREEZE = "CREATE_FREEZE"
    RUN_ANALYSIS = "RUN_ANALYSIS"
    MANUSCRIPT_DRAFT = "MANUSCRIPT_DRAFT"
    FINAL_AUDIT = "FINAL_AUDIT"
    REPAIR = "REPAIR"


@dataclass
class PendingAction:
    id: str
    kind: ActionKind
    paper_id: str | None
    stage: str | None
    question: str
    required_output: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)
    blocking: bool = False
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PendingAction":
        return cls(
            id=raw["id"],
            kind=ActionKind(raw["kind"]),
            paper_id=raw.get("paper_id"),
            stage=raw.get("stage"),
            question=raw.get("question", ""),
            required_output=dict(raw.get("required_output", {})),
            context=dict(raw.get("context", {})),
            blocking=bool(raw.get("blocking", False)),
            created_at=raw.get("created_at", utcnow()),
        )


@dataclass
class DirectorState:
    path: str
    bound_paper_id: str | None = None
    bound_stage: str | None = None
    pending_action: PendingAction | None = None
    records: dict[str, Any] = field(default_factory=dict)
    applied_action_ids: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = DIRECTOR_SCHEMA_VERSION
    operating_mode: str = DIRECTOR_MODE
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def load(cls, path: str) -> "DirectorState":
        if not os.path.exists(path):
            return cls(path=path)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        mode = raw.get("operating_mode", DIRECTOR_MODE)
        if mode != DIRECTOR_MODE:
            raise DirectorError(
                f"{path} declares operating_mode={mode!r}; expected {DIRECTOR_MODE}"
            )
        pending_raw = raw.get("pending_action")
        return cls(
            path=path,
            bound_paper_id=raw.get("bound_paper_id"),
            bound_stage=raw.get("bound_stage"),
            pending_action=(PendingAction.from_dict(pending_raw)
                            if isinstance(pending_raw, dict) else None),
            records=dict(raw.get("records", {})),
            applied_action_ids=list(raw.get("applied_action_ids", [])),
            history=list(raw.get("history", [])),
            schema_version=int(raw.get("schema_version", DIRECTOR_SCHEMA_VERSION)),
            operating_mode=DIRECTOR_MODE,
            updated_at=raw.get("updated_at", utcnow()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operating_mode": DIRECTOR_MODE,
            "bound_paper_id": self.bound_paper_id,
            "bound_stage": self.bound_stage,
            "pending_action": self.pending_action.to_dict() if self.pending_action else None,
            "records": self.records,
            "applied_action_ids": self.applied_action_ids[-200:],
            "history": self.history[-500:],
            "updated_at": self.updated_at,
        }

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.updated_at = utcnow()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def reset_for_new_paper(self, paper_id: str | None, stage: str | None) -> None:
        """Start a clean orchestration lineage; never inherit old candidate work."""
        self.bound_paper_id = paper_id
        self.bound_stage = stage
        self.pending_action = None
        self.records = {}
        self.applied_action_ids = []
        self.history = [{
            "at": utcnow(),
            "event": "DIRECTOR_BOUND",
            "paper_id": paper_id,
            "stage": stage,
        }]
        self.save()

    def reconcile(self, paper: SinglePaperState) -> None:
        pid = paper.active_paper.paper_id if paper.active_paper else None
        stage = paper.stage.value if paper.stage else None
        if pid != self.bound_paper_id:
            self.reset_for_new_paper(pid, stage)
            return
        changed = False
        if stage != self.bound_stage:
            self.bound_stage = stage
            # A pending instruction made for an earlier stage is stale by definition.
            self.pending_action = None
            changed = True
        if changed:
            self.save()

    def record(self, name: str, payload: dict[str, Any]) -> None:
        self.records[name] = {"updated_at": utcnow(), **payload}
        self.history.append({"at": utcnow(), "event": "RECORD", "name": name})
        self.save()

    def set_pending(self, action: PendingAction) -> PendingAction:
        self.pending_action = action
        self.history.append({
            "at": utcnow(),
            "event": "ACTION_CREATED",
            "action_id": action.id,
            "kind": action.kind.value,
            "paper_id": action.paper_id,
        })
        self.save()
        return action

    def clear_pending(self, action_id: str, result: str) -> None:
        self.applied_action_ids.append(action_id)
        self.history.append({
            "at": utcnow(),
            "event": "ACTION_APPLIED",
            "action_id": action_id,
            "result": result,
        })
        self.pending_action = None
        self.save()


def _action(kind: ActionKind, paper: SinglePaperState, *, question: str,
            required_output: dict[str, Any], context: dict[str, Any] | None = None,
            blocking: bool = False) -> PendingAction:
    return PendingAction(
        id=f"DA-{kind.value.lower()}-{uuid.uuid4().hex[:10]}",
        kind=kind,
        paper_id=paper.active_paper.paper_id if paper.active_paper else None,
        stage=paper.stage.value if paper.stage else None,
        question=question,
        required_output=required_output,
        context=context or {},
        blocking=blocking,
    )


def _lake_snapshot(catalog: GMSCatalog | None) -> dict[str, Any]:
    if catalog is None:
        return {
            "status": "UNAVAILABLE",
            "instruction": "refresh the GMS catalog before making a data-dependent decision",
        }
    available = [d for d in catalog.datasets if d.availability == Availability.AVAILABLE.value]
    domains = Counter(d.domain for d in available)
    sources = Counter(d.source_id for d in available)
    routes = Counter(d.analysis_route for d in available)
    concepts = Counter(c for d in available for c in d.concepts)

    def tier(d) -> int:
        p = d.remote_path.upper()
        if "03_RESEARCH" in p:
            return 0
        if "02_CURATED" in p:
            return 1
        return 2

    examples = sorted(
        available,
        key=lambda d: (tier(d), d.bytes if d.bytes is not None else 10**30, d.remote_path),
    )[:30]
    return {
        "status": "READY",
        "lake_repo": catalog.lake_repo,
        "lake_repo_sha": catalog.lake_repo_sha,
        "generated_at": catalog.generated_at,
        "objects_total": len(catalog.datasets),
        "objects_available": len(available),
        "domains": dict(domains.most_common()),
        "sources": dict(sources.most_common()),
        "routes": dict(routes.most_common()),
        "top_concepts": [k for k, _ in concepts.most_common(50)],
        "representative_objects": [
            {
                "id": d.id,
                "domain": d.domain,
                "source_id": d.source_id,
                "dataset_id": d.dataset_id,
                "remote_path": d.remote_path,
                "granularity": d.granularity,
                "coverage_start": d.coverage_start,
                "coverage_end": d.coverage_end,
                "analysis_route": d.analysis_route,
                "bytes": d.bytes,
                "concepts": d.concepts[:12],
            }
            for d in examples
        ],
    }


def _load_catalog(path: str | None) -> GMSCatalog | None:
    if not path or not os.path.exists(path):
        return None
    return GMSCatalog.load(path)


def _paper_context(paper: SinglePaperState) -> dict[str, Any]:
    if not paper.active_paper:
        return {}
    c = paper.active_paper
    return {
        "paper_id": c.paper_id,
        "working_title": c.working_title,
        "research_question": c.research_question,
        "phenomenon": c.phenomenon,
        "mechanism": c.mechanism,
        "contribution": c.contribution,
        "unit_of_analysis": c.unit_of_analysis,
        "intended_design": c.intended_design,
        "primary_exposure": c.primary_exposure,
        "primary_outcome": c.primary_outcome,
        "required_constructs": c.required_constructs,
        "target_journal_family": c.target_journal_family,
        "known_threats": c.known_threats,
    }


def ensure_action(paper: SinglePaperState, director: DirectorState,
                  catalog: GMSCatalog | None = None) -> PendingAction | None:
    """Create at most one current action and persist it.

    Calling this repeatedly is idempotent while the same action is pending.
    """
    director.reconcile(paper)
    if director.pending_action:
        return director.pending_action

    if paper.current_problem:
        return director.set_pending(_action(
            ActionKind.REPAIR,
            paper,
            question="Repair the named problem without opening a replacement topic.",
            required_output={
                "action_id": "copy from request",
                "decision": "REPAIRED or GENUINE_BLOCKER",
                "repair_summary": "what changed and what evidence verifies it",
                "blocker": "required only for GENUINE_BLOCKER",
            },
            context={"problem": paper.current_problem, "paper": _paper_context(paper)},
        ))

    if paper.active_paper is None or paper.stage is PaperStage.RETIRED:
        return director.set_pending(_action(
            ActionKind.DISCOVER_TOPIC,
            paper,
            question=(
                "Using the current GMS lake and current literature, produce no more than three "
                "serious Management Science questions, select exactly one, and return a Topic "
                "Charter. Do not use historical candidates or create a reserve queue."
            ),
            required_output={
                "action_id": "copy from request",
                "shortlist": [
                    {
                        "id": "fresh paper id",
                        "title": "working title",
                        "research_question": "single focused question",
                        "importance": "why it matters",
                        "mechanism": "principal mechanism",
                        "residual_contribution": "what remains unanswered",
                        "data_fit": "lake/public-data feasibility",
                        "identification": "credible design path",
                    }
                ],
                "selected_id": "exactly one shortlist id",
                "charter": {
                    "paper_id": "must equal selected_id",
                    "working_title": "...",
                    "research_question": "...",
                    "phenomenon": "...",
                    "mechanism": "...",
                    "contribution": "...",
                    "unit_of_analysis": "...",
                    "intended_design": "...",
                    "primary_outcome": "...",
                    "primary_exposure": "...",
                    "required_constructs": [],
                    "target_journal_family": "...",
                    "known_threats": [],
                },
            },
            context={
                "max_shortlist": MAX_DISCOVERY_SHORTLIST,
                "rules": [
                    "fresh current discovery only; historical candidate state is excluded",
                    "prefer important questions with a coherent mechanism and credible design",
                    "use the lake first but allow free authoritative public sources for real gaps",
                    "reject only on genuine blockers; ordinary weakness is a repair target",
                    "once selected, broad discovery stops",
                ],
                "lake": _lake_snapshot(catalog),
            },
        ))

    stage = paper.stage
    pctx = _paper_context(paper)

    if stage is PaperStage.SELECTED:
        return director.set_pending(_action(
            ActionKind.DEVELOP_LITERATURE_THEORY,
            paper,
            question=(
                "Deepen the selected paper only: verify the closest current literature, sharpen "
                "the residual contribution, specify one coherent mechanism and its strongest rival, "
                "and define the constructs/data needs. Do not propose another topic."
            ),
            required_output={
                "action_id": "copy from request",
                "closest_papers": [
                    {"title": "...", "year": 2026, "doi_or_url": "...", "why_close": "..."}
                ],
                "mechanism": "principal explanatory chain",
                "rival_explanation": "strongest rival",
                "observable_implications": ["..."],
                "residual_contribution": "defensible contribution after closest papers",
                "required_constructs": ["..."],
                "known_threats": ["..."],
            },
            context={"paper": pctx, "novelty_closure": "admission closure only"},
        ))

    if stage is PaperStage.DEVELOPING:
        previous = director.records.get("data_feasibility")
        return director.set_pending(_action(
            ActionKind.DATA_FEASIBILITY,
            paper,
            question=(
                "Build an outcome-blind data-feasibility map for the active paper. Resolve each "
                "required construct lake-first; use a free authoritative external source only for "
                "a real gap. Keep the same research question."
            ),
            required_output={
                "action_id": "copy from request",
                "requirements": [
                    {
                        "name": "construct name",
                        "concepts": ["catalog search terms"],
                        "necessity": "ESSENTIAL | IMPORTANT | OPTIONAL",
                        "granularity": "required unit or null",
                        "coverage_start": "required start or null",
                        "coverage_end": "required end or null",
                    }
                ],
                "external_sources": [
                    {
                        "requirement": "construct name",
                        "source_id": "stable source id",
                        "url": "official/public URL",
                        "verified": True,
                        "free": True,
                        "official_or_authoritative": True,
                        "access_class": "OPEN or HELD",
                        "granularity": "...",
                        "coverage_start": "...",
                        "coverage_end": "...",
                        "licence": "...",
                    }
                ],
            },
            context={"paper": pctx, "lake": _lake_snapshot(catalog),
                     "previous_attempt": previous},
        ))

    if stage is PaperStage.DATA_FEASIBLE:
        return director.set_pending(_action(
            ActionKind.DESIGN_CLOSURE,
            paper,
            question=(
                "Close the exact design and power plan for this paper using the feasible data route. "
                "Return REPAIR rather than changing topics if anything is still weak."
            ),
            required_output={
                "action_id": "copy from request",
                "decision": "PASS | REPAIR | GENUINE_BLOCKER",
                "repair_summary": "required when decision=REPAIR",
                "blocker": "enumerated genuine blocker when applicable",
                "estimand": "...",
                "design": "...",
                "sample_definition": "...",
                "treatment": "...",
                "outcome": "...",
                "controls": [],
                "exclusions": [],
                "window_start": None,
                "window_end": None,
                "pre_period_end": None,
                "models": [],
                "primary_contrasts": [],
                "multiplicity_policy": "...",
                "dataset_hashes": {"canonical dataset identity": "sha256"},
                "power": {
                    "status": "PASS | REPAIR | GENUINE_BLOCKER",
                    "method": "pre-period MDE/power method",
                    "plausible_effect": "...",
                    "mde": "...",
                    "evidence": "...",
                },
            },
            context={"paper": pctx, "data_feasibility": director.records.get("data_feasibility")},
        ))

    if stage is PaperStage.DESIGN_READY:
        audit = director.records.get("pre_freeze_audit", {})
        if audit.get("decision") == "PASS":
            return director.set_pending(_action(
                ActionKind.CREATE_FREEZE,
                paper,
                question="Mechanically write the approved design freeze; no further judgment is needed.",
                required_output={"mechanical": True, "command": "director mechanical"},
                context={"paper": pctx, "design": director.records.get("design_closure")},
                blocking=True,
            ))
        return director.set_pending(_action(
            ActionKind.PRE_FREEZE_AUDIT,
            paper,
            question=(
                "Perform one consolidated hostile pre-freeze audit of the active paper. Return PASS, "
                "REPAIR, or one enumerated GENUINE_BLOCKER. Do not generate replacement topics."
            ),
            required_output={
                "action_id": "copy from request",
                "decision": "PASS | REPAIR | GENUINE_BLOCKER",
                "findings": ["ranked concrete findings"],
                "repair_summary": "required for REPAIR",
                "blocker": "required for GENUINE_BLOCKER",
                "novelty_closure": "PASS or evidence-backed DIRECT_SCOOP",
                "measurement": "PASS or repair finding",
                "identification": "PASS or repair finding",
                "power": "PASS or repair finding",
                "access_licence_ethics": "PASS or repair finding",
            },
            context={
                "paper": pctx,
                "literature_theory": director.records.get("literature_theory"),
                "data_feasibility": director.records.get("data_feasibility"),
                "design_closure": director.records.get("design_closure"),
            },
            blocking=True,
        ))

    if stage in (PaperStage.FROZEN, PaperStage.ANALYZING):
        return director.set_pending(_action(
            ActionKind.RUN_ANALYSIS,
            paper,
            question=(
                "Execute only the frozen and analysis-locked plan, then mark results complete after "
                "the results bridge and strict provenance checks pass."
            ),
            required_output={
                "mechanical": True,
                "start_command": "director mark-analysis-started --freeze state/freeze.json",
                "complete_command": "director mark-results-complete --results <manifest>",
            },
            context={"paper": pctx, "freeze": director.records.get("freeze")},
        ))

    if stage is PaperStage.RESULTS_COMPLETE:
        return director.set_pending(_action(
            ActionKind.MANUSCRIPT_DRAFT,
            paper,
            question=(
                "Draft the canonical manuscript for the active paper from verified results only. "
                "Do not introduce unsupported numbers or reopen topic discovery."
            ),
            required_output={
                "action_id": "copy from request",
                "manuscript_ref": "Drive/path reference to canonical manuscript",
                "claims_summary": ["major claims tied to evidence"],
                "limitations": ["..."],
                "deviations": ["declared deviations from freeze, if any"],
            },
            context={"paper": pctx, "results": director.records.get("results")},
        ))

    if stage in (PaperStage.MANUSCRIPT, PaperStage.FINAL_AUDIT):
        return director.set_pending(_action(
            ActionKind.FINAL_AUDIT,
            paper,
            question=(
                "Audit the final manuscript for scientific logic, numeric provenance, citations, "
                "figures/tables, claim strength, reproducibility, current journal compliance and a "
                "final bounded novelty refresh. Repair the manuscript; do not replace the topic."
            ),
            required_output={
                "action_id": "copy from request",
                "decision": "PASS | REPAIR | GENUINE_BLOCKER",
                "findings": ["..."],
                "repair_summary": "required for REPAIR",
                "blocker": "required for GENUINE_BLOCKER",
                "checks": {
                    "scientific_logic": "PASS",
                    "numeric_provenance": "PASS",
                    "citations": "PASS",
                    "figures_tables": "PASS",
                    "claim_strength": "PASS",
                    "reproducibility": "PASS",
                    "journal_compliance": "PASS",
                    "novelty_refresh": "PASS",
                },
            },
            context={"paper": pctx, "manuscript": director.records.get("manuscript")},
            blocking=True,
        ))

    if stage is PaperStage.SUBMISSION_READY:
        return None
    raise DirectorError(f"no director route for stage {stage}")


def _require_fields(raw: dict[str, Any], names: list[str], where: str) -> None:
    missing = [n for n in names if raw.get(n) in (None, "", [])]
    if missing:
        raise DirectorError(f"{where} missing required fields: {', '.join(missing)}")


def _covers(value: str | None, required: str | None, *, start: bool) -> bool:
    if not required:
        return True
    if not value:
        return False
    # ISO dates and zero-padded years compare correctly lexicographically; plain
    # four-digit years also do. Unknown formats fail closed rather than guessing.
    v, r = str(value), str(required)
    return v <= r if start else v >= r


def _rank_lake_match(d) -> tuple[int, int, str]:
    p = d.remote_path.upper()
    tier = 0 if "03_RESEARCH" in p else 1 if "02_CURATED" in p else 2
    route = 0 if d.analysis_route == AnalysisRoute.DIRECT_FETCH.value else \
            1 if d.analysis_route == AnalysisRoute.CURATED_QUERY.value else 2
    return (tier * 10 + route, d.bytes if d.bytes is not None else 10**30, d.remote_path)


def resolve_data_requirements(requirements: list[dict[str, Any]], catalog: GMSCatalog | None,
                              external_sources: list[dict[str, Any]]) -> dict[str, Any]:
    external_by_req: dict[str, list[dict[str, Any]]] = {}
    for src in external_sources:
        external_by_req.setdefault(str(src.get("requirement", "")), []).append(src)

    resolutions = []
    unresolved_essential = []
    unresolved_important = []
    dataset_hashes: dict[str, str] = {}

    for req in requirements:
        _require_fields(req, ["name", "concepts", "necessity"], "data requirement")
        name = str(req["name"])
        necessity = str(req["necessity"]).upper()
        if necessity not in {"ESSENTIAL", "IMPORTANT", "OPTIONAL"}:
            raise DirectorError(f"{name}: invalid necessity {necessity!r}")
        concepts = [str(x).lower() for x in req.get("concepts", []) if str(x).strip()]
        if not concepts:
            raise DirectorError(f"{name}: concepts cannot be empty")

        hits = []
        if catalog is not None:
            for d in catalog.query(concepts=concepts, availability=Availability.AVAILABLE.value):
                if req.get("granularity") and d.granularity != req.get("granularity"):
                    continue
                if not _covers(d.coverage_start, req.get("coverage_start"), start=True):
                    continue
                if not _covers(d.coverage_end, req.get("coverage_end"), start=False):
                    continue
                hits.append(d)
        if hits:
            chosen = sorted(hits, key=_rank_lake_match)[0]
            status = ("LAKE_QUERY" if chosen.analysis_route == AnalysisRoute.QUERY_LAYER_REQUIRED.value
                      else "LAKE_EXACT")
            if chosen.sha256 and status == "LAKE_EXACT":
                dataset_hashes[chosen.remote_path] = chosen.sha256
            resolutions.append({
                "requirement": name,
                "necessity": necessity,
                "status": status,
                "source_id": chosen.source_id,
                "dataset_id": chosen.dataset_id,
                "remote_path": chosen.remote_path,
                "sha256": chosen.sha256,
                "analysis_route": chosen.analysis_route,
            })
            continue

        verified_external = None
        for src in external_by_req.get(name, []):
            if not src.get("verified") or not src.get("free") or not src.get("official_or_authoritative"):
                continue
            if str(src.get("access_class", "")).upper() not in {"OPEN", "HELD"}:
                continue
            if not src.get("url") or not src.get("licence"):
                continue
            if req.get("granularity") and src.get("granularity") != req.get("granularity"):
                continue
            if not _covers(src.get("coverage_start"), req.get("coverage_start"), start=True):
                continue
            if not _covers(src.get("coverage_end"), req.get("coverage_end"), start=False):
                continue
            verified_external = src
            break
        if verified_external:
            resolutions.append({
                "requirement": name,
                "necessity": necessity,
                "status": "EXTERNAL_VERIFIED",
                "source": verified_external,
            })
            sha = verified_external.get("sha256")
            identity = verified_external.get("dataset_identity") or verified_external.get("source_id")
            if sha and identity and SHA256_RE.match(str(sha)):
                dataset_hashes[str(identity)] = str(sha).lower()
            continue

        resolutions.append({
            "requirement": name,
            "necessity": necessity,
            "status": "UNRESOLVED",
        })
        if necessity == "ESSENTIAL":
            unresolved_essential.append(name)
        elif necessity == "IMPORTANT":
            unresolved_important.append(name)

    return {
        "status": "REPAIR" if unresolved_essential else "PASS",
        "requirements": requirements,
        "resolutions": resolutions,
        "unresolved_essential": unresolved_essential,
        "unresolved_important": unresolved_important,
        "dataset_hashes": dataset_hashes,
    }


def _decision(raw: dict[str, Any]) -> str:
    value = str(raw.get("decision", "")).upper()
    if value not in {"PASS", "REPAIR", "GENUINE_BLOCKER"}:
        raise DirectorError("decision must be PASS, REPAIR, or GENUINE_BLOCKER")
    return value


def _retire_from_answer(paper: SinglePaperState, answer: dict[str, Any]) -> None:
    blocker = answer.get("blocker")
    if not blocker:
        raise DirectorError("GENUINE_BLOCKER requires blocker")
    try:
        GenuineBlocker(str(blocker))
    except ValueError as exc:
        allowed = ", ".join(x.value for x in GenuineBlocker)
        raise DirectorError(f"invalid blocker {blocker!r}; allowed: {allowed}") from exc
    paper.retire(str(blocker), str(answer.get("repair_summary") or answer.get("detail") or ""))


def apply_answer(paper: SinglePaperState, director: DirectorState, answer: dict[str, Any],
                 catalog: GMSCatalog | None = None) -> str:
    director.reconcile(paper)
    action = director.pending_action
    if not action:
        raise DirectorError("there is no pending director action")
    action_id = str(answer.get("action_id", ""))
    if action_id != action.id:
        if action_id in director.applied_action_ids:
            return "ALREADY_APPLIED"
        raise DirectorError(
            f"answer action_id {action_id!r} does not match pending action {action.id!r}"
        )

    if action.kind is ActionKind.DISCOVER_TOPIC:
        shortlist = answer.get("shortlist")
        if not isinstance(shortlist, list) or not (1 <= len(shortlist) <= MAX_DISCOVERY_SHORTLIST):
            raise DirectorError(f"shortlist must contain 1..{MAX_DISCOVERY_SHORTLIST} topics")
        ids = [str(x.get("id", "")) for x in shortlist if isinstance(x, dict)]
        if len(ids) != len(shortlist) or any(not x for x in ids) or len(set(ids)) != len(ids):
            raise DirectorError("each shortlist item needs one unique non-empty id")
        selected_id = str(answer.get("selected_id", ""))
        if selected_id not in ids:
            raise DirectorError("selected_id must name exactly one shortlist item")
        charter_raw = answer.get("charter")
        if not isinstance(charter_raw, dict):
            raise DirectorError("topic discovery answer requires charter")
        _require_fields(
            charter_raw,
            ["paper_id", "working_title", "research_question", "phenomenon",
             "mechanism", "contribution", "unit_of_analysis", "intended_design",
             "primary_outcome", "primary_exposure"],
            "topic charter",
        )
        if str(charter_raw.get("paper_id")) != selected_id:
            raise DirectorError("charter.paper_id must equal selected_id")
        charter = TopicCharter.from_dict(charter_raw)
        paper.admit(charter)
        director.reset_for_new_paper(charter.paper_id, paper.stage.value)
        director.record("topic_selection", {
            "selected_id": selected_id,
            "shortlist": shortlist,
            "selection_rationale": answer.get("selection_rationale", ""),
        })
        director.applied_action_ids.append(action_id)
        director.pending_action = None
        director.save()
        return "ADMITTED"

    if action.paper_id != (paper.active_paper.paper_id if paper.active_paper else None):
        raise DirectorError("pending action is bound to a different paper")

    if action.kind is ActionKind.DEVELOP_LITERATURE_THEORY:
        _require_fields(
            answer,
            ["closest_papers", "mechanism", "rival_explanation",
             "observable_implications", "residual_contribution", "required_constructs"],
            "literature/theory answer",
        )
        if not isinstance(answer["closest_papers"], list) or len(answer["closest_papers"]) < 2:
            raise DirectorError("literature closure requires at least two verified closest papers")
        if not isinstance(answer["observable_implications"], list):
            raise DirectorError("observable_implications must be a list")
        if not isinstance(answer["required_constructs"], list):
            raise DirectorError("required_constructs must be a list")
        paper.active_paper.mechanism = str(answer["mechanism"])
        paper.active_paper.contribution = str(answer["residual_contribution"])
        paper.active_paper.required_constructs = [str(x) for x in answer["required_constructs"]]
        threats = [str(x) for x in answer.get("known_threats", [])]
        paper.active_paper.known_threats = list(dict.fromkeys(paper.active_paper.known_threats + threats))
        paper.save()
        director.record("literature_theory", {
            "closest_papers": answer["closest_papers"],
            "mechanism": answer["mechanism"],
            "rival_explanation": answer["rival_explanation"],
            "observable_implications": answer["observable_implications"],
            "residual_contribution": answer["residual_contribution"],
            "required_constructs": answer["required_constructs"],
            "known_threats": threats,
        })
        director.clear_pending(action_id, "LITERATURE_THEORY_CLOSED")
        paper.transition(PaperStage.DEVELOPING)
        director.reconcile(paper)
        return "DEVELOPING"

    if action.kind is ActionKind.DATA_FEASIBILITY:
        requirements = answer.get("requirements")
        external = answer.get("external_sources", [])
        if not isinstance(requirements, list) or not requirements:
            raise DirectorError("data feasibility requires a non-empty requirements list")
        if not isinstance(external, list):
            raise DirectorError("external_sources must be a list")
        result = resolve_data_requirements(requirements, catalog, external)
        director.record("data_feasibility", result)
        director.clear_pending(action_id, result["status"])
        if result["status"] == "REPAIR":
            paper.evolve(
                "data feasibility repair: unresolved essential constructs "
                + ", ".join(result["unresolved_essential"])
            )
            return "REPAIR"
        paper.active_paper.required_constructs = [str(r["name"]) for r in requirements]
        paper.save()
        paper.transition(PaperStage.DATA_FEASIBLE)
        director.reconcile(paper)
        return "DATA_FEASIBLE"

    if action.kind is ActionKind.DESIGN_CLOSURE:
        decision = _decision(answer)
        if decision == "GENUINE_BLOCKER":
            _retire_from_answer(paper, answer)
            director.record("design_closure", {"decision": decision, **answer})
            director.clear_pending(action_id, "RETIRED")
            director.reconcile(paper)
            return "RETIRED"
        if decision == "REPAIR":
            summary = str(answer.get("repair_summary", "")).strip()
            if not summary:
                raise DirectorError("REPAIR requires repair_summary")
            director.record("design_closure", {"decision": decision, **answer})
            director.clear_pending(action_id, "REPAIR")
            paper.evolve("design closure repair: " + summary)
            return "REPAIR"
        _require_fields(
            answer,
            ["estimand", "design", "sample_definition", "treatment", "outcome",
             "models", "primary_contrasts", "multiplicity_policy", "dataset_hashes", "power"],
            "design closure",
        )
        hashes = answer.get("dataset_hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise DirectorError("PASS requires at least one frozen dataset SHA-256 identity")
        bad = [k for k, v in hashes.items() if not SHA256_RE.match(str(v))]
        if bad:
            raise DirectorError("dataset_hashes contain invalid SHA-256 for: " + ", ".join(bad))
        power = answer.get("power")
        if not isinstance(power, dict) or str(power.get("status", "")).upper() != "PASS":
            raise DirectorError("design PASS requires power.status=PASS")
        record = {
            "decision": "PASS",
            "estimand": answer["estimand"],
            "design": answer["design"],
            "sample_definition": answer["sample_definition"],
            "treatment": answer["treatment"],
            "outcome": answer["outcome"],
            "controls": list(answer.get("controls", [])),
            "exclusions": list(answer.get("exclusions", [])),
            "window_start": answer.get("window_start"),
            "window_end": answer.get("window_end"),
            "pre_period_end": answer.get("pre_period_end"),
            "models": list(answer["models"]),
            "primary_contrasts": list(answer["primary_contrasts"]),
            "multiplicity_policy": answer["multiplicity_policy"],
            "dataset_hashes": {str(k): str(v).lower() for k, v in hashes.items()},
            "power": power,
        }
        paper.active_paper.intended_design = str(answer["design"])
        paper.active_paper.primary_exposure = str(answer["treatment"])
        paper.active_paper.primary_outcome = str(answer["outcome"])
        paper.save()
        director.record("design_closure", record)
        director.clear_pending(action_id, "DESIGN_READY")
        paper.transition(PaperStage.DESIGN_READY)
        director.reconcile(paper)
        return "DESIGN_READY"

    if action.kind is ActionKind.PRE_FREEZE_AUDIT:
        decision = _decision(answer)
        if decision == "GENUINE_BLOCKER":
            _retire_from_answer(paper, answer)
            director.record("pre_freeze_audit", {"decision": decision, **answer})
            director.clear_pending(action_id, "RETIRED")
            director.reconcile(paper)
            return "RETIRED"
        if decision == "REPAIR":
            summary = str(answer.get("repair_summary", "")).strip()
            if not summary:
                raise DirectorError("REPAIR requires repair_summary")
            director.record("pre_freeze_audit", {"decision": decision, **answer})
            director.clear_pending(action_id, "REPAIR")
            paper.evolve("pre-freeze audit repair: " + summary)
            return "REPAIR"
        _require_fields(answer, ["findings", "novelty_closure", "measurement",
                                 "identification", "power", "access_licence_ethics"],
                        "pre-freeze audit")
        record = {"decision": "PASS", **answer}
        director.record("pre_freeze_audit", record)
        director.clear_pending(action_id, "PASS")
        return "FREEZE_READY"

    if action.kind is ActionKind.MANUSCRIPT_DRAFT:
        _require_fields(answer, ["manuscript_ref", "claims_summary", "limitations", "deviations"],
                        "manuscript answer")
        director.record("manuscript", {
            "manuscript_ref": answer["manuscript_ref"],
            "claims_summary": answer["claims_summary"],
            "limitations": answer["limitations"],
            "deviations": answer["deviations"],
        })
        director.clear_pending(action_id, "MANUSCRIPT")
        paper.transition(PaperStage.MANUSCRIPT)
        director.reconcile(paper)
        return "MANUSCRIPT"

    if action.kind is ActionKind.FINAL_AUDIT:
        decision = _decision(answer)
        if decision == "GENUINE_BLOCKER":
            _retire_from_answer(paper, answer)
            director.record("final_audit", {"decision": decision, **answer})
            director.clear_pending(action_id, "RETIRED")
            director.reconcile(paper)
            return "RETIRED"
        if decision == "REPAIR":
            summary = str(answer.get("repair_summary", "")).strip()
            if not summary:
                raise DirectorError("REPAIR requires repair_summary")
            director.record("final_audit", {"decision": decision, **answer})
            director.clear_pending(action_id, "REPAIR")
            return "REPAIR"
        checks = answer.get("checks")
        required_checks = {
            "scientific_logic", "numeric_provenance", "citations", "figures_tables",
            "claim_strength", "reproducibility", "journal_compliance", "novelty_refresh",
        }
        if not isinstance(checks, dict) or any(str(checks.get(k, "")).upper() != "PASS"
                                               for k in required_checks):
            raise DirectorError("final PASS requires every mandated audit check to equal PASS")
        director.record("final_audit", {"decision": "PASS", **answer})
        director.clear_pending(action_id, "SUBMISSION_READY")
        if paper.stage is PaperStage.MANUSCRIPT:
            paper.transition(PaperStage.FINAL_AUDIT)
        paper.transition(PaperStage.SUBMISSION_READY)
        director.reconcile(paper)
        return "SUBMISSION_READY"

    if action.kind in {ActionKind.CREATE_FREEZE, ActionKind.RUN_ANALYSIS}:
        raise DirectorError(f"{action.kind.value} is mechanical; do not answer it as judgment")

    if action.kind is ActionKind.REPAIR:
        decision = str(answer.get("decision", "")).upper()
        if decision == "GENUINE_BLOCKER":
            _retire_from_answer(paper, answer)
            director.clear_pending(action_id, "RETIRED")
            director.reconcile(paper)
            return "RETIRED"
        if decision != "REPAIRED":
            raise DirectorError("repair action decision must be REPAIRED or GENUINE_BLOCKER")
        summary = str(answer.get("repair_summary", "")).strip()
        if not summary:
            raise DirectorError("REPAIRED requires repair_summary")
        paper.clear_problem(summary)
        director.record("last_repair", {"summary": summary})
        director.clear_pending(action_id, "REPAIRED")
        return "REPAIRED"

    raise DirectorError(f"unsupported answer kind {action.kind.value}")


def create_freeze(paper: SinglePaperState, director: DirectorState, out: str) -> str:
    director.reconcile(paper)
    action = director.pending_action
    if not action or action.kind is not ActionKind.CREATE_FREEZE:
        raise DirectorError("CREATE_FREEZE is not the pending action")
    if paper.stage is not PaperStage.DESIGN_READY:
        raise DirectorError("design must be DESIGN_READY before freeze creation")
    design = director.records.get("design_closure", {})
    audit = director.records.get("pre_freeze_audit", {})
    if design.get("decision") != "PASS" or audit.get("decision") != "PASS":
        raise DirectorError("design closure and pre-freeze audit must both PASS")
    if not paper.active_paper:
        raise DirectorError("no active paper")
    manifest = FreezeManifest(
        candidate_id=paper.active_paper.paper_id,
        question=paper.active_paper.research_question,
        estimand=str(design["estimand"]),
        design=str(design["design"]),
        sample_definition=str(design["sample_definition"]),
        treatment=str(design["treatment"]),
        outcome=str(design["outcome"]),
        controls=list(design.get("controls", [])),
        exclusions=list(design.get("exclusions", [])),
        window_start=design.get("window_start"),
        window_end=design.get("window_end"),
        pre_period_end=design.get("pre_period_end"),
        models=list(design.get("models", [])),
        primary_contrasts=list(design.get("primary_contrasts", [])),
        multiplicity_policy=str(design.get("multiplicity_policy", "none")),
        dataset_hashes=dict(design["dataset_hashes"]),
        note="Created by the single-paper Research Director after hostile pre-freeze PASS.",
    )
    fid = manifest.save(out)
    director.record("freeze", {
        "freeze_id": fid,
        "freeze_hash": manifest.freeze_hash,
        "path": out,
    })
    director.clear_pending(action.id, "FROZEN")
    paper.transition(PaperStage.FROZEN)
    director.reconcile(paper)
    return fid


def mark_analysis_started(paper: SinglePaperState, director: DirectorState, freeze_path: str) -> str:
    director.reconcile(paper)
    if paper.stage is not PaperStage.FROZEN:
        raise DirectorError("analysis can start only from FROZEN")
    try:
        manifest = FreezeManifest.load(freeze_path)
    except (FileNotFoundError, FreezeViolation) as exc:
        raise DirectorError(f"cannot start analysis without an intact freeze: {exc}") from exc
    if not paper.active_paper or manifest.candidate_id != paper.active_paper.paper_id:
        raise DirectorError("freeze belongs to a different paper")
    paper.transition(PaperStage.ANALYZING)
    if director.pending_action and director.pending_action.kind is ActionKind.RUN_ANALYSIS:
        director.clear_pending(director.pending_action.id, "ANALYZING")
    director.reconcile(paper)
    return "ANALYZING"


def mark_results_complete(paper: SinglePaperState, director: DirectorState, results_path: str) -> str:
    director.reconcile(paper)
    if paper.stage is not PaperStage.ANALYZING:
        raise DirectorError("results can complete only from ANALYZING")
    if not os.path.isfile(results_path):
        raise DirectorError(f"results manifest does not exist: {results_path}")
    h = hashlib.sha256()
    with open(results_path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    director.record("results", {"manifest": results_path, "sha256": h.hexdigest()})
    if director.pending_action and director.pending_action.kind is ActionKind.RUN_ANALYSIS:
        director.clear_pending(director.pending_action.id, "RESULTS_COMPLETE")
    paper.transition(PaperStage.RESULTS_COMPLETE)
    director.reconcile(paper)
    return "RESULTS_COMPLETE"


def _load_answer(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise DirectorError("director answer must be a JSON object")
    return raw


def apply_if_present(paper: SinglePaperState, director: DirectorState, answer_path: str,
                     catalog: GMSCatalog | None) -> str:
    if not os.path.exists(answer_path):
        return "NO_ANSWER"
    answer = _load_answer(answer_path)
    action_id = str(answer.get("action_id", ""))
    if action_id in director.applied_action_ids:
        return "ALREADY_APPLIED"
    if not director.pending_action:
        return "STALE_ANSWER_NO_PENDING_ACTION"
    if action_id != director.pending_action.id:
        return "STALE_ANSWER_FOR_OTHER_ACTION"
    return apply_answer(paper, director, answer, catalog)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.director")
    p.add_argument("--state", default="state/single_paper.json")
    p.add_argument("--director", default="state/director.json")
    p.add_argument("--catalog", default="state/gms_lake_catalog.json")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("ensure")
    e.add_argument("--action-out", default="state/director_action.json")

    a = sub.add_parser("apply")
    a.add_argument("--answer", required=True)

    ap = sub.add_parser("apply-if-present")
    ap.add_argument("--answer", default="state/director_answer.json")

    m = sub.add_parser("mechanical")
    m.add_argument("--freeze-out", default="state/freeze.json")

    s = sub.add_parser("status")
    s.add_argument("--action-out", default=None)

    mas = sub.add_parser("mark-analysis-started")
    mas.add_argument("--freeze", default="state/freeze.json")

    mrc = sub.add_parser("mark-results-complete")
    mrc.add_argument("--results", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paper = SinglePaperState.load(args.state)
    director = DirectorState.load(args.director)
    catalog = _load_catalog(args.catalog)

    try:
        if args.command == "ensure":
            action = ensure_action(paper, director, catalog)
            if action is None:
                print("SUBMISSION_READY -- no research action pending")
                return 0
            os.makedirs(os.path.dirname(args.action_out) or ".", exist_ok=True)
            with open(args.action_out, "w", encoding="utf-8") as fh:
                json.dump(action.to_dict(), fh, indent=2, sort_keys=True)
            print(f"{action.kind.value} {action.id}")
            print(action.question)
            return 0
        if args.command == "apply":
            result = apply_answer(paper, director, _load_answer(args.answer), catalog)
            print(result)
            return 0
        if args.command == "apply-if-present":
            print(apply_if_present(paper, director, args.answer, catalog))
            return 0
        if args.command == "mechanical":
            action = ensure_action(paper, director, catalog)
            if action is None:
                print("NO_ACTION")
                return 0
            if action.kind is ActionKind.CREATE_FREEZE:
                print(create_freeze(paper, director, args.freeze_out))
            else:
                print(f"NO_MECHANICAL_STEP -- pending {action.kind.value}")
            return 0
        if args.command == "status":
            director.reconcile(paper)
            action = ensure_action(paper, director, catalog)
            print(f"paper       {paper.active_paper.paper_id if paper.active_paper else '-'}")
            print(f"stage       {paper.stage.value if paper.stage else 'NO_ACTIVE_PAPER'}")
            print(f"action      {action.kind.value if action else '-'}")
            print(f"action id   {action.id if action else '-'}")
            print(f"blocking    {'yes' if action and action.blocking else 'no'}")
            if args.action_out and action:
                with open(args.action_out, "w", encoding="utf-8") as fh:
                    json.dump(action.to_dict(), fh, indent=2, sort_keys=True)
            return 0
        if args.command == "mark-analysis-started":
            print(mark_analysis_started(paper, director, args.freeze))
            return 0
        if args.command == "mark-results-complete":
            print(mark_results_complete(paper, director, args.results))
            return 0
    except (DirectorError, SinglePaperError) as exc:
        print(f"DIRECTOR ERROR\n{exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
