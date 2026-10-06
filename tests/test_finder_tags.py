"""Finder tags by project state (D70): the stream format, the app owning every tag of a project
folder, and the sync that puts right a tag changed by hand (D72). The xattr calls are faked so it
runs on macOS too."""

from __future__ import annotations

import errno
import os
import plistlib
from pathlib import Path

import pytest

from mhl_sentinel import finder_tags as ft
from mhl_sentinel.clock import utcnow
from mhl_sentinel.db import Database
from mhl_sentinel.models import ProjectState


class FakeXattrs:
    """In-memory stand-in for ``os.getxattr`` / ``setxattr`` / ``removexattr``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.store: dict[tuple[str, str], bytes] = {}
        self.writes = 0
        monkeypatch.setattr(os, "getxattr", self.get, raising=False)
        monkeypatch.setattr(os, "setxattr", self.set, raising=False)

    def get(self, path: Path, name: str) -> bytes:
        try:
            return self.store[(str(path), name)]
        except KeyError:
            raise OSError(errno.ENODATA, "No data available") from None

    def set(self, path: Path, name: str, value: bytes) -> None:
        self.writes += 1
        self.store[(str(path), name)] = value


@pytest.fixture
def xattrs(monkeypatch: pytest.MonkeyPatch) -> FakeXattrs:
    return FakeXattrs(monkeypatch)


def stored(x: FakeXattrs, path: Path) -> list[str]:
    raw = x.store[(str(path), ft.XATTR)]
    assert raw.endswith(b"\0")  # Samba streams_xattr keeps one trailing NUL
    value = plistlib.loads(raw[:-1])
    assert isinstance(value, list)
    return value


def test_states_map_to_three_tags() -> None:
    assert ft.tag_for(ProjectState.SEALED) == ft.TAG_OK
    for state in (
        ProjectState.UNSEALED,
        ProjectState.CHANGED,
        ProjectState.QUEUED,
        ProjectState.HASHING,
    ):
        assert ft.tag_for(state) == ft.TAG_PENDING
    assert ft.tag_for(ProjectState.NEEDS_REVIEW) == ft.TAG_REVIEW
    assert ft.tag_for(ProjectState.ERROR) == ft.TAG_REVIEW
    assert ft.tag_for(ProjectState.IGNORED) is None
    assert ft.tag_for(ProjectState.MISSING) is None


def test_decode_and_encode() -> None:
    assert ft.decode(None) == []
    assert ft.decode(b"not a plist") is None
    assert ft.decode(plistlib.dumps({"a": 1}, fmt=plistlib.FMT_BINARY)) is None
    assert ft.decode(plistlib.dumps(["Red\n6", 3], fmt=plistlib.FMT_BINARY)) is None
    data = ft.encode(["Red\n6", "Client"])
    assert data.startswith(b"bplist00")
    assert ft.decode(data) == ["Red\n6", "Client"]
    assert ft.decode(ft.encode([])) == []


def test_wanted_is_the_app_tag_alone() -> None:
    assert ft.wanted(ft.TAG_OK) == ["MHL OK\n2"]
    assert ft.wanted(None) == []


def test_apply_adds_replaces_and_clears(tmp_path: Path, xattrs: FakeXattrs) -> None:
    assert not ft.apply(tmp_path, None)  # no stream and no tag wanted: nothing written
    assert ft.apply(tmp_path, ft.TAG_PENDING)
    assert stored(xattrs, tmp_path) == ["MHL pendiente\n5"]
    assert not ft.apply(tmp_path, ft.TAG_PENDING)  # nothing to change, nothing written
    assert xattrs.writes == 1
    assert ft.apply(tmp_path, ft.TAG_OK)
    assert stored(xattrs, tmp_path) == ["MHL OK\n2"]
    assert ft.apply(tmp_path, None)  # an empty list, not a removed stream (#24)
    assert stored(xattrs, tmp_path) == []
    assert not ft.apply(tmp_path, None)


def test_apply_removes_every_other_tag(tmp_path: Path, xattrs: FakeXattrs) -> None:
    """D72: the team's tags go too, so a green tag of any name cannot pass for "MHL OK"."""
    team = ["Client\n4", "Archivado\n2", "Hold"]
    xattrs.store[(str(tmp_path), ft.XATTR)] = plistlib.dumps(team, fmt=plistlib.FMT_BINARY)
    assert ft.apply(tmp_path, ft.TAG_REVIEW)  # a stream written without the NUL reads as well
    assert stored(xattrs, tmp_path) == ["MHL revisar\n6"]


def test_apply_puts_right_a_tag_faked_by_hand(tmp_path: Path, xattrs: FakeXattrs) -> None:
    for fake in (["MHL OK\n2"], ["MHL OK\n2", "MHL pendiente\n5"], ["MHL pendiente\n2"]):
        ft.write_raw(tmp_path, ft.encode(fake))
        assert ft.apply(tmp_path, ft.TAG_PENDING)
        assert stored(xattrs, tmp_path) == ["MHL pendiente\n5"]


def test_apply_overwrites_an_unreadable_stream(tmp_path: Path, xattrs: FakeXattrs) -> None:
    xattrs.store[(str(tmp_path), ft.XATTR)] = b"garbage\0"
    assert ft.apply(tmp_path, ft.TAG_OK)
    assert stored(xattrs, tmp_path) == ["MHL OK\n2"]


def test_other_xattr_errors_propagate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_: object) -> None:
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(os, "getxattr", denied, raising=False)
    with pytest.raises(PermissionError):
        ft.read_raw(tmp_path)


