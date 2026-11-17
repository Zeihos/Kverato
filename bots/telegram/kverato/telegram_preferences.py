"""Per-player Telegram display preferences. Stored apart from the game economy."""
from __future__ import annotations

import time
from contextlib import closing
from .db import Database


class TelegramPreferences:
    _fields = {'cards': 'cards_enabled', 'avatar': 'avatar_enabled'}

    def __init__(self, db: Database):
        self.db = db
        with self.db.transaction() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS tg_player_preferences (
                user_id INTEGER PRIMARY KEY,
                cards_enabled INTEGER NOT NULL DEFAULT 1 CHECK(cards_enabled IN (0,1)),
                avatar_enabled INTEGER NOT NULL DEFAULT 1 CHECK(avatar_enabled IN (0,1)),
                updated_at INTEGER NOT NULL DEFAULT 0
            )''')

    def get(self, user_id: int) -> dict[str, bool]:
        with closing(self.db.connect()) as con:
            row = con.execute('SELECT cards_enabled, avatar_enabled FROM tg_player_preferences WHERE user_id=?',
                              (user_id,)).fetchone()
        return {'cards': bool(row['cards_enabled']) if row else True,
                'avatar': bool(row['avatar_enabled']) if row else True}

    def set(self, user_id: int, feature: str, enabled: bool) -> dict[str, bool]:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError('Invalid user ID')
        field = self._fields.get(feature)
        if field is None or type(enabled) is not bool:
            raise ValueError('Invalid preference')
        with self.db.transaction() as con:
            con.execute('INSERT OR IGNORE INTO tg_player_preferences(user_id) VALUES(?)', (user_id,))
            # SQL identifier comes exclusively from static allowlist above.
            con.execute(f'UPDATE tg_player_preferences SET {field}=?, updated_at=? WHERE user_id=?',
                        (int(enabled), int(time.time()), user_id))
        return self.get(user_id)
