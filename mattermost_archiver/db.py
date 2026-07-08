"""SQLite helpers for the Mattermost archive."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now_ms() -> int:
    """Return current Unix time in milliseconds."""
    return int(time.time() * 1000)


def json_dumps(value: Any) -> str | None:
    """Serialize optional JSON values deterministically."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and row access enabled."""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the archive schema if it does not exist."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def upsert_channel(conn: sqlite3.Connection, channel: dict[str, Any], seen_at: int | None = None) -> None:
    """Insert or update one Mattermost channel record."""
    current_ms = seen_at if seen_at is not None else now_ms()
    conn.execute(
        """
        INSERT INTO channels (
          id, team_id, name, display_name, type, last_post_at,
          last_seen_at, first_indexed_at, is_member, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(id) DO UPDATE SET
          team_id = excluded.team_id,
          name = excluded.name,
          display_name = excluded.display_name,
          type = excluded.type,
          last_post_at = excluded.last_post_at,
          last_seen_at = excluded.last_seen_at,
          is_member = 1,
          updated_at = excluded.updated_at
        """,
        (
            channel["id"],
            channel.get("team_id"),
            channel.get("name"),
            channel.get("display_name"),
            channel.get("type"),
            channel.get("last_post_at"),
            current_ms,
            current_ms,
            current_ms,
        ),
    )


def mark_channels_not_seen(conn: sqlite3.Connection, seen_channel_ids: set[str], updated_at: int | None = None) -> None:
    """Mark channels missing from the latest discovery run as not currently visible."""
    current_ms = updated_at if updated_at is not None else now_ms()
    if not seen_channel_ids:
        conn.execute("UPDATE channels SET is_member = 0, updated_at = ?", (current_ms,))
        return

    placeholders = ",".join("?" for _ in seen_channel_ids)
    conn.execute(
        f"UPDATE channels SET is_member = 0, updated_at = ? WHERE id NOT IN ({placeholders})",
        (current_ms, *seen_channel_ids),
    )


def upsert_post(conn: sqlite3.Connection, post: dict[str, Any], ingested_at: int | None = None) -> None:
    """Insert or update one Mattermost post record."""
    current_ms = ingested_at if ingested_at is not None else now_ms()
    conn.execute(
        """
        INSERT INTO posts (
          id, channel_id, user_id, root_id, parent_id, create_at, update_at,
          delete_at, edit_at, message, type, hashtags, props_json,
          metadata_json, raw_json, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          channel_id = excluded.channel_id,
          user_id = excluded.user_id,
          root_id = excluded.root_id,
          parent_id = excluded.parent_id,
          create_at = excluded.create_at,
          update_at = excluded.update_at,
          delete_at = excluded.delete_at,
          edit_at = excluded.edit_at,
          message = excluded.message,
          type = excluded.type,
          hashtags = excluded.hashtags,
          props_json = excluded.props_json,
          metadata_json = excluded.metadata_json,
          raw_json = excluded.raw_json,
          ingested_at = excluded.ingested_at
        """,
        (
            post["id"],
            post["channel_id"],
            post.get("user_id"),
            post.get("root_id"),
            post.get("parent_id"),
            post["create_at"],
            post.get("update_at"),
            post.get("delete_at", 0),
            post.get("edit_at", 0),
            post.get("message"),
            post.get("type"),
            post.get("hashtags"),
            json_dumps(post.get("props")),
            json_dumps(post.get("metadata")),
            json_dumps(post),
            current_ms,
        ),
    )


def upsert_user(conn: sqlite3.Connection, user: dict[str, Any], seen_at: int | None = None) -> None:
    """Insert or update one Mattermost user record."""
    current_ms = seen_at if seen_at is not None else now_ms()
    conn.execute(
        """
        INSERT INTO users (
          id, username, first_name, last_name, nickname, last_seen_at, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          username = excluded.username,
          first_name = excluded.first_name,
          last_name = excluded.last_name,
          nickname = excluded.nickname,
          last_seen_at = excluded.last_seen_at,
          raw_json = excluded.raw_json
        """,
        (
            user["id"],
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
            user.get("nickname"),
            current_ms,
            json_dumps(user),
        ),
    )


def get_watermark(conn: sqlite3.Connection, channel_id: str) -> sqlite3.Row | None:
    """Return the watermark row for a channel, if present."""
    return conn.execute(
        "SELECT * FROM watermarks WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()


def ensure_watermark(conn: sqlite3.Connection, channel_id: str) -> sqlite3.Row:
    """Create a default watermark row if needed and return it."""
    conn.execute(
        "INSERT OR IGNORE INTO watermarks (channel_id) VALUES (?)",
        (channel_id,),
    )
    row = get_watermark(conn, channel_id)
    if row is None:
        raise RuntimeError(f"watermark was not created for channel {channel_id}")
    return row


def update_watermark(
    conn: sqlite3.Connection,
    channel_id: str,
    *,
    backfill_complete: bool | None = None,
    last_post_create_at: int | None = None,
    last_sync_at: int | None = None,
    last_success_at: int | None = None,
    last_error: str | None = None,
) -> None:
    """Upsert selected watermark values for a channel."""
    ensure_watermark(conn, channel_id)
    existing = get_watermark(conn, channel_id)
    assert existing is not None

    conn.execute(
        """
        UPDATE watermarks
        SET backfill_complete = ?,
            last_post_create_at = ?,
            last_sync_at = ?,
            last_success_at = ?,
            last_error = ?
        WHERE channel_id = ?
        """,
        (
            int(backfill_complete) if backfill_complete is not None else existing["backfill_complete"],
            last_post_create_at if last_post_create_at is not None else existing["last_post_create_at"],
            last_sync_at if last_sync_at is not None else existing["last_sync_at"],
            last_success_at if last_success_at is not None else existing["last_success_at"],
            last_error,
            channel_id,
        ),
    )


def get_stats(conn: sqlite3.Connection) -> dict[str, int | None]:
    """Return basic archive counts and latest post timestamp."""
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM channels) AS channels,
          (SELECT COUNT(*) FROM posts) AS posts,
          (SELECT COUNT(*) FROM users) AS users,
          (SELECT COUNT(*) FROM watermarks) AS watermarks,
          (SELECT MAX(create_at) FROM posts) AS latest_post_create_at
        """
    ).fetchone()
    return dict(row)
