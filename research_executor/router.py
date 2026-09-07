from __future__ import annotations

import argparse
from pathlib import Path

from research_executor.public_status import write_public_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Research CoScientist public executor router")
    parser.add_argument("--job-id", required=True, help="Opaque job identifier only")
    parser.add_argument("--task-type", default="SMOKE_TEST", choices=["SMOKE_TEST"])
    parser.add_argument("--output", default="out/status.json")
    args = parser.parse_args()

    output = write_public_status(args.job_id, Path(args.output), args.task_type)
    print(f"PUBLIC_EXECUTOR_SAFE status written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
