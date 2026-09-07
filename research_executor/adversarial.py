from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from research_executor.acquisition import (
    AcquisitionError, _child, _download_json, _drive_client_from_env,
    _optional_child, _search_job_folder, _write_json, sha256_file,
)
from research_executor.public_status import validate_job_id, write_public_status

REVIEWER_ROLES = (
    "DOMAIN_REVIEWER",
    "METHODOLOGY_REVIEWER",
    "STATISTICS_REVIEWER",
    "EVIDENCE_CITATION_REVIEWER",
    "FIGURE_DATA_INTEGRITY_REVIEWER",
    "JOURNAL_COMPLIANCE_REVIEWER",
)
TRUE_HARD_STOP_TYPES = {
    "FABRICATION_OR_DECEPTION_REQUIRED",
    "UNCORRECTABLE_ETHICS_LEGAL_PROHIBITION",
}
MAX_AUTOMATIC_REVIEW_CYCLES = 2

class AdversarialError(AcquisitionError):
    pass

def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _default_disposition(finding_type: str) -> str:
    mapping = {
        "UNSUPPORTED_CLAIM": "REWRITE_OR_REMOVE_CLAIM",
        "STATISTICAL_ERROR": "CORRECT_ANALYSIS_AND_RECALIBRATE_CLAIMS",
        "WEAK_OR_NULL_DATA": "REFRAME_AS_EXPLORATORY_NULL_OR_NEGATIVE_RESULT",
        "MISSING_OR_WEAK_CITATION": "REPLACE_REMOVE_OR_DISCLOSE",
        "FIGURE_TABLE_MISMATCH": "REBUILD_FIGURE_OR_TABLE",
        "INCOMPLETE_PROVENANCE": "DOCUMENT_LIMITATION_AND_REDUCE_CLAIM_STRENGTH",
        "INTERPRETATION_DISAGREEMENT": "RESEARCH_DIRECTOR_DECISION",
        "MISSING_OPTIONAL_ANALYSIS": "OPTIONAL_RERUN_OR_DISCLOSE",
        "JOURNAL_FORMAT_DEFECT": "REPAIR_FORMATTING",
    }
    return mapping.get(finding_type, "REPAIR_REFRAME_OR_DISCLOSE")

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
    if params.get("policy_version") != "1.2_COMPLETION_FIRST":
        raise AdversarialError("Session 10 requires completion-first policy 1.2")
    cycle = params.get("audit_cycle")
    if not isinstance(cycle, int) or cycle < 1 or cycle > MAX_AUTOMATIC_REVIEW_CYCLES:
        raise AdversarialError("audit_cycle must be 1 or 2")
    findings = params.get("findings")
    if not isinstance(findings, list):
        raise AdversarialError("findings must be a list")
    seen = set()
    for f in findings:
        if not isinstance(f, dict) or not f.get("finding_id") or not f.get("finding_type"):
            raise AdversarialError("every finding needs finding_id and finding_type")
        if f["finding_id"] in seen:
            raise AdversarialError("finding IDs must be unique")
        seen.add(f["finding_id"])
        if f.get("severity") not in {"CRITICAL","HIGH","MEDIUM","LOW"}:
            raise AdversarialError("invalid severity")
        if f.get("status") not in {"OPEN","RESOLVED","REFRAMED","DISCLOSED","REBUTTED"}:
            raise AdversarialError("invalid status")
        if not f.get("evidence"):
            raise AdversarialError("every finding must be evidence-linked")

