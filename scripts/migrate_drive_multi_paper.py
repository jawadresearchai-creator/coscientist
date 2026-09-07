"""Migrate canonical Drive state from the legacy global lock to MULTI_PAPER.

The migration is deliberately non-destructive:
- root legacy state/director/answer files remain untouched;
- existing per-paper folders remain untouched;
- new paper-scoped control files and a root paper_registry.json are created;
- MS-ASTRA-REVALUE-2026 is reactivated for repair because the owner explicitly
  replaced the global one-paper policy with concurrent multi-paper operation.

This script is intended to run in GitHub Actions with Drive credentials injected
through secrets. It contains no outcome analysis.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from coscientist.director import DirectorState
from coscientist.drive import DriveClient, DriveCredentials, DriveError, FOLDER_MIME
from coscientist.models import utcnow
from coscientist.multi_paper import PaperRecord, PaperRegistry, migrate_legacy_root_state
from coscientist.single_paper import PaperStage, SinglePaperState, TopicCharter

ASTRA_ID = "MS-ASTRA-REVALUE-2026"


def exact_child(client: DriveClient, parent: str, name: str, *, folder: bool | None = None):
    hits = [x for x in client.list_folder(parent) if x.get("name") == name]
    if folder is True:
        hits = [x for x in hits if x.get("mimeType") == FOLDER_MIME]
    if folder is False:
        hits = [x for x in hits if x.get("mimeType") != FOLDER_MIME]
    if len(hits) != 1:
        raise DriveError(f"expected exactly one {name!r} under {parent}; found {len(hits)}")
    return hits[0]


def assert_absent(client: DriveClient, parent: str, name: str) -> None:
    hits = [x for x in client.list_folder(parent) if x.get("name") == name]
    if hits:
        raise DriveError(
            f"refusing non-idempotent migration: {name!r} already exists under {parent}"
        )


def upload_new(client: DriveClient, local: Path, parent: str, name: str | None = None) -> str:
    remote_name = name or local.name
    assert_absent(client, parent, remote_name)
    return client.upload(str(local), parent, remote_name)


def build_astra_state(path: Path) -> SinglePaperState:
    state = SinglePaperState.load(str(path))
    charter = TopicCharter(
        paper_id=ASTRA_ID,
        working_title="OpenAI Astra Launch and Cross-Sectional Equity Revaluation",
        research_question=(
            "How did the OpenAI Astra launch revalue U.S.-listed firms across pre-event "
            "AI exposure and firm characteristics?"
        ),
        phenomenon="Public-equity revaluation around the OpenAI Astra launch",
        mechanism="Heterogeneous exposure to AI capability, complementarity and competitive displacement",
        contribution="Outcome-blind event-study evidence on heterogeneous firm revaluation after a frontier-AI release",
        unit_of_analysis="U.S.-listed operating firm",
        intended_design="Outcome-blind cross-sectional U.S. equity event study",
        primary_outcome="Abnormal equity return around the prespecified Astra event window",
        primary_exposure="Pre-event firm exposure to frontier-AI opportunity/displacement",
        required_constructs=[
            "pre-event equity prices", "market beta", "firm size", "profitability",
            "leverage", "intangibles/R&D", "industry", "pre-event SEC textual exposure",
        ],
        target_journal_family="Management Science / Information Systems / Strategy",
        known_threats=["concurrent firm-specific news", "event-time ambiguity", "measurement error in textual exposure"],
    )
    state.admit(charter)
    state.transition(PaperStage.DEVELOPING)
    state.record_problem(
        "DATA_COMPLETENESS_V3_REPAIR",
        "Owner-authorized multi-paper reactivation; complete SEC full-text, strict beta windows and debt-definition repair before data-feasibility closure.",
    )
    state.history.append({
        "at": utcnow(),
        "event": "MULTI_PAPER_REACTIVATED",
        "paper_id": ASTRA_ID,
        "classification": "OWNER_AUTHORIZED_ARCHITECTURE_MIGRATION",
        "note": "Historical USER_WITHDRAWN record is retained as archival evidence of the superseded global one-paper policy.",
    })
    state.save()
    return state


def main() -> int:
    state_root_id = os.environ["COSCI_STATE_ROOT_ID"]
    astra_folder_id = os.environ["ASTRA_STUDY_FOLDER_ID"]
    creds = DriveCredentials.from_env()
    client = DriveClient(creds)

    # Fail rather than create duplicate control files if the migration was already applied.
    if any(x.get("name") == "paper_registry.json" for x in client.list_folder(state_root_id)):
        raise DriveError("paper_registry.json already exists; migration is already applied or requires an explicit update path")

    with tempfile.TemporaryDirectory(prefix="cosci_multi_paper_") as td:
        root = Path(td)
        local_state = root / "state"
        local_state.mkdir(parents=True)

        # Retrieve the current legacy global state without modifying it.
        for name in ("single_paper.json", "director.json", "director_answer.json"):
            remote = exact_child(client, state_root_id, name, folder=False)
            client.download(remote["id"], str(local_state / name))

        registry = PaperRegistry.load(str(local_state / "paper_registry.json"))
        current = migrate_legacy_root_state(
            registry,
            legacy_state_path=str(local_state / "single_paper.json"),
            legacy_director_path=str(local_state / "director.json"),
            legacy_answer_path=str(local_state / "director_answer.json"),
            state_root=str(local_state),
            set_focus=False,
        )
        if current is None:
            raise DriveError("legacy root contains no active paper to migrate")

        # Reactivate Astra as a second concurrent paper under the owner's new policy.
        astra_dir = local_state / ASTRA_ID
        astra_dir.mkdir(parents=True, exist_ok=True)
        astra_state = build_astra_state(astra_dir / "paper_state.json")
        astra_director = DirectorState.load(str(astra_dir / "director.json"))
        astra_director.reset_for_new_paper(ASTRA_ID, astra_state.stage.value)
        (astra_dir / "director_answer.json").write_text("{}\n", encoding="utf-8")
        registry.register(PaperRecord(
            paper_id=ASTRA_ID,
            paper_state_path=f"state/{ASTRA_ID}/paper_state.json",
            director_path=f"state/{ASTRA_ID}/director.json",
            answer_path=f"state/{ASTRA_ID}/director_answer.json",
            artifact_root=f"state/{ASTRA_ID}",
            registry_status="REPAIR",
            metadata={
                "reactivated_from_archival_withdrawal": True,
                "reactivation_reason": "Owner replaced global one-paper rule with concurrent multi-paper operation",
            },
        ), set_focus=True)

        # Normalize current paper's paths to canonical Drive-relative paths.
        cur = registry.papers[current.paper_id]
        cur.paper_state_path = f"state/{current.paper_id}/paper_state.json"
        cur.director_path = f"state/{current.paper_id}/director.json"
        cur.answer_path = f"state/{current.paper_id}/director_answer.json"
        cur.artifact_root = f"state/{current.paper_id}"
        cur.metadata["migrated_from_legacy_root"] = True
        registry.save()
        registry.validate()

        # Resolve existing paper folders.
        current_folder = exact_child(client, state_root_id, current.paper_id, folder=True)["id"]
        astra_folder = exact_child(client, state_root_id, ASTRA_ID, folder=True)["id"]
        if astra_folder != astra_folder_id:
            raise DriveError(
                f"ASTRA_STUDY_FOLDER_ID mismatch: configured {astra_folder_id}, resolved {astra_folder}"
            )

        uploaded: dict[str, str] = {}
        # Current active legacy paper: copy its control state into its own namespace.
        cur_local = local_state / current.paper_id
        for name in ("paper_state.json", "director.json", "director_answer.json"):
            uploaded[f"{current.paper_id}/{name}"] = upload_new(client, cur_local / name, current_folder)

        # Astra: new paper-local state; scientific data snapshots remain untouched.
        for name in ("paper_state.json", "director.json", "director_answer.json"):
            uploaded[f"{ASTRA_ID}/{name}"] = upload_new(client, astra_dir / name, astra_folder)

        uploaded["paper_registry.json"] = upload_new(
            client, local_state / "paper_registry.json", state_root_id
        )

        receipt = {
            "schema_version": 1,
            "event": "MULTI_PAPER_MIGRATION",
            "at": utcnow(),
            "operating_mode": "MULTI_PAPER",
            "focus_paper_id": registry.focus_paper_id,
            "registered_papers": sorted(registry.papers),
            "active_or_repair_papers": registry.active_paper_ids(),
            "legacy_root_files_preserved": True,
            "astra_historical_withdrawal_preserved": True,
            "uploaded_ids": uploaded,
        }
        receipt_path = local_state / "multi_paper_migration_receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        uploaded["multi_paper_migration_receipt.json"] = upload_new(
            client, receipt_path, state_root_id
        )

        print(json.dumps(receipt | {"uploaded_ids": uploaded}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
