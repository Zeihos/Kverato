"""Discord-independent event validation, state protection and safe formatting."""
from __future__ import annotations

import re
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from threading import Lock

from .service import ALIASES, Context

_PLATFORM = 'discord'
_VK_MENTION = re.compile(r'\[id([1-9][0-9]{0,18})\|([^\]\n]{1,80})\]')
_SAFE_ARGS = 700


def normalize(raw: str) -> tuple[str, str] | None:
    """Parse a known game command. Never run arbitrary chat as game input."""
    if not isinstance(raw, str) or len(raw) > _SAFE_ARGS + 30:
        return None
    value = raw.strip()
    if not value:
        return None
    first, _, args = value.partition(' ')
    alias = first.lstrip('/!.').casefold()
    command = ALIASES.get(alias)
    if not command or len(args) > _SAFE_ARGS:
        return None
    return command, '/' + alias + ((' ' + args) if args else '')


def format_answer(value: str) -> str:
    """User names remain plain text; no Discord mentions are generated."""
    return _VK_MENTION.sub(lambda match: f'{match.group(2)} (ID {match.group(1)})', value)


def chunks(text: str, limit: int = 1900) -> list[str]:
    """Split long catalogs without losing data or exceeding Discord limits."""
    if limit < 80:
        raise ValueError('Chunk limit too small')
    value = format_answer(text)
    pieces = []
    while value:
        if len(value) <= limit:
            pieces.append(value)
            break
        boundary = value.rfind('\n', 0, limit + 1)
        if boundary < limit // 2:
            boundary = value.rfind(' ', 0, limit + 1)
        if boundary < limit // 2:
            boundary = limit
        pieces.append(value[:boundary])
        value = value[boundary:]
    return pieces or ['Нет данных.']


def make_context(user_id: int, channel_id: int, display_name: str, in_guild: bool,
                 target_id: int | None = None) -> Context:
    if type(user_id) is not int or user_id <= 0 or type(channel_id) is not int or channel_id <= 0:
        raise ValueError('Invalid Discord snowflake')
    name = re.sub(r'[\x00-\x1f\x7f]', ' ', display_name or '')[:60].strip() or f'Игрок {user_id}'
    return Context(_PLATFORM, str(user_id), str(channel_id), name, group=in_guild,
                   admin=False, target_id=str(target_id) if target_id else None)


class InteractionStore:
    """Persistent at-most-once guard. A crash after reserving can drop an action.

    Game actions are never replayed automatically after an unknown network/result
    state. Economic mutations themselves remain transactional in BotService.
    """
    def __init__(self, db):
        self.db = db
        with self.db.transaction() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS discord_interactions (
                interaction_id TEXT PRIMARY KEY, reserved_at INTEGER NOT NULL)''')
            con.execute('''CREATE TABLE IF NOT EXISTS discord_preferences (
                user_id TEXT PRIMARY KEY, cards INTEGER NOT NULL DEFAULT 1
                CHECK(cards IN (0, 1)), avatars INTEGER NOT NULL DEFAULT 1
                CHECK(avatars IN (0, 1)))''')
            # Existing Discord installations have the older cards-only table.
            columns = {r['name'] for r in con.execute('PRAGMA table_info(discord_preferences)')}
            if 'avatars' not in columns:
                con.execute('ALTER TABLE discord_preferences ADD COLUMN avatars INTEGER NOT NULL DEFAULT 1 CHECK(avatars IN (0,1))')

    def reserve(self, interaction_id: int) -> bool:
        if type(interaction_id) is not int or interaction_id <= 0:
            raise ValueError('Invalid interaction ID')
        try:
            with self.db.transaction() as con:
                con.execute('INSERT INTO discord_interactions VALUES (?,?)',
                            (str(interaction_id), int(time.time())))
            return True
        except sqlite3.IntegrityError:
            return False

    def preferences(self, user_id: int) -> dict[str, bool]:
        with closing(self.db.connect()) as con:
            row = con.execute('SELECT cards,avatars FROM discord_preferences WHERE user_id=?',
                              (str(user_id),)).fetchone()
        return {'cards': bool(row['cards']), 'avatars': bool(row['avatars'])} if row else {'cards': True, 'avatars': True}

    def cards_enabled(self, user_id: int) -> bool:
        return self.preferences(user_id)['cards']

    def avatars_enabled(self, user_id: int) -> bool:
        return self.preferences(user_id)['avatars']

    def set_preference(self, user_id: int, preference: str, enabled: bool) -> None:
        if preference not in ('cards', 'avatars') or type(enabled) is not bool:
            raise ValueError('Invalid preference')
        # Explicit allowlist; no client-provided SQL identifier reaches SQLite.
        with self.db.transaction() as con:
            con.execute(f"INSERT INTO discord_preferences(user_id,{preference}) VALUES(?,?) "
              f"ON CONFLICT(user_id) DO UPDATE SET {preference}=excluded.{preference}",
                        (str(user_id), int(enabled)))

    def set_cards(self, user_id: int, enabled: bool) -> None:
        self.set_preference(user_id, 'cards', enabled)

    def prune(self, now: int | None = None) -> int:
        timestamp = int(time.time() if now is None else now) - 30*86400
        with self.db.transaction() as con:
            cursor = con.execute('DELETE FROM discord_interactions WHERE reserved_at < ?',
                                 (timestamp,))
        return cursor.rowcount


class RateLimiter:
    """Per-user request ceiling (default 10 commands per 15 seconds)."""
    def __init__(self, count: int = 10, seconds: float = 15.0, clock=time.monotonic):
        self.count, self.seconds, self.clock = count, seconds, clock
        self._slots = defaultdict(deque)
        self._lock = Lock()

    def allow(self, user_id: int) -> bool:
        now = self.clock()
        with self._lock:
            q = self._slots[user_id]
            while q and now-q[0] >= self.seconds:
                q.popleft()
            if len(q) >= self.count:
                return False
            q.append(now)
            if len(self._slots) > 3000:
                self._slots = defaultdict(deque, {k:v for k,v in self._slots.items() if v and now-v[-1] < self.seconds})
            return True