def test_real_xattrs_when_the_filesystem_has_them(tmp_path: Path) -> None:
    if not hasattr(os, "setxattr"):
        pytest.skip("no Linux xattr API here")
    try:
        os.setxattr(tmp_path, "user.probe", b"1")
    except OSError:
        pytest.skip("this filesystem has no user xattrs")
    assert ft.apply(tmp_path, ft.TAG_OK)
    assert ft.decode(ft.read_raw(tmp_path)) == ["MHL OK\n2"]
    assert ft.apply(tmp_path, None)
    assert ft.decode(ft.read_raw(tmp_path)) == []


def project_db(tmp_path: Path) -> tuple[Database, dict[str, int]]:
    db = Database(tmp_path / "state.db", mounts_file=tmp_path / "no-mounts").open()
    now = utcnow()
    ids = {name: db.upsert_project(f"2025/{name}", name, False, now) for name in ("A", "B", "C")}
    db.set_state(ids["A"], ProjectState.SEALED)
    db.set_state(ids["B"], ProjectState.NEEDS_REVIEW)
    db.set_state(ids["C"], ProjectState.IGNORED)
    return db, ids


def test_sync_rechecks_every_folder_at_every_call(tmp_path: Path) -> None:
    """D72: a tag changed by hand is put right at the next tick, not only on a state change."""
    calls: list[tuple[str, str | None]] = []

    def fake(path: Path, tag: str | None) -> bool:
        calls.append((path.name, tag))
        return False

    db, ids = project_db(tmp_path)
    sync = ft.TagSync(apply_fn=fake, touch_fn=lambda _: None, supported=True)
    archive = tmp_path / "archive"
    assert sync.sync(db, archive, True) == []
    assert calls == [("A", ft.TAG_OK), ("B", ft.TAG_REVIEW), ("C", None)]
    calls.clear()
    assert sync.sync(db, archive, True) == []
    assert calls == [("A", ft.TAG_OK), ("B", ft.TAG_REVIEW), ("C", None)]
    calls.clear()
    db.set_state(ids["B"], ProjectState.QUEUED)
    db.set_state(ids["C"], ProjectState.MISSING)
    sync.sync(db, archive, True)
    assert calls == [("A", ft.TAG_OK), ("B", ft.TAG_PENDING)]
    db.close()


def test_sync_touches_nothing_when_turned_off(tmp_path: Path) -> None:
    """D72: off, the folders are the team's again; not even the app's tags are removed."""

    def never(path: Path, tag: str | None) -> bool:
        raise AssertionError("called")

    db, _ = project_db(tmp_path)
    assert ft.TagSync(apply_fn=never, supported=True).sync(db, tmp_path, False) == []
    db.close()


def test_sync_reports_a_failure_once(tmp_path: Path) -> None:
    fail = {"A", "B", "C"}

    def failing(path: Path, tag: str | None) -> bool:
        if path.name not in fail:
            return False
        if path.name == "A":
            raise OSError(errno.EPERM, "Operation not permitted")
        raise OSError("no strerror")

    db, ids = project_db(tmp_path)
    sync = ft.TagSync(apply_fn=failing, touch_fn=lambda _: None, supported=True)
    errors = sync.sync(db, tmp_path, True)
    assert errors == [
        "Finder tag not set on 2025/A: Operation not permitted",
        "Finder tag not set on 2025/B: no strerror",
        "Finder tag not set on 2025/C: no strerror",
    ]
    assert sync.sync(db, tmp_path, True) == []  # not every minute
    db.set_state(ids["B"], ProjectState.QUEUED)  # another tag: a new message
    assert sync.sync(db, tmp_path, True) == ["Finder tag not set on 2025/B: no strerror"]
    fail.discard("A")  # works again, then fails again: reported again
    assert sync.sync(db, tmp_path, True) == []
    fail.add("A")
    assert sync.sync(db, tmp_path, True) == [
        "Finder tag not set on 2025/A: Operation not permitted"
    ]
    db.set_state(ids["C"], ProjectState.MISSING)
    sync.sync(db, tmp_path, True)
    assert ids["C"] not in sync.reported
    sync.reported[999] = None  # a project forgotten since: its id may come back
    sync.sync(db, tmp_path, True)
    assert 999 not in sync.reported
    sync.sync(db, tmp_path, False)
    assert sync.reported == {}
    db.close()


def test_sync_does_nothing_without_xattr_support(tmp_path: Path) -> None:
    def never(path: Path, tag: str | None) -> bool:
        raise AssertionError("called")

    db, _ = project_db(tmp_path)
    assert ft.TagSync(apply_fn=never, supported=False).sync(db, tmp_path, True) == []
    db.close()


def test_sync_touches_each_parent_once_after_a_change(tmp_path: Path) -> None:
    """The macOS SMB client refreshes a listing only when the folder mtime changes."""
    touched: list[Path] = []

    def changed(path: Path, tag: str | None) -> bool:
        return path.name != "C"  # C was already right: nothing written

    db, _ = project_db(tmp_path)
    sync = ft.TagSync(apply_fn=changed, touch_fn=touched.append, supported=True)
    assert sync.sync(db, tmp_path, True) == []
    assert touched == [tmp_path / "2025"]
    db.close()


def test_sync_reports_a_parent_it_cannot_touch(tmp_path: Path) -> None:
    def refuse(path: Path) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    db, _ = project_db(tmp_path)
    sync = ft.TagSync(apply_fn=lambda p, t: True, touch_fn=refuse, supported=True)
    assert sync.sync(db, tmp_path, True) == [
        "Finder may show the old tag of 2025/A: Operation not permitted"
    ]
    db.close()


def test_touch_bumps_the_mtime(tmp_path: Path) -> None:
    os.utime(tmp_path, (0, 0))
    ft.touch(tmp_path)
    assert tmp_path.stat().st_mtime > 0
