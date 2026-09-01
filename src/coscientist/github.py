"""Read-only GitHub status client for the GMS data-lake control plane.

CoScientist never transports datasets through GitHub.  This module exists only
so the scientific engine can pin the exact GMS lake repository state that
produced the manifests/catalog it is using and can see whether ingestion is
healthy.  There are deliberately no mutation methods in this client.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"


class GitHubReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubRepoStatus:
    repo: str
    reachable: bool
    head_sha: str = ""
    default_branch: str = ""
    latest_runs: tuple[dict[str, Any], ...] = ()


@dataclass
class GitHubReadClient:
    repo: str
    token: str = ""
    opener: Any = urllib.request.urlopen

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, opener=urllib.request.urlopen):
        env = os.environ if env is None else env
        repo = env.get("GMSDL_GITHUB_REPO", "").strip()
        if not repo or "/" not in repo:
            raise GitHubReadError("GMSDL_GITHUB_REPO must be set to owner/repository")
        return cls(repo=repo, token=env.get("GMSDL_GITHUB_TOKEN", ""), opener=opener)

    def _get_json(self, path: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "coscientist-gms-readonly",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{API}{path}", headers=headers)
        try:
            with self.opener(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise GitHubReadError(f"GitHub read failed for {path}: {exc}") from exc

    def status(self, run_limit: int = 10) -> GitHubRepoStatus:
        repo = self._get_json(f"/repos/{self.repo}")
        branch = repo.get("default_branch") or "main"
        commit = self._get_json(f"/repos/{self.repo}/commits/{branch}")
        runs = self._get_json(
            f"/repos/{self.repo}/actions/runs?per_page={max(1, min(run_limit, 100))}"
        )
        compact = []
        for r in runs.get("workflow_runs", []):
            compact.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "head_sha": r.get("head_sha"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
            })
        return GitHubRepoStatus(
            repo=self.repo,
            reachable=True,
            head_sha=commit.get("sha", ""),
            default_branch=branch,
            latest_runs=tuple(compact),
        )
