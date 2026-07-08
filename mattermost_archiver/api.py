"""Small Mattermost REST API client used by the archiver."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MattermostAPIError(RuntimeError):
    """Raised when a Mattermost API request fails."""


@dataclass(frozen=True)
class MattermostClient:
    """Minimal Mattermost API client.

    The client intentionally wraps only the endpoints needed for archive sync.
    """

    base_url: str
    token: str
    timeout: int = 30

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url must not be empty")
        if not self.token.strip():
            raise ValueError("token must not be empty")

    @property
    def api_base_url(self) -> str:
        """Return normalized Mattermost API base URL."""
        return f"{self.base_url.rstrip('/')}/api/v4"

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.api_base_url}{path}{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "mattermost-sqlite-archiver",
            },
            method="GET",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise MattermostAPIError(f"Mattermost API HTTP {exc.code}: {error_body}") from exc
        except URLError as exc:
            raise MattermostAPIError(f"Mattermost API request failed: {exc.reason}") from exc

        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise MattermostAPIError(f"Mattermost API returned invalid JSON: {exc}") from exc

    def get_me(self) -> dict[str, Any]:
        """Return the authenticated user."""
        return self._request("/users/me")

    def get_my_teams(self) -> list[dict[str, Any]]:
        """Return teams visible to the authenticated user."""
        return self._request("/users/me/teams")

    def get_my_channels(self, team_id: str) -> list[dict[str, Any]]:
        """Return channels in one team visible to the authenticated user."""
        return self._request(f"/users/me/teams/{team_id}/channels")

    def get_channel_posts(
        self,
        channel_id: str,
        *,
        page: int = 0,
        per_page: int = 200,
    ) -> dict[str, Any]:
        """Return one paginated page of posts for a channel."""
        return self._request(
            f"/channels/{channel_id}/posts",
            {"page": page, "per_page": per_page},
        )

    def get_channel_posts_since(self, channel_id: str, since_ms: int) -> dict[str, Any]:
        """Return posts created or changed since a millisecond timestamp."""
        return self._request(
            f"/channels/{channel_id}/posts",
            {"since": since_ms},
        )
