import hashlib
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from research_executor.acquisition import (
    AcquisitionError,
    FOLDER_MIME,
    acquire_with_fallback,
    load_registry,
    run_private_acquisition_job,
    select_zero_cost_route,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(payload)
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


def opener_for(mapping):
    def opener(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        value = mapping[url]
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)
    return opener


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
            ],
            "CPF": [], "RESF": [], "LOGF": [], "OUTF": [],
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
        mime = "application/json" if name.endswith(".json") else "application/octet-stream"
        self.files[folder_id].append({"id": new_id, "name": name, "mimeType": mime})
        self.uploads.append((folder_id, name))
        return new_id


def manifest(job_id="COSCI-S04-HTTP-001", source_class="HTTP_FILE"):
    return {
        "schema_version": "cosci.job/1.0",
        "job_id": job_id,
        "project_id": "RESEARCH-COSCIENTIST-BUILD",
        "task_type": "DATA_ACQUISITION",
        "privacy_class": "PRIVATE_RESEARCH",
        "cost_policy": {"require_zero_cost": True, "automatic_paid_services": False},
        "parameters": {
            "source_class": source_class,
            "artifact_name": "sample.json" if source_class == "REST_JSON_API" else "sample.txt",
            "candidates": [{"url": "https://example.test/data", "cost": "FREE"}],
        },
    }


class AcquisitionTests(unittest.TestCase):
    def test_registry_selects_free_http_route(self):
        route = select_zero_cost_route(load_registry(), "HTTP_FILE", "https://example.test/data")
        self.assertEqual(route["cost"], "FREE")
        self.assertTrue(route["supports_resume"])

    def test_paid_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(AcquisitionError):
                acquire_with_fallback(
                    "HTTP_FILE",
                    [{"url": "https://example.test/data", "cost": "PAID"}],
                    Path(td) / "x.bin",
                    opener=opener_for({}),
                )

    def test_fallback_uses_second_zero_cost_candidate(self):
        first = "https://example.test/missing"
        second = "https://example.test/good"
        with tempfile.TemporaryDirectory() as td:
            meta = acquire_with_fallback(
                "HTTP_FILE",
                [{"url": first, "cost": "FREE"}, {"url": second, "cost": "FREE"}],
                Path(td) / "x.txt",
                opener=opener_for({first: OSError("offline"), second: b"hello"}),
            )
        self.assertEqual(meta["candidate_index"], 1)
        self.assertEqual(meta["fallbacks_attempted"], 1)
        self.assertEqual(meta["sha256"], hashlib.sha256(b"hello").hexdigest())

    def test_rest_json_requires_valid_json(self):
        url = "https://example.test/json"
        with tempfile.TemporaryDirectory() as td:
            meta = acquire_with_fallback(
                "REST_JSON_API",
                [{"url": url, "cost": "FREE"}],
                Path(td) / "x.json",
                opener=opener_for({url: b'{"ok": true}'}),
            )
        self.assertEqual(meta["source_class"], "REST_JSON_API")

    def test_private_drive_job_publishes_output_result_checkpoint_and_log(self):
        m = manifest()
        fake = FakeDrive(m)
        url = m["parameters"]["candidates"][0]["url"]
        with tempfile.TemporaryDirectory() as td:
            status = run_private_acquisition_job(
                m["job_id"], client=fake, workdir=td,
                opener=opener_for({url: b"public data\n"}),
            )
        self.assertEqual(status["status"], "SUCCEEDED")
        names = {name for _, name in fake.uploads}
        self.assertEqual(names, {"checkpoint.json", "sample.txt", "result.json", "execution.log"})
        result_obj = next(x for x in fake.files["RESF"] if x["name"] == "result.json")
        result = json.loads(fake.content[result_obj["id"]])
        self.assertEqual(result["outputs"][0]["sha256"], hashlib.sha256(b"public data\n").hexdigest())
        self.assertEqual(result["next_state"], "WAITING_FOR_RESEARCH_DIRECTOR")


if __name__ == "__main__":
    unittest.main()
