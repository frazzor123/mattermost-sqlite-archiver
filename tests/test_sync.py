from mattermost_archiver import db, sync


class FakeClient:
    def __init__(self):
        self.teams = [{"id": "team-id"}]
        self.channels = {
            "team-id": [
                {"id": "channel-id", "name": "town-square", "display_name": "Town Square", "last_post_at": 300},
            ]
        }
        self.pages = {
            ("channel-id", 0): [
                {"id": "post-3", "channel_id": "channel-id", "user_id": "user-2", "create_at": 300, "message": "three"},
                {"id": "post-2", "channel_id": "channel-id", "user_id": "user-1", "create_at": 200, "message": "two"},
            ],
            ("channel-id", 1): [
                {"id": "post-1", "channel_id": "channel-id", "user_id": "user-1", "create_at": 100, "message": "one"},
            ],
        }
        self.since = {}
        self.users = {
            "user-1": {"id": "user-1", "username": "userone", "first_name": "User", "last_name": "One"},
            "user-2": {"id": "user-2", "username": "usertwo", "first_name": "User", "last_name": "Two"},
        }
        self.teams_calls = 0
        self.channels_calls = 0
        self.posts_calls = []
        self.since_calls = []
        self.user_calls = []

    def get_my_teams(self):
        self.teams_calls += 1
        return self.teams

    def get_my_channels(self, team_id):
        self.channels_calls += 1
        return self.channels[team_id]

    def get_channel_posts(self, channel_id, *, page=0, per_page=200):
        self.posts_calls.append((channel_id, page, per_page))
        posts = self.pages.get((channel_id, page), [])
        return {
            "order": [post["id"] for post in posts],
            "posts": {post["id"]: post for post in posts},
        }

    def get_channel_posts_since(self, channel_id, since_ms):
        self.since_calls.append((channel_id, since_ms))
        posts = self.since.get((channel_id, since_ms), [])
        return {
            "order": [post["id"] for post in posts],
            "posts": {post["id"]: post for post in posts},
        }

    def get_user(self, user_id):
        self.user_calls.append(user_id)
        return self.users[user_id]


def make_conn(tmp_path):
    conn = db.connect(tmp_path / "archive.sqlite")
    db.init_db(conn)
    return conn


def test_posts_from_response_preserves_order():
    response = {
        "order": ["b", "a", "missing"],
        "posts": {
            "a": {"id": "a"},
            "b": {"id": "b"},
        },
    }

    assert sync.posts_from_response(response) == [{"id": "b"}, {"id": "a"}]


def test_sync_all_backfills_new_channel(tmp_path):
    conn = make_conn(tmp_path)
    client = FakeClient()

    result = sync.sync_all(client, conn, per_page=2, run_at=1000)

    assert result.channels_seen == 1
    assert result.channels_backfilled == 1
    assert result.posts_saved == 3
    assert client.posts_calls == [("channel-id", 0, 2), ("channel-id", 1, 2)]

    stats = db.get_stats(conn)
    assert stats["channels"] == 1
    assert stats["posts"] == 3
    assert stats["users"] == 2
    assert sorted(client.user_calls) == ["user-1", "user-2"]

    enriched = conn.execute("SELECT username, channel_display_name FROM posts_enriched WHERE id = 'post-1'").fetchone()
    assert enriched["username"] == "userone"
    assert enriched["channel_display_name"] == "Town Square"

    watermark = db.get_watermark(conn, "channel-id")
    assert watermark is not None
    assert watermark["backfill_complete"] == 1
    assert watermark["last_post_create_at"] == 300
    assert watermark["last_success_at"] == 1000


def test_sync_all_skips_when_last_post_at_is_not_newer(tmp_path):
    conn = make_conn(tmp_path)
    client = FakeClient()
    sync.sync_all(client, conn, per_page=2, run_at=1000)
    client.posts_calls.clear()

    result = sync.sync_all(client, conn, per_page=2, run_at=2000)

    assert result.channels_skipped == 1
    assert result.posts_saved == 0
    assert client.posts_calls == []
    assert client.since_calls == []


def test_sync_all_incremental_when_last_post_at_is_newer(tmp_path):
    conn = make_conn(tmp_path)
    client = FakeClient()
    sync.sync_all(client, conn, per_page=2, run_at=1000)

    client.channels["team-id"][0]["last_post_at"] = 400
    client.since[("channel-id", 300)] = [
        {"id": "post-4", "channel_id": "channel-id", "create_at": 400, "message": "four"},
    ]

    result = sync.sync_all(client, conn, per_page=2, run_at=2000)

    assert result.channels_incremental == 1
    assert result.posts_saved == 1
    assert client.since_calls == [("channel-id", 300)]
    assert db.get_stats(conn)["posts"] == 4
    watermark = db.get_watermark(conn, "channel-id")
    assert watermark is not None
    assert watermark["last_post_create_at"] == 400


def test_sync_all_populates_missing_users_for_existing_posts(tmp_path):
    conn = make_conn(tmp_path)
    client = FakeClient()
    db.upsert_channel(conn, {"id": "channel-id", "last_post_at": 300})
    db.upsert_post(conn, {"id": "post-1", "channel_id": "channel-id", "user_id": "user-1", "create_at": 100})
    db.update_watermark(conn, "channel-id", backfill_complete=True, last_post_create_at=300)

    result = sync.sync_all(client, conn, per_page=2, run_at=1000)

    assert result.channels_skipped == 1
    assert db.get_stats(conn)["users"] == 1
    assert client.user_calls == ["user-1"]


def test_sync_all_records_channel_errors(tmp_path):
    conn = make_conn(tmp_path)
    client = FakeClient()

    def broken_posts(channel_id, *, page=0, per_page=200):
        raise RuntimeError("boom")

    client.get_channel_posts = broken_posts

    result = sync.sync_all(client, conn, per_page=2, run_at=1000)

    assert result.errors == 1
    watermark = db.get_watermark(conn, "channel-id")
    assert watermark is not None
    assert watermark["last_error"] == "boom"
