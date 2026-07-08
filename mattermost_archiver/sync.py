"""Synchronization logic for archiving Mattermost posts into SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from mattermost_archiver import db

DEFAULT_PER_PAGE = 200


class MattermostAPI(Protocol):
    """Subset of API client methods needed by the sync engine."""

    def get_my_teams(self) -> list[dict[str, Any]]: ...

    def get_my_channels(self, team_id: str) -> list[dict[str, Any]]: ...

    def get_channel_posts(
        self,
        channel_id: str,
        *,
        page: int = 0,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> dict[str, Any]: ...

    def get_channel_posts_since(self, channel_id: str, since_ms: int) -> dict[str, Any]: ...


@dataclass
class SyncResult:
    """Counters for one sync run."""

    teams_seen: int = 0
    channels_seen: int = 0
    channels_backfilled: int = 0
    channels_incremental: int = 0
    channels_skipped: int = 0
    posts_seen: int = 0
    posts_saved: int = 0
    errors: int = 0


def posts_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Return posts in Mattermost response order."""
    posts = response.get("posts") or {}
    order = response.get("order") or []
    return [posts[post_id] for post_id in order if post_id in posts]


def max_create_at(posts: list[dict[str, Any]], fallback: int = 0) -> int:
    """Return max create_at from posts or fallback."""
    if not posts:
        return fallback
    return max(int(post.get("create_at") or 0) for post in posts)


def discover_channels(client: MattermostAPI, conn, *, seen_at: int | None = None) -> list[dict[str, Any]]:
    """Fetch visible teams/channels and upsert channels into SQLite."""
    teams = client.get_my_teams()
    channels: list[dict[str, Any]] = []
    seen_channel_ids: set[str] = set()

    for team in teams:
        team_id = team["id"]
        for channel in client.get_my_channels(team_id):
            channel = {**channel, "team_id": channel.get("team_id") or team_id}
            db.upsert_channel(conn, channel, seen_at=seen_at)
            seen_channel_ids.add(channel["id"])
            channels.append(channel)

    db.mark_channels_not_seen(conn, seen_channel_ids, updated_at=seen_at)
    return channels


def save_posts(conn, posts: list[dict[str, Any]], *, ingested_at: int | None = None) -> int:
    """Store posts and return number of posts processed."""
    for post in posts:
        db.upsert_post(conn, post, ingested_at=ingested_at)
    return len(posts)


def backfill_channel(
    client: MattermostAPI,
    conn,
    channel_id: str,
    *,
    per_page: int = DEFAULT_PER_PAGE,
    run_at: int | None = None,
) -> int:
    """Fetch complete channel history and mark backfill complete."""
    page = 0
    total = 0
    latest_create_at = 0

    while True:
        response = client.get_channel_posts(channel_id, page=page, per_page=per_page)
        posts = posts_from_response(response)
        if not posts:
            break

        save_posts(conn, posts, ingested_at=run_at)
        total += len(posts)
        latest_create_at = max(latest_create_at, max_create_at(posts))

        if len(posts) < per_page:
            break
        page += 1

    db.update_watermark(
        conn,
        channel_id,
        backfill_complete=True,
        last_post_create_at=latest_create_at,
        last_sync_at=run_at,
        last_success_at=run_at,
        last_error=None,
    )
    return total


def incremental_sync_channel(
    client: MattermostAPI,
    conn,
    channel_id: str,
    since_ms: int,
    *,
    run_at: int | None = None,
) -> int:
    """Fetch posts changed since a timestamp and update watermark."""
    response = client.get_channel_posts_since(channel_id, since_ms)
    posts = posts_from_response(response)
    save_posts(conn, posts, ingested_at=run_at)

    latest_create_at = max_create_at(posts, fallback=since_ms)
    db.update_watermark(
        conn,
        channel_id,
        backfill_complete=True,
        last_post_create_at=latest_create_at,
        last_sync_at=run_at,
        last_success_at=run_at,
        last_error=None,
    )
    return len(posts)


def sync_all(client: MattermostAPI, conn, *, per_page: int = DEFAULT_PER_PAGE, run_at: int | None = None) -> SyncResult:
    """Discover channels and synchronize all readable posts."""
    run_at = run_at if run_at is not None else db.now_ms()
    result = SyncResult()

    channels = discover_channels(client, conn, seen_at=run_at)
    result.teams_seen = len(client.get_my_teams())
    result.channels_seen = len(channels)

    for channel in channels:
        channel_id = channel["id"]
        watermark = db.ensure_watermark(conn, channel_id)
        last_post_at = int(channel.get("last_post_at") or 0)
        last_post_create_at = int(watermark["last_post_create_at"] or 0)

        try:
            if not watermark["backfill_complete"]:
                count = backfill_channel(client, conn, channel_id, per_page=per_page, run_at=run_at)
                result.channels_backfilled += 1
            elif last_post_at <= last_post_create_at:
                db.update_watermark(conn, channel_id, last_sync_at=run_at, last_error=None)
                result.channels_skipped += 1
                continue
            else:
                count = incremental_sync_channel(client, conn, channel_id, last_post_create_at, run_at=run_at)
                result.channels_incremental += 1

            result.posts_seen += count
            result.posts_saved += count
        except Exception as exc:
            result.errors += 1
            db.update_watermark(conn, channel_id, last_sync_at=run_at, last_error=str(exc))

    conn.commit()
    return result
