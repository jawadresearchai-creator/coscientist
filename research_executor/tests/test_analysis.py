import gzip
import hashlib
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from coscientist.drive import FOLDER_MIME
from research_executor.analysis import (
    AnalysisError,
    fastq_qc_summary,
    nextflow_adapter_command,
    nfcore_adapter_command,
    r_adapter_command,
    run_private_analysis_job,
)


def sample_fastq_gz():
    raw = b"@r1\nACGT\n+\nIIII\n@r2\nGGNN\n+\n!!!!\n"
    return gzip.compress(raw, mtime=0)


def manifest(job_id="COSCI-S06-ANALYSIS-001"):
    payload = sample_fastq_gz()
    return {
        "schema_version": "cosci.job/1.0",
        "job_id": job_id,
        "project_id": "RESEARCH-COSCIENTIST-BUILD",
        "task_type": "ANALYSIS",
        "privacy_class": "PRIVATE_RESEARCH",
        "cost_policy": {"require_zero_cost": True, "automatic_paid_services": False},
        "parameters": {
            "analysis_kind": "FASTQ_QC_SUMMARY",
            "analysis": {"route_id": "python-builtin", "version": "fastq-qc/1.0"},
            "inputs": [{
                "name": "input.fastq.gz",
                "drive_file_id": "INPUT",
                "source_result_file_id": "SOURCE_RESULT",
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
            }],
        },
    }


class FakeDrive:
    def __init__(self, m):
        self.job_id = m["job_id"]
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
        self.content = {
            "MAN": (json.dumps(m, sort_keys=True) + "\n").encode(),
            "INPUT": sample_fastq_gz(),
        }
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


class AnalysisTests(unittest.TestCase):
    def test_fastq_qc_summary(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.fastq.gz"
            path.write_bytes(sample_fastq_gz())
            qc = fastq_qc_summary(path)
        self.assertEqual(qc["read_count"], 2)
        self.assertEqual(qc["total_bases"], 8)
        self.assertEqual(qc["gc_fraction"], 0.5)
        self.assertEqual(qc["n_fraction"], 0.25)
        self.assertEqual(qc["mean_phred33"], 20.0)

    def test_analysis_adapters_are_deterministic(self):
        self.assertEqual(r_adapter_command("x.R", "a"), ["Rscript", "--vanilla", "x.R", "a"])
        self.assertEqual(nextflow_adapter_command("main.nf", "-profile", "docker"),
                         ["nextflow", "run", "main.nf", "-profile", "docker"])
        self.assertEqual(nfcore_adapter_command("rnaseq", "3.22.2", "--help")[:5],
                         ["nextflow", "run", "nf-core/rnaseq", "-r", "3.22.2"])

    def test_private_analysis_publishes_separate_derived_and_provenance(self):
        m = manifest()
        fake = FakeDrive(m)
        with tempfile.TemporaryDirectory() as td:
            status = run_private_analysis_job(m["job_id"], client=fake, workdir=td)
        self.assertEqual(status["status"], "SUCCEEDED")
        names = {name for _, name in fake.uploads}
        self.assertEqual(names, {
            "checkpoint.json", "fastq_qc.json", "environment.json", "provenance.json",
            "result.json", "execution.log"
        })
        result_obj = next(x for x in fake.files["RESF"] if x["name"] == "result.json")
        result = json.loads(fake.content[result_obj["id"]])
        self.assertTrue(result["validation"]["passed"])
        self.assertEqual(result["metrics"]["read_count"], 2)
        self.assertEqual(result["next_state"], "WAITING_FOR_RESEARCH_DIRECTOR")
        self.assertEqual(len(fake.files["OUTF"]), 1)
        self.assertEqual(len(fake.files["METAF"]), 2)

    def test_private_analysis_rerun_is_idempotent(self):
        m = manifest()
        fake = FakeDrive(m)
        with tempfile.TemporaryDirectory() as td:
            run_private_analysis_job(m["job_id"], client=fake, workdir=td)
            count = len(fake.uploads)
            status = run_private_analysis_job(m["job_id"], client=fake, workdir=td)
        self.assertTrue(status["resumed"])
        self.assertEqual(len(fake.uploads), count)

    def test_input_sha_mismatch_is_rejected(self):
        m = manifest()
        m["parameters"]["inputs"][0]["expected_sha256"] = "0" * 64
        fake = FakeDrive(m)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(AnalysisError):
                run_private_analysis_job(m["job_id"], client=fake, workdir=td)


if __name__ == "__main__":
    unittest.main()
