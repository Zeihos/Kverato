#!/usr/bin/env python3
"""Проверка миграции из НАСТОЯЩИХ архивов v1.0, сохранности данных и backup.

Без токенов, реальных баз игроков и сторонних Python-библиотек. Работает
одинаково на Windows и Linux. Проверяет исходный db.py из релизного ZIP.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    "vk": ("KVERATO-VK-v1.0-FINAL.zip", "VK"),
    "telegram": ("KVERATO-TG-v1.0-FINAL.zip", "TG"),
    "discord": ("KVERATO-DISCORD-v1.0-FINAL.zip", "DISCORD"),
}

OLD_STAGE = r'''
import sys
from kverato.db import Database

path, platform = sys.argv[1:]
db = Database(path)
with db.connect() as con:
    for uid, coins, xp in (("101", 4321, 640), ("202", 876, 300)):
        con.execute(
            "INSERT INTO players (platform,user_id,display_name,created_at) "
            "VALUES (?,?,?,?)",
            (platform, uid, "Игрок " + uid, 1740000000))
        con.execute(
            "UPDATE players SET coins=?,xp=?,badge=?,daily_at=?,work_at=? "
            "WHERE platform=? AND user_id=?",
            (coins, xp, "Почётный игрок", 1750000000, 1750000030,
             platform, uid))
    cols = {r["name"] for r in con.execute("PRAGMA table_info(players)")}
    for col, value in (
        ("bank_coins", 1200),
        ("daily_streak", 7),
        ("games_won", 4),
    ):
        if col in cols:
            con.execute(
                f"UPDATE players SET {col}=? WHERE platform=? AND user_id=?",
                (value, platform, "101"))
    con.execute(
        "INSERT INTO purchases (platform,user_id,item_key,purchased_at) "
        "VALUES (?,?,?,?)", (platform, "101", "vip", 1760000000))
    con.execute(
        "INSERT INTO transfers (platform,from_user_id,to_user_id,amount,created_at) "
        "VALUES (?,?,?,?,?)",
        (platform, "101", "202", 42, 1760000010))
print("CREATED v1.0 database:", platform)
'''

NEW_STAGE = r'''
import sys
from datetime import datetime, timezone
from pathlib import Path
from kverato.db import Database
from kverato.operations import (
    snapshot, verify_sqlite, verify_checksum, daily_prestart_backup
)

path = Path(sys.argv[1])
backup = path.with_name("before_upgrade.sqlite3")
target, digest = snapshot(path, backup)
assert target == backup and len(digest) == 64
assert verify_sqlite(backup)[0] and verify_checksum(backup)
assert not verify_checksum(path), "Original should not have a .sha256 sidecar"

directory = path.parent / "prestart"
for day in (18, 19, 20):
    result = daily_prestart_backup(
        path, directory,
        now=datetime(2026, 11, day, tzinfo=timezone.utc),
        keep=2)
    assert result is not None and verify_checksum(result)
files = sorted(directory.glob("kverato_prestart_*.sqlite3"))
assert [p.name for p in files] == [
    "kverato_prestart_20261119.sqlite3",
    "kverato_prestart_20261120.sqlite3",
], files
assert all(verify_sqlite(item)[0] and verify_checksum(item) for item in files)
assert daily_prestart_backup(
    path, directory, now=datetime(2026, 11, 20, tzinfo=timezone.utc),
    keep=2) == files[-1]

for keep in (0, -1, 366, True, 1.5):
    try:
        daily_prestart_backup(path, directory, keep=keep)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid keep={keep!r} was accepted")

# Damaged hash must stop the next startup backup instead of being trusted.
sidecar = files[-1].with_name(files[-1].name + ".sha256")
original = sidecar.read_bytes()
sidecar.write_text("0" * 64, encoding="ascii")
assert not verify_checksum(files[-1])
try:
    daily_prestart_backup(
        path, directory, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
except RuntimeError:
    pass
else:
    raise AssertionError("Corrupt backup hash was accepted")
finally:
    sidecar.write_bytes(original)
assert verify_checksum(files[-1])
assert not daily_prestart_backup(path.parent / "missing.sqlite3",
                                 directory)

# Upgrade and rerun initialization (migration must be idempotent).
db = Database(path)
db = Database(path)
assert verify_sqlite(path)[0]
with db.connect() as con:
    existing = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"weekly_rewards", "savings_goals"}.issubset(existing)
    assert con.execute(
        "SELECT COUNT(*) FROM players WHERE user_id IN ('101','202')"
    ).fetchone()[0] == 2
    assert tuple(con.execute(
        "SELECT coins, xp FROM players WHERE user_id='101'"
    ).fetchone()) == (4321, 640), "Legacy coins or XP were lost"
    assert con.execute(
        "SELECT COUNT(*) FROM purchases WHERE user_id='101'"
    ).fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM transfers WHERE from_user_id='101'"
    ).fetchone()[0] == 1
print("UPGRADED and verified:", path.name)
'''


def run(stage: str, directory: Path, dbpath: Path, platform: str = "") -> None:
    args = [sys.executable, "-c", stage, str(dbpath)]
    if platform:
        args.append(platform)
    result = subprocess.run(
        args, cwd=directory, capture_output=True, text=True,
        encoding="utf-8", timeout=45, check=False)
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise AssertionError(
            f"Stage failed, exit={result.returncode}, cwd={directory}")
    print(result.stdout.strip())


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_snapshot(dbpath: Path, original=None):
    """Take all rows in original table columns, including older player fields."""
    result = {}
    with sqlite3.connect(dbpath) as con:
        tables = [
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        ]
        if original is not None:
            missing = set(original) - set(tables)
            assert not missing, f"Tables removed during upgrade: {missing}"
            tables = list(original)
        for name in tables:
            columns = (original[name][0] if original is not None else [
                col[1] for col in con.execute(
                    f"PRAGMA table_info({quote(name)})")
            ])
            fields = ",".join(map(quote, columns))
            rows = con.execute(
                f"SELECT {fields} FROM {quote(name)}").fetchall()
            rows = sorted((tuple(row) for row in rows), key=repr)
            result[name] = (columns, rows)
    return result


def check_one(platform: str, zip_name: str, zip_tag: str) -> None:
    archive = ROOT / "releases" / "v1.0" / zip_name
    with tempfile.TemporaryDirectory(prefix="kverato_upgrade_") as dirname:
        temp = Path(dirname)
        legacy = temp / "legacy"
        package = legacy / "kverato"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        with zipfile.ZipFile(archive) as source:
            inner = f"KVERATO-{zip_tag}-v1.0/kverato/db.py"
            assert inner in source.namelist(), f"Missing original module {inner}"
            (package / "db.py").write_bytes(source.read(inner))
        dbpath = temp / "players.sqlite3"
        run(OLD_STAGE, legacy, dbpath, platform)
        old = table_snapshot(dbpath)
        assert old["players"][1] and old["purchases"][1] and old["transfers"][1]
        assert "weekly_rewards" not in old and "savings_goals" not in old, (
            "Expected genuine pre-v1.1 release database")
        run(NEW_STAGE, ROOT / "bots" / platform, dbpath)
        upgraded = table_snapshot(dbpath, old)
        if old != upgraded:
            failures = [name for name in old if old[name] != upgraded[name]]
            raise AssertionError(f"Lost v1.0 records on {platform}: {failures}")

        backup = temp / "before_upgrade.sqlite3"
        backed = table_snapshot(backup, old)
        assert backed == old, "Pre-upgrade snapshot did not preserve v1.0 rows"
        with sqlite3.connect(dbpath) as con:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert con.execute("PRAGMA foreign_key_check").fetchone() is None
        print(
            f"OK {platform}: {len(old)} original tables, "
            "all rows preserved, backups verified, migration idempotent")


def main() -> int:
    for platform, (filename, tag) in RELEASES.items():
        check_one(platform, filename, tag)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, AssertionError, sqlite3.Error,
            subprocess.TimeoutExpired, zipfile.BadZipFile) as exc:
        print(f"UPGRADE FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
