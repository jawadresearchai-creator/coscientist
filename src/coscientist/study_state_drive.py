"""Per-paper immutable scientific artifacts in the canonical Drive state root.

The flat control files (single_paper.json, director.json, director_answer.json)
remain small mutable orchestration projections.  Each paper gets one uniquely
named child folder under the same state root for write-once lifecycle artifacts:
freeze.json, analysis_lock.json, receipts, journal plan, and later replication
metadata.  Duplicate names or in-place replacement are refused.
"""
from __future__ import annotations

import argparse
import os

from .drive import DriveClient, DriveCredentials, DriveError, FOLDER_MIME
from .integrity import sha256_file


class StudyStateError(DriveError):
    pass


def _client() -> DriveClient:
    return DriveClient(DriveCredentials.from_env())


def ensure_study_folder(client: DriveClient, root_folder_id: str, study_id: str) -> str:
    hits = [f for f in client.list_folder(root_folder_id)
            if f.get("name") == study_id and f.get("mimeType") == FOLDER_MIME]
    if len(hits) > 1:
        raise StudyStateError(
            f"ambiguous study state: {len(hits)} folders named {study_id!r}"
        )
    if hits:
        return hits[0]["id"]
    return client.create_folder(study_id, root_folder_id)


def _file_hits(client: DriveClient, folder_id: str, name: str) -> list[dict]:
    return [f for f in client.list_folder(folder_id)
            if f.get("name") == name and f.get("mimeType") != FOLDER_MIME]


def push_immutable(client: DriveClient, root_folder_id: str, study_id: str,
                   local_path: str, remote_name: str | None = None) -> str:
    if not os.path.isfile(local_path):
        raise StudyStateError(f"local artifact does not exist: {local_path}")
    remote_name = remote_name or os.path.basename(local_path)
    folder_id = ensure_study_folder(client, root_folder_id, study_id)
    hits = _file_hits(client, folder_id, remote_name)
    if len(hits) > 1:
        raise StudyStateError(
            f"study {study_id}: {len(hits)} files named {remote_name!r}; identity is ambiguous"
        )
    local_sha = sha256_file(local_path)
    if hits:
        # Download and hash instead of trusting Drive's md5 metadata: the
        # scientific identity everywhere else in the system is SHA-256.
        tmp = local_path + ".drive-existing"
        try:
            remote_sha = client.download(hits[0]["id"], tmp)
            if remote_sha != local_sha:
                raise StudyStateError(
                    f"study {study_id}/{remote_name} already exists with different bytes; "
                    "immutable lifecycle artifacts cannot be overwritten"
                )
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return hits[0]["id"]
    return client.upload(local_path, folder_id, remote_name)


def pull_artifact(client: DriveClient, root_folder_id: str, study_id: str,
                  remote_name: str, dest: str, *, required: bool = True) -> bool:
    folders = [f for f in client.list_folder(root_folder_id)
               if f.get("name") == study_id and f.get("mimeType") == FOLDER_MIME]
    if len(folders) > 1:
        raise StudyStateError(f"ambiguous study folder {study_id!r}")
    if not folders:
        if required:
            raise StudyStateError(f"study folder {study_id!r} does not exist")
        return False
    hits = _file_hits(client, folders[0]["id"], remote_name)
    if len(hits) > 1:
        raise StudyStateError(
            f"study {study_id}: expected at most one {remote_name}, found {len(hits)}"
        )
    if not hits:
        if required:
            raise StudyStateError(f"study {study_id}: missing {remote_name}")
        if os.path.exists(dest):
            os.remove(dest)
        return False
    client.download(hits[0]["id"], dest)
    return True


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.study_state_drive")
    p.add_argument("command", choices=["ensure", "push", "pull", "pull-optional"])
    p.add_argument("--study", required=True)
    p.add_argument("--root-folder", required=True)
    p.add_argument("--path")
    p.add_argument("--name")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    client = _client()
    if args.command == "ensure":
        print(ensure_study_folder(client, args.root_folder, args.study))
        return 0
    if not args.path or not args.name:
        raise SystemExit("--path and --name are required for push/pull")
    if args.command == "push":
        print(push_immutable(client, args.root_folder, args.study, args.path, args.name))
        return 0
    found = pull_artifact(
        client, args.root_folder, args.study, args.name, args.path,
        required=args.command == "pull",
    )
    print("FOUND" if found else "MISSING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
