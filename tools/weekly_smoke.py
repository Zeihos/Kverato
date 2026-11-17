#!/usr/bin/env python3
"""Регрессия еженедельных наград на трёх ядрах КВЕРАТО без доступа к API."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("vk", "telegram", "discord")

SCENARIO = r'''
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from kverato.db import Database
from kverato.service import BotService, Context
from kverato.v07 import MAX_WALLET

now = [1_800_000_000]
with TemporaryDirectory() as folder:
    filename = Path(folder) / "legacy.sqlite3"
    db = Database(filename)
    game = BotService(db, clock=lambda: now[0])
    owner = Context(platform=PLATFORM, user_id="owner-1",
                    chat_id="private-1", name="Игрок")
    assert game.handle(owner, "/start")
    assert "бонус" in game.handle(owner, "/daily").lower()

    with db.connect() as con:
        original = con.execute(
            "SELECT coins,daily_streak FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()
        assert original["coins"] == 300
        assert original["daily_streak"] == 1
        # Legacy v1.0 databases did not contain the v1.1 weekly ledger.
        con.execute("DROP TABLE weekly_rewards")

    # Upgrading must create the new ledger WITHOUT resetting v1.0 data.
    db = Database(filename)
    game = BotService(db, clock=lambda: now[0])
    with db.connect() as con:
        preserved = con.execute(
            "SELECT coins,daily_streak FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()
        assert tuple(preserved) == (300, 1)
        assert con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='weekly_rewards'"
        ).fetchone()[0] == 1

    # Eight simultaneous requests may create only ONE 1000-coin reward.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: game.handle(owner, "/weekly"), range(8)))
    assert sum("+1 000" in response for response in results) == 1, results
    with db.connect() as con:
        player = con.execute(
            "SELECT coins,daily_streak FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()
        assert tuple(player) == (1300, 1)
        ledger = con.execute(
            "SELECT claimed_at FROM weekly_rewards WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()
        assert ledger["claimed_at"] == now[0]
    assert "через" in game.handle(owner, "неделя")
    now[0] += 7 * 24 * 3600 - 1
    assert "через" in game.handle(owner, "/weekly")
    with db.connect() as con:
        assert con.execute(
            "SELECT coins FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()[0] == 1300
    now[0] += 1
    assert "+1 000" in game.handle(owner, "еженедельно")
    with db.connect() as con:
        assert con.execute(
            "SELECT coins FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()[0] == 2300

    # A full wallet must not consume the once-a-week opportunity.
    other = Context(platform=PLATFORM, user_id="owner-2",
                    chat_id="private-2", name="Друг")
    assert game.handle(other, "/start")
    with db.connect() as con:
        con.execute(
            "UPDATE players SET coins=? WHERE platform=? AND user_id=?",
            (MAX_WALLET - 999, PLATFORM, other.user_id))
    assert "Кошелёк заполнен" in game.handle(other, "/weekly")
    with db.connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM weekly_rewards WHERE platform=? AND user_id=?",
            (PLATFORM, other.user_id)).fetchone()[0] == 0
        con.execute(
            "UPDATE players SET coins=100 WHERE platform=? AND user_id=?",
            (PLATFORM, other.user_id))
    assert "+1 000" in game.handle(other, "/weekly")

    # Platform and account namespaces must remain separate.
    alt = Context(platform="other-platform", user_id=owner.user_id,
                  chat_id="other-chat", name="Другой мир")
    assert "+1 000" in game.handle(alt, "/weekly")

    # Opening upgraded data again retains balances and weekly claims.
    reloaded = Database(filename)
    with reloaded.connect() as con:
        assert con.execute(
            "SELECT coins FROM players WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()[0] == 2300
        assert con.execute(
            "SELECT COUNT(*) FROM weekly_rewards WHERE platform=? AND user_id=?",
            (PLATFORM, owner.user_id)).fetchone()[0] == 1

print("OK " + PLATFORM + ": v1.0 migration, 8 concurrent calls, cooldown, wallet, persistence")
'''


def main() -> int:
    for platform in PLATFORMS:
        root = ROOT / "bots" / platform
        if not (root / "kverato" / "service.py").is_file():
            print(f"Нет игрового ядра: {root}", file=sys.stderr)
            return 1
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"PLATFORM = {platform!r}\n" + SCENARIO],
                cwd=root, capture_output=True, text=True, encoding="utf-8",
                timeout=50, check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"Тайм-аут проверки {platform}", file=sys.stderr)
            return 1
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
