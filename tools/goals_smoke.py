#!/usr/bin/env python3
"""Проверка личных целей накопления на VK, Telegram и Discord без сетевых API."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("vk", "telegram", "discord")

SCENARIO = r'''
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from kverato.db import Database
from kverato.service import BotService, Context
from kverato.v07 import MAX_WALLET

clock = [1_800_000_000]
with TemporaryDirectory() as tmp:
    path = Path(tmp) / "game.sqlite3"
    db = Database(path)
    game = BotService(db, clock=lambda: clock[0])
    ctx = Context(platform=PLATFORM, user_id="101", chat_id="dm", name="Тестер")
    assert game.handle(ctx, "/start")

    def read():
        with db.connect() as con:
            p = con.execute(
                "SELECT coins, bank_coins, xp, daily_streak FROM players "
                "WHERE platform=? AND user_id=?",
                (PLATFORM, ctx.user_id)).fetchone()
            goal = con.execute(
                "SELECT target_coins FROM savings_goals "
                "WHERE platform=? AND user_id=?",
                (PLATFORM, ctx.user_id)).fetchone()
            return tuple(p), goal["target_coins"] if goal else None

    assert "не установлена" in game.handle(ctx, "цель")
    assert read()[1] is None
    before = read()

    for command in (
        "/goal 0", "/goal -10", "/goal +10", "/goal 1.5",
        "/goal 1000000000001", "/goal 999999999999999999999",
        "/goal １２３", "/goal set", "/goal set 100 extra",
        "/goal clear extra", "/goal abc",
    ):
        assert game.handle(ctx, command), command
        assert read()[1] is None, command

    assert "Новая цель" in game.handle(ctx, "цель 5000")
    current, target = read()
    assert target == 5000
    assert current[0] == 100
    assert "2%" in game.handle(ctx, "цель")
    for command in ("/goal", "накопить", "/goal status", "/goal show"):
        reply = game.handle(ctx, command)
        assert "5 000" in reply and "2%" in reply, (command, reply)
        assert read()[1] == 5000
        assert read()[0][0] == 100

    assert "Ежедневный бонус" in game.handle(ctx, "/daily")
    assert "6%" in game.handle(ctx, "/goal")
    assert read()[0][0] == 300
    assert read()[0][3] == 1

    # Bank balance does not count toward savings progress.
    with db.connect() as con:
        con.execute("UPDATE players SET bank_coins=100000 WHERE platform=? AND user_id=?",
                    (PLATFORM, ctx.user_id))
    assert "6%" in game.handle(ctx, "/goal")

    assert "Новая цель" in game.handle(ctx, "goal set 250")
    completed = game.handle(ctx, "/goal")
    assert "100%" in completed and "Цель достигнута" in completed, completed
    assert read()[0][0] == 300
    assert read()[0][1] == 100000

    assert "удалена" in game.handle(ctx, "/goal clear")
    assert read()[1] is None
    assert "нет цели" in game.handle(ctx, "цель сбросить")
    assert read()[1] is None

    # Concurrent changes must never create duplicate rows.
    amounts = [1000 + n * 300 for n in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        replies = list(executor.map(
            lambda value: game.handle(ctx, f"/goal set {value}"), amounts
        ))
    assert all("Новая цель" in reply for reply in replies), replies
    with db.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM savings_goals WHERE platform=? AND user_id=?",
            (PLATFORM, ctx.user_id)).fetchone()[0]
    assert count == 1
    assert read()[1] in amounts

    other = Context(platform=PLATFORM, user_id="202", chat_id="dm", name="Друг")
    assert "не установлена" in game.handle(other, "/goal")
    assert "Новая цель" in game.handle(other, "/goal 8500")
    assert read()[1] in amounts

    other_world = Context(platform="another-platform", user_id="101",
                          chat_id="dm", name="Другой мир")
    assert "не установлена" in game.handle(other_world, "/goal")
    assert "Новая цель" in game.handle(other_world, "/goal 700")
    assert read()[1] in amounts

    group = Context(platform=PLATFORM, user_id="101", chat_id="group",
                    name="Тестер", group=True)
    assert game.handle(group, "цель") is None  # Ordinary group speech stays silent.
    assert "ЦЕЛЬ НАКОПЛЕНИЯ" in game.handle(group, "/goal")
    assert read()[0][0] == 300

    # Existing coins and claimed rewards survive adding the new table.
    old_target = read()[1]
    with db.connect() as con:
        con.execute("DROP TABLE savings_goals")
    db = Database(path)
    game = BotService(db, clock=lambda: clock[0])
    assert read()[1] is None
    assert read()[0][0] == 300
    assert read()[0][3] == 1
    assert "Новая цель" in game.handle(ctx, f"/goal set {old_target}")
    db = Database(path)
    game = BotService(db, clock=lambda: clock[0])
    assert read()[1] == old_target

    # Storage-level constraint also rejects bad goals if service validation is bypassed.
    try:
        with db.transaction() as con:
            con.execute(
                "UPDATE savings_goals SET target_coins=? "
                "WHERE platform=? AND user_id=?",
                (0, PLATFORM, ctx.user_id),
            )
        raise AssertionError("Invalid amount unexpectedly accepted")
    except sqlite3.IntegrityError:
        pass

    assert read()[0][0] == 300

print(f"OK {PLATFORM}: progress, validation, wallet vs bank, concurrency, migration")
'''


def main() -> int:
    for platform in PLATFORMS:
        folder = ROOT / "bots" / platform
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"PLATFORM = {platform!r}\n" + SCENARIO],
                cwd=folder, capture_output=True, text=True, encoding="utf-8",
                timeout=45, check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"Тайм-аут: {platform}", file=sys.stderr)
            return 1
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
