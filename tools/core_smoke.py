#!/usr/bin/env python3
"""Проверки игрового ядра КВЕРАТО без токенов и внешних библиотек."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"vk": "vk", "telegram": "telegram", "discord": "discord"}

SCRIPT = r'''
import tempfile
from pathlib import Path
from kverato.db import Database
from kverato.service import BotService, Context
from kverato.operations import snapshot, verify_sqlite, verify_checksum

with tempfile.TemporaryDirectory() as folder:
    dbpath = Path(folder) / "players.sqlite3"
    database = Database(dbpath)
    game = BotService(database)
    player = Context(platform=PLATFORM, user_id="player-101", chat_id="test-room", name="Player")
    assert game.handle(player, "start")
    assert game.handle(player, "profile")
    with database.connect() as con:
        before = con.execute(
            "SELECT coins FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, "player-101")).fetchone()[0]
    assert game.handle(player, "daily")
    with database.connect() as con:
        after = con.execute(
            "SELECT coins FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, "player-101")).fetchone()[0]
    assert after > before, (before, after)
    assert game.handle(player, "daily")
    with database.connect() as con:
        repeat = con.execute(
            "SELECT coins FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, "player-101")).fetchone()[0]
    assert repeat == after, "Daily bonus charged more than once"
    assert game.handle(player, "balance")
    assert verify_sqlite(dbpath)[0]
    backup = Path(folder) / "backup.sqlite3"
    snapshot(dbpath, backup)
    assert verify_sqlite(backup)[0]
    assert verify_checksum(backup)
print("OK " + PLATFORM + ": SQLite, daily, balance, backup")
'''

def main() -> int:
    for directory, platform in PLATFORMS.items():
        source = ROOT / "bots" / directory
        if not (source / "kverato" / "service.py").is_file():
            print(f"Missing game source: {source}", file=sys.stderr)
            return 1
        code = f"PLATFORM = {platform!r}\n" + SCRIPT
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=source,
            capture_output=True, text=True, encoding="utf-8", timeout=35)
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
