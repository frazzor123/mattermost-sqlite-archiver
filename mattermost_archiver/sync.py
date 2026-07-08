"""Synchronization logic for archiving Mattermost posts into SQLite."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mattermost_archiver import api, db

DEFAULT_PER_PAGE = 200
DEFAULT_DB_PATH = Path("data/mattermost.sqlite")


def load_dotenv(path: str | Path = ".env") -> int:
    """Load simple KEY=VALUE pairs from a dotenv file without overriding env."""
    env_path = Path(path)
    if not env_path.exists():
        return 0

    loaded = 0
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
        loaded += 1
    return loaded


def env_required(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_db_path() -> Path:
    """Return configured database path."""
    return Path(os.environ.get("ARCHIVER_DB_PATH", DEFAULT_DB_PATH)).expanduser()


class MattermostAPI(Protocol):
    """Subset of API client methods needed by the sync engine."""

    def get_my_teams(self) -> list[dict[str, Any]]: ...

    def get_my_channels(self, team_id: str) -> list[dict[str, Any]]: ...

    def get_user(self, user_id: str) -> dict[str, Any]: ...

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


def sync_users_for_posts(
    client: MattermostAPI,
    conn,
    posts: list[dict[str, Any]],
    *,
    seen_user_ids: set[str],
    seen_at: int | None = None,
) -> int:
    """Fetch and upsert users referenced by posts, once per run."""
    synced = 0
    user_ids = sorted({str(post["user_id"]) for post in posts if post.get("user_id")})
    for user_id in user_ids:
        if user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        try:
            user = client.get_user(user_id)
        except Exception:
            continue
        db.upsert_user(conn, user, seen_at=seen_at)
        synced += 1
    return synced


def sync_missing_users(
    client: MattermostAPI,
    conn,
    *,
    seen_user_ids: set[str],
    seen_at: int | None = None,
) -> int:
    """Backfill users for existing posts that were archived before user sync existed."""
    synced = 0
    for user_id in db.get_missing_user_ids(conn):
        if user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        try:
            user = client.get_user(user_id)
        except Exception:
            continue
        db.upsert_user(conn, user, seen_at=seen_at)
        synced += 1
    return synced


def backfill_channel(
    client: MattermostAPI,
    conn,
    channel_id: str,
    *,
    per_page: int = DEFAULT_PER_PAGE,
    run_at: int | None = None,
    seen_user_ids: set[str] | None = None,
) -> int:
    """Fetch complete channel history and mark backfill complete."""
    page = 0
    total = 0
    latest_create_at = 0
    seen_user_ids = seen_user_ids if seen_user_ids is not None else set()

    while True:
        response = client.get_channel_posts(channel_id, page=page, per_page=per_page)
        posts = posts_from_response(response)
        if not posts:
            break

        save_posts(conn, posts, ingested_at=run_at)
        sync_users_for_posts(client, conn, posts, seen_user_ids=seen_user_ids, seen_at=run_at)
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
    seen_user_ids: set[str] | None = None,
) -> int:
    """Fetch posts changed since a timestamp and update watermark."""
    response = client.get_channel_posts_since(channel_id, since_ms)
    posts = posts_from_response(response)
    seen_user_ids = seen_user_ids if seen_user_ids is not None else set()
    save_posts(conn, posts, ingested_at=run_at)
    sync_users_for_posts(client, conn, posts, seen_user_ids=seen_user_ids, seen_at=run_at)

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
    seen_user_ids: set[str] = set()

    for channel in channels:
        channel_id = channel["id"]
        watermark = db.ensure_watermark(conn, channel_id)
        last_post_at = int(channel.get("last_post_at") or 0)
        last_post_create_at = int(watermark["last_post_create_at"] or 0)

        try:
            if not watermark["backfill_complete"]:
                count = backfill_channel(
                    client,
                    conn,
                    channel_id,
                    per_page=per_page,
                    run_at=run_at,
                    seen_user_ids=seen_user_ids,
                )
                result.channels_backfilled += 1
            elif last_post_at <= last_post_create_at:
                db.update_watermark(conn, channel_id, last_sync_at=run_at, last_error=None)
                result.channels_skipped += 1
                continue
            else:
                count = incremental_sync_channel(
                    client,
                    conn,
                    channel_id,
                    last_post_create_at,
                    run_at=run_at,
                    seen_user_ids=seen_user_ids,
                )
                result.channels_incremental += 1

            result.posts_seen += count
            result.posts_saved += count
        except Exception as exc:
            result.errors += 1
            db.update_watermark(conn, channel_id, last_sync_at=run_at, last_error=str(exc))

    sync_missing_users(client, conn, seen_user_ids=seen_user_ids, seen_at=run_at)

    conn.commit()
    return result


def run_from_env(*, dotenv_path: str | Path = ".env", per_page: int = DEFAULT_PER_PAGE) -> SyncResult:
    """Load configuration, run a full sync, and return counters."""
    load_dotenv(dotenv_path)
    client = api.MattermostClient(
        base_url=env_required("MATTERMOST_URL"),
        token=env_required("MATTERMOST_TOKEN"),
    )
    conn = db.connect(env_db_path())
    try:
        db.init_db(conn)
        return sync_all(client, conn, per_page=per_page)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    """Build command-line parser for direct module execution."""
    parser = argparse.ArgumentParser(description="Sync readable Mattermost channel posts into SQLite.")
    parser.add_argument("--env-file", default=".env", help="dotenv file to load before reading environment")
    parser.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE, help="Mattermost posts page size")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run one sync from environment configuration."""
    args = build_parser().parse_args(argv)
    result = run_from_env(dotenv_path=args.env_file, per_page=args.per_page)
    print(
        "Sync complete: "
        f"channels_seen={result.channels_seen} "
        f"backfilled={result.channels_backfilled} "
        f"incremental={result.channels_incremental} "
        f"skipped={result.channels_skipped} "
        f"posts_saved={result.posts_saved} "
        f"errors={result.errors}"
    )


if __name__ == "__main__":
    main()
