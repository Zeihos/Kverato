"""Durable duplicate-delivery guard for VK Long Poll events.

A VK event ID is stable for one conversation. Claims are committed before
running economic commands, so concurrent/redelivered events are not replayed.
A claimed event is intentionally never retried automatically after a crash:
we cannot safely know whether its economic transaction already committed.
"""
from __future__ import annotations

import time

from .db import Database


class VKDeliveryGuard:
    def __init__(self, db: Database, clock=time.time):
        self.db = db
        self.clock = clock

    @staticmethod
    def key(peer_id: object, conversation_message_id: object,
            global_message_id: object = None) -> str | None:
        """Use VK's per-conversation id, then fall back to the global id."""
        try:
            peer = int(peer_id)
            local = int(conversation_message_id or 0)
            global_id = int(global_message_id or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        if peer <= 0:
            return None
        if local > 0:
            return f'{peer}:c{local}'
        if global_id > 0:
            return f'{peer}:g{global_id}'
        return None

    @staticmethod
    def keys(peer_id: object, conversation_message_id: object,
             global_message_id: object = None) -> tuple[str, ...]:
        """Return both VK message ID aliases when available."""
        try:
            peer = int(peer_id)
            local = int(conversation_message_id or 0)
            global_id = int(global_message_id or 0)
        except (TypeError, ValueError, OverflowError):
            return ()
        if peer <= 0:
            return ()
        keys = []
        if local > 0:
            keys.append(f'{peer}:c{local}')
        if global_id > 0:
            keys.append(f'{peer}:g{global_id}')
        return tuple(keys)

    def claim_aliases(self, keys: tuple[str, ...]) -> bool:
        """Claim all aliases atomically, preventing alternate-ID redelivery."""
        if not keys:
            return False
        with self.db.transaction() as con:
            existing = None
            for key in keys:
                existing = con.execute('SELECT status FROM vk_processed_events WHERE event_key=?',
                                       (key,)).fetchone()
                if existing:
                    break
            now = int(self.clock())
            if existing:
                # Link previously stored single-ID records: if a redelivery
                # carries both IDs, link the missing alias *without* executing.
                for key in keys:
                    con.execute('INSERT OR IGNORE INTO vk_processed_events(event_key,received_at,status) '
                                'VALUES(?,?,?)', (key, now, existing['status']))
                return False
            for key in keys:
                con.execute('INSERT INTO vk_processed_events(event_key,received_at,status) '
                            "VALUES(?,?,'claimed')", (key, now))
        return True

    def finish_aliases(self, keys: tuple[str, ...], success: bool) -> None:
        if not keys:
            return
        with self.db.transaction() as con:
            for key in keys:
                con.execute('UPDATE vk_processed_events SET status=? '
                            "WHERE event_key=? AND status='claimed'",
                            ('done' if success else 'uncertain', key))

    def claim(self, key: str | None) -> bool:
        if key is None:
            return False  # Fail closed: without a stable ID, monetary replay is unsafe.
        with self.db.transaction() as con:
            added = con.execute('''INSERT OR IGNORE INTO vk_processed_events
                (event_key,received_at,status) VALUES(?,?,'claimed')''',
                (key, int(self.clock()))).rowcount
        return bool(added)

    def finish(self, key: str | None, success: bool) -> None:
        if key is None:
            return
        with self.db.transaction() as con:
            con.execute('''UPDATE vk_processed_events SET status=?
                WHERE event_key=? AND status='claimed' ''',
                ('done' if success else 'uncertain', key))

    def prune(self, days: int = 30) -> int:
        """Call on start; keep recent events including failed/uncertain ones."""
        with self.db.transaction() as con:
            return con.execute('DELETE FROM vk_processed_events WHERE received_at < ?',
                               (int(self.clock()) - days * 86400,)).rowcount
