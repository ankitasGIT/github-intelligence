import httpx
import pytest
import respx

from app.services.github_client import GitHubAPIError, GitHubClient, GitHubRateLimitError

BASE = "https://api.github.com"


@respx.mock
def test_paginates_across_link_header() -> None:
    page1_url = f"{BASE}/repos/acme/backend/pulls"
    page2_url = f"{BASE}/repos/acme/backend/pulls?page=2"

    route1 = respx.get(page1_url).mock(
        return_value=httpx.Response(
            200, json=[{"number": 1}], headers={"Link": f'<{page2_url}>; rel="next"'}
        )
    )
    route2 = respx.get(page2_url).mock(return_value=httpx.Response(200, json=[{"number": 2}]))

    with GitHubClient(token="fake") as client:
        items = list(client.list_pull_requests("acme", "backend"))

    assert [item["number"] for item in items] == [1, 2]
    assert route1.called
    assert route2.called


@respx.mock
def test_stops_when_no_next_link() -> None:
    respx.get(f"{BASE}/repos/acme/backend/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 1}, {"number": 2}])
    )
    with GitHubClient(token="fake") as client:
        items = list(client.list_issues("acme", "backend"))
    assert len(items) == 2


@respx.mock
def test_raises_rate_limit_error_on_403_with_zero_remaining() -> None:
    respx.get(f"{BASE}/repos/acme/backend").mock(
        return_value=httpx.Response(
            403,
            json={"message": "rate limit exceeded"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1234567890"},
        )
    )
    with GitHubClient(token="fake") as client:
        with pytest.raises(GitHubRateLimitError) as exc_info:
            client.get_repository("acme", "backend")
    assert exc_info.value.reset_at == 1234567890


@respx.mock
def test_403_without_rate_limit_headers_is_a_plain_api_error() -> None:
    # A permissions 403 (e.g. token lacks access) must not be misclassified as a rate limit.
    respx.get(f"{BASE}/repos/acme/private").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    with GitHubClient(token="fake") as client:
        with pytest.raises(GitHubAPIError) as exc_info:
            client.get_repository("acme", "private")
    assert exc_info.value.status_code == 403


@respx.mock
def test_raises_api_error_on_404() -> None:
    respx.get(f"{BASE}/repos/acme/missing").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    with GitHubClient(token="fake") as client:
        with pytest.raises(GitHubAPIError) as exc_info:
            client.get_repository("acme", "missing")
    assert exc_info.value.status_code == 404
