"""prefs JSON fallback: non-object JSON must be treated as empty."""

from buysmarter.memory import prefs


def test_read_file_non_object_json_is_empty(tmp_path, monkeypatch, caplog) -> None:
    path = tmp_path / "prefs.json"
    path.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setenv("BUYSMARTER_PREFS_FILE", str(path))
    with caplog.at_level("WARNING"):
        assert prefs._read_file() == {}
    assert "not a JSON object" in caplog.text
