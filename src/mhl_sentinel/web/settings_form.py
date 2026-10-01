"""Settings page (sketch D45): form values ↔ :class:`Settings`, inline errors, warnings.

A new :class:`Settings` is built from the form with the env-only fields (``archive_root``,
``config_dir``, ``port``) and ``hash_format`` copied from the current one, so validation is the
model's own (``config.py``), never a second copy of the rules here.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from mhl_sentinel.config import DAYS, ENV_ONLY_FIELDS, Settings

FORM_FIELDS: tuple[str, ...] = (
    "project_depth",
    "ignore_prefixes",
    "exclude_globs",
    "days",
    "start",
    "end",
    "timezone",
    "settle_hours",
    "verify_interval_days",
    "scan_interval_minutes",
    "log_level",
)
LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error")
DAY_LABELS: tuple[tuple[str, str], ...] = tuple((d, d.capitalize()) for d in DAYS)

# Which environment variable can override each form field (precedence: env > YAML).
_ENV_FOR_FIELD: dict[str, str] = {
    "project_depth": "PROJECT_DEPTH",
    "ignore_prefixes": "IGNORE_PREFIXES",
    "exclude_globs": "EXCLUDE_GLOBS",
    "days": "WORKING_HOURS",
    "start": "WORKING_HOURS",
    "end": "WORKING_HOURS",
    "timezone": "TIMEZONE",
    "settle_hours": "SETTLE_HOURS",
    "verify_interval_days": "VERIFY_INTERVAL_DAYS",
    "scan_interval_minutes": "SCAN_INTERVAL_MINUTES",
    "log_level": "LOG_LEVEL",
}
_SPLIT = re.compile(r"[,\n]")


@dataclass(slots=True)
class FormState:
    values: dict[str, Any]
    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    saved: bool = False


def values_from_settings(settings: Settings) -> dict[str, Any]:
    wh = settings.working_hours
    return {
        "project_depth": str(settings.project_depth),
        "ignore_prefixes": "  ".join(settings.ignore_prefixes),
        "exclude_globs": ", ".join(settings.exclude_globs),
        "days": list(wh.days),
        "start": wh.start,
        "end": wh.end,
        "timezone": settings.timezone,
        "settle_hours": str(settings.settle_hours),
        "verify_interval_days": str(settings.verify_interval_days),
        "scan_interval_minutes": str(settings.scan_interval_minutes),
        "log_level": settings.log_level,
    }


def values_from_form(form: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    def one(name: str) -> str:
        items = form.get(name) or [""]
        return str(items[-1]).strip()

    values: dict[str, Any] = {name: one(name) for name in FORM_FIELDS if name != "days"}
    values["days"] = [str(d).strip().lower() for d in form.get("days", []) if str(d).strip()]
    return values


def parse_globs(text: str) -> list[str]:
    out: list[str] = []
    for part in _SPLIT.split(text):
        glob = part.strip()
        if glob and glob not in out:
            out.append(glob)
    return out


def _field_for(loc: tuple[int | str, ...]) -> str:
    if not loc:
        return "form"
    head = str(loc[0])
    if head == "working_hours":
        if len(loc) > 1 and str(loc[1]) in ("days", "start", "end"):
            return str(loc[1])
        return "end"  # model-level check (start == end)
    return head


def _message(msg: str) -> str:
    return msg.removeprefix("Value error, ")


def build_settings(
    values: Mapping[str, Any], current: Settings
) -> tuple[Settings | None, dict[str, str]]:
    """Validate through :class:`Settings`. Returns ``(settings, {})`` or ``(None, errors)``."""
    kwargs: dict[str, Any] = {name: getattr(current, name) for name in ENV_ONLY_FIELDS}
    kwargs["hash_format"] = current.hash_format  # D30: fixed, not editable in the GUI
    kwargs.update(
        project_depth=values["project_depth"],
        ignore_prefixes="".join(str(values["ignore_prefixes"]).split()),
        exclude_globs=parse_globs(str(values["exclude_globs"])),
        working_hours={
            "days": list(values["days"]),
            "start": values["start"],
            "end": values["end"],
        },
        timezone=values["timezone"],
        settle_hours=values["settle_hours"],
        verify_interval_days=values["verify_interval_days"],
        scan_interval_minutes=values["scan_interval_minutes"],
        log_level=values["log_level"],
    )
    try:
        return Settings(**kwargs), {}
    except ValidationError as exc:
        errors: dict[str, str] = {}
        for err in exc.errors():
            name = _field_for(tuple(err["loc"]))
            errors.setdefault(name, _message(str(err["msg"])))
        return None, errors


def glob_warnings(old: Settings, new: Settings, any_sealed: bool) -> list[str]:
    """ASC MHL ignore patterns can only grow (D14): removing one does not bring files back."""
    if not any_sealed:
        return []
    removed = [g for g in old.exclude_globs if g not in new.exclude_globs]
    if not removed:
        return []
    return [
        "Removed file types (" + ", ".join(removed) + ") stay excluded in projects that are "
        "already sealed: in ASC MHL the excluded patterns of a history can only grow. "
        "They apply only to projects sealed from now on."
    ]


def env_overrides(environ: Mapping[str, str] | None = None) -> set[str]:
    """Form fields that an ``MHLS_*`` variable overrides at the next start."""
    env = os.environ if environ is None else environ
    keys = {k.upper() for k in env if k.upper().startswith("MHLS_")}
    out: set[str] = set()
    for name, suffix in _ENV_FOR_FIELD.items():
        var = "MHLS_" + suffix
        if var in keys or any(k.startswith(var + "__") for k in keys):
            out.add(name)
    return out
