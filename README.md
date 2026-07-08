# Mattermost SQLite Archiver

A small Mattermost archive tool that stores readable Mattermost messages in a local SQLite database.

It is designed for simple, machine-readable exports of Mattermost team/channel history without running a separate service or database server. The project was vibe coded and intentionally kept small.

## What it does

- Authenticates against the Mattermost REST API with a user or bot token
- Discovers all teams visible to that token
- Discovers all channels visible to that token
- Backfills the full message history for newly discovered channels
- Uses `last_post_at` and local watermarks to skip unchanged channels
- Incrementally syncs new or changed posts on later runs
- Stores normalized rows plus original raw JSON in SQLite
- Syncs referenced users into a `users` table
- Provides a `posts_enriched` view with channel and user names
- Keeps credentials out of the repository

## Use cases

- Personal or team Mattermost message archive
- SQLite export for search, analysis, reporting, or backup workflows
- Lightweight Mattermost channel history collector
- Local-first archive without PostgreSQL, Elasticsearch, or another server process

## Requirements

- Python 3.11+
- A Mattermost server
- A Mattermost personal access token or bot token
- Read access to the channels that should be archived

No external Python runtime dependencies are required for normal sync runs.

## Installation

Clone the repository:

```sh
git clone <repository-url>
cd mattermost-sqlite-archiver
```

Create a local `.env` file:

```sh
cp .env.example .env
```

Edit `.env`:

```env
MATTERMOST_URL=https://mattermost.example.com
MATTERMOST_TOKEN=replace-me
ARCHIVER_DB_PATH=data/mattermost.sqlite
```

`ARCHIVER_DB_PATH` is optional. If omitted, the default is:

```text
data/mattermost.sqlite
```

## Running a sync

Run one sync:

```sh
python3 -m mattermost_archiver.sync
```

Use a custom env file:

```sh
python3 -m mattermost_archiver.sync --env-file /path/to/.env
```

Use a custom Mattermost page size:

```sh
python3 -m mattermost_archiver.sync --per-page 200
```

The command loads `.env`, initializes the SQLite database if needed, discovers visible teams/channels, then archives posts.

Example output:

```text
2026-07-08T12:00:00Z Sync complete: channels_seen=12 backfilled=1 incremental=2 skipped=9 posts_saved=143 errors=0
```

## How channel discovery works

The tool does not require a static channel list.

On every run it asks Mattermost:

1. Which teams can this token access?
2. Which channels can this token access in those teams?
3. Which channels have new posts?

If the token gains access to a new channel, the next run discovers it automatically and starts a full backfill for that channel.

## Backfill behavior

For a newly discovered channel, the tool fetches full history with Mattermost pagination:

```text
GET /api/v4/channels/{channel_id}/posts?page=0&per_page=200
GET /api/v4/channels/{channel_id}/posts?page=1&per_page=200
...
```

Posts are upserted by Mattermost post ID, so reruns are safe.

After the backfill is complete, future runs use the stored channel watermark and the Mattermost `last_post_at` value to decide whether a channel can be skipped.

## Data scope

The archive contains messages the configured token can read through the Mattermost API.

That can include public channels, private channels, direct messages, or group messages depending on the token's access. The tool does not apply an additional channel-type filter.

## SQLite schema

### `channels`

```sql
CREATE TABLE channels (
  id TEXT PRIMARY KEY,
  team_id TEXT,
  name TEXT,
  display_name TEXT,
  type TEXT,
  last_post_at INTEGER,
  last_seen_at INTEGER,
  first_indexed_at INTEGER,
  is_member INTEGER DEFAULT 1,
  updated_at INTEGER
);
```

### `posts`

```sql
CREATE TABLE posts (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  user_id TEXT,
  root_id TEXT,
  parent_id TEXT,
  create_at INTEGER NOT NULL,
  update_at INTEGER,
  delete_at INTEGER DEFAULT 0,
  edit_at INTEGER DEFAULT 0,
  message TEXT,
  type TEXT,
  hashtags TEXT,
  props_json TEXT,
  metadata_json TEXT,
  raw_json TEXT NOT NULL,
  ingested_at INTEGER NOT NULL,
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);
```

### `watermarks`

```sql
CREATE TABLE watermarks (
  channel_id TEXT PRIMARY KEY,
  backfill_complete INTEGER DEFAULT 0,
  last_post_create_at INTEGER DEFAULT 0,
  last_sync_at INTEGER,
  last_success_at INTEGER,
  last_error TEXT,
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);
```

### `users`

```sql
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  nickname TEXT,
  last_seen_at INTEGER,
  raw_json TEXT
);
```

### `posts_enriched`

`posts_enriched` is a convenience view that joins posts with channel and user metadata:

```sql
CREATE VIEW posts_enriched AS
SELECT
  posts.*,
  channels.name AS channel_name,
  channels.display_name AS channel_display_name,
  channels.type AS channel_type,
  users.username AS username,
  users.first_name AS first_name,
  users.last_name AS last_name,
  users.nickname AS nickname
FROM posts
LEFT JOIN channels ON channels.id = posts.channel_id
LEFT JOIN users ON users.id = posts.user_id;
```

## Inspecting the archive

Count archived channels:

```sh
sqlite3 data/mattermost.sqlite 'select count(*) from channels;'
```

Count archived posts:

```sh
sqlite3 data/mattermost.sqlite 'select count(*) from posts;'
```

Show recent enriched posts:

```sh
sqlite3 data/mattermost.sqlite \
  'select channel_display_name, username, datetime(create_at / 1000, "unixepoch"), substr(message, 1, 80) from posts_enriched order by create_at desc limit 10;'
```

## Development

Create a virtual environment and install test dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install pytest
```

Run tests:

```sh
python -m pytest tests -q
```

## Safety notes

- Do not commit `.env` files
- Do not commit SQLite databases
- Treat archived messages as sensitive data
- Store tokens in environment variables or local `.env` files only
- Review access rights on the Mattermost side before running a backfill

## License

No license has been selected yet.
