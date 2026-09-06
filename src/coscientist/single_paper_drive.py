"""Google Drive persistence for the canonical single-paper state.

The state is one small raw JSON file named `single_paper.json`. Duplicate names
are refused because two competing state files would recreate the ambiguity the
single-paper model is designed to eliminate.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request

from .drive import API, UPLOAD_API, DriveClient, DriveCredentials, DriveError
from .single_paper import STATE_FILENAME, SinglePaperState

MAX_STATE_BYTES = 10 * 1024 * 1024


def _client() -> DriveClient:
    return DriveClient(DriveCredentials.from_env())


def pull(state: SinglePaperState, client: DriveClient, folder_id: str) -> bool:
    hits = [f for f in client.list_folder(folder_id) if f.get("name") == STATE_FILENAME]
    if len(hits) > 1:
        raise DriveError(
            f"canonical state is ambiguous: {len(hits)} files named {STATE_FILENAME!r}"
        )
    if not hits:
        state.save()
        return False
    client.download(hits[0]["id"], state.path)
    # Validate immediately; a malformed/wrong-mode Drive file must not silently
    # become active scientific state.
    SinglePaperState.load(state.path)
    return True


def push(state: SinglePaperState, client: DriveClient, folder_id: str) -> str:
    state.save()
    size = os.path.getsize(state.path)
    if size > MAX_STATE_BYTES:
        raise DriveError(
            f"single-paper state is {size:,} bytes; canonical state must remain small"
        )
    hits = [f for f in client.list_folder(folder_id) if f.get("name") == STATE_FILENAME]
    if len(hits) > 1:
        raise DriveError(
            f"canonical state is ambiguous: {len(hits)} files named {STATE_FILENAME!r}"
        )
    if not hits:
        return client.upload(state.path, folder_id, STATE_FILENAME)

    payload = open(state.path, "rb").read()
    req = urllib.request.Request(
        f"{UPLOAD_API}/files/{hits[0]['id']}?uploadType=media&fields=id",
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
    return json.loads(body.decode()).get("id", hits[0]["id"]) if body else hits[0]["id"]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m coscientist.single_paper_drive")
    p.add_argument("command", choices=["pull", "push"])
    p.add_argument("--state", default="state/single_paper.json")
    p.add_argument("--folder", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    state = SinglePaperState.load(args.state)
    client = _client()
    if args.command == "pull":
        found = pull(state, client, args.folder)
        print("loaded canonical state from Drive" if found else
              "no canonical state existed; initialized NO_ACTIVE_PAPER")
    else:
        print(push(state, client, args.folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
