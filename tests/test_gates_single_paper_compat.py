"""Compatibility safeguards retained by the single-paper G3 redesign."""

from coscientist.gates import g3_access
from coscientist.lake import LakeCatalog
from coscientist.models import Candidate, Construct, Verdict
from coscientist.registry import SourceRegistry


def test_g3_defers_when_essential_source_suitability_is_unknown(tmp_path):
    import json
    import yaml

    reg_path = tmp_path / "sources.yaml"
    reg_path.write_text(yaml.safe_dump({
        "sources": [{
            "id": "UNKNOWN_FIT",
            "name": "unknown-fit source",
            "access_class": "OPEN",
            "redistributable": True,
            "concepts": ["outcome_y"],
            # Deliberately omit granularity/coverage metadata.
        }]
    }))
    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps({"datasets": []}))

    candidate = Candidate(
        id="MS-COMPAT-1",
        title="t",
        question="q",
        design="panel",
        constructs=[
            Construct(
                "y",
                "outcome",
                ["outcome_y"],
                preferred_source="UNKNOWN_FIT",
                granularity="firm-quarter",
            )
        ],
    )

    res = g3_access(
        candidate,
        SourceRegistry.load(str(reg_path)),
        LakeCatalog.load(str(cat_path)),
    )
    assert res.verdict is Verdict.DEFER
    assert res.evidence["unverified"] == ["y"]
