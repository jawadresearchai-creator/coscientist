from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coscientist.drive import DriveClient, DriveCredentials, FOLDER_MIME

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRYAD_BASE = "https://datadryad.org"
DATASET_DOI = "10.5061/dryad.wpzgmsbk0"
DRYAD_FILE_ID = 607133
EXPECTED_NAME = "Jacobsen_et_al._DEGs.xlsx"
CREDENTIAL_FILE_NAME = "datadryad.txt"
RAW_ROOT_NAME = "01_RAW_IMMUTABLE"
DRYAD_DOMAIN_NAME = "23_PUBLIC_OMICS_DRYAD"
DATASET_FOLDER_NAME = "JACOBSEN_MECHANICAL_IMPEDANCE_DRYAD_wpzgmsbk0"
PROVENANCE_NAME = "Jacobsen_et_al._DEGs.provenance.json"


class JacobsenAcquisitionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _drive_exact_search(client: DriveClient, name: str, *, folder: bool | None = None) -> dict[str, Any]:
    safe_name = name.replace("'", "\\'")
    terms = [f"name = '{safe_name}'", "trashed = false"]
    if folder is True:
        terms.append(f"mimeType = '{FOLDER_MIME}'")
    elif folder is False:
        terms.append(f"mimeType != '{FOLDER_MIME}'")
    q = urllib.parse.quote(" and ".join(terms))
    url = f"{DRIVE_API}/files?q={q}&pageSize=100&fields=files(id,name,size,mimeType,parents)"
    payload = json.loads(client._get(url).decode("utf-8"))
    hits = payload.get("files", [])
    if len(hits) != 1:
        raise JacobsenAcquisitionError(
            f"expected exactly one private Drive object named {name!r}; found {len(hits)}"
        )
    return hits[0]


def _get_or_create_folder(client: DriveClient, parent_id: str, name: str) -> str:
    hits = [
        x for x in client.list_folder(parent_id)
        if x.get("name") == name and x.get("mimeType") == FOLDER_MIME
    ]
    if len(hits) > 1:
        raise JacobsenAcquisitionError(f"ambiguous Drive folder {name!r}: {len(hits)} copies")
    if hits:
        return hits[0]["id"]
    return client.create_folder(name, parent_id)


def _parse_dryad_credentials(text: str) -> dict[str, str]:
    raw = text.strip()
    if not raw:
        raise JacobsenAcquisitionError("Dryad credential file is empty")

    # JSON form, if used.
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JacobsenAcquisitionError("Dryad credential JSON is invalid") from exc
        def first(*keys: str) -> str:
            for key in keys:
                value = obj.get(key)
                if value:
                    return str(value).strip()
            return ""
        return {
            "client_id": first("client_id", "application_id", "app_id"),
            "client_secret": first("client_secret", "application_secret", "secret"),
            "access_token": first("access_token", "token", "bearer_token"),
        }

    values: dict[str, str] = {"client_id": "", "client_secret": "", "access_token": ""}
    unlabeled: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([^:=]+)\s*[:=]\s*(.+)$", line)
        if not match:
            unlabeled.append(line)
            continue
        key = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip().lower()).strip("_")
        value = match.group(2).strip().strip('"').strip("'")
        if key in {"client_id", "application_id", "app_id", "applicationid"}:
            values["client_id"] = value
        elif key in {"client_secret", "application_secret", "app_secret", "secret"}:
            values["client_secret"] = value
        elif key in {"access_token", "bearer_token", "token"}:
            values["access_token"] = value

    if not values["client_id"] and not values["client_secret"] and not values["access_token"]:
        if len(unlabeled) >= 2:
            values["client_id"], values["client_secret"] = unlabeled[0], unlabeled[1]
        elif len(unlabeled) == 1:
            values["access_token"] = unlabeled[0]
    return values


