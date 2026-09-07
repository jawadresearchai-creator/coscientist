from __future__ import annotations

import argparse

from research_executor.public_status import validate_job_id, validate_task_type


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a privacy-safe public dispatch")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--task-type", required=True)
    args = parser.parse_args()
    validate_job_id(args.job_id)
    validate_task_type(args.task_type)
    print("Dispatch validation PASS: opaque job ID + public task type only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
