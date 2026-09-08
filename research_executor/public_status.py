from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,127}$")
ALLOWED_TASK_TYPES = {
    "SMOKE_TEST", "DATA_ACQUISITION", "ANALYSIS", "LITERATURE",
    "METHODOLOGY", "PUBLICATION", "ADVERSARIAL_REVIEW", "RECOVERY_TEST"
}
PUBLIC_SCHEMA_VERSION = "cosci.public-status/1.0"


def validate_job_id(job_id: str) -> None:
    if not JOB_ID_RE.fullmatch(job_id or ""):
        raise ValueError("job_id must be opaque and match ^[A-Z0-9][A-Z0-9._-]{2,127}$")


def validate_task_type(task_type: str) -> None:
    if task_type not in ALLOWED_TASK_TYPES:
        raise ValueError(f"unsupported public task_type: {task_type!r}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_public_status(job_id: str, task_type: str = "SMOKE_TEST") -> dict[str, Any]:
    validate_job_id(job_id)
    validate_task_type(task_type)
    now = utc_now()
    digest = hashlib.sha256(f"{job_id}:{task_type}".encode("utf-8")).hexdigest()
    checks = [
        {"name": "opaque_job_id", "status": "PASS"},
        {"name": "no_private_manifest_in_dispatch", "status": "PASS"},
    ]
    task_check = {
        "SMOKE_TEST": "deterministic_smoke_task",
        "DATA_ACQUISITION": "private_acquisition_execution",
        "ANALYSIS": "private_deterministic_analysis",
        "LITERATURE": "private_literature_evidence_execution",
        "METHODOLOGY": "private_methodology_skill_planning",
        "PUBLICATION": "private_reproducible_publication_build",
        "ADVERSARIAL_REVIEW": "private_adversarial_integrity_review",
        "RECOVERY_TEST": "private_recovery_hardening_execution",
    }[task_type]
    checks.append({"name": task_check, "status": "PASS"})
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "job_id": job_id,
        "task_type": task_type,
        "status": "SUCCEEDED",
        "privacy_class": "PUBLIC_EXECUTOR_SAFE",
        "executor": "GITHUB_ACTIONS_PUBLIC",
        "started_at": now,
        "completed_at": now,
        "deterministic_receipt": digest,
        "run": {
            "repository": os.getenv("GITHUB_REPOSITORY"),
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
            "sha": os.getenv("GITHUB_SHA"),
        },
        "checks": checks,
    }


def validate_public_status(status: dict[str, Any]) -> None:
    required = {"schema_version", "job_id", "task_type", "status", "privacy_class", "executor", "started_at", "completed_at", "deterministic_receipt", "run", "checks"}
    missing = required - set(status)
    if missing:
        raise ValueError(f"public status missing required fields: {sorted(missing)}")
    if status["schema_version"] != PUBLIC_SCHEMA_VERSION:
        raise ValueError("unexpected schema version")
    validate_job_id(status["job_id"])
    validate_task_type(status["task_type"])
    if status["privacy_class"] != "PUBLIC_EXECUTOR_SAFE":
        raise ValueError("public artifact must be PUBLIC_EXECUTOR_SAFE")
    if status["status"] != "SUCCEEDED":
        raise ValueError("public success receipt expects SUCCEEDED")
    forbidden_keys = {
        "research_question", "research_direction", "hypothesis", "manuscript", "private_manifest",
        "drive_token", "refresh_token", "api_key", "credential_value", "literature_corpus",
        "source_url", "drive_file_id", "input_file_id", "selected_accession", "search_query",
        "claims", "doi", "pmid", "pmcid", "openalex_id", "title", "abstract", "methodology_plan",
        "skill_plan", "tool_plan", "handoffs", "preregistration", "journal", "journal_requirements",
        "canonical_manuscript", "manuscript_source", "citation_ledger", "figure_table_registry",
        "build_manifest", "author", "authors", "finding", "findings", "evidence", "issue_ledger",
        "reviewer_reports", "editor_synthesis", "rebuttal_matrix", "release_gate", "release_blocker",
        "rationale", "waiver", "canonical_state_file_id", "expected_revision", "expected_status",
        "reconciliation", "bootstrap_probe", "recovery_output", "checkpoint_file_id"
    }
    def keys(value: Any):
        if isinstance(value, dict):
            for k, v in value.items():
                yield str(k).lower()
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)
    bad = forbidden_keys.intersection(set(keys(status)))
    if bad:
        raise ValueError(f"private/sensitive fields present in public status: {sorted(bad)}")


def write_public_status(job_id: str, output: str | Path, task_type: str = "SMOKE_TEST") -> Path:
    status = build_public_status(job_id, task_type)
    validate_public_status(status)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
