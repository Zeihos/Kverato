#!/usr/bin/env python3
"""Регрессия страниц и фильтров личной выписки КВЕРАТО.

Сценарий выполняется на временных SQLite-базах каждой платформы, без API.
"""
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
from kverato.service import BotService, Context

now = [1_800_000_000]
with TemporaryDirectory() as folder:
    path = Path(folder) / "player.sqlite3"
    db = Database(path)
    game = BotService(db, clock=lambda: now[0])
    a = Context(platform=PLATFORM, user_id="101", chat_id="private-a", name="Отправитель")
    b = Context(platform=PLATFORM, user_id="202", chat_id="private-b", name="Получатель")
    stranger = Context(platform=PLATFORM, user_id="303", chat_id="private-c", name="Сосед")
    for person in (a, b, stranger):
        assert game.handle(person, "/start")
    with db.transaction() as con:
        con.execute(
            "UPDATE players SET coins=50000 WHERE platform=? AND user_id=?",
            (PLATFORM, a.user_id))
        con.execute(
            "UPDATE players SET coins=5000 WHERE platform=? AND user_id=?",
            (PLATFORM, b.user_id))

    # 14 incoming + 14 outgoing transfers in an alternating timeline.
    for number in range(28):
        if number % 2:
            assert "Переведено" in game.handle(b, "/transfer 101 5")
        else:
            assert "Переведено" in game.handle(a, "/transfer 202 10")
        now[0] += 60
    # 3 purchases, made AFTER the transfers, appear before them in combined mode.
    for badge in ("vip", "star", "legend"):
        assert "приобретён" in game.handle(a, f"/buy {badge}")
        now[0] += 60

    def balance():
        with db.connect() as con:
            return [(r["user_id"], r["coins"], r["bank_coins"]) for r in
                    con.execute(
                        "SELECT user_id,coins,bank_coins FROM players "
                        "WHERE platform=? ORDER BY user_id", (PLATFORM,))]

    before = balance()

    def entries(message):
        return [line for line in message.splitlines() if line.startswith("• ")]

    combined = [game.handle(a, "/statement" if page == 1 else f"/statement {page}")
                for page in range(1, 5)]
    assert len(entries(combined[0])) == 10 and "Страница 1/4" in combined[0]
    assert len(entries(combined[1])) == 10 and "Страница 2/4" in combined[1]
    assert len(entries(combined[2])) == 10 and "Страница 3/4" in combined[2]
    assert len(entries(combined[3])) == 1 and "Страница 4/4" in combined[3]
    assert sum("Значок" in line for line in entries(combined[0])) == 3
    all_lines = [line for page in combined for line in entries(page)]
    assert len(set(all_lines)) == 31, "Page entries overlap or were lost"

    incoming = game.handle(a, "/statement входящие")
    incoming2 = game.handle(a, "/statement incoming 2")
    assert len(entries(incoming)) == 10 and len(entries(incoming2)) == 4
    assert all("↙ +" in line for line in entries(incoming + "\n" + incoming2))
    outgoing = game.handle(a, "выписка исходящие 2")
    assert len(entries(outgoing)) == 4
    assert all("↗ −" in line for line in entries(outgoing))
    transfers = game.handle(a, "операции переводы 3")
    assert len(entries(transfers)) == 8
    assert not any("Значок" in line for line in entries(transfers))
    purchases = game.handle(a, "/statement покупки")
    assert len(entries(purchases)) == 3
    assert all("Значок" in line for line in entries(purchases))
    assert "Страница 1/1" in purchases
    assert "Страница 1/4" in game.handle(a, "/statement все 1")
    assert "Страница 1/4" in game.handle(a, "/statement all")

    for wrong in (
        "/statement 0", "/statement 21", "/statement -1",
        "/statement 99999999999999999999", "/statement incoming nope",
        "/statement incoming 1 2", "/statement １", "/statement 1 OR 1=1",
        "/statement 1;DROP", "/statement outgoing 02",
    ):
        answer = game.handle(a, wrong)
        assert "🧾 Выписка:" in answer, (wrong, answer)
        assert not entries(answer)
    assert "доступно страниц" in game.handle(a, "/statement покупки 2")

    assert "Пока нет переводов" in game.handle(stranger, "/statement")
    other_world = Context(platform=PLATFORM + "-elsewhere", user_id="101",
                          chat_id="dm", name="Другой мир")
    assert "Пока нет переводов" in game.handle(other_world, "/statement")
    public = Context(platform=PLATFORM, user_id=a.user_id,
                     chat_id="public", name=a.name, group=True)
    assert game.handle(public, "выписка") is None
    reply = game.handle(public, "/statement входящие")
    assert "только в сообщениях с ботом" in reply
    assert "Переведено" not in reply and not entries(reply)

    assert balance() == before, "Read-only statement changed player balances"
    game = BotService(Database(path), clock=lambda: now[0])
    assert entries(game.handle(a, "/statement покупки")) == entries(purchases)
    assert balance() == before
print(f"OK {PLATFORM}: 31 ledger entries, pagination, filters, privacy, persistence")
'''


def main() -> int:
    for platform in PLATFORMS:
        folder = ROOT / "bots" / platform
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"PLATFORM = {platform!r}\n" + SCENARIO],
                cwd=folder, capture_output=True, text=True, encoding="utf-8",
                timeout=50, check=False)
        except subprocess.TimeoutExpired:
            print(f"Тайм-аут проверки: {platform}", file=sys.stderr)
            return 1
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
