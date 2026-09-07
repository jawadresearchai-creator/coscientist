import base64
import hashlib
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from research_executor.acquisition import FOLDER_MIME
from research_executor.publication import (
    build_canonical_source,
    load_publication_registry,
    normalize_doi,
    render_docx,
    render_pdf,
    retrieve_journal_requirements,
    run_private_publication_job,
    validate_publication_manifest,
)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeHeaders(dict):
    def items(self):
        return super().items()


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_type: str):
        super().__init__(payload)
        self.status = 200
        self.headers = FakeHeaders({"content-type": content_type})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def publication_opener(request, timeout=0):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    if "journals.plos.org" in url:
        return FakeResponse(
            b"<html><body><h1>PLOS ONE Submission Guidelines</h1><p>Figures Tables References Data Availability</p></body></html>",
            "text/html; charset=utf-8",
        )
    if "api.crossref.org/works/" in url:
        doi = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        payload = {
            "message": {
                "DOI": doi,
                "title": ["Acceptance fixture evidence source"],
            }
        }
        return FakeResponse(json.dumps(payload).encode(), "application/json")
    raise AssertionError(f"unexpected URL class: {url.split(':', 1)[0]}")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fixture_manifest(job_id="COSCI-S09-PUB-001"):
    analysis = (
        json.dumps(
            {
                "schema_version": "fixture.analysis/1.0",
                "fixture": True,
                "metrics": {"metric_a": 1.25},
            },
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    evidence = (
        json.dumps(
            {
                "schema_version": "fixture.evidence/1.0",
                "fixture": True,
                "claim": "Fixture claim only",
                "doi": "10.1016/j.pbi.2014.04.009",
            },
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    source_artifacts = [
        {
            "name": "analysis_fixture.json",
            "kind": "ANALYSIS_RESULT",
            "drive_file_id": "ANALYSIS",
            "sha256": sha(analysis),
        },
        {
            "name": "evidence_fixture.json",
            "kind": "EVIDENCE_PACKET",
            "drive_file_id": "EVIDENCE",
            "sha256": sha(evidence),
        },
        {
            "name": "figure_fixture.png",
            "kind": "FIGURE",
            "drive_file_id": "FIGURE",
            "sha256": sha(PNG_1X1),
        },
    ]
    return (
        {
            "schema_version": "cosci.job/1.0",
            "job_id": job_id,
            "project_id": "RESEARCH-COSCIENTIST-BUILD",
            "task_type": "PUBLICATION",
            "privacy_class": "PRIVATE_RESEARCH",
            "cost_policy": {
                "require_zero_cost": True,
                "automatic_paid_services": False,
            },
            "parameters": {
                "acceptance_fixture": True,
                "journal_requirements": {
                    "journal_key": "PLOS_ONE_ACCEPTANCE_FIXTURE",
                    "official_url": "https://journals.plos.org/plosone/s/submission-guidelines",
                    "required_markers": [
                        "PLOS ONE",
                        "Figures",
                        "Tables",
                        "References",
                    ],
                },
                "source_artifacts": source_artifacts,
                "journal_overrides": {
                    "font_name": "Times New Roman",
                    "font_size_pt": 11,
                    "line_spacing": 1.5,
                    "paragraph_alignment": "JUSTIFIED",
                    "figure_width_inches": 4.0,
                    "figure_mode": "EMBED_AND_SEPARATE",
                    "table_mode": "EMBED_EDITABLE",
                },
                "manuscript": {
                    "document_status": "ENGINE_ACCEPTANCE_FIXTURE_NOT_SCIENTIFIC_MANUSCRIPT",
                    "title": "Publication engine acceptance fixture",
                    "authors": ["Research CoScientist Fixture"],
                    "sections": [
                        {
                            "section_id": "abstract",
                            "heading": "Abstract",
                            "paragraphs": [
                                {
                                    "text": "This document tests the publication engine only; it is not a scientific manuscript.",
                                    "citation_refs": [],
                                    "result_refs": [],
                                }
                            ],
                        },
                        {
                            "section_id": "results",
                            "heading": "Results",
                            "paragraphs": [
                                {
                                    "text": "The fixture result is bound to a hash-validated private analysis artifact.",
                                    "citation_refs": ["CIT-001"],
                                    "result_refs": ["analysis_fixture.json"],
                                }
                            ],
                        },
                    ],
                    "citations": [
                        {
                            "citation_id": "CIT-001",
                            "doi": "10.1016/j.pbi.2014.04.009",
                            "title": "Acceptance fixture evidence source",
                            "evidence_artifact": "evidence_fixture.json",
                        }
                    ],
                    "figures": [
                        {
                            "figure_id": "FIG-1",
                            "caption": "Fixture image used only to validate embedding and separate-file packaging.",
                            "artifact_name": "figure_fixture.png",
                        }
                    ],
                    "tables": [
                        {
                            "table_id": "TBL-1",
                            "caption": "Fixture editable table.",
                            "columns": ["Metric", "Value"],
                            "rows": [["metric_a", "1.25"]],
                            "result_artifact": "analysis_fixture.json",
                        }
                    ],
                    "availability_statement": "Acceptance fixture source artifacts are private build inputs.",
                },
            },
        },
        {"ANALYSIS": analysis, "EVIDENCE": evidence, "FIGURE": PNG_1X1},
    )


class FakeDrive:
    def __init__(self, manifest, sources):
        self.job_id = manifest["job_id"]
        self.job_folder = {
            "id": "JOBF",
            "name": self.job_id,
            "mimeType": FOLDER_MIME,
        }
        self.files = {
            "JOBF": [
                {"id": "MAN", "name": "job.json", "mimeType": "application/json"},
                {"id": "CPF", "name": "checkpoints", "mimeType": FOLDER_MIME},
                {"id": "RESF", "name": "results", "mimeType": FOLDER_MIME},
                {"id": "LOGF", "name": "logs", "mimeType": FOLDER_MIME},
                {"id": "OUTF", "name": "outputs", "mimeType": FOLDER_MIME},
                {"id": "METAF", "name": "metadata", "mimeType": FOLDER_MIME},
            ],
            "CPF": [],
            "RESF": [],
            "LOGF": [],
            "OUTF": [],
            "METAF": [],
        }
        self.content = {
            "MAN": (json.dumps(manifest, sort_keys=True) + "\n").encode(),
            **sources,
        }
        self.uploads = []

    def _get(self, url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(
            "q", [""]
        )[0]
        if self.job_id in q:
            return json.dumps({"files": [self.job_folder]}).encode()
        return json.dumps({"files": []}).encode()

    def list_folder(self, folder_id):
        return list(self.files.get(folder_id, []))

    def download(self, file_id, dest, expected_sha256=None):
        raw = self.content[file_id]
        Path(dest).write_bytes(raw)
        digest = sha(raw)
        if expected_sha256 and digest != expected_sha256:
            raise AssertionError("fake expected sha mismatch")
        return digest

    def upload(self, local_path, folder_id, name=None):
        name = name or Path(local_path).name
        new_id = f"UP{len(self.uploads) + 1}"
        raw = Path(local_path).read_bytes()
        self.content[new_id] = raw
        mime = (
            "application/json"
            if name.endswith(".json")
            else "text/plain"
            if name.endswith(".log")
            else "application/octet-stream"
        )
        self.files[folder_id].append(
            {"id": new_id, "name": name, "mimeType": mime}
        )
        self.uploads.append((folder_id, name))
        return new_id


class PublicationTests(unittest.TestCase):
    def test_registry_enforces_private_reproducible_zero_cost_build(self):
        registry = load_publication_registry()
        self.assertTrue(registry["policy"]["single_canonical_source_required"])
        self.assertTrue(registry["policy"]["citation_verification_required"])
        self.assertFalse(registry["policy"]["automatic_paid_routes"])

    def test_acceptance_fixture_requires_non_scientific_label(self):
        manifest, _ = fixture_manifest()
        validate_publication_manifest(manifest, manifest["job_id"])
        manifest["parameters"]["manuscript"]["document_status"] = "DRAFT"
        with self.assertRaises(Exception):
            validate_publication_manifest(manifest, manifest["job_id"])

    def test_official_requirements_are_retrieved_with_content_hash(self):
        manifest, _ = fixture_manifest()
        requirements = retrieve_journal_requirements(
            manifest["parameters"]["journal_requirements"],
            opener=publication_opener,
        )
        self.assertEqual(requirements["marker_validation"], "PASS")
        self.assertRegex(requirements["content_sha256"], r"^[0-9a-f]{64}$")

    def test_doi_normalization(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.1016/J.PBI.2014.04.009"),
            "10.1016/j.pbi.2014.04.009",
        )
        self.assertIsNone(normalize_doi("not-a-doi"))

    def test_docx_and_pdf_render_deterministically(self):
        manifest, sources = fixture_manifest()
        requirements = retrieve_journal_requirements(
            manifest["parameters"]["journal_requirements"],
            opener=publication_opener,
        )
        refs = [
            {**item, "bytes": len(sources[item["drive_file_id"]])}
            for item in manifest["parameters"]["source_artifacts"]
        ]
        source = build_canonical_source(
            manifest["parameters"], refs, requirements
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = {}
            for spec in manifest["parameters"]["source_artifacts"]:
                path = root / spec["name"]
                path.write_bytes(sources[spec["drive_file_id"]])
                paths[spec["name"]] = path
            a, b = root / "a.docx", root / "b.docx"
            c, d = root / "a.pdf", root / "b.pdf"
            render_docx(source, paths, a)
            render_docx(source, paths, b)
            render_pdf(source, paths, c)
            render_pdf(source, paths, d)
            self.assertEqual(sha(a.read_bytes()), sha(b.read_bytes()))
            self.assertEqual(sha(c.read_bytes()), sha(d.read_bytes()))
            self.assertTrue(a.read_bytes().startswith(b"PK"))
            self.assertTrue(c.read_bytes().startswith(b"%PDF"))

    def test_private_publication_job_builds_and_reruns_idempotently(self):
        manifest, sources = fixture_manifest()
        fake = FakeDrive(manifest, sources)
        with tempfile.TemporaryDirectory() as td:
            first = run_private_publication_job(
                manifest["job_id"],
                client=fake,
                workdir=td,
                opener=publication_opener,
            )
            upload_count = len(fake.uploads)
            second = run_private_publication_job(
                manifest["job_id"],
                client=fake,
                workdir=td,
                opener=publication_opener,
            )
        self.assertEqual(first["status"], "SUCCEEDED")
        self.assertFalse(first["resumed"])
        self.assertTrue(second["resumed"])
        self.assertEqual(len(fake.uploads), upload_count)
        names = {name for _, name in fake.uploads}
        required = {
            "checkpoint.json",
            "journal_requirements.json",
            "canonical_manuscript.json",
            "citation_ledger.json",
            "figure_table_registry.json",
            "manuscript.docx",
            "manuscript.pdf",
            "build_manifest.json",
            "submission-FIG-1.png",
            "publication_provenance.json",
            "result.json",
            "execution.log",
        }
        self.assertEqual(names, required)
        result_obj = next(
            x for x in fake.files["RESF"] if x["name"] == "result.json"
        )
        result = json.loads(fake.content[result_obj["id"]])
        self.assertTrue(result["validation"]["passed"])
        self.assertEqual(result["next_state"], "READY_FOR_ADVERSARIAL_REVIEW")
        self.assertEqual(result["metrics"]["verified_doi_count"], 1)


if __name__ == "__main__":
    unittest.main()
