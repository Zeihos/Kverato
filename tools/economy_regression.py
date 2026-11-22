#!/usr/bin/env python3
"""Регрессия переводов, покупок и приватной выписки на всех платформах.

Используются только временные SQLite-базы; API и реальные токены не требуются.
"""
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
from kverato.service import BotService, Context, SHOP
from kverato.v07 import MAX_WALLET

assert SHOP["vip"][1] == 1500
time_now = [1_800_000_000]
with TemporaryDirectory() as directory:
    path = Path(directory) / "temporary.sqlite3"
    db = Database(path)
    game = BotService(db, clock=lambda: time_now[0])
    sender = Context(platform=PLATFORM, user_id="101", chat_id="dm", name="Отправитель")
    recipient = Context(platform=PLATFORM, user_id="202", chat_id="dm", name="Получатель")
    outsider = Context(platform=PLATFORM, user_id="303", chat_id="dm", name="Другой игрок")
    for ctx in (sender, recipient, outsider):
        assert game.handle(ctx, "/start")

    def money():
        with db.connect() as con:
            return {
                row["user_id"]: row["coins"]
                for row in con.execute("SELECT user_id,coins FROM players WHERE platform=?",
                                       (PLATFORM,))
            }

    def count(table):
        with db.connect() as con:
            return con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE platform=?", (PLATFORM,)
            ).fetchone()[0]

    initial = money()
    assert all(initial[key] == 100 for key in ("101", "202", "303")), initial
    empty = game.handle(sender, "/statement")
    assert "Пока нет переводов и покупок" in empty, empty
    assert money() == initial

    assert "Переведено" in game.handle(sender, "/transfer 202 30")
    assert money() == {"101": 70, "202": 130, "303": 100}
    assert count("transfers") == 1

    out = game.handle(sender, "/statement")
    inside = game.handle(recipient, "выписка")
    assert "↗ −30" in out and "игрока 202" in out, out
    assert "↙ +30" in inside and "игрока 101" in inside, inside
    assert "Пока нет" in game.handle(outsider, "/statement")
    assert money() == {"101": 70, "202": 130, "303": 100}

    # No ghost players, self-transfers, invalid amounts or overdrafts.
    for text in ("/transfer 404 10", "/transfer 101 10", "/transfer 202 0",
                 "/transfer 202 -10", "/transfer 202 1000000", "/transfer 202 100"):
        assert "Переведено" not in game.handle(sender, text), text
    assert count("transfers") == 1
    assert money() == {"101": 70, "202": 130, "303": 100}

    # Group command must not reveal a private account history.
    group = Context(platform=PLATFORM, user_id="101", chat_id="public-room",
                    name="Отправитель", group=True)
    assert game.handle(group, "выписка") is None
    private = game.handle(group, "/statement")
    assert "только в сообщениях с ботом" in private and "30" not in private
    assert count("transfers") == 1
    assert money()["101"] == 70

    # A user may buy the same badge only once under simultaneous requests.
    with db.connect() as con:
        con.execute("UPDATE players SET coins=5000 WHERE platform=? AND user_id=?",
                    (PLATFORM, sender.user_id))
    with ThreadPoolExecutor(max_workers=8) as pool:
        purchase_answers = list(pool.map(lambda _: game.handle(sender, "/buy vip"),
                                         range(8)))
    assert sum("приобретён" in value for value in purchase_answers) == 1, purchase_answers
    assert count("purchases") == 1
    assert money()["101"] == 3500
    assert "Значок «VIP»" in game.handle(sender, "/statement")
    assert "Значок «VIP»" not in game.handle(recipient, "/statement")

    # Concurrent transfers conserve the combined balance exactly.
    with ThreadPoolExecutor(max_workers=8) as pool:
        transfer_answers = list(pool.map(lambda _: game.handle(sender, "/transfer 202 100"),
                                         range(8)))
    assert sum("Переведено" in value for value in transfer_answers) == 8, transfer_answers
    assert count("transfers") == 9
    accounts = money()
    assert accounts["101"] == 2700 and accounts["202"] == 930, accounts
    assert sum(accounts.values()) == 2700 + 930 + 100
    assert len([line for line in game.handle(sender, "/statement").splitlines()
                if line.startswith("• ")]) == 10

    # Full recipient wallet rejects a transfer without debiting the sender.
    with db.connect() as con:
        con.execute("UPDATE players SET coins=? WHERE platform=? AND user_id=?",
                    (MAX_WALLET - 10, PLATFORM, recipient.user_id))
    before = money()
    assert "лимит кошелька" in game.handle(sender, "/transfer 202 20")
    assert money() == before
    assert count("transfers") == 9

    # No cross-user or cross-platform disclosure.
    another_platform = Context(platform=PLATFORM + "_other", user_id="101",
                               chat_id="dm", name="Игрок")
    assert "Пока нет" in game.handle(another_platform, "/statement")
    assert "↗ −100" not in game.handle(outsider, "/statement")
    assert money() == before

    # Persistence of all transfers and purchases after opening the DB anew.
    game = BotService(Database(path), clock=lambda: time_now[0])
    assert "↗ −100" in game.handle(sender, "/statement")
    assert "Значок «VIP»" in game.handle(sender, "операции")
    assert count("purchases") == 1 and count("transfers") == 9
    assert money() == before

print(f"OK {PLATFORM}: transfers, concurrent purchases, balance preservation, private statement")
'''


def main() -> int:
    for platform in PLATFORMS:
        folder = ROOT / "bots" / platform
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"PLATFORM = {platform!r}\n" + SCENARIO],
                cwd=folder, capture_output=True, text=True, encoding="utf-8",
                timeout=60, check=False,
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
