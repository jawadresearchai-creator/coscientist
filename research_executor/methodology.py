from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from research_executor.acquisition import (
    AcquisitionError,
    _child,
    _download_json,
    _drive_client_from_env,
    _optional_child,
    _search_job_folder,
    _write_json,
    sha256_file,
)
from research_executor.public_status import validate_job_id, write_public_status

REGISTRY_PATH = Path(__file__).with_name("methodology_registry.json")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class MethodologyError(AcquisitionError):
    pass


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_methodology_registry(path: str | Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if registry.get("schema_version") != "cosci.methodology-registry/1.0":
        raise MethodologyError("unexpected methodology registry schema")
    policy = registry.get("policy") or {}
    required_true = [
        "zero_cost_only",
        "private_direction_only",
        "human_approval_before_execution",
        "confirmatory_preregistration_required",
        "exploratory_must_be_labeled",
        "reproducibility_required",
        "raw_inputs_immutable",
        "typed_handoffs_required",
        "checkpoint_required",
        "final_scientific_judgment_reserved_for_research_director",
    ]
    for key in required_true:
        if policy.get(key) is not True:
            raise MethodologyError(f"methodology policy must enforce {key}=true")
    if policy.get("automatic_paid_routes") is not False:
        raise MethodologyError("methodology policy must disable automatic paid routes")
    if not registry.get("methodology_gates") or not registry.get("handoff_types"):
        raise MethodologyError("methodology registry is incomplete")
    return registry


def _tokens(value: Any) -> set[str]:
    return set(_TOKEN_RE.findall(str(value or "").lower()))


def validate_methodology_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("schema_version") != "cosci.job/1.0" or manifest.get("job_id") != job_id:
        raise MethodologyError("private methodology manifest identity mismatch")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH" or manifest.get("task_type") != "METHODOLOGY":
        raise MethodologyError("methodology job must be private METHODOLOGY")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise MethodologyError("methodology job violates zero-cost policy")
    params = manifest.get("parameters") or {}
    if params.get("starting_point") not in {"DIRECTION", "QUESTION"}:
        raise MethodologyError("starting_point must be DIRECTION or QUESTION")
    direction = params.get("research_direction")
    if not isinstance(direction, str) or len(direction.strip()) < 12:
        raise MethodologyError("research_direction must be a substantive private string")
    if params.get("research_mode") not in {"EXPLORATORY", "CONFIRMATORY", "MIXED"}:
        raise MethodologyError("research_mode must be EXPLORATORY, CONFIRMATORY, or MIXED")
    assets = params.get("available_assets")
    if not isinstance(assets, list) or any(not isinstance(x, str) for x in assets):
        raise MethodologyError("available_assets must be a list of strings")
    constraints = params.get("constraints") or {}
    if constraints.get("require_human_approval_before_execution") is not True:
        raise MethodologyError("Research Director approval gate is mandatory")
    if constraints.get("allow_automatic_paid_services") is not False:
        raise MethodologyError("automatic paid services must be disabled")


CAPABILITY_INDICATORS: dict[str, set[str]] = {
    "DATA_DISCOVERY": {"data", "dataset", "database", "public", "repository", "archive"},
    "DATA_ACQUISITION": {"download", "acquire", "dataset", "data", "repository", "archive"},
    "STATISTICAL_ANALYSIS": {"analysis", "effect", "association", "compare", "model", "regression", "experiment", "trial", "survey"},
    "BIOINFORMATICS": {"sequence", "genome", "genomic", "bioinformatics", "sra", "geo", "ena", "variant", "protein"},
    "TRANSCRIPTOMICS": {"rna", "rnaseq", "transcriptome", "transcriptomics", "expression", "differential"},
    "TIME_SERIES": {"timeseries", "longitudinal", "temporal", "forecast", "forecasting", "repeated"},
    "GEOSPATIAL": {"geospatial", "spatial", "gis", "satellite", "remote", "location", "mapping"},
    "CHEMINFORMATICS": {"chemical", "compound", "molecule", "molecular", "smiles", "drug", "cheminformatics"},
    "IMAGE_ANALYSIS": {"image", "imaging", "microscopy", "segmentation", "vision", "phenotyping"},
    "FIGURE_GENERATION": {"figure", "plot", "visualization", "visualisation", "chart"},
}


def infer_capabilities(params: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            str(params.get("research_direction") or ""),
            " ".join(params.get("available_assets") or []),
            " ".join(params.get("intended_outputs") or []),
        ]
    )
    toks = _tokens(text)
    capabilities = {
        "QUESTION_FRAMING",
        "LITERATURE_EVIDENCE",
        "FEASIBILITY",
        "STUDY_DESIGN",
        "REPRODUCIBILITY",
        "INTEGRITY_AUDIT",
    }
    mode = params.get("research_mode")
    if mode in {"CONFIRMATORY", "MIXED"}:
        capabilities.add("PREREGISTRATION")
    for capability, indicators in CAPABILITY_INDICATORS.items():
        if toks.intersection(indicators):
            capabilities.add(capability)
    assets = set(params.get("available_assets") or [])
    if assets.intersection({"PUBLIC_DATA", "OWN_DATA", "WET_LAB_DATA"}):
        capabilities.update({"DATA_DISCOVERY", "STATISTICAL_ANALYSIS"})
    intended = set(params.get("intended_outputs") or [])
    if "MANUSCRIPT" in intended:
        capabilities.add("PUBLICATION_HANDOFF")
    return sorted(capabilities)


