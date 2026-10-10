"""Thin wrapper around the GitHub REST API: pagination and rate-limit handling only.
No normalization logic here -- see app.services.normalizers for that."""

from collections.abc import Iterator
from typing import Any

import httpx

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


class GitHubRateLimitError(RuntimeError):
    """Raised on a 403/429 that indicates a rate limit, not a permissions error.
    Fails fast rather than sleeping (which could be up to an hour) -- the caller
    (SyncService) records this as an actionable failed sync run instead."""

    def __init__(self, reset_at: int | None) -> None:
        self.reset_at = reset_at
        message = "GitHub API rate limit exceeded"
        if reset_at:
            message += f"; resets at unix timestamp {reset_at}"
        super().__init__(message)


class GitHubAPIError(RuntimeError):
    def __init__(self, status_code: int, url: str, body: str) -> None:
        self.status_code = status_code
        super().__init__(f"GitHub API error {status_code} for {url}: {body[:500]}")


class GitHubClient:
    def __init__(
        self, token: str, *, base_url: str = GITHUB_API_BASE, timeout: float = 30.0
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, url, **kwargs)
        if response.status_code in (403, 429):
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0" or "Retry-After" in response.headers:
                reset_at = response.headers.get("X-RateLimit-Reset")
                raise GitHubRateLimitError(int(reset_at) if reset_at else None)
        if response.is_error:
            raise GitHubAPIError(response.status_code, str(response.url), response.text)
        return response

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        next_url: str | None = url
        next_params: dict[str, Any] | None = params
        while next_url:
            response = self._request("GET", next_url, params=next_params)
            yield from response.json()
            next_url = response.links.get("next", {}).get("url")
            next_params = None  # a `next` Link URL already carries its own query params

    def get_authenticated_user(self) -> dict[str, Any]:
        return self._request("GET", "/user").json()

    def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{name}").json()

    def list_pull_requests(self, owner: str, name: str) -> Iterator[dict[str, Any]]:
        return self._paginate(
            f"/repos/{owner}/{name}/pulls", {"state": "all", "sort": "created", "direction": "asc"}
        )

    def get_pull_request(self, owner: str, name: str, number: int) -> dict[str, Any]:
        # additions/deletions/changed_files only appear on the single-PR detail endpoint,
        # not in the list response above.
        return self._request("GET", f"/repos/{owner}/{name}/pulls/{number}").json()

    def list_reviews(self, owner: str, name: str, pr_number: int) -> Iterator[dict[str, Any]]:
        return self._paginate(f"/repos/{owner}/{name}/pulls/{pr_number}/reviews")

    def list_requested_reviewers(self, owner: str, name: str, pr_number: int) -> dict[str, Any]:
        url = f"/repos/{owner}/{name}/pulls/{pr_number}/requested_reviewers"
        return self._request("GET", url).json()

    def list_issues(self, owner: str, name: str) -> Iterator[dict[str, Any]]:
        # GitHub's /issues endpoint also returns pull requests (they carry a `pull_request` key);
        # the normalizer filters those out since PRs are synced separately, via list_pull_requests.
        return self._paginate(
            f"/repos/{owner}/{name}/issues", {"state": "all", "sort": "created", "direction": "asc"}
        )

    def list_commits(self, owner: str, name: str) -> Iterator[dict[str, Any]]:
        return self._paginate(f"/repos/{owner}/{name}/commits")

    def list_milestones(self, owner: str, name: str) -> Iterator[dict[str, Any]]:
        return self._paginate(f"/repos/{owner}/{name}/milestones", {"state": "all"})
