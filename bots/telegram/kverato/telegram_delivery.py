"""Persistent replay protection for Telegram updates.

Claims are persisted *before* gameplay transactions. If a worker dies in the
middle, the update is never replayed blindly: a financial action may have
committed already. Telegram getUpdates offset is not itself sufficient.
"""
from __future__ import annotations
import time
from .db import Database


class TelegramDeliveryGuard:
    def __init__(self, db: Database, clock=time.time):
        self.db, self.clock = db, clock
        with db.transaction() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS tg_processed_updates (
                update_id INTEGER PRIMARY KEY,
                created_at INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('claimed', 'done', 'uncertain'))
            )''')
            con.execute('CREATE INDEX IF NOT EXISTS idx_tg_updates_created_at ON tg_processed_updates(created_at)')

    def claim(self, update_id: int) -> bool:
        if type(update_id) is not int or update_id < 0:
            return False
        with self.db.transaction() as con:
            cur = con.execute('INSERT OR IGNORE INTO tg_processed_updates(update_id,created_at,status) '
                              "VALUES(?,?,'claimed')", (update_id, int(self.clock())))
        return cur.rowcount == 1

    def finish(self, update_id: int, success: bool) -> None:
        with self.db.transaction() as con:
            con.execute('UPDATE tg_processed_updates SET status=? WHERE update_id=? AND status=\'claimed\'',
                        ('done' if success else 'uncertain', update_id))

    def prune(self, days: int = 30) -> int:
        with self.db.transaction() as con:
            return con.execute('DELETE FROM tg_processed_updates WHERE created_at < ?',
                               (int(self.clock()) - days * 86400,)).rowcount
