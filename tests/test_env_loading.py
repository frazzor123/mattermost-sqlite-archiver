import os
import re

from mattermost_archiver import sync


def test_main_prints_timestamped_summary(monkeypatch, capsys):
    monkeypatch.setattr(sync, "run_from_env", lambda **kwargs: sync.SyncResult(channels_seen=1, channels_skipped=1))

    sync.main([])

    output = capsys.readouterr().out.strip()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z Sync complete: ", output)
    assert "channels_seen=1" in output
    assert "skipped=1" in output


def test_load_dotenv_sets_missing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MATTERMOST_URL=https://mattermost.example.com\n"
        "MATTERMOST_TOKEN=test-token\n"
        "ARCHIVER_DB_PATH=./data/test.sqlite\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MATTERMOST_URL", raising=False)
    monkeypatch.delenv("MATTERMOST_TOKEN", raising=False)
    monkeypatch.delenv("ARCHIVER_DB_PATH", raising=False)

    loaded = sync.load_dotenv(env_file)

    assert loaded == 3
    assert os.environ["MATTERMOST_URL"] == "https://mattermost.example.com"
    assert os.environ["MATTERMOST_TOKEN"] == "test-token"
    assert os.environ["ARCHIVER_DB_PATH"] == "./data/test.sqlite"


def test_load_dotenv_does_not_override_existing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MATTERMOST_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("MATTERMOST_TOKEN", "existing-token")

    loaded = sync.load_dotenv(env_file)

    assert loaded == 0
    assert os.environ["MATTERMOST_TOKEN"] == "existing-token"


def test_load_dotenv_handles_quotes_and_comments(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "EMPTY=\n"
        "QUOTED='value with spaces'\n"
        'DOUBLE="another value"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("EMPTY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.delenv("DOUBLE", raising=False)

    loaded = sync.load_dotenv(env_file)

    assert loaded == 3
    assert os.environ["EMPTY"] == ""
    assert os.environ["QUOTED"] == "value with spaces"
    assert os.environ["DOUBLE"] == "another value"