def _oauth_token(creds: dict[str, str]) -> str:
    if creds.get("client_id") and creds.get("client_secret"):
        body = urllib.parse.urlencode({
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "grant_type": "client_credentials",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{DRYAD_BASE}/oauth/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise JacobsenAcquisitionError("Dryad OAuth token exchange failed") from exc
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise JacobsenAcquisitionError("Dryad OAuth response contained no access token")
        return token
    token = str(creds.get("access_token") or "").strip()
    if token:
        return token
    raise JacobsenAcquisitionError(
        "Dryad credential file contains neither client_id/client_secret nor access_token"
    )


def _dryad_json(path: str, token: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json", "X-API-Version": "2.1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{DRYAD_BASE}/api/v2{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise JacobsenAcquisitionError(
            f"Dryad metadata request failed with HTTP {exc.code}"
        ) from None
    except Exception as exc:
        raise JacobsenAcquisitionError("Dryad metadata request failed") from exc


def _download_with_curl(token: str, dest: Path) -> None:
    # curl -L intentionally drops Authorization on a cross-host redirect to Dryad's
    # presigned object-storage URL. Do not use --location-trusted here.
    cmd = [
        "curl", "-fsSL", "--retry", "3", "--retry-all-errors",
        "-H", f"Authorization: Bearer {token}",
        "-H", "X-API-Version: 2.1.0",
        "-o", str(dest),
        f"{DRYAD_BASE}/api/v2/files/{DRYAD_FILE_ID}/download",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        raise JacobsenAcquisitionError(
            f"Dryad file download failed with curl exit code {proc.returncode}"
        )


def _validate_xlsx(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise JacobsenAcquisitionError("downloaded object is not a valid XLSX/ZIP container")
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            raise JacobsenAcquisitionError(f"XLSX integrity check failed at member {bad!r}")
        names = set(zf.namelist())
        required = {"[Content_Types].xml", "xl/workbook.xml"}
        if not required.issubset(names):
            raise JacobsenAcquisitionError("downloaded ZIP is not a valid Excel workbook")


def _metadata_digest(meta: dict[str, Any]) -> tuple[str, str]:
    digest_type = str(meta.get("digestType") or "").lower().replace("_", "-")
    digest = str(meta.get("digest") or "").lower().strip()
    return digest_type, digest


def main() -> int:
    client = DriveClient(DriveCredentials.from_env())

    # Credential never leaves ephemeral runner storage and is never printed.
    cred_obj = _drive_exact_search(client, CREDENTIAL_FILE_NAME, folder=False)
    with tempfile.TemporaryDirectory(prefix="jacobsen-dryad-") as td:
        root = Path(td)
        cred_path = root / CREDENTIAL_FILE_NAME
        client.download(cred_obj["id"], str(cred_path))
        creds = _parse_dryad_credentials(cred_path.read_text(encoding="utf-8-sig"))
        token = _oauth_token(creds)
        cred_path.unlink(missing_ok=True)

        # Authenticate before downloading bytes.
        _dryad_json("/test", token)
        meta = _dryad_json(f"/files/{DRYAD_FILE_ID}", token)
        remote_name = str(meta.get("path") or meta.get("name") or "")
        if Path(remote_name).name != EXPECTED_NAME:
            raise JacobsenAcquisitionError(
                f"Dryad file id {DRYAD_FILE_ID} resolved to unexpected filename {Path(remote_name).name!r}"
            )

        artifact = root / EXPECTED_NAME
        _download_with_curl(token, artifact)
        _validate_xlsx(artifact)

        got_size = artifact.stat().st_size
        remote_size = int(meta.get("size") or 0)
        if remote_size and got_size != remote_size:
            raise JacobsenAcquisitionError(
                f"Dryad size mismatch: metadata={remote_size} bytes, downloaded={got_size} bytes"
            )

        got_sha256 = sha256_file(artifact)
        digest_type, remote_digest = _metadata_digest(meta)
        if remote_digest and digest_type in {"sha-256", "sha256"} and got_sha256 != remote_digest:
            raise JacobsenAcquisitionError("Dryad SHA-256 digest mismatch")

        raw_root = _drive_exact_search(client, RAW_ROOT_NAME, folder=True)
        domain_id = _get_or_create_folder(client, raw_root["id"], DRYAD_DOMAIN_NAME)
        dataset_id = _get_or_create_folder(client, domain_id, DATASET_FOLDER_NAME)

        existing = [x for x in client.list_folder(dataset_id) if x.get("name") == EXPECTED_NAME]
        if len(existing) > 1:
            raise JacobsenAcquisitionError(
                f"immutable destination contains {len(existing)} copies of {EXPECTED_NAME}"
            )
        if existing:
            existing_path = root / (EXPECTED_NAME + ".existing")
            existing_sha = client.download(existing[0]["id"], str(existing_path))
            if existing_sha != got_sha256:
                raise JacobsenAcquisitionError(
                    "immutable Drive copy exists but differs from current authoritative Dryad bytes"
                )
            drive_file_id = existing[0]["id"]
            publication_state = "REUSED_IDENTICAL_EXISTING"
        else:
            drive_file_id = client.upload(str(artifact), dataset_id, EXPECTED_NAME)
            publication_state = "UPLOADED_NEW_IMMUTABLE"

        receipt = {
            "schema_version": "cosci.acquisition_receipt/1.0",
            "dataset": "Jacobsen et al. mechanical-impedance RNA-seq",
            "dataset_doi": DATASET_DOI,
            "dryad_file_id": DRYAD_FILE_ID,
            "filename": EXPECTED_NAME,
            "bytes": got_size,
            "sha256": got_sha256,
            "dryad_digest_type": digest_type or None,
            "dryad_digest": remote_digest or None,
            "dryad_metadata_size": remote_size or None,
            "source_metadata": f"{DRYAD_BASE}/api/v2/files/{DRYAD_FILE_ID}",
            "source_download": f"{DRYAD_BASE}/api/v2/files/{DRYAD_FILE_ID}/download",
            "credential_source": "private Google Drive datadryad.txt; contents ephemeral and not logged",
            "drive_publication_state": publication_state,
            "drive_file_id": drive_file_id,
            "drive_folder_id": dataset_id,
            "xlsx_container_validation": "PASS",
            "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        receipt_path = root / PROVENANCE_NAME
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        existing_receipt = [x for x in client.list_folder(dataset_id) if x.get("name") == PROVENANCE_NAME]
        if not existing_receipt:
            client.upload(str(receipt_path), dataset_id, PROVENANCE_NAME)

        # Public-safe stdout: no credentials, source-private identifiers, or Drive IDs.
        print(f"JACOBSEN_DRYAD_ACQUISITION=PASS bytes={got_size} sha256={got_sha256}")
        print(f"publication_state={publication_state}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
