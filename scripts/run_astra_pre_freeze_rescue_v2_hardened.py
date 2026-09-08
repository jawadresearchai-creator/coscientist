"""Hardened launcher for the outcome-blind Astra pre-freeze power rescue."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rescue_astra_pre_freeze_power_v2 as rescue
import run_astra_pre_freeze_repair_v2_hardened as hardened
from coscientist.astra_design_repair import conservative_inverse_variance_weights

# Force the reused V2 EDGAR flag builder through the audited transport.
rescue.base.fetch_sec_master = hardened.fetch_sec_master_identity

# Degenerate/stale zero-sigma histories must never receive infinite precision.
# The committed primitive maps them to the p90 sigma before inversion, i.e. the
# lowest allowed precision weight, while leaving candidate order unchanged.
rescue._weights_from_sigma = conservative_inverse_variance_weights

if __name__ == "__main__":
    raise SystemExit(rescue.main())