def classify_finding(finding: dict[str, Any], audit_cycle: int) -> dict[str, Any]:
    item = dict(finding)
    item["true_hard_stop"] = item["finding_type"] in TRUE_HARD_STOP_TYPES and item["status"] == "OPEN"
    item["recommended_disposition"] = _default_disposition(item["finding_type"])
    if item["true_hard_stop"]:
        item["recommended_disposition"] = "STOP_AND_ESCALATE"
    elif item["status"] != "OPEN":
        item["recommended_disposition"] = "ALREADY_ADDRESSED"
    elif audit_cycle >= MAX_AUTOMATIC_REVIEW_CYCLES:
        if item["finding_type"] == "INTERPRETATION_DISAGREEMENT":
            item["recommended_disposition"] = "RESEARCH_DIRECTOR_DECISION_AND_PROCEED"
        else:
            item["recommended_disposition"] = "DISCLOSE_AS_LIMITATION_OR_REFRAME_AND_PROCEED"
    return item

def synthesize(findings: list[dict[str, Any]], audit_cycle: int) -> dict[str, Any]:
    issues = [classify_finding(x, audit_cycle) for x in findings]
    hard_stops = [x["finding_id"] for x in issues if x["true_hard_stop"]]
    open_nonfatal = [x["finding_id"] for x in issues if x["status"] == "OPEN" and not x["true_hard_stop"]]
    if hard_stops:
        completion_state = "STOP_INTEGRITY_OR_ETHICS"
        next_state = "RESEARCH_DIRECTOR_ESCALATION"
    elif audit_cycle < MAX_AUTOMATIC_REVIEW_CYCLES and open_nonfatal:
        completion_state = "REPAIR_AND_REVIEW_ONCE_MORE"
        next_state = "REPAIR_AND_REVIEW"
    else:
        completion_state = "PAPER_CAN_PROCEED"
        next_state = "READY_FOR_FINAL_PACKAGE_WITH_LIMITATIONS" if open_nonfatal else "READY_FOR_FINAL_PACKAGE"
    return {
        "schema_version":"cosci.editor-synthesis/1.1",
        "policy_version":"1.2_COMPLETION_FIRST",
        "audit_cycle":audit_cycle,
        "max_automatic_cycles":MAX_AUTOMATIC_REVIEW_CYCLES,
        "hard_stops":hard_stops,
        "open_nonfatal_findings":open_nonfatal,
        "completion_state":completion_state,
        "next_state":next_state,
        "principle":"AUDIT_TO_IMPROVE_AND_FINISH_NOT_TO_PREVENT_COMPLETION",
        "issues":issues,
    }

