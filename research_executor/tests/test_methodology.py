import hashlib
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from research_executor.acquisition import FOLDER_MIME
from research_executor.methodology import (
    build_methodology_plan,
    infer_capabilities,
    load_methodology_registry,
    rank_tool_names,
    run_private_methodology_job,
    select_skills,
)


class FakeDrive:
    def __init__(self, manifest):
        self.job_id = manifest["job_id"]
        self.job_folder = {"id": "JOBF", "name": self.job_id, "mimeType": FOLDER_MIME}
        self.files = {
            "JOBF": [
                {"id": "MAN", "name": "job.json", "mimeType": "application/json"},
                {"id": "CPF", "name": "checkpoints", "mimeType": FOLDER_MIME},
                {"id": "RESF", "name": "results", "mimeType": FOLDER_MIME},
                {"id": "LOGF", "name": "logs", "mimeType": FOLDER_MIME},
                {"id": "OUTF", "name": "outputs", "mimeType": FOLDER_MIME},
                {"id": "METAF", "name": "metadata", "mimeType": FOLDER_MIME},
            ],
            "CPF": [], "RESF": [], "LOGF": [], "OUTF": [], "METAF": [],
        }
        self.content = {"MAN": (json.dumps(manifest, sort_keys=True) + "\n").encode()}
        self.uploads = []

    def _get(self, url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q", [""])[0]
        if self.job_id in q:
            return json.dumps({"files": [self.job_folder]}).encode()
        return json.dumps({"files": []}).encode()

    def list_folder(self, folder_id):
        return list(self.files.get(folder_id, []))

    def download(self, file_id, dest, expected_sha256=None):
        raw = self.content[file_id]
        Path(dest).write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def upload(self, local_path, folder_id, name=None):
        name = name or Path(local_path).name
        new_id = f"UP{len(self.uploads)+1}"
        raw = Path(local_path).read_bytes()
        self.content[new_id] = raw
        mime = "application/json" if name.endswith(".json") else "text/plain"
        self.files[folder_id].append({"id": new_id, "name": name, "mimeType": mime})
        self.uploads.append((folder_id, name))
        return new_id


def manifest(job_id="COSCI-S08-METHOD-001", mode="MIXED"):
    return {
        "schema_version": "cosci.job/1.0",
        "job_id": job_id,
        "project_id": "RESEARCH-COSCIENTIST-BUILD",
        "task_type": "METHODOLOGY",
        "privacy_class": "PRIVATE_RESEARCH",
        "cost_policy": {"require_zero_cost": True, "automatic_paid_services": False},
        "parameters": {
            "starting_point": "DIRECTION",
            "research_direction": "Investigate plant stress signaling using public transcriptomics data and reproducible statistical analysis",
            "research_mode": mode,
            "available_assets": ["PUBLIC_LITERATURE", "PUBLIC_DATA"],
            "intended_outputs": ["RESEARCH_PLAN", "MANUSCRIPT"],
            "constraints": {
                "require_human_approval_before_execution": True,
                "allow_automatic_paid_services": False
            }
        }
    }


class MethodologyTests(unittest.TestCase):
    def test_capability_inference_is_generic_and_modality_based(self):
        caps = infer_capabilities(manifest()["parameters"])
        self.assertIn("QUESTION_FRAMING", caps)
        self.assertIn("TRANSCRIPTOMICS", caps)
        self.assertIn("STATISTICAL_ANALYSIS", caps)
        self.assertIn("DATA_DISCOVERY", caps)
        self.assertIn("PUBLICATION_HANDOFF", caps)

    def test_confirmatory_or_mixed_plan_blocks_execution_until_approval(self):
        registry = load_methodology_registry()
        plan = build_methodology_plan(manifest()["parameters"], registry)
        prereg = next(x for x in plan["gates"] if x["gate_id"] == "PREREGISTER_CONFIRMATORY")
        execute = next(x for x in plan["gates"] if x["gate_id"] == "EXECUTE_REGISTERED_PLAN")
        self.assertEqual(prereg["status"], "PLANNED")
        self.assertEqual(execute["status"], "BLOCKED_PENDING_RESEARCH_DIRECTOR_APPROVAL")
        self.assertTrue(plan["confirmatory_contract"]["preregistration_required"])
        self.assertEqual(plan["next_state"], "WAITING_FOR_RESEARCH_DIRECTOR_APPROVAL")

    def test_exploratory_mode_does_not_mislabel_preregistration(self):
        m = manifest(mode="EXPLORATORY")
        registry = load_methodology_registry()
        plan = build_methodology_plan(m["parameters"], registry)
        prereg = next(x for x in plan["gates"] if x["gate_id"] == "PREREGISTER_CONFIRMATORY")
        self.assertEqual(prereg["status"], "NOT_REQUIRED_FOR_MODE")
        self.assertFalse(plan["confirmatory_contract"]["preregistration_required"])

    def test_skill_selection_uses_starting_point_and_capabilities(self):
        m = manifest()
        registry = load_methodology_registry()
        plan = build_methodology_plan(m["parameters"], registry)
        skills = select_skills(m["parameters"], plan)
        names = {(x["source"], x["skill"]) for x in skills["selected_skills"]}
        self.assertIn(("ai4s-skills", "research-explorer"), names)
        self.assertIn(("ai4s-skills", "experiment-suite"), names)
        self.assertIn(("science-superpowers", "preregistering-analysis"), names)
        self.assertTrue(skills["k_dense_selective_discovery"])

    def test_tooluniverse_name_ranking_is_deterministic(self):
        names = ["PubMed_search_articles", "OpenTargets_get_associations", "RNA_expression_tool", "Geospatial_map"]
        first = rank_tool_names(names, ["pubmed", "literature"])
        second = rank_tool_names(names, ["pubmed", "literature"])
        self.assertEqual(first, second)
        self.assertEqual(first[0]["name"], "PubMed_search_articles")

    def test_private_methodology_job_publishes_typed_plan_and_reruns_idempotently(self):
        m = manifest()
        fake = FakeDrive(m)
        with tempfile.TemporaryDirectory() as td:
            first = run_private_methodology_job(m["job_id"], client=fake, workdir=td)
            upload_count = len(fake.uploads)
            second = run_private_methodology_job(m["job_id"], client=fake, workdir=td)
        self.assertEqual(first["status"], "SUCCEEDED")
        self.assertFalse(first["resumed"])
        self.assertTrue(second["resumed"])
        self.assertEqual(len(fake.uploads), upload_count)
        names = {name for _, name in fake.uploads}
        self.assertEqual(names, {
            "checkpoint.json", "methodology_plan.json", "skill_plan.json",
            "tool_discovery_plan.json", "reproducibility_contract.json", "handoffs.json",
            "methodology_provenance.json", "result.json", "execution.log"
        })
        result_obj = next(x for x in fake.files["RESF"] if x["name"] == "result.json")
        result = json.loads(fake.content[result_obj["id"]])
        self.assertTrue(result["validation"]["passed"])
        self.assertEqual(result["next_state"], "WAITING_FOR_RESEARCH_DIRECTOR_APPROVAL")


if __name__ == "__main__":
    unittest.main()
