"""Private owner-only Telegram bot status; all queries are read-only.

Do not include bot tokens, sqlite paths, player identifiers, usernames or
private balances in operational messages.
"""
from __future__ import annotations
from contextlib import closing
import time


def status_snapshot(db, uptime: float) -> dict[str, int | bool]:
    with closing(db.connect()) as con:
        healthy = con.execute('PRAGMA quick_check(1)').fetchone()[0] == 'ok'
        healthy = healthy and con.execute('PRAGMA foreign_key_check').fetchone() is None
        players = con.execute("SELECT COUNT(*) FROM players WHERE platform='telegram'").fetchone()[0]
        duels = con.execute("SELECT COUNT(*) FROM duels WHERE platform='telegram' AND status='pending'").fetchone()[0]
        friends = con.execute("SELECT COUNT(*) FROM tg_friend_links WHERE status='pending' AND expires_at > ?", (int(time.time()),)).fetchone()[0]
        updates = con.execute("SELECT COUNT(*) FROM tg_processed_updates WHERE status='uncertain'").fetchone()[0]
    return {'healthy': healthy, 'players': players, 'duels': duels,
            'friends': friends, 'uncertain': updates,
            'uptime_minutes': max(0, int(uptime // 60))}


def format_status(data: dict[str, int | bool], version: str) -> str:
    return (f'🛠 КВЕРАТО TG v{version} | Состояние\n\n'
            '🟢 Telegram: подключён (Long Poll)\n'
            f'{"✅" if data["healthy"] else "⚠️"} SQLite: {"целостность OK" if data["healthy"] else "требует проверки"}\n'
            f'👥 Игроков Telegram: {data["players"]}\n'
            f'⚔️ Ожидают решения дуэли: {data["duels"]}\n'
            f'🤝 Активных заявок в друзья: {data["friends"]}\n'
            f'⚠️ Неопределённых событий: {data["uncertain"]}\n'
            f'⏱ Время работы: {data["uptime_minutes"]} мин.\n\n'
            'Данные доступны только владельцу в личном чате.')
