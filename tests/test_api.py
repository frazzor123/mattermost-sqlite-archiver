import json
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from mattermost_archiver import api


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def make_http_error():
    return HTTPError(
        "https://mattermost.example.com",
        500,
        "error",
        Message(),
        BytesIO(b'{"message":"server error"}'),
    )


def test_client_rejects_empty_config():
    with pytest.raises(ValueError):
        api.MattermostClient("", "token")
    with pytest.raises(ValueError):
        api.MattermostClient("https://mattermost.example.com", "")


def test_get_me_sets_auth_header_and_parses_json(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["accept"] = request.get_header("Accept")
        seen["timeout"] = timeout
        return FakeResponse(json.dumps({"id": "user-id"}).encode())

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    client = api.MattermostClient("https://mattermost.example.com/", "token", timeout=12)

    assert client.get_me() == {"id": "user-id"}
    assert seen == {
        "url": "https://mattermost.example.com/api/v4/users/me",
        "auth": "Bearer token",
        "accept": "application/json",
        "timeout": 12,
    }


def test_get_my_teams(monkeypatch):
    monkeypatch.setattr(api, "urlopen", lambda request, timeout: FakeResponse(b'[{"id":"team-id"}]'))
    client = api.MattermostClient("https://mattermost.example.com", "token")

    assert client.get_my_teams() == [{"id": "team-id"}]


def test_get_my_channels_uses_team_id(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse(b'[{"id":"channel-id"}]')

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    client = api.MattermostClient("https://mattermost.example.com", "token")

    assert client.get_my_channels("team-id") == [{"id": "channel-id"}]
    assert seen["url"] == "https://mattermost.example.com/api/v4/users/me/teams/team-id/channels"


def test_get_channel_posts_adds_pagination(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse(b'{"order":["post-id"],"posts":{}}')

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    client = api.MattermostClient("https://mattermost.example.com", "token")

    assert client.get_channel_posts("channel-id", page=2, per_page=50)["order"] == ["post-id"]
    assert seen["url"] == "https://mattermost.example.com/api/v4/channels/channel-id/posts?page=2&per_page=50"


def test_get_user_uses_user_id(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse(b'{"id":"user-id","username":"user"}')

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    client = api.MattermostClient("https://mattermost.example.com", "token")

    assert client.get_user("user-id") == {"id": "user-id", "username": "user"}
    assert seen["url"] == "https://mattermost.example.com/api/v4/users/user-id"


def test_get_channel_posts_since_adds_since(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse(b'{"order":[],"posts":{}}')

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    client = api.MattermostClient("https://mattermost.example.com", "token")

    client.get_channel_posts_since("channel-id", 123456)
    assert seen["url"] == "https://mattermost.example.com/api/v4/channels/channel-id/posts?since=123456"


def test_http_error_becomes_api_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise make_http_error()

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    client = api.MattermostClient("https://mattermost.example.com", "token")

    with pytest.raises(api.MattermostAPIError, match="HTTP 500"):
        client.get_me()


def test_url_error_becomes_api_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise URLError("network down")

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    client = api.MattermostClient("https://mattermost.example.com", "token")

    with pytest.raises(api.MattermostAPIError, match="network down"):
        client.get_me()


def test_invalid_json_becomes_api_error(monkeypatch):
    monkeypatch.setattr(api, "urlopen", lambda request, timeout: FakeResponse(b'not json'))
    client = api.MattermostClient("https://mattermost.example.com", "token")

    with pytest.raises(api.MattermostAPIError, match="invalid JSON"):
        client.get_me()
