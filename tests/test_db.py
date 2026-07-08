import json
import sqlite3

from mattermost_archiver import db


def make_conn(tmp_path):
    conn = db.connect(tmp_path / "archive.sqlite")
    db.init_db(conn)
    return conn


def test_upsert_channel_is_idempotent(tmp_path):
    conn = make_conn(tmp_path)
    channel = {
        "id": "channel-id",
        "team_id": "team-id",
        "name": "town-square",
        "display_name": "Town Square",
        "type": "O",
        "last_post_at": 1000,
    }

    db.upsert_channel(conn, channel, seen_at=2000)
    db.upsert_channel(conn, {**channel, "display_name": "Town Square 2", "last_post_at": 3000}, seen_at=4000)

    rows = conn.execute("SELECT * FROM channels").fetchall()
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Town Square 2"
    assert rows[0]["last_post_at"] == 3000
    assert rows[0]["first_indexed_at"] == 2000
    assert rows[0]["last_seen_at"] == 4000


def test_upsert_post_stores_raw_json_and_updates(tmp_path):
    conn = make_conn(tmp_path)
    db.upsert_channel(conn, {"id": "channel-id"})
    post = {
        "id": "post-id",
        "channel_id": "channel-id",
        "user_id": "user-id",
        "create_at": 123,
        "message": "hello",
        "props": {"b": 2, "a": 1},
        "metadata": {"files": []},
    }

    db.upsert_post(conn, post, ingested_at=1000)
    db.upsert_post(conn, {**post, "message": "edited", "update_at": 456}, ingested_at=2000)

    row = conn.execute("SELECT * FROM posts WHERE id = 'post-id'").fetchone()
    assert row["message"] == "edited"
    assert row["update_at"] == 456
    assert row["ingested_at"] == 2000
    assert json.loads(row["props_json"]) == {"a": 1, "b": 2}
    assert json.loads(row["raw_json"])["message"] == "edited"


def test_foreign_keys_are_enforced(tmp_path):
    conn = make_conn(tmp_path)
    try:
        db.upsert_post(conn, {"id": "post-id", "channel_id": "missing", "create_at": 1})
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected foreign key failure")


def test_watermark_lifecycle(tmp_path):
    conn = make_conn(tmp_path)
    db.upsert_channel(conn, {"id": "channel-id"})

    watermark = db.ensure_watermark(conn, "channel-id")
    assert watermark["backfill_complete"] == 0
    assert watermark["last_post_create_at"] == 0

    db.update_watermark(
        conn,
        "channel-id",
        backfill_complete=True,
        last_post_create_at=999,
        last_sync_at=1000,
        last_success_at=1001,
    )
    updated = db.get_watermark(conn, "channel-id")
    assert updated is not None
    assert updated["backfill_complete"] == 1
    assert updated["last_post_create_at"] == 999
    assert updated["last_sync_at"] == 1000
    assert updated["last_success_at"] == 1001
    assert updated["last_error"] is None


def test_get_stats(tmp_path):
    conn = make_conn(tmp_path)
    db.upsert_channel(conn, {"id": "channel-id"})
    db.ensure_watermark(conn, "channel-id")
    db.upsert_user(conn, {"id": "user-id", "username": "user"})
    db.upsert_post(conn, {"id": "post-id", "channel_id": "channel-id", "create_at": 123})

    assert db.get_stats(conn) == {
        "channels": 1,
        "posts": 1,
        "users": 1,
        "watermarks": 1,
        "latest_post_create_at": 123,
    }
