#!/usr/bin/env python3
"""Проверка команды «награды» на VK, Telegram и Discord без API и токенов."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("vk", "telegram", "discord")

SCENARIO = r'''
from pathlib import Path
from tempfile import TemporaryDirectory

from kverato.db import Database
from kverato.service import ALIASES, BotService, Context

assert ALIASES["rewards"] == ALIASES["награды"] == ALIASES["таймеры"]

time_now = [1_800_000_000]
with TemporaryDirectory() as folder:
    db = Database(Path(folder) / "players.sqlite3")
    service = BotService(db, clock=lambda: time_now[0], randbelow=lambda n: 0)
    player = Context(platform=PLATFORM, user_id="101", chat_id="private", name="Тестер")
    assert service.handle(player, "/start")

    def state():
        with db.connect() as con:
            p = con.execute(
                "SELECT coins, daily_at, daily_streak, work_at, work_count "
                "FROM players WHERE platform=? AND user_id=?",
                (PLATFORM, player.user_id)).fetchone()
            w = con.execute(
                "SELECT claimed_at FROM weekly_rewards WHERE platform=? AND user_id=?",
                (PLATFORM, player.user_id)).fetchone()
            return tuple(p), w["claimed_at"] if w else None

    before = state()
    assert before == ((100, 0, 0, 0, 0), None), before
    for command in ("награды", "/rewards", "таймеры"):
        result = service.handle(player, command)
        assert result.startswith("🎁 КВЕРАТО | НАГРАДЫ И ТАЙМЕРЫ"), result
        assert result.count("✅ доступно") == 3, result
        assert "Ежедневный" in result and "Недельный" in result and "Работа" in result
        assert state() == before, "Просмотр изменил экономику"

    assert "Ежедневный бонус" in service.handle(player, "/daily")
    assert "+1 000" in service.handle(player, "/weekly")
    assert "Работа выполнена" in service.handle(player, "/work")
    current = state()
    assert current[0][0] > before[0][0]
    assert current[0][1] == time_now[0]
    assert current[0][3] == time_now[0]
    assert current[1] == time_now[0]

    waiting = service.handle(player, "/rewards")
    assert waiting.count("⏳ через") == 3, waiting
    assert state() == current, "Просмотр ошибочно повторил начисление"
    time_now[0] += 30 * 60
    middle = service.handle(player, "награды")
    assert "💼 Работа — ✅ доступно" in middle, middle
    assert "🎁 Ежедневный бонус — ⏳" in middle, middle
    assert "🗓 Недельный бонус (+1 000) — ⏳" in middle, middle
    assert state() == current

    time_now[0] += 24 * 3600 - 30 * 60
    after_day = service.handle(player, "таймеры")
    assert "🎁 Ежедневный бонус — ✅ доступно" in after_day
    assert "💼 Работа — ✅ доступно" in after_day
    assert "🗓 Недельный бонус (+1 000) — ⏳" in after_day
    assert state() == current

    time_now[0] += 6 * 24 * 3600
    after_week = service.handle(player, "/rewards")
    assert after_week.count("✅ доступно") == 3, after_week
    assert state() == current

    stranger = Context(platform=PLATFORM, user_id="102", chat_id="private", name="Друг")
    unrelated = service.handle(stranger, "награды")
    assert unrelated.count("✅ доступно") == 3, unrelated
    another_platform = Context(platform=PLATFORM + "_other", user_id="101",
                               chat_id="private", name="Другой мир")
    assert service.handle(another_platform, "/rewards").count("✅ доступно") == 3

    group_context = Context(platform=PLATFORM, user_id="101", chat_id="group",
                            name="Тестер", group=True)
    assert service.handle(group_context, "награды") is None
    assert "НАГРАДЫ И ТАЙМЕРЫ" in service.handle(group_context, "/rewards")
    assert state() == current

    # Opening the database again should preserve claims, with no extra writes.
    reloaded = BotService(Database(db.path), clock=lambda: time_now[0])
    assert reloaded.handle(player, "/rewards").count("✅ доступно") == 3
    assert state() == current

print(f"OK {PLATFORM}: ready/waiting timers, exact cooldowns, no payouts on views")
'''


def main() -> int:
    for platform in PLATFORMS:
        root = ROOT / "bots" / platform
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"PLATFORM = {platform!r}\n" + SCENARIO],
                cwd=root, capture_output=True, text=True, encoding="utf-8",
                timeout=45, check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"Тайм-аут тестирования {platform}", file=sys.stderr)
            return 1
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