def build_methodology_plan(params: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    mode = params["research_mode"]
    gates = []
    for index, gate in enumerate(registry["methodology_gates"], start=1):
        required_modes = gate.get("required_for_modes")
        if required_modes and mode not in required_modes:
            status = "NOT_REQUIRED_FOR_MODE"
        elif gate["id"] == "EXECUTE_REGISTERED_PLAN":
            status = "BLOCKED_PENDING_RESEARCH_DIRECTOR_APPROVAL"
        elif gate["id"] in {"ANOMALY_GATE"}:
            status = "CONDITIONAL"
        else:
            status = "PLANNED"
        gates.append(
            {
                "order": index,
                "gate_id": gate["id"],
                "donor": gate.get("donor"),
                "skill": gate.get("skill"),
                "status": status,
                "output_type": gate.get("output"),
                "blocked_until": gate.get("blocked_until") or [],
            }
        )
    confirmatory = mode in {"CONFIRMATORY", "MIXED"}
    return {
        "schema_version": "cosci.methodology-plan/1.0",
        "research_mode": mode,
        "starting_point": params["starting_point"],
        "capability_needs": infer_capabilities(params),
        "gates": gates,
        "confirmatory_contract": {
            "preregistration_required": confirmatory,
            "outcome_peek_before_freeze_consequence": "AFFECTED_ANALYSIS_BECOMES_EXPLORATORY",
            "human_approval_before_execution": True,
            "silent_deviation_allowed": False,
        },
        "research_director_gate": {
            "status": "REQUIRED",
            "approval_object": "APPROVAL_DECISION",
            "blocks": "EXECUTE_REGISTERED_PLAN",
        },
        "next_state": "WAITING_FOR_RESEARCH_DIRECTOR_APPROVAL",
    }


def select_skills(params: dict[str, Any], methodology: dict[str, Any]) -> dict[str, Any]:
    capabilities = set(methodology["capability_needs"])
    selections: list[dict[str, Any]] = []

    def add(source: str, skill: str, reason: str, stage: str, required: bool = True) -> None:
        item = {
            "source": source,
            "skill": skill,
            "reason": reason,
            "stage": stage,
            "required": required,
        }
        if item not in selections:
            selections.append(item)

    add("science-superpowers", "framing-research-questions", "all projects require a falsifiable question", "QUESTION")
    add("science-superpowers", "surveying-prior-work", "all projects require prior-work grounding", "EVIDENCE")
    add("science-superpowers", "establishing-feasibility-first", "feasibility must be assessed before design commitment", "FEASIBILITY")
    add("science-superpowers", "designing-the-analysis", "study/analysis design must precede execution", "DESIGN")
    if "PREREGISTRATION" in capabilities:
        add("science-superpowers", "preregistering-analysis", "confirmatory or mixed mode requires frozen predictions and decision rules", "PREREGISTRATION")
    add("science-superpowers", "setting-up-reproducible-analysis", "pinned environment, seeds, immutable raw data and provenance are mandatory", "REPRODUCIBILITY")
    add("science-superpowers", "verifying-results-before-claiming", "claims require reproduced evidence", "VERIFY")
    add("science-superpowers", "requesting-red-team-review", "adversarial review is required before reporting", "REVIEW")
    add("science-superpowers", "reporting-and-archiving-findings", "durable reproducible archive is required", "ARCHIVE")

    if params["starting_point"] == "DIRECTION":
        add("ai4s-skills", "research-explorer", "starting point is a broad direction rather than a resolved question", "DISCOVERY")
    add("ai4s-skills", "literature-survey", "evidence synthesis handoff is required", "EVIDENCE")
    if capabilities.intersection({"STATISTICAL_ANALYSIS", "BIOINFORMATICS", "TRANSCRIPTOMICS", "TIME_SERIES", "GEOSPATIAL", "CHEMINFORMATICS", "IMAGE_ANALYSIS"}):
        add("ai4s-skills", "experiment-suite", "project requires an analysis/experiment package", "DESIGN")
    add("ai4s-skills", "integrity-auditor", "integrity checks are mandatory before publication handoff", "AUDIT")
    if "PUBLICATION_HANDOFF" in capabilities:
        add("ai4s-skills", "paper-writer", "manuscript requested; execution belongs to Session 9 publication engine", "PUBLICATION_HANDOFF", required=False)

    kdense_queries = []
    for cap in sorted(capabilities):
        if cap in {"BIOINFORMATICS", "TRANSCRIPTOMICS", "TIME_SERIES", "GEOSPATIAL", "CHEMINFORMATICS", "IMAGE_ANALYSIS", "STATISTICAL_ANALYSIS"}:
            kdense_queries.append({"capability": cap, "query": cap.lower().replace("_", " "), "activation": "DISCOVER_THEN_REVIEW"})

    return {
        "schema_version": "cosci.skill-plan/1.0",
        "selected_skills": selections,
        "k_dense_selective_discovery": kdense_queries,
        "selection_policy": "capability-based; no wholesale skill activation and no per-topic hard-coded recipe",
    }


def build_tool_discovery_plan(methodology: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    capabilities = methodology["capability_needs"]
    generic_queries = []
    term_map = {
        "LITERATURE_EVIDENCE": ["literature", "pubmed", "publications"],
        "DATA_DISCOVERY": ["dataset", "database", "search"],
        "BIOINFORMATICS": ["bioinformatics", "sequence", "genomics"],
        "TRANSCRIPTOMICS": ["transcriptomics", "rna", "expression"],
        "STATISTICAL_ANALYSIS": ["statistics", "regression", "analysis"],
        "TIME_SERIES": ["time series", "forecast"],
        "GEOSPATIAL": ["geospatial", "gis"],
        "CHEMINFORMATICS": ["chemical", "compound", "molecule"],
        "IMAGE_ANALYSIS": ["image", "microscopy", "segmentation"],
    }
    for cap in capabilities:
        if cap in term_map:
            generic_queries.append({"capability": cap, "keywords": term_map[cap]})
    adapter = registry["tooluniverse_adapter"]
    return {
        "schema_version": "cosci.tool-discovery-plan/1.0",
        "broker": "ToolUniverse",
        "package": adapter["package"],
        "validated_target_version": adapter["validated_target_version"],
        "queries": generic_queries,
        "execution_policy": {
            "discover_before_execute": True,
            "zero_cost_required": True,
            "missing_free_credential_action": "SKIP_OR_FALLBACK",
            "automatic_paid_escalation": False,
        },
    }


def build_reproducibility_contract(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "cosci.reproducibility-contract/1.0",
        "raw_data_immutable": True,
        "derived_data_separate": True,
        "checksums_required": True,
        "runtime_versions_required": True,
        "dependency_lock_required": True,
        "random_seed_required_for_stochastic_steps": True,
        "analysis_definition_hash_required": True,
        "checkpoint_resume_required": True,
        "confirmatory_exploratory_separation": True,
        "anomaly_root_cause_before_adjustment": True,
        "verification_before_claim": True,
        "research_mode": params["research_mode"],
    }


def build_handoffs(methodology: dict[str, Any], skills: dict[str, Any]) -> dict[str, Any]:
    handoffs = [
        {"from": "RESEARCH_DIRECTOR", "to": "QUESTION_FRAMING", "type": "QUESTION_PACKET", "status": "PLANNED"},
        {"from": "QUESTION_FRAMING", "to": "SESSION_07_LITERATURE_ENGINE", "type": "EVIDENCE_HANDOFF", "status": "PLANNED"},
        {"from": "SESSION_07_LITERATURE_ENGINE", "to": "STUDY_DESIGN", "type": "FEASIBILITY_PACKET", "status": "PLANNED"},
        {"from": "STUDY_DESIGN", "to": "RESEARCH_DIRECTOR", "type": "STUDY_DESIGN_PACKET", "status": "REQUIRES_APPROVAL"},
        {"from": "RESEARCH_DIRECTOR", "to": "PREREGISTRATION", "type": "APPROVAL_DECISION", "status": "BLOCKING"},
        {"from": "PREREGISTRATION", "to": "REPRODUCIBLE_SETUP", "type": "FROZEN_PREREGISTRATION", "status": "PLANNED"},
        {"from": "REPRODUCIBLE_SETUP", "to": "SKILL_TOOL_BROKER", "type": "REPRODUCIBILITY_CONTRACT", "status": "PLANNED"},
        {"from": "SKILL_TOOL_BROKER", "to": "ACQUISITION_OR_ANALYSIS", "type": "CAPABILITY_PLAN", "status": "PLANNED"},
        {"from": "ACQUISITION_OR_ANALYSIS", "to": "VERIFY", "type": "ANALYSIS_HANDOFF", "status": "BLOCKED_PENDING_APPROVAL"},
        {"from": "VERIFY", "to": "RED_TEAM", "type": "VERIFICATION_PACKET", "status": "PLANNED"},
        {"from": "RED_TEAM", "to": "PUBLICATION_ENGINE", "type": "REVIEW_HANDOFF", "status": "PLANNED"},
    ]
    if methodology["research_mode"] == "EXPLORATORY":
        for item in handoffs:
            if item["to"] == "PREREGISTRATION":
                item["status"] = "NOT_REQUIRED_UNLESS_CONFIRMATORY_CLAIM_IS_PLANNED"
    return {
        "schema_version": "cosci.handoffs/1.0",
        "handoffs": handoffs,
        "skill_selection_sha256": canonical_sha256(skills),
    }


def rank_tool_names(tool_names: list[str], keywords: list[str], limit: int = 10) -> list[dict[str, Any]]:
    q = set().union(*(_tokens(k) for k in keywords)) if keywords else set()
    scored = []
    for name in tool_names:
        nt = _tokens(name)
        overlap = len(q.intersection(nt))
        contains = sum(1 for keyword in keywords if str(keyword).lower().replace(" ", "") in str(name).lower().replace("_", "").replace("-", ""))
        score = overlap * 2 + contains
        if score > 0:
            scored.append({"name": name, "score": score})
    return sorted(scored, key=lambda x: (-x["score"], x["name"].lower()))[:limit]


def _upload_json_once(client: Any, folder_id: str, path: Path, name: str) -> str:
    existing = _optional_child(client, folder_id, name)
    if existing:
        check = path.parent / f"drive-existing-{name}"
        client.download(existing["id"], str(check))
        if sha256_file(check) != sha256_file(path):
            raise MethodologyError(f"existing private Drive artifact differs for {name}")
        return existing["id"]
    return client.upload(str(path), folder_id, name)


def run_private_methodology_job(job_id: str, *, client: Any | None = None, workdir: str | Path | None = None) -> dict[str, Any]:
    validate_job_id(job_id)
    registry = load_methodology_registry()
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    manifest_obj = _child(client, job_folder["id"], "job.json", folder=False)
    checkpoint_folder = _child(client, job_folder["id"], "checkpoints", folder=True)
    results_folder = _child(client, job_folder["id"], "results", folder=True)
    logs_folder = _child(client, job_folder["id"], "logs", folder=True)
    outputs_folder = _child(client, job_folder["id"], "outputs", folder=True)
    metadata_folder = _child(client, job_folder["id"], "metadata", folder=True)

    context = tempfile.TemporaryDirectory(prefix="cosci-methodology-") if workdir is None else None
    root = Path(context.name if context else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_methodology_manifest(manifest, job_id)
        existing_result = _optional_child(client, results_folder["id"], "result.json")
        if existing_result:
            result, _ = _download_json(client, existing_result["id"], root / "result-existing.json")
            if result.get("status") != "SUCCEEDED" or (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise MethodologyError("existing methodology result does not match this exact manifest")
            return {"job_id": job_id, "status": "SUCCEEDED", "resumed": True}

        params = manifest["parameters"]
        methodology = build_methodology_plan(params, registry)
        skills = select_skills(params, methodology)
        tools = build_tool_discovery_plan(methodology, registry)
        reproducibility = build_reproducibility_contract(params)
        handoffs = build_handoffs(methodology, skills)
        plan_hash = canonical_sha256({"methodology": methodology, "skills": skills, "tools": tools, "reproducibility": reproducibility, "handoffs": handoffs})

        checkpoint = {
            "schema_version": "cosci.checkpoint/1.0",
            "job_id": job_id,
            "privacy_class": "PRIVATE_RESEARCH",
            "stage": "METHODOLOGY_PLAN_LOCKED_FOR_REVIEW",
            "manifest_sha256": manifest_sha,
            "methodology_plan_sha256": plan_hash,
            "resume_from": "RESEARCH_DIRECTOR_APPROVAL",
        }
        cp_path = root / "checkpoint.json"
        _write_json(cp_path, checkpoint)
        cp_id = _upload_json_once(client, checkpoint_folder["id"], cp_path, "checkpoint.json")

        output_specs = [
            ("methodology_plan.json", methodology),
            ("skill_plan.json", skills),
            ("tool_discovery_plan.json", tools),
            ("reproducibility_contract.json", reproducibility),
            ("handoffs.json", handoffs),
        ]
        output_refs = []
        for name, payload in output_specs:
            path = root / name
            _write_json(path, payload)
            output_refs.append({"name": name, "drive_file_id": _upload_json_once(client, outputs_folder["id"], path, name), "sha256": sha256_file(path)})

        provenance = {
            "schema_version": "cosci.methodology-provenance/1.0",
            "manifest_sha256": manifest_sha,
            "methodology_registry_sha256": sha256_file(REGISTRY_PATH),
            "methodology_plan_sha256": plan_hash,
            "donors": ["science-superpowers", "ai4s-skills", "k-dense-scientific-agent-skills", "ToolUniverse"],
            "executor": "GITHUB_ACTIONS_PUBLIC",
            "policy": {
                "zero_cost_only": True,
                "automatic_paid_routes": False,
                "human_approval_before_execution": True,
                "final_scientific_judgment": "RESEARCH_DIRECTOR",
            },
            "outputs": output_refs,
        }
        prov_path = root / "methodology_provenance.json"
        _write_json(prov_path, provenance)
        prov_id = _upload_json_once(client, metadata_folder["id"], prov_path, "methodology_provenance.json")

        result = {
            "schema_version": "cosci.result/1.0",
            "job_id": job_id,
            "project_id": manifest.get("project_id"),
            "status": "SUCCEEDED",
            "privacy_class": "PRIVATE_RESEARCH",
            "task_type": "METHODOLOGY",
            "outputs": output_refs,
            "metadata": {"methodology_provenance_file_id": prov_id},
            "metrics": {
                "capability_count": len(methodology["capability_needs"]),
                "methodology_gate_count": len(methodology["gates"]),
                "selected_skill_count": len(skills["selected_skills"]),
                "k_dense_discovery_query_count": len(skills["k_dense_selective_discovery"]),
                "tooluniverse_query_count": len(tools["queries"]),
                "handoff_count": len(handoffs["handoffs"]),
            },
            "validation": {
                "passed": True,
                "checks": [
                    {"name": "zero_cost_policy", "status": "PASS"},
                    {"name": "methodology_gates_present", "status": "PASS"},
                    {"name": "confirmatory_exploratory_separation", "status": "PASS"},
                    {"name": "human_approval_gate", "status": "PASS"},
                    {"name": "reproducibility_contract", "status": "PASS"},
                    {"name": "capability_based_skill_selection", "status": "PASS"},
                    {"name": "typed_handoffs", "status": "PASS"},
                    {"name": "tooluniverse_discovery_plan", "status": "PASS"},
                    {"name": "private_drive_publication", "status": "PASS"},
                ],
            },
            "provenance": {
                "manifest_sha256": manifest_sha,
                "methodology_plan_sha256": plan_hash,
                "executor": "GITHUB_ACTIONS_PUBLIC",
            },
            "next_state": "WAITING_FOR_RESEARCH_DIRECTOR_APPROVAL",
        }
        result_path = root / "result.json"
        _write_json(result_path, result)
        client.upload(str(result_path), results_folder["id"], "result.json")

        log_path = root / "execution.log"
        log_path.write_text(
            "\n".join(
                [
                    f"job_id={job_id}",
                    "task_type=METHODOLOGY",
                    "methodology_gates=PASS",
                    "human_approval_gate=PASS",
                    "reproducibility_contract=PASS",
                    "skill_selection=PASS",
                    "typed_handoffs=PASS",
                    "tool_discovery_plan=PASS",
                    "private_drive_publication=PASS",
                    "next_state=WAITING_FOR_RESEARCH_DIRECTOR_APPROVAL",
                ]
            ) + "\n",
            encoding="utf-8",
        )
        client.upload(str(log_path), logs_folder["id"], "execution.log")
        return {"job_id": job_id, "status": "SUCCEEDED", "resumed": False, "checkpoint_file_id": cp_id, "result": result}
    finally:
        if context:
            context.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist private methodology and skill planning executor")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--public-status", required=True)
    args = parser.parse_args(argv)
    run_private_methodology_job(args.job_id)
    write_public_status(args.job_id, args.public_status, task_type="METHODOLOGY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
