"""Google Drive acquisition: authoritative data in, deliverables out.

Drive holds the data and receives the results; the repository holds only code.
That separation is what lets the repository become public without publishing
unsubmitted findings, and it is why this module exists rather than a `data/`
directory in git.

Credentials come from Actions secrets and are never written to disk. A refresh
token is exchanged for an access token per run, so a revoked or expired
credential fails here -- loudly, at the start -- rather than halfway through a
build.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(RuntimeError):
    pass


class CredentialError(DriveError):
    """The credential is absent, expired or revoked."""


@dataclass
class DriveCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    root_folder_id: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "DriveCredentials":
        env = os.environ if env is None else env
        needed = ["GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_TOKEN"]
        missing = [k for k in needed if not env.get(k)]
        if missing:
            raise CredentialError(
                f"missing Drive secrets: {', '.join(missing)}. These live in Actions "
                "secrets and are injected at run time; they are never in the repo."
            )
        token_value = env["GDRIVE_TOKEN"].strip()
        # The GMS lake stores the rclone OAuth token JSON in the same secret
        # name.  CoScientist historically interpreted the whole value as the
        # refresh token.  Accept both representations so one Google identity
        # can serve both systems without copying credentials into code.
        if token_value.startswith("{"):
            try:
                parsed = json.loads(token_value)
            except json.JSONDecodeError as exc:
                raise CredentialError("GDRIVE_TOKEN looks like JSON but is invalid") from exc
            token_value = str(parsed.get("refresh_token") or "").strip()
            if not token_value:
                raise CredentialError("GDRIVE_TOKEN JSON contains no refresh_token")
        return cls(env["GDRIVE_CLIENT_ID"], env["GDRIVE_CLIENT_SECRET"],
                   token_value, env.get("GDRIVE_ROOT_FOLDER_ID", ""))


def _post_form(url: str, fields: dict[str, str], opener=urllib.request.urlopen) -> dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with opener(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def access_token(creds: DriveCredentials, opener=urllib.request.urlopen) -> str:
    """Exchange the refresh token. This is also the liveness check.

    A Google OAuth consent screen left in Testing status expires refresh tokens
    after seven days. Doing the exchange up front turns that from a silent
    mid-run failure into an immediate, named one.
    """
    try:
        payload = _post_form(TOKEN_URL, {
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
            "grant_type": "refresh_token",
        }, opener)
    except Exception as exc:
        raise CredentialError(
            f"Drive refresh failed: {exc}. If the OAuth consent screen is still in "
            "Testing status, refresh tokens expire after seven days -- publish it."
        ) from exc
    if "access_token" not in payload:
        raise CredentialError(f"Drive refresh returned no access token: {payload}")
    return payload["access_token"]


@dataclass
class DriveClient:
    """Minimal Drive v3 client. Injectable opener so tests need no network."""

    creds: DriveCredentials
    opener: Any = urllib.request.urlopen
    _token: str = field(default="", repr=False)

    def token(self) -> str:
        if not self._token:
            self._token = access_token(self.creds, self.opener)
        return self._token

    CHUNK = 8 * 1024 * 1024

    def _open(self, url: str):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token()}"})
        return self.opener(req, timeout=600)

    def _get(self, url: str) -> bytes:
        """Small responses only -- listings and metadata. Never file content."""
        with self._open(url) as resp:
            return resp.read()

    def list_folder(self, folder_id: str) -> list[dict[str, Any]]:
        """Every file in the folder, following pagination.

        Drive returns 100 files per page by default. Without following
        `nextPageToken` a lake folder with 150 datasets silently reports 100,
        and a frozen dataset on page two is indistinguishable from one that
        does not exist.
        """
        q = urllib.parse.quote(f"'{folder_id}' in parents and trashed=false")
        files: list[dict[str, Any]] = []
        page_token = ""
        while True:
            url = (f"{API}/files?q={q}&pageSize=1000"
                   "&fields=nextPageToken,files(id,name,size,md5Checksum,mimeType)")
            if page_token:
                url += f"&pageToken={urllib.parse.quote(page_token)}"
            payload = json.loads(self._get(url).decode())
            files.extend(payload.get("files", []))
            page_token = payload.get("nextPageToken", "")
            if not page_token:
                return files

    def download(self, file_id: str, dest: str, expected_sha256: str | None = None) -> str:
        """Stream to disk, hashing as the bytes arrive.

        The previous implementation did `blob = resp.read()` and hashed the
        result, so a dataset had to fit in RAM before it could be checked. The
        per-study acquisition ceiling is 25 GB and a GitHub runner has 8-16 GB:
        the ceiling this engine sets for itself was unreachable by the code that
        implements it. Hashing incrementally alongside the write costs nothing
        and removes the size limit entirely.
        """
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        tmp = dest + ".part"
        digest = hashlib.sha256()
        total = 0
        try:
            with self._open(f"{API}/files/{file_id}?alt=media") as resp, \
                    open(tmp, "wb") as out:
                while chunk := resp.read(self.CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
                    total += len(chunk)
            got = digest.hexdigest()
            if expected_sha256 and got != expected_sha256:
                raise DriveError(
                    f"{dest}: downloaded bytes hash to {got[:12]}, freeze expects "
                    f"{expected_sha256[:12]}. The authoritative dataset changed."
                )
        except BaseException:
            # A half-written dataset is worse than none: the next step would
            # hash it, fail, and report drift rather than a broken transfer.
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        os.replace(tmp, dest)
        return got

    def fetch_frozen(self, folder_id: str, dataset_hashes: dict[str, str],
                     dest_dir: str) -> dict[str, str]:
        """Retrieve exactly the datasets the freeze names, and nothing else.

        Drive permits duplicate filenames, so a name does not identify a file.
        Building `{f["name"]: f}` let whichever duplicate happened to be last in
        the listing win: with a stale `panel.csv` beside the frozen one, the
        engine could pick the stale copy and report "the authoritative dataset
        changed" while the correct bytes sat in the same folder.

        Content is the authority here, not the name and not the file id -- so
        every candidate sharing the name is tried, and the one whose bytes match
        the freeze is the one that is kept.
        """
        by_name: dict[str, list[dict[str, Any]]] = {}
        for f in self.list_folder(folder_id):
            by_name.setdefault(f["name"], []).append(f)

        missing = [n for n in dataset_hashes if n not in by_name]
        if missing:
            raise DriveError(
                f"frozen datasets not present in Drive folder {folder_id}: "
                f"{', '.join(sorted(missing))}"
            )

        got: dict[str, str] = {}
        for name, expected in sorted(dataset_hashes.items()):
            candidates = by_name[name]
            dest = os.path.join(dest_dir, name)
            last: Exception | None = None
            for cand in candidates:
                try:
                    got[name] = self.download(cand["id"], dest, expected)
                    break
                except DriveError as exc:
                    last = exc
            else:
                extra = (f" {len(candidates)} files share this name in the folder; "
                         "none of them match the frozen bytes."
                         if len(candidates) > 1 else "")
                raise DriveError(f"{last}{extra}")
        return got

    def find_child(self, parent_id: str, name: str, *, folder: bool | None = None) -> dict[str, Any]:
        """Resolve one exact child without recursively browsing Drive.

        Path traversal is deliberately segment-by-segment.  CoScientist must
        not crawl the multi-terabyte lake merely to locate one frozen object.
        Duplicate names are rejected because a path is not authoritative when
        it resolves to multiple Drive objects.
        """
        hits = [f for f in self.list_folder(parent_id) if f.get("name") == name]
        if folder is True:
            hits = [f for f in hits if f.get("mimeType") == FOLDER_MIME]
        elif folder is False:
            hits = [f for f in hits if f.get("mimeType") != FOLDER_MIME]
        if not hits:
            raise DriveError(f"Drive path component {name!r} not found under {parent_id}")
        if len(hits) > 1:
            raise DriveError(
                f"Drive path component {name!r} is ambiguous under {parent_id}: "
                f"{len(hits)} objects share the name"
            )
        return hits[0]

    def resolve_path(self, root_folder_id: str, remote_path: str) -> dict[str, Any]:
        """Resolve a canonical lake-relative path to one Drive object."""
        parts = [p for p in remote_path.replace("\\", "/").split("/") if p and p != "."]
        if not parts or any(p == ".." for p in parts):
            raise DriveError(f"invalid Drive relative path: {remote_path!r}")
        parent = root_folder_id
        obj: dict[str, Any] | None = None
        for i, part in enumerate(parts):
            obj = self.find_child(parent, part, folder=(i < len(parts) - 1))
            if i < len(parts) - 1:
                parent = obj["id"]
        assert obj is not None
        if obj.get("mimeType") == FOLDER_MIME:
            raise DriveError(f"Drive path resolves to a folder, not a file: {remote_path}")
        return obj

    def download_path(self, root_folder_id: str, remote_path: str, dest: str,
                      expected_sha256: str | None = None) -> str:
        obj = self.resolve_path(root_folder_id, remote_path)
        return self.download(obj["id"], dest, expected_sha256)

    def fetch_sqlite_manifests(self, manifest_folder_id: str, dest_dir: str) -> list[str]:
        """Download only small SQLite control manifests to local ephemeral disk."""
        os.makedirs(dest_dir, exist_ok=True)
        found = []
        for f in self.list_folder(manifest_folder_id):
            name = f.get("name", "")
            lower = name.lower()
            if not lower.endswith((".sqlite", ".sqlite3", ".db")):
                continue
            dest = os.path.join(dest_dir, os.path.basename(name))
            self.download(f["id"], dest)
            found.append(dest)
        if not found:
            raise DriveError(f"no SQLite manifests found in Drive folder {manifest_folder_id}")
        return sorted(found)

    def create_folder(self, name: str, parent_id: str) -> str:
        """Create a Drive folder and return its id."""
        meta = json.dumps({"name": name, "parents": [parent_id],
                           "mimeType": FOLDER_MIME}).encode()
        req = urllib.request.Request(
            f"{API}/files?fields=id,name", data=meta,
            headers={"Authorization": f"Bearer {self.token()}",
                     "Content-Type": "application/json; charset=UTF-8"})
        with self.opener(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        if not payload.get("id"):
            raise DriveError(f"Drive created no folder id for {name!r}")
        return payload["id"]

    def fetch_study_state(self, state_root_id: str, study_id: str,
                          dest_dir: str) -> dict[str, str]:
        """Bootstrap the immutable freeze and analysis lock from Drive.

        Scientific state is intentionally git-ignored.  A clean GitHub checkout
        therefore cannot assume `state/freeze.json` exists; it has to retrieve
        the pre-outcome records before it is allowed to fetch any outcome data.
        The state root contains one uniquely named child folder per study.
        """
        folders = [f for f in self.list_folder(state_root_id)
                   if f.get("name") == study_id and f.get("mimeType") == FOLDER_MIME]
        if len(folders) != 1:
            raise DriveError(
                f"expected exactly one state folder named {study_id!r} under "
                f"{state_root_id}; found {len(folders)}"
            )
        files = self.list_folder(folders[0]["id"])
        by_name: dict[str, list[dict[str, Any]]] = {}
        for f in files:
            by_name.setdefault(f.get("name", ""), []).append(f)
        required = ("freeze.json", "analysis_lock.json")
        for name in required:
            if len(by_name.get(name, [])) != 1:
                raise DriveError(
                    f"study {study_id}: expected exactly one {name}, found "
                    f"{len(by_name.get(name, []))}. State identity must be unambiguous."
                )
        os.makedirs(dest_dir, exist_ok=True)
        got: dict[str, str] = {}
        for name in required:
            dest = os.path.join(dest_dir, name)
            got[name] = self.download(by_name[name][0]["id"], dest)
        return got

    def upload(self, local_path: str, folder_id: str, name: str | None = None) -> str:
        """Deliver a result to Drive with a resumable session.

        Results belong in Drive, not in git. Resumable rather than multipart so
        a large figure or a packaged dataset streams from disk instead of being
        assembled in memory first.
        """
        name = name or os.path.basename(local_path)
        size = os.path.getsize(local_path)
        meta = json.dumps({"name": name, "parents": [folder_id]}).encode()

        init = urllib.request.Request(
            f"{UPLOAD_API}/files?uploadType=resumable", data=meta,
            headers={"Authorization": f"Bearer {self.token()}",
                     "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Length": str(size)})
        with self.opener(init, timeout=120) as resp:
            session = resp.headers.get("Location") if hasattr(resp, "headers") else None
            if not session:
                # Some fakes (and some proxies) answer the initiate call with the
                # finished resource instead of a session URL.
                body = resp.read()
                return json.loads(body.decode())["id"] if body else ""

        with open(local_path, "rb") as fh:
            put = urllib.request.Request(
                session, data=fh, method="PUT",
                headers={"Authorization": f"Bearer {self.token()}",
                         "Content-Length": str(size),
                         "Content-Type": "application/octet-stream"})
            with self.opener(put, timeout=1800) as resp:
                return json.loads(resp.read().decode()).get("id", "")