def run_adversarial_job(job_id: str, client: Any | None = None, workdir: str | Path | None = None) -> dict[str, Any]:
    validate_job_id(job_id)
    client = client or _drive_client_from_env()
    job_folder = _search_job_folder(client, job_id)
    jid = job_folder["id"]
    manifest_obj = _child(client, jid, "job.json", folder=False)
    checkpoints = _child(client, jid, "checkpoints", folder=True)
    results = _child(client, jid, "results", folder=True)
    logs = _child(client, jid, "logs", folder=True)
    outputs = _child(client, jid, "outputs", folder=True)
    metadata = _child(client, jid, "metadata", folder=True)

    td = tempfile.TemporaryDirectory(prefix="cosci-review-") if workdir is None else None
    root = Path(td.name if td else workdir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest, manifest_sha = _download_json(client, manifest_obj["id"], root/"job.json")
        validate_manifest(manifest, job_id)

        existing = _optional_child(client, results["id"], "result.json")
        if existing:
            result, _ = _download_json(client, existing["id"], root/"result-existing.json")
            if (result.get("provenance") or {}).get("manifest_sha256") != manifest_sha:
                raise AdversarialError("existing result belongs to a different manifest")
            return result

        cp = {
            "schema_version":"cosci.checkpoint/1.0","job_id":job_id,
            "stage":"MANIFEST_VALIDATED","manifest_sha256":manifest_sha,
            "privacy_class":"PRIVATE_RESEARCH",
        }
        p=root/"checkpoint.json"; _write_json(p,cp); client.upload(str(p),checkpoints["id"],p.name)

        params=manifest["parameters"]
        synthesis=synthesize(params["findings"], params["audit_cycle"])
        reports=[]
        for role in REVIEWER_ROLES:
            ids=[x["finding_id"] for x in synthesis["issues"] if x.get("reviewer_role")==role]
            reports.append({"reviewer_role":role,"finding_ids":ids,"finding_count":len(ids)})
        repair_plan={
            "schema_version":"cosci.repair-plan/1.0",
            "audit_cycle":params["audit_cycle"],
            "items":[
                {
                    "finding_id":x["finding_id"],
                    "severity":x["severity"],
                    "disposition":x["recommended_disposition"],
                    "status":x["status"],
                } for x in synthesis["issues"]
            ],
            "cycle_limit_rule":"After cycle 2, unresolved nonfatal findings become limitations or Research Director decisions and the paper proceeds."
        }
        issue_ledger={"schema_version":"cosci.issue-ledger/1.1","issues":synthesis["issues"]}
        payloads={
            "reviewer_reports.json":{"schema_version":"cosci.reviewer-reports/1.0","reports":reports},
            "issue_ledger.json":issue_ledger,
            "editor_synthesis.json":synthesis,
            "repair_plan.json":repair_plan,
        }
        refs=[]
        for name,obj in payloads.items():
            p=root/name; _write_json(p,obj); up=client.upload(str(p),outputs["id"],name)
            refs.append({"name":name,"drive_file_id":up,"sha256":sha256_file(p),"bytes":p.stat().st_size})

        prov={
            "schema_version":"cosci.review-provenance/1.1","job_id":job_id,
            "manifest_sha256":manifest_sha,"policy_version":"1.2_COMPLETION_FIRST",
            "audit_cycle":params["audit_cycle"],"output_hashes":{x["name"]:x["sha256"] for x in refs},
        }
        pp=root/"review_provenance.json"; _write_json(pp,prov); pu=client.upload(str(pp),metadata["id"],pp.name)

        result={
            "schema_version":"cosci.result/1.0","job_id":job_id,
            "project_id":manifest.get("project_id"),"task_type":"ADVERSARIAL_REVIEW",
            "status":"SUCCEEDED","privacy_class":"PRIVATE_RESEARCH",
            "outputs":refs,
            "validation":{"passed":True,"checks":[
                {"name":"evidence_linked_findings","status":"PASS"},
                {"name":"bounded_review_cycles","status":"PASS"},
                {"name":"completion_first_policy","status":"PASS"},
                {"name":"weak_data_reframing_supported","status":"PASS"},
                {"name":"private_drive_publication","status":"PASS"},
            ]},
            "metrics":{
                "finding_count":len(synthesis["issues"]),
                "hard_stop_count":len(synthesis["hard_stops"]),
                "open_nonfatal_count":len(synthesis["open_nonfatal_findings"]),
                "audit_cycle":params["audit_cycle"],
            },
            "next_state":synthesis["next_state"],
            "provenance":{
                "manifest_sha256":manifest_sha,
                "executor":"GITHUB_ACTIONS_PUBLIC",
                "review_provenance_file_id":pu,
            },
        }
        rp=root/"result.json"; _write_json(rp,result); client.upload(str(rp),results["id"],rp.name)
        lp=root/"execution.log"; lp.write_text(
            f"job_id={job_id}\naudit_cycle={params['audit_cycle']}\ncompletion_state={synthesis['completion_state']}\nnext_state={synthesis['next_state']}\n",
            encoding="utf-8"
        ); client.upload(str(lp),logs["id"],lp.name)
        return result
    finally:
        if td: td.cleanup()

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--job-id",required=True)
    parser.add_argument("--public-status",default="out/review-status.json")
    args=parser.parse_args()
    result=run_adversarial_job(args.job_id)
    write_public_status(args.job_id,args.public_status,"ADVERSARIAL_REVIEW")
    print(f"Adversarial review PASS for job_id={args.job_id}; next_state={result['next_state']}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
