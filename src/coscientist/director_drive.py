"""Google Drive persistence for the single-paper Research Director.

The director state and the reasoning-plane answer inbox are small JSON control
files. Duplicate names are refused because two competing orchestration states
or two simultaneous answers would make the one-paper process ambiguous.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request

from .director import ANSWER_FILENAME, DIRECTOR_FILENAME, DirectorState
from .drive import UPLOAD_API, DriveClient, DriveCredentials, DriveError

MAX_CONTROL_BYTES = 10 * 1024 * 1024


def _client() -> DriveClient:
    return DriveClient(DriveCredentials.from_env())


def _hits(client: DriveClient, folder_id: str, name: str) -> list[dict]:
    return [f for f in client.list_folder(folder_id) if f.get("name") == name]


def pull_state(state: DirectorState, client: DriveClient, folder_id: str) -> bool:
    hits = _hits(client, folder_id, DIRECTOR_FILENAME)
    if len(hits) > 1:
        raise DriveError(
            f"director state is ambiguous: {len(hits)} files named {DIRECTOR_FILENAME!r}"
        )
    if not hits:
        state.save()
        return False
    client.download(hits[0]["id"], state.path)
    DirectorState.load(state.path)
    return True


def _patch_small_json(path: str, client: DriveClient, file_id: str) -> str:
    size = os.path.getsize(path)
    if size > MAX_CONTROL_BYTES:
        raise DriveError(f"control file is too large: {size:,} bytes")
    with open(path, "rb") as fh:
        payload = fh.read()
    req = urllib.request.Request(
        f"{UPLOAD_API}/files/{file_id}?uploadType=media&fields=id",
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {client.token()}",
            "Content-Type": "application/json",
            "Content-Length": str(size),
        },
    )
    with client.opener(req, timeout=120) as resp:
        body = resp.read()
    return json.loads(body.decode()).get("id", file_id) if body else file_id


def push_state(state: DirectorState, client: DriveClient, folder_id: str) -> str:
    state.save()
    hits = _hits(client, folder_id, DIRECTOR_FILENAME)
    if len(hits) > 1:
        raise DriveError(
            f"director state is ambiguous: {len(hits)} files named {DIRECTOR_FILENAME!r}"
        )
    if not hits:
        return client.upload(state.path, folder_id, DIRECTOR_FILENAME)
    return _patch_small_json(state.path, client, hits[0]["id"])


def pull_answer(dest: str, client: DriveClient, folder_id: str) -> bool:
    """Fetch the single overwriteable reasoning-plane answer if it exists.

    The remote file is deliberately not deleted after application. The director
    remembers applied action IDs, so the same answer becomes a harmless no-op;
    the reasoning plane overwrites this one inbox file for the next action.
    """
    hits = _hits(client, folder_id, ANSWER_FILENAME)
    if len(hits) > 1:
        raise DriveError(
            f"answer inbox is ambiguous: {len(hits)} files named {ANSWER_FILENAME!r}"
        )
    if not hits:
        if os.path.exists(dest):
            os.remove(dest)
        return False
    client.download(hits[0]["id"], dest)
    size = os.path.getsize(dest)
    if size > MAX_CONTROL_BYTES:
        raise DriveError(f"director answer is too large: {size:,} bytes")
    with open(dest, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise DriveError("director_answer.json must contain one JSON object")
    return True


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.director_drive")
    p.add_argument("command", choices=["pull", "push", "pull-answer"])
    p.add_argument("--state", default="state/director.json")
    p.add_argument("--answer", default="state/director_answer.json")
    p.add_argument("--folder", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    client = _client()
    if args.command == "pull-answer":
        found = pull_answer(args.answer, client, args.folder)
        print("loaded director answer" if found else "no director answer present")
        return 0

    state = DirectorState.load(args.state)
    if args.command == "pull":
        found = pull_state(state, client, args.folder)
        print("loaded director state from Drive" if found else
              "no director state existed; initialized empty director")
    else:
        print(push_state(state, client, args.folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
