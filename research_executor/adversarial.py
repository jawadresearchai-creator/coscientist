from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_executor.acquisition import (
    AcquisitionError, _child, _download_json, _drive_client_from_env,
    _optional_child, _search_job_folder, _write_json, sha256_file,
)
from research_executor.public_status import validate_job_id, write_public_status

RELEASE_BLOCKER_TYPES = {
    "DATA_INTEGRITY_FAILURE",
    "UNREPRODUCIBLE_CENTRAL_ANALYSIS",
    "CENTRAL_STATISTICAL_ERROR",
    "FABRICATED_OR_UNVERIFIABLE_CENTRAL_EVIDENCE",
    "UNSUPPORTED_CENTRAL_CLAIM",
    "ETHICS_LEGAL_SUBMISSION_VIOLATION",
    "MATERIAL_FIGURE_TABLE_MISREPRESENTATION",
    "MISSING_VERIFICATION_ARTIFACT_FOR_CENTRAL_RESULT",
}
REVIEWER_ROLES = (
    "DOMAIN_REVIEWER",
    "METHODOLOGY_REVIEWER",
    "STATISTICS_REVIEWER",
    "EVIDENCE_CITATION_REVIEWER",
    "FIGURE_DATA_INTEGRITY_REVIEWER",
    "JOURNAL_COMPLIANCE_REVIEWER",
)
RD_ACTIONS = {
    "RESOLVE", "REBUT_WITH_EVIDENCE", "DOWNGRADE_WITH_RATIONALE",
    "ACCEPT_AS_LIMITATION", "WAIVE_WITH_RATIONALE",
}

class AdversarialError(AcquisitionError):
    pass

def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def validate_manifest(manifest: dict[str, Any], job_id: str) -> None:
    validate_job_id(job_id)
    if manifest.get("job_id") != job_id or manifest.get("task_type") != "ADVERSARIAL_REVIEW":
        raise AdversarialError("private adversarial manifest identity/task mismatch")
    if manifest.get("privacy_class") != "PRIVATE_RESEARCH":
        raise AdversarialError("adversarial manifest must be PRIVATE_RESEARCH")
    cost = manifest.get("cost_policy") or {}
    if cost.get("require_zero_cost") is not True or cost.get("automatic_paid_services") is not False:
        raise AdversarialError("zero-cost policy violation")
    params = manifest.get("parameters") or {}
    if params.get("policy_version") != "1.1":
        raise AdversarialError("Session 10 requires release policy 1.1")
    findings = params.get("findings")
    if not isinstance(findings, list):
        raise AdversarialError("findings must be a list")
    ids = set()
    for f in findings:
        if not isinstance(f, dict) or not f.get("finding_id") or not f.get("finding_type"):
            raise AdversarialError("every finding needs finding_id and finding_type")
        if f["finding_id"] in ids:
            raise AdversarialError("finding IDs must be unique")
        ids.add(f["finding_id"])
        if f.get("severity") not in {"CRITICAL","HIGH","MEDIUM","LOW"}:
            raise AdversarialError("invalid severity")
        if f.get("status") not in {"OPEN","RESOLVED","REBUTTED","DOWNGRADED","ACCEPTED_LIMITATION","WAIVED"}:
            raise AdversarialError("invalid finding status")
        if not f.get("evidence"):
            raise AdversarialError("every finding must be evidence-linked")
        action = f.get("research_director_action")
        if action and action not in RD_ACTIONS:
            raise AdversarialError("invalid Research Director action")

def classify(finding: dict[str, Any]) -> dict[str, Any]:
    item = dict(finding)
    meets = item["finding_type"] in RELEASE_BLOCKER_TYPES
    item["release_blocker"] = bool(meets)
    if item["status"] == "WAIVED" and meets:
        raise AdversarialError("RELEASE_BLOCKER cannot be waived without evidence-based reclassification")
    item["automatic_release_block"] = bool(meets and item["status"] == "OPEN")
    if item["severity"] in {"CRITICAL","HIGH"} and not meets and item["status"] == "OPEN":
        item["research_director_judgment_required"] = True
    else:
        item["research_director_judgment_required"] = False
    return item

