# Mattermost SQLite Archiver

Kleiner Archiver für Mattermost-Teams: Er sammelt alle Nachrichten aus Channels, in denen ein Bot/User Mitglied ist, und speichert sie maschinenlesbar in SQLite.

## Ziel

- Mattermost API mit Bot-/User-Token abfragen
- Teams und Channels automatisch erkennen
- neue Channels automatisch aufnehmen
- komplette Channel-History backfillen
- neue Posts inkrementell nachziehen
- Rohdaten verlustarm in SQLite speichern
- keine Secrets im Repository speichern

## Erwartete Umgebung

Die Laufzeit liest Konfiguration aus Umgebungsvariablen:

```env
MATTERMOST_URL=https://mattermost.example.com
MATTERMOST_TOKEN=...
```

## Geplanter Ablauf

1. `GET /api/v4/users/me` — Token prüfen
2. `GET /api/v4/users/me/teams` — Teams des Bots finden
3. `GET /api/v4/users/me/teams/{team_id}/channels` — Channels finden, in denen der Bot Mitglied ist
4. Für neue Channels: komplette History backfillen
5. Für bekannte Channels: `last_post_at` gegen lokale Watermark prüfen
6. Neue Posts speichern

## Channel-Handling

Das Script soll keine statische Channel-Liste brauchen.

Wenn der Bot in Mattermost zu einem neuen Channel hinzugefügt wird:

- der nächste Lauf erkennt den Channel automatisch
- der Channel wird in SQLite registriert
- die komplette History wird backfilled
- danach läuft der Channel inkrementell weiter

## Vorgeschlagene SQLite-Struktur

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

## Sync-Logik

```text
für jeden Lauf:
  Teams holen
  Channels je Team holen
  channels.last_post_at aktualisieren

  für jeden Channel:
    wenn neu:
      komplette History backfillen
    sonst wenn backfill_complete = 0:
      Backfill fortsetzen
    sonst wenn channel.last_post_at <= watermark.last_post_create_at:
      Channel überspringen
    sonst:
      Posts seit watermark.last_post_create_at holen
```

## Backfill

Mattermost liefert Posts paginiert, typischerweise neueste zuerst:

```text
GET /api/v4/channels/{channel_id}/posts?page=0&per_page=200
GET /api/v4/channels/{channel_id}/posts?page=1&per_page=200
...
```

Da `posts.id` Primary Key ist, ist die Reihenfolge beim Speichern unkritisch.

## Datenumfang

Archiviert werden alle Nachrichten, die der konfigurierte Bot/User über die Mattermost API lesen kann. Die Channel-Liste wird bei jedem Lauf aus der aktuellen Mitgliedschaft abgeleitet.

## Initialisierung

```sh
python3 scripts/init_db.py
```

Standardmäßig wird die Datenbank hier erstellt:

```text
data/mattermost.sqlite
```

Alternativ kann der Pfad gesetzt werden:

```sh
ARCHIVER_DB_PATH=/path/to/archive.sqlite python3 scripts/init_db.py
```

## Sync ausführen

```sh
python3 -m mattermost_archiver.sync
```

Das Modul lädt standardmäßig `.env`, initialisiert die SQLite-Datenbank und führt einen Sync-Lauf aus.

Optionen:

```sh
python3 -m mattermost_archiver.sync --env-file /path/to/.env --per-page 200
```

## Sicherheit

- Token nur über `.env` oder Umgebungsvariablen
- `.env` niemals committen
- SQLite-Datenbanken nicht committen
- Rohdaten lokal halten und Retention bewusst festlegen

## Status

Initiales Repository. Die Datenbank-Initialisierung ist vorhanden.
