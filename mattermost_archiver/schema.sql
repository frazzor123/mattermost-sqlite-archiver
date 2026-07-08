PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
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

CREATE TABLE IF NOT EXISTS posts (
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

CREATE TABLE IF NOT EXISTS watermarks (
  channel_id TEXT PRIMARY KEY,
  backfill_complete INTEGER DEFAULT 0,
  last_post_create_at INTEGER DEFAULT 0,
  last_sync_at INTEGER,
  last_success_at INTEGER,
  last_error TEXT,
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  nickname TEXT,
  last_seen_at INTEGER,
  raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_channels_team
ON channels(team_id);

CREATE INDEX IF NOT EXISTS idx_channels_last_post
ON channels(last_post_at);

CREATE INDEX IF NOT EXISTS idx_posts_channel_create
ON posts(channel_id, create_at);

CREATE INDEX IF NOT EXISTS idx_posts_root
ON posts(root_id);

CREATE INDEX IF NOT EXISTS idx_posts_create
ON posts(create_at);

CREATE VIEW IF NOT EXISTS posts_enriched AS
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
