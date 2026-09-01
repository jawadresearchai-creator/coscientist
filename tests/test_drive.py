"""Drive acquisition: the boundary where the freeze meets the network."""
import hashlib
import io
import json

import pytest

from coscientist.drive import (CredentialError, DriveClient, DriveCredentials,
                               DriveError, access_token)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(files, *, token_ok=True, page_size=None):
    """A Drive that serves exactly `files`.

    `files` is either {name: bytes} or a list of (file_id, name, bytes), the
    latter so duplicate names -- which Drive genuinely permits -- can be
    represented at all.
    """
    if isinstance(files, dict):
        entries = [(f"id-{n}", n, b) for n, b in files.items()]
    else:
        entries = list(files)
    blobs = {fid: b for fid, _, b in entries}
    rows = [{"id": fid, "name": n, "size": str(len(b))} for fid, n, b in entries]

    def _open(req, timeout=None):
        url = req.full_url
        if "oauth2" in url:
            return FakeResponse(json.dumps(
                {"access_token": "tok"} if token_ok else {"error": "invalid_grant"}).encode())
        if "/files?q=" in url:
            if page_size is None:
                return FakeResponse(json.dumps({"files": rows}).encode())
            token = ""
            if "pageToken=" in url:
                token = url.split("pageToken=")[1].split("&")[0]
            start = int(token or 0)
            page = rows[start:start + page_size]
            payload = {"files": page}
            if start + page_size < len(rows):
                payload["nextPageToken"] = str(start + page_size)
            return FakeResponse(json.dumps(payload).encode())
        if "alt=media" in url:
            fid = url.split("/files/")[1].split("?")[0]
            return FakeResponse(blobs[fid])
        raise AssertionError(f"unexpected request {url}")
    return _open


def creds():
    return DriveCredentials("cid", "csecret", "rtoken", "root")


def test_missing_secrets_are_named_not_guessed():
    with pytest.raises(CredentialError, match="GDRIVE_CLIENT_SECRET"):
        DriveCredentials.from_env({"GDRIVE_CLIENT_ID": "x", "GDRIVE_TOKEN": "y"})


def test_a_dead_refresh_token_fails_at_the_start_of_the_run():
    """Google expires refresh tokens after seven days while the consent screen
    sits in Testing. Doing the exchange up front turns a silent mid-build
    failure into an immediate, named one."""
    with pytest.raises(CredentialError, match="no access token"):
        access_token(creds(), opener_for({}, token_ok=False))


def test_fetch_retrieves_exactly_what_the_freeze_names(tmp_path):
    """A folder listing is not a shopping list.

    The freeze decides what this analysis may see. An extra file sitting in the
    same Drive folder never reaches the runner, so it cannot be read by an
    analysis script that goes looking.
    """
    panel = b"firm,quarter,y\n1,1,2.0\n"
    extra = b"tempting,but,unfrozen\n"
    files = {"panel.csv": panel, "helpful_extra.csv": extra}
    client = DriveClient(creds(), opener_for(files))
    got = client.fetch_frozen(
        "folder", {"panel.csv": hashlib.sha256(panel).hexdigest()}, str(tmp_path))
    assert set(got) == {"panel.csv"}
    assert (tmp_path / "panel.csv").exists()
    assert not (tmp_path / "helpful_extra.csv").exists()


def test_a_dataset_the_freeze_names_but_drive_lacks_stops_the_run(tmp_path):
    client = DriveClient(creds(), opener_for({"other.csv": b"x"}))
    with pytest.raises(DriveError, match="not present in Drive"):
        client.fetch_frozen("folder", {"panel.csv": "a" * 64}, str(tmp_path))


def test_bytes_that_do_not_match_the_freeze_are_refused(tmp_path):
    """Hashing at the boundary, so freeze-verify compares what actually landed
    rather than trusting the transfer."""
    client = DriveClient(creds(), opener_for({"panel.csv": b"changed since freezing"}))
    with pytest.raises(DriveError, match="authoritative dataset changed"):
        client.fetch_frozen("folder", {"panel.csv": "a" * 64}, str(tmp_path))


def test_a_refused_download_leaves_no_partial_file(tmp_path):
    """A half-written dataset on disk is worse than none: the next step would
    hash it, fail, and report drift rather than a broken transfer."""
    client = DriveClient(creds(), opener_for({"panel.csv": b"wrong"}))
    with pytest.raises(DriveError):
        client.fetch_frozen("folder", {"panel.csv": "a" * 64}, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_duplicate_names_are_resolved_by_content(tmp_path):
    """Drive permits duplicate filenames, so a name does not identify a file.

    Building `{f["name"]: f}` let whichever duplicate came last in the listing
    win. With a stale `panel.csv` beside the frozen one, the engine could pick
    the stale copy and report "the authoritative dataset changed" while the
    correct bytes sat in the same folder. Content is the authority, so every
    candidate sharing the name is tried and the matching one is kept.
    """
    good, stale = b"firm,quarter,y\n1,1,2.0\n", b"firm,quarter,y\n1,1,9.9\n"
    client = DriveClient(creds(), opener_for([
        ("id-stale", "panel.csv", stale),      # listed first, and wrong
        ("id-good", "panel.csv", good),
    ]))
    got = client.fetch_frozen(
        "folder", {"panel.csv": hashlib.sha256(good).hexdigest()}, str(tmp_path))
    assert got["panel.csv"] == hashlib.sha256(good).hexdigest()
    assert (tmp_path / "panel.csv").read_bytes() == good


def test_duplicates_that_all_mismatch_say_so(tmp_path):
    client = DriveClient(creds(), opener_for([
        ("id-a", "panel.csv", b"one"), ("id-b", "panel.csv", b"two"),
    ]))
    with pytest.raises(DriveError, match="2 files share this name"):
        client.fetch_frozen("folder", {"panel.csv": "a" * 64}, str(tmp_path))


def test_listing_follows_pagination():
    """A lake folder with more than one page of files.

    Without following `nextPageToken` a frozen dataset on page two is
    indistinguishable from one that does not exist.
    """
    entries = [(f"id-{i}", f"ds_{i:03d}.csv", b"x") for i in range(250)]
    client = DriveClient(creds(), opener_for(entries, page_size=100))
    listed = client.list_folder("folder")
    assert len(listed) == 250
    assert listed[-1]["name"] == "ds_249.csv"


def test_a_large_file_is_never_held_in_memory(tmp_path, monkeypatch):
    """The ceiling this engine sets for itself was unreachable by its own code.

    `blob = resp.read()` meant a dataset had to fit in RAM before it could be
    hashed. The per-study acquisition ceiling is 25 GB; a runner has 8-16 GB.
    """
    blob = b"a" * (5 * 1024 * 1024)
    client = DriveClient(creds(), opener_for({"big.csv": blob}))
    monkeypatch.setattr(DriveClient, "CHUNK", 64 * 1024)

    reads: list[int] = []
    original = FakeResponse.read

    def counting_read(self, size=-1):
        chunk = original(self, size)
        if size and size > 0:
            reads.append(len(chunk))
        return chunk

    monkeypatch.setattr(FakeResponse, "read", counting_read)
    digest = client.download("id-big.csv", str(tmp_path / "big.csv"),
                             hashlib.sha256(blob).hexdigest())
    assert digest == hashlib.sha256(blob).hexdigest()
    assert max(reads) <= 64 * 1024, "content was read in one call, not streamed"
    assert len(reads) > 50
