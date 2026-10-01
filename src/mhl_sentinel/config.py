"""Settings: defaults < ``<config_dir>/config.yaml`` < env ``MHLS_*`` (D36, docs/arquitectura.md).

``archive_root``, ``config_dir`` and ``port`` come only from the environment (they depend on the
container mounts); a value for them in the YAML is ignored. The GUI writes the YAML with
:func:`save_yaml`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ENV_ONLY_FIELDS: frozenset[str] = frozenset({"archive_root", "config_dir", "port"})
CONFIG_FILENAME = "config.yaml"
DEFAULT_CONFIG_DIR = Path("/config")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Fields written by save_yaml, in this stable order (everything except ENV_ONLY_FIELDS).
YAML_FIELDS: tuple[str, ...] = (
    "project_depth",
    "ignore_prefixes",
    "exclude_globs",
    "working_hours",
    "settle_hours",
    "verify_interval_days",
    "scan_interval_minutes",
    "log_level",
    "timezone",
    "theme",
    "hash_format",
)

_YAML_HEADER = (
    "# MHL Sentinel settings, written by the web GUI. Edit by hand only while the app is stopped.\n"
    "# Precedence: defaults < this file < environment variables MHLS_* (nested with __).\n"
    "# archive_root, config_dir and port are set only through the environment.\n"
)


class WorkingHoursConfig(BaseModel):
    """Studio working hours (D33): during them the app does not touch the server."""

    days: list[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"])
    start: str = "09:00"
    end: str = "19:00"

    @field_validator("days")
    @classmethod
    def _check_days(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for day in value:
            norm = day.strip().lower()
            if norm not in DAYS:
                raise ValueError(f"unknown day {day!r}; expected one of {', '.join(DAYS)}")
            if norm not in out:
                out.append(norm)
        return sorted(out, key=DAYS.index)

    @field_validator("start", "end")
    @classmethod
    def _check_time(cls, value: str) -> str:
        value = value.strip()
        if not _HHMM.match(value):
            raise ValueError(f"time {value!r} must be HH:MM (00:00-23:59)")
        return value

    @model_validator(mode="after")
    def _check_slot(self) -> WorkingHoursConfig:
        if self.start == self.end:
            raise ValueError("working_hours.start and end must differ (use days: [] for none)")
        return self


class _YamlSource(PydanticBaseSettingsSource):
    """Settings source backed by an already-parsed YAML mapping."""

    def __init__(self, settings_cls: type[BaseSettings], data: dict[str, Any]) -> None:
        super().__init__(settings_cls)
        self._data = data

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class Settings(BaseSettings):
    """Application settings. Defaults are the contract (docs/arquitectura.md, Configuración)."""

    model_config = SettingsConfigDict(
        env_prefix="MHLS_",
        env_nested_delimiter="__",
        extra="ignore",
        validate_default=True,
    )

    archive_root: Path = Path("/archive")  # fixed by the mount; env only
    project_depth: int = Field(default=1, ge=1)  # D18
    ignore_prefixes: str = "_@#."  # D19
    exclude_globs: list[str] = Field(default_factory=list)  # D14; only grow (spec)
    working_hours: WorkingHoursConfig = Field(default_factory=WorkingHoursConfig)  # D33
    settle_hours: int = Field(default=168, ge=0)  # D31
    verify_interval_days: int = Field(default=90, ge=1)  # D23
    scan_interval_minutes: int = Field(default=60, ge=1)
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    theme: Literal["auto", "light", "dark"] = "auto"  # D56: GUI theme; auto follows the system
    hash_format: Literal["xxh128"] = "xxh128"  # D30, not editable in the GUI
    # IANA zone of the working hours. Not env TZ: the process always runs with TZ=UTC
    # because ascmhl writes dates with the current offset.
    timezone: str = "UTC"
    config_dir: Path = DEFAULT_CONFIG_DIR  # config.yaml + state.db; env only
    port: int = Field(default=8080, ge=1, le=65535)  # env only

    @field_validator("log_level", mode="before")
    @classmethod
    def _lower_level(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # init > env > yaml > defaults. The YAML mapping travels through init kwargs under a
        # private key so that load_settings() can choose the file at runtime.
        yaml_data: dict[str, Any] = {}
        if isinstance(init_settings, InitSettingsSource):
            yaml_data = init_settings.init_kwargs.pop("_yaml", None) or {}
        return (init_settings, env_settings, _YamlSource(settings_cls, yaml_data))

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        name = value.strip()
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"timezone {name!r} is not a valid IANA time zone") from exc
        return name

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def config_file(self) -> Path:
        return self.config_dir / CONFIG_FILENAME

    @property
    def db_path(self) -> Path:
        return self.config_dir / "state.db"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: top level must be a mapping")
    return {str(k): v for k, v in loaded.items() if str(k) not in ENV_ONLY_FIELDS}


def load_settings(config_dir: Path | None = None) -> Settings:
    """Build :class:`Settings` reading ``<config_dir>/config.yaml`` if it exists.

    ``config_dir`` defaults to env ``MHLS_CONFIG_DIR`` or ``/config``. An explicit argument wins
    over the environment.
    """
    if config_dir is None:
        config_dir = Path(os.environ.get("MHLS_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    data = _read_yaml(config_dir / CONFIG_FILENAME)
    return Settings(_yaml=data, config_dir=config_dir)  # type: ignore[call-arg]


def save_yaml(settings: Settings, path: Path) -> None:
    """Write the GUI-editable fields to ``path`` (stable key order, header comment), atomically."""
    dumped = settings.model_dump(mode="json")
    ordered = {key: dumped[key] for key in YAML_FIELDS}
    body = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False, allow_unicode=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_YAML_HEADER + body, encoding="utf-8")
    tmp.replace(path)
