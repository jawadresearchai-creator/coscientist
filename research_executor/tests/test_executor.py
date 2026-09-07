import json
import tempfile
import unittest
from pathlib import Path

from research_executor.public_status import (
    build_public_status,
    validate_job_id,
    validate_public_status,
    write_public_status,
)


class PublicExecutorTests(unittest.TestCase):
    def test_valid_opaque_job_id(self):
        validate_job_id("COSCI-SMOKE-20260907-001")

    def test_rejects_job_id_that_could_smuggle_private_payload(self):
        with self.assertRaises(ValueError):
            validate_job_id("topic=secret research idea")

    def test_status_is_public_only(self):
        status = build_public_status("COSCI-SMOKE-20260907-001")
        validate_public_status(status)
        self.assertEqual(status["privacy_class"], "PUBLIC_EXECUTOR_SAFE")
        encoded = json.dumps(status).lower()
        for forbidden in ["research_question", "hypothesis", "manuscript", "refresh_token", "api_key"]:
            self.assertNotIn(forbidden, encoded)

    def test_smoke_receipt_is_deterministic_for_same_input(self):
        a = build_public_status("COSCI-SMOKE-20260907-001")
        b = build_public_status("COSCI-SMOKE-20260907-001")
        self.assertEqual(a["deterministic_receipt"], b["deterministic_receipt"])

    def test_write_and_read_status(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_public_status("COSCI-SMOKE-20260907-001", Path(td) / "status.json")
            status = json.loads(p.read_text(encoding="utf-8"))
            validate_public_status(status)

    def test_rejects_private_field_in_status(self):
        status = build_public_status("COSCI-SMOKE-20260907-001")
        status["research_question"] = "should never be public"
        with self.assertRaises(ValueError):
            validate_public_status(status)


if __name__ == "__main__":
    unittest.main()
