import hashlib
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from research_executor.acquisition import FOLDER_MIME
from research_executor.literature import (
    build_claim_source_map,
    build_evidence_packets,
    build_novelty_court,
    deduplicate_records,
    normalize_doi,
    run_private_literature_job,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


def scholarly_opener(request, timeout=0):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    if "api.crossref.org" in url:
        payload = {
            "message": {
                "items": [{
                    "DOI": "10.1000/example",
                    "title": ["Extracellular ATP signaling in plant stress"],
                    "abstract": "Extracellular ATP participates in plant stress signaling and downstream responses.",
                    "author": [{"given": "A", "family": "Author"}],
                    "published": {"date-parts": [[2024, 1, 1]]},
                    "container-title": ["Plant Journal"],
                    "type": "journal-article",
                    "URL": "https://doi.org/10.1000/example",
                    "is-referenced-by-count": 7,
                }]
            }
        }
        return FakeResponse(json.dumps(payload).encode())
    if "europepmc" in url:
        payload = {
            "resultList": {
                "result": [{
                    "id": "12345",
                    "pmid": "12345",
                    "pmcid": "PMC12345",
                    "doi": "10.1000/example",
                    "title": "Extracellular ATP signaling in plant stress",
                    "abstractText": "Plant extracellular ATP is implicated in signaling responses to stress and mechanical stimuli.",
                    "pubYear": "2024",
                    "firstPublicationDate": "2024-01-01",
                    "pubType": "research article",
                    "journalTitle": "Plant Journal",
                    "authorString": "A Author, B Author",
                    "citedByCount": 8,
                    "source": "MED",
                }]
            }
        }
        return FakeResponse(json.dumps(payload).encode())
    raise AssertionError(f"unexpected URL: {url}")


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


def manifest(job_id="COSCI-S07-LIT-001"):
    return {
        "schema_version": "cosci.job/1.0",
        "job_id": job_id,
        "project_id": "RESEARCH-COSCIENTIST-BUILD",
        "task_type": "LITERATURE",
        "privacy_class": "PRIVATE_RESEARCH",
        "cost_policy": {"require_zero_cost": True, "automatic_paid_services": False},
        "parameters": {
            "research_question": "What evidence links extracellular ATP to plant stress responses?",
            "search_query": "extracellular ATP plant stress signaling",
            "max_results_per_source": 5,
            "claims": [
                "Extracellular ATP participates in plant stress signaling",
                "Mechanical stimuli can be associated with extracellular ATP signaling",
            ],
        },
    }


class LiteratureTests(unittest.TestCase):
    def test_normalize_doi(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")
        self.assertIsNone(normalize_doi("not-a-doi"))

    def test_deduplicates_same_doi_across_sources(self):
        records = [
            {"source": "crossref", "source_record_id": "10.1000/x", "doi": "10.1000/x", "pmid": None, "pmcid": None, "openalex_id": None, "title": "Same title", "normalized_title": "same title", "abstract": "short", "year": 2024, "publication_date": None, "type": "article", "journal": "J", "authors": ["A"], "citation_count": 1, "url": "u1"},
            {"source": "europe-pmc", "source_record_id": "1", "doi": "10.1000/x", "pmid": "1", "pmcid": None, "openalex_id": None, "title": "Same title", "normalized_title": "same title", "abstract": "a much longer abstract", "year": 2024, "publication_date": "2024-01-01", "type": "article", "journal": "J", "authors": ["B"], "citation_count": 2, "url": "u2"},
        ]
        works, lineage = deduplicate_records(records)
        self.assertEqual(len(works), 1)
        self.assertEqual(len(works[0]["source_records"]), 2)
        self.assertEqual(works[0]["identifiers"]["pmid"], ["1"])
        self.assertEqual(len(lineage), 1)

    def test_claim_mapping_and_novelty_are_conservative(self):
        records = [
            {"source": "crossref", "source_record_id": "x", "doi": "10.1000/x", "pmid": None, "pmcid": None, "openalex_id": None, "title": "Extracellular ATP signaling in plant stress", "normalized_title": "extracellular atp signaling in plant stress", "abstract": "ATP signaling affects plant stress responses", "year": 2024, "publication_date": None, "type": "article", "journal": "J", "authors": [], "citation_count": 1, "url": "u"}
        ]
        works, _ = deduplicate_records(records)
        packets = build_evidence_packets(works)
        mapping = build_claim_source_map(["ATP signaling affects plant stress"], packets)
        self.assertTrue(mapping[0]["candidate_evidence"])
        novelty = build_novelty_court("Extracellular ATP signaling in plant stress", works)
        self.assertEqual(novelty["screen"], "DIRECT_TITLE_COLLISION")
        self.assertEqual(novelty["final_novelty_judgment"], "REQUIRES_RESEARCH_DIRECTOR")

    def test_private_literature_job_publishes_traceable_artifacts(self):
        m = manifest()
        fake = FakeDrive(m)
        with tempfile.TemporaryDirectory() as td:
            status = run_private_literature_job(m["job_id"], client=fake, workdir=td, opener=scholarly_opener, openalex_key=None)
        self.assertEqual(status["status"], "SUCCEEDED")
        names = {name for _, name in fake.uploads}
        self.assertEqual(names, {
            "checkpoint.json", "literature_corpus.json", "evidence_packets.json",
            "claim_source_map.json", "novelty_court.json", "source_provenance.json",
            "result.json", "execution.log"
        })
        result_obj = next(x for x in fake.files["RESF"] if x["name"] == "result.json")
        result = json.loads(fake.content[result_obj["id"]])
        self.assertTrue(result["validation"]["passed"])
        self.assertEqual(result["metrics"]["source_result_counts"]["crossref"], 1)
        self.assertEqual(result["metrics"]["source_result_counts"]["europe-pmc"], 1)
        self.assertEqual(result["metrics"]["source_result_counts"]["openalex-free-key"], 0)

    def test_private_literature_rerun_is_idempotent(self):
        m = manifest()
        fake = FakeDrive(m)
        with tempfile.TemporaryDirectory() as td:
            first = run_private_literature_job(m["job_id"], client=fake, workdir=td, opener=scholarly_opener, openalex_key=None)
            upload_count = len(fake.uploads)
            second = run_private_literature_job(m["job_id"], client=fake, workdir=td, opener=scholarly_opener, openalex_key=None)
        self.assertFalse(first["resumed"])
        self.assertTrue(second["resumed"])
        self.assertEqual(len(fake.uploads), upload_count)


if __name__ == "__main__":
    unittest.main()
