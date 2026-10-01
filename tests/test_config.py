"""Settings: defaults, YAML, env precedence, validation and save_yaml round trip."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mhl_sentinel.config import (
    YAML_FIELDS,
    Settings,
    WorkingHoursConfig,
    load_settings,
    save_yaml,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("MHLS_"):
            monkeypatch.delenv(key)


def test_defaults_match_contract(tmp_path: Path) -> None:
    s = load_settings(tmp_path)
    assert s.archive_root == Path("/archive")
    assert s.project_depth == 1
    assert s.ignore_prefixes == "_@#."
    assert s.exclude_globs == []
    assert s.working_hours == WorkingHoursConfig(
        days=["mon", "tue", "wed", "thu", "fri"], start="09:00", end="19:00"
    )
    assert s.settle_hours == 168
    assert s.verify_interval_days == 90
    assert s.scan_interval_minutes == 60
    assert s.log_level == "info"
    assert s.hash_format == "xxh128"
    assert s.timezone == "UTC"
    assert s.config_dir == tmp_path
    assert s.port == 8080
    assert s.db_path == tmp_path / "state.db"


def test_yaml_overrides_defaults_and_env_overrides_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.yaml").write_text(
        "settle_hours: 24\n"
        "timezone: Europe/Madrid\n"
        "working_hours: {days: [sat, mon], start: '22:00', end: '06:00'}\n"
        "port: 9999\n"  # env-only: ignored from YAML
        "archive_root: /elsewhere\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MHLS_SETTLE_HOURS", "48")
    monkeypatch.setenv("MHLS_WORKING_HOURS__START", "23:00")
    s = load_settings(tmp_path)
    assert s.settle_hours == 48
    assert s.timezone == "Europe/Madrid"
    assert s.working_hours.days == ["mon", "sat"]  # normalised order
    assert s.working_hours.start == "23:00"  # env nested wins
    assert s.working_hours.end == "06:00"  # rest of the nested model from YAML
    assert s.port == 8080
    assert s.archive_root == Path("/archive")


def test_env_only_fields_and_config_dir_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MHLS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("MHLS_PORT", "8181")
    monkeypatch.setenv("MHLS_ARCHIVE_ROOT", "/data")
    (tmp_path / "config.yaml").write_text("project_depth: 2\n", encoding="utf-8")
    s = load_settings()
    assert s.config_dir == tmp_path
    assert s.port == 8181
    assert s.archive_root == Path("/data")
    assert s.project_depth == 2


@pytest.mark.parametrize(
    "hours",
    [
        {"days": ["monday"]},
        {"start": "9:00"},
        {"end": "24:00"},
        {"start": "10:00", "end": "10:00"},
    ],
)
def test_working_hours_validation(hours: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkingHoursConfig.model_validate(hours)


def test_invalid_timezone_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MHLS_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValidationError):
        Settings()


def test_save_yaml_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MHLS_PORT", "8181")
    s = load_settings(tmp_path).model_copy(
        update={
            "exclude_globs": ["*.md"],
            "timezone": "Europe/Madrid",
            "working_hours": WorkingHoursConfig(days=["fri"], start="20:00", end="07:00"),
        }
    )
    target = tmp_path / "config.yaml"
    save_yaml(s, target)
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# ")
    data = yaml.safe_load(text)
    assert list(data) == list(YAML_FIELDS)
    assert "port" not in data and "archive_root" not in data and "config_dir" not in data
    monkeypatch.delenv("MHLS_PORT")
    again = load_settings(tmp_path)
    assert again.exclude_globs == ["*.md"]
    assert again.timezone == "Europe/Madrid"
    assert again.working_hours.days == ["fri"]
    assert again.working_hours.start == "20:00"
    # save again → byte-identical (stable order)
    save_yaml(again, target)
    assert target.read_text(encoding="utf-8") == text
