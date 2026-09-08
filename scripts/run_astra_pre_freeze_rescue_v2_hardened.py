"""Hardened launcher for the outcome-blind Astra pre-freeze power rescue."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rescue_astra_pre_freeze_power_v2 as rescue
import run_astra_pre_freeze_repair_v2_hardened as hardened

# The rescue reuses the base V2 EDGAR flag builder. Force that builder through
# the already-audited identity-encoding/header-validating SEC transport.
rescue.base.fetch_sec_master = hardened.fetch_sec_master_identity

if __name__ == "__main__":
    raise SystemExit(rescue.main())