def review_findings(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues = [classify(x) for x in findings]
    by_role = {r: [] for r in REVIEWER_ROLES}
    for issue in issues:
        role = issue.get("reviewer_role") or "DOMAIN_REVIEWER"
        if role not in by_role:
            role = "DOMAIN_REVIEWER"
        by_role[role].append(issue["finding_id"])
    reports = [{"reviewer_role": r, "finding_ids": ids, "finding_count": len(ids)} for r, ids in by_role.items()]
    blockers = [x["finding_id"] for x in issues if x["automatic_release_block"]]
    judgment = [x["finding_id"] for x in issues if x["research_director_judgment_required"]]
    synthesis = {
        "schema_version": "cosci.editor-synthesis/1.0",
        "unresolved_release_blockers": blockers,
        "research_director_judgment_required": judgment,
        "release_gate": "BLOCKED" if blockers else "READY_FOR_RESEARCH_DIRECTOR",
        "policy": "Only unresolved RELEASE_BLOCKER findings hard-stop release.",
    }
    return reports, {"issues": issues, "editor_synthesis": synthesis}

def run_adversarial_job(job_id: str, client: Any | None = None, workdir: str | Path | None = None) -> dict[str, Any]:
    validate_job_id(job_id)
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    job_id_drive = job_folder["id"]
    manifest_obj = _child(client, job_id_drive, "job.json", folder=False)
    checkpoints = _child(client, job_id_drive, "checkpoints", folder=True)
    results = _child(client, job_id_drive, "results", folder=True)
    logs = _child(client, job_id_drive, "logs", folder=True)
    outputs = _child(client, job_id_drive, "outputs", folder=True)
    metadata = _child(client, job_id_drive, "metadata", folder=True)

    td = tempfile.TemporaryDirectory(prefix="cosci-review-") if workdir is None else None
    root = Path(td.name if td else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root / "job.json")
        validate_manifest(manifest, job_id)

        existing = _optional_child(client, results["id"], "result.json")
        if existing:
            result, _ = _download_json(client, existing["id"], root / "result-existing.json")
            if (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise AdversarialError("existing result belongs to a different manifest")
            return result

        cp = {
            "schema_version":"cosci.checkpoint/1.0","job_id":job_id,
            "stage":"MANIFEST_VALIDATED","manifest_sha256":manifest_sha,
            "privacy_class":"PRIVATE_RESEARCH",
        }
        p = root / "checkpoint.json"; _write_json(p, cp); client.upload(str(p), checkpoints["id"], p.name)

        reports, reviewed = review_findings((manifest.get("parameters") or {}).get("findings") or [])
        issue_ledger = {
            "schema_version":"cosci.issue-ledger/1.0",
            "policy_version":"1.1",
            "issues":reviewed["issues"],
        }
        rebuttal = {
            "schema_version":"cosci.rebuttal-matrix/1.0",
            "items":[
                {
                    "finding_id":x["finding_id"],
                    "status":x["status"],
                    "research_director_action":x.get("research_director_action"),
                    "rationale":x.get("rationale"),
                } for x in reviewed["issues"]
            ],
        }
        editor = reviewed["editor_synthesis"]
        release_gate = {
            "schema_version":"cosci.release-gate/1.0",
            "status":"BLOCKED" if editor["unresolved_release_blockers"] else "PASS",
            "unresolved_release_blockers":editor["unresolved_release_blockers"],
            "nonblocking_judgment_items":editor["research_director_judgment_required"],
            "rule":"Only unresolved RELEASE_BLOCKER findings automatically prevent final/release state.",
        }
        payloads = {
            "reviewer_reports.json":{"schema_version":"cosci.reviewer-reports/1.0","reports":reports},
            "issue_ledger.json":issue_ledger,
            "editor_synthesis.json":editor,
            "rebuttal_matrix.json":rebuttal,
            "release_gate.json":release_gate,
        }
        output_refs = []
        for name, obj in payloads.items():
            p = root / name; _write_json(p,obj); up=client.upload(str(p),outputs["id"],name)
            output_refs.append({"name":name,"drive_file_id":up,"sha256":sha256_file(p),"bytes":p.stat().st_size})

        prov = {
            "schema_version":"cosci.review-provenance/1.0","job_id":job_id,
            "manifest_sha256":manifest_sha,"policy_version":"1.1",
            "reviewer_roles":list(REVIEWER_ROLES),"output_hashes":{x["name"]:x["sha256"] for x in output_refs},
        }
        pp=root/"review_provenance.json"; _write_json(pp,prov); pu=client.upload(str(pp),metadata["id"],pp.name)

        blocked = bool(editor["unresolved_release_blockers"])
        result = {
            "schema_version":"cosci.result/1.0","job_id":job_id,
            "project_id":manifest.get("project_id"),"task_type":"ADVERSARIAL_REVIEW",
            "status":"SUCCEEDED","privacy_class":"PRIVATE_RESEARCH",
            "outputs":output_refs,
            "validation":{"passed":True,"checks":[
                {"name":"evidence_linked_findings","status":"PASS"},
                {"name":"stable_issue_ids","status":"PASS"},
                {"name":"release_blocker_policy_v1_1","status":"PASS"},
                {"name":"editor_synthesis","status":"PASS"},
                {"name":"research_director_override_policy","status":"PASS"},
                {"name":"private_drive_publication","status":"PASS"},
            ]},
            "metrics":{
                "finding_count":len(issue_ledger["issues"]),
                "release_blocker_count":len(editor["unresolved_release_blockers"]),
                "judgment_item_count":len(editor["research_director_judgment_required"]),
            },
            "next_state":"BLOCKED_FOR_RELEASE" if blocked else "READY_FOR_FINAL_PACKAGE",
            "provenance":{"manifest_sha256":manifest_sha,"executor":"GITHUB_ACTIONS_PUBLIC","review_provenance_file_id":pu},
        }
        rp=root/"result.json"; _write_json(rp,result); client.upload(str(rp),results["id"],rp.name)
        lp=root/"execution.log"; lp.write_text(
            f"job_id={job_id}\nreview=PASS\nrelease_gate={release_gate['status']}\nprivate_publication=PASS\n",
            encoding="utf-8"
        ); client.upload(str(lp),logs["id"],lp.name)
        return result
    finally:
        if td: td.cleanup()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--public-status", default="out/review-status.json")
    args = parser.parse_args()
    result = run_adversarial_job(args.job_id)
    write_public_status(args.job_id,args.public_status,"ADVERSARIAL_REVIEW")
    print(f"Adversarial review PASS for job_id={args.job_id}; next_state={result['next_state']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
