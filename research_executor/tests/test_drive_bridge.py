import hashlib
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from research_executor.drive_bridge import DriveBridgeError, run_drive_smoke
from coscientist.drive import FOLDER_MIME


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
            ],
            "CPF": [], "RESF": [], "LOGF": [],
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


def manifest(job_id="COSCI-S03-SMOKE-001"):
    return {
        "schema_version": "cosci.job/1.0",
        "job_id": job_id,
        "project_id": "BUILD",
        "task_type": "SMOKE_TEST",
        "privacy_class": "PRIVATE_RESEARCH",
        "execution": {"preferred_backend": "GITHUB_ACTIONS_PUBLIC"},
        "cost_policy": {"require_zero_cost": True, "automatic_paid_services": False},
        "parameters": {"payload": {"a": 17, "b": 25, "operation": "sum"}},
    }


class DriveBridgeTests(unittest.TestCase):
    def test_end_to_end_private_smoke(self):
        fake = FakeDrive(manifest())
        with tempfile.TemporaryDirectory() as td:
            public = run_drive_smoke("COSCI-S03-SMOKE-001", client=fake, workdir=td)
        self.assertEqual(public["status"], "SUCCEEDED")
        self.assertEqual(public["privacy_class"], "PUBLIC_EXECUTOR_SAFE")
        self.assertEqual({name for _, name in fake.uploads},
                         {"checkpoint.json", "result.json", "execution.log"})
        result_file = next(x for x in fake.files["RESF"] if x["name"] == "result.json")
        result = json.loads(fake.content[result_file["id"]])
        self.assertEqual(result["metrics"]["deterministic_result"], 42)
        self.assertEqual(result["next_state"], "WAITING_FOR_RESEARCH_DIRECTOR")

    def test_rerun_is_idempotent(self):
        fake = FakeDrive(manifest())
        with tempfile.TemporaryDirectory() as td:
            run_drive_smoke("COSCI-S03-SMOKE-001", client=fake, workdir=td)
            first_upload_count = len(fake.uploads)
            run_drive_smoke("COSCI-S03-SMOKE-001", client=fake, workdir=td)
        self.assertEqual(len(fake.uploads), first_upload_count)

    def test_manifest_mismatch_is_rejected(self):
        fake = FakeDrive(manifest("COSCI-OTHER-001"))
        fake.job_id = "COSCI-S03-SMOKE-001"
        fake.job_folder["name"] = fake.job_id
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(DriveBridgeError):
                run_drive_smoke("COSCI-S03-SMOKE-001", client=fake, workdir=td)

    def test_zero_cost_policy_is_enforced(self):
        m = manifest()
        m["cost_policy"]["automatic_paid_services"] = True
        fake = FakeDrive(m)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(DriveBridgeError):
                run_drive_smoke("COSCI-S03-SMOKE-001", client=fake, workdir=td)


if __name__ == "__main__":
    unittest.main()
