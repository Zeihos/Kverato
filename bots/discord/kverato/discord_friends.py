"""Discord-only opt-in friendship requests (no rewards or balance mutations).

All transitions run in SQLite BEGIN IMMEDIATE to prevent double acceptance.
No Discord usernames are treated as stable identity; only numeric IDs count.
"""
from __future__ import annotations

import re
import time
from contextlib import closing
from dataclasses import dataclass

from .db import Database

REQUEST_TTL = 7 * 86400
RETRY_COOLDOWN = 24 * 3600
MAX_FRIENDS = 100
MAX_OUTGOING = 10
MAX_INCOMING = 30
DAILY_REQUESTS = 8


def valid_uid(value: object) -> str | None:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,18}', value):
        return None
    return str(int(value))


def pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if int(a) < int(b) else (b, a)


@dataclass(frozen=True)
class FriendResult:
    code: str
    target: str | None = None


class DiscordFriends:
    def __init__(self, db: Database, clock=time.time):
        self.db, self.clock = db, clock
        with db.transaction() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS discord_friend_links (
                low_id TEXT NOT NULL, high_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined','removed','cancelled')),
                requested_by TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(low_id,high_id)
            )''')
            con.execute('CREATE INDEX IF NOT EXISTS idx_discord_friend_low ON discord_friend_links(low_id,status)')
            con.execute('CREATE INDEX IF NOT EXISTS idx_discord_friend_high ON discord_friend_links(high_id,status)')
            con.execute('''CREATE TABLE IF NOT EXISTS discord_friend_blocks (
                owner_id TEXT NOT NULL, target_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(owner_id,target_id)
            )''')
            con.execute('''CREATE TABLE IF NOT EXISTS discord_friend_request_quota (
                user_id TEXT NOT NULL, utc_day INTEGER NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0 CHECK(sent>=0),
                PRIMARY KEY(user_id,utc_day)
            )''')

    @staticmethod
    def _count(con, uid: str, status: str, now: int, direction: str = '') -> int:
        extra = ''
        params: list = [uid, uid, status]
        if status == 'pending':
            extra += ' AND expires_at > ?'
            params.append(now)
            if direction == 'out':
                extra += ' AND requested_by=?'
                params.append(uid)
            elif direction == 'in':
                extra += ' AND requested_by<>?'
                params.append(uid)
        return con.execute('SELECT COUNT(*) FROM discord_friend_links '
                           'WHERE (low_id=? OR high_id=?) AND status=?' + extra, params).fetchone()[0]

    def request(self, sender: int | str, recipient: int | str) -> FriendResult:
        a, b = valid_uid(sender), valid_uid(recipient)
        if not a or not b: return FriendResult('invalid')
        if a == b: return FriendResult('self')
        lo, hi = pair(a, b)
        now = int(self.clock())
        with self.db.transaction() as con:
            # Only actual Discord players may send and receive requests.
            if con.execute("SELECT COUNT(*) FROM players WHERE platform='discord' AND user_id IN (?,?)", (a,b)).fetchone()[0] != 2:
                return FriendResult('unknown')
            if con.execute('SELECT 1 FROM discord_friend_blocks WHERE (owner_id=? AND target_id=?) OR (owner_id=? AND target_id=?)',
                           (a,b,b,a)).fetchone():
                return FriendResult('unavailable')
            old = con.execute('SELECT * FROM discord_friend_links WHERE low_id=? AND high_id=?', (lo,hi)).fetchone()
            if old:
                if old['status'] == 'accepted': return FriendResult('already')
                if old['status'] == 'pending' and old['expires_at'] > now:
                    return FriendResult('sent' if old['requested_by'] == a else 'incoming', b)
                if old['status'] in ('declined','removed') and now - old['updated_at'] < RETRY_COOLDOWN:
                    return FriendResult('cooldown')
            if self._count(con,a,'accepted',now) >= MAX_FRIENDS or self._count(con,b,'accepted',now) >= MAX_FRIENDS:
                return FriendResult('full')
            if self._count(con,a,'pending',now,'out') >= MAX_OUTGOING or self._count(con,b,'pending',now,'in') >= MAX_INCOMING:
                return FriendResult('limit')
            today = now // 86400
            used = con.execute('SELECT sent FROM discord_friend_request_quota WHERE user_id=? AND utc_day=?', (a,today)).fetchone()
            if used and used['sent'] >= DAILY_REQUESTS:
                return FriendResult('daily_limit')
            con.execute('''INSERT INTO discord_friend_links(low_id,high_id,status,requested_by,created_at,updated_at,expires_at)
                VALUES(?,?,'pending',?,?,?,?) ON CONFLICT(low_id,high_id) DO UPDATE SET
                status='pending', requested_by=excluded.requested_by, created_at=excluded.created_at,
                updated_at=excluded.updated_at, expires_at=excluded.expires_at''',
                (lo,hi,a,now,now,now+REQUEST_TTL))
            con.execute('''INSERT INTO discord_friend_request_quota(user_id,utc_day,sent) VALUES(?,?,1)
                           ON CONFLICT(user_id,utc_day) DO UPDATE SET sent=sent+1''', (a,today))
            return FriendResult('created', b)

    def transition(self, actor: int | str, other: int | str, action: str) -> FriendResult:
        a,b = valid_uid(actor),valid_uid(other)
        if not a or not b or a == b or action not in ('accept','decline','cancel','remove'):
            return FriendResult('invalid')
        lo,hi = pair(a,b)
        now = int(self.clock())
        with self.db.transaction() as con:
            row = con.execute('SELECT * FROM discord_friend_links WHERE low_id=? AND high_id=?', (lo,hi)).fetchone()
            if row is None: return FriendResult('missing')
            if action in ('accept','decline'):
                if row['status'] != 'pending' or row['requested_by'] == a: return FriendResult('missing')
                if row['expires_at'] <= now:
                    return FriendResult('expired')
                if action == 'accept':
                    if (self._count(con,a,'accepted',now) >= MAX_FRIENDS
                            or self._count(con,b,'accepted',now) >= MAX_FRIENDS):
                        return FriendResult('full')
                    new = 'accepted'
                else: new='declined'
            elif action == 'cancel':
                if row['status'] != 'pending' or row['requested_by'] != a: return FriendResult('missing')
                new='cancelled'
            else:
                if row['status'] != 'accepted': return FriendResult('missing')
                new='removed'
            con.execute('UPDATE discord_friend_links SET status=?,updated_at=?,expires_at=0 WHERE low_id=? AND high_id=?',
                        (new,now,lo,hi))
            return FriendResult(new,b)

    def block(self, actor: int | str, other: int | str, *, enabled: bool = True) -> FriendResult:
        a,b = valid_uid(actor), valid_uid(other)
        if not a or not b or a == b: return FriendResult('invalid')
        with self.db.transaction() as con:
            if enabled:
                con.execute('INSERT OR IGNORE INTO discord_friend_blocks VALUES(?,?,?)', (a,b,int(self.clock())))
                lo,hi = pair(a,b)
                con.execute('DELETE FROM discord_friend_links WHERE low_id=? AND high_id=?', (lo,hi))
            else:
                con.execute('DELETE FROM discord_friend_blocks WHERE owner_id=? AND target_id=?', (a,b))
        return FriendResult('blocked' if enabled else 'unblocked',b)

    def overview(self, user: int | str) -> dict:
        uid = valid_uid(user)
        if not uid: raise ValueError('Invalid user ID')
        now = int(self.clock())
        with closing(self.db.connect()) as con:
            rows=con.execute('''SELECT low_id,high_id,status,requested_by,expires_at
                FROM discord_friend_links WHERE (low_id=? OR high_id=?) AND
                (status='accepted' OR (status='pending' AND expires_at > ?))
                ORDER BY updated_at DESC''', (uid,uid,now)).fetchall()
            ids={r['high_id'] if r['low_id']==uid else r['low_id'] for r in rows}
            names={}
            if ids:
                # Build parameterized placeholders from bounded integer ID set.
                placeholders=','.join('?' for _ in ids)
                names={r['user_id']: str(r['display_name']).replace('\n',' ').replace('\r',' ')[:40]
                       for r in con.execute('SELECT user_id,display_name FROM players WHERE platform=? AND user_id IN ('+placeholders+')',
                                            ['discord',*ids])}
        result={'friends':[], 'incoming':[], 'outgoing':[]}
        for r in rows:
            other=r['high_id'] if r['low_id']==uid else r['low_id']
            if r['status']=='accepted': category='friends'
            else: category='outgoing' if r['requested_by']==uid else 'incoming'
            result[category].append((other, names.get(other, f'Игрок {other}')))
        return result


    def is_friend(self, actor: int | str, other: int | str) -> bool:
        """Revalidate friendship at tap time, including removed/blocked entries."""
        a, b = valid_uid(actor), valid_uid(other)
        if not a or not b or a == b:
            return False
        lo, hi = pair(a, b)
        with closing(self.db.connect()) as con:
            return con.execute(
                "SELECT 1 FROM discord_friend_links WHERE low_id=? AND high_id=? AND status='accepted'",
                (lo, hi)).fetchone() is not None

    def leaderboard(self, user: int | str, *, metric: str = 'xp') -> list[tuple[str, str, int, int]]:
        """Read-only ranking of the player and their *accepted* Discord friends.

        No coins or private inventory details are exposed, and fetching a ranking
        never creates accounts or updates experience. Rows are bounded by the
        friendship cap (100) plus the player; ranks are stable on equal values.
        """
        uid = valid_uid(user)
        if not uid or metric not in ('xp', 'wins'):
            raise ValueError('Invalid leaderboard request')
        with closing(self.db.connect()) as con:
            rows = con.execute("""SELECT p.user_id, p.display_name, p.xp, p.games_won
                FROM discord_friend_links AS f
                JOIN players AS p ON p.platform='discord' AND
                    p.user_id=CASE WHEN f.low_id=? THEN f.high_id ELSE f.low_id END
                WHERE f.status='accepted' AND (f.low_id=? OR f.high_id=?)
                UNION ALL
                SELECT user_id,display_name,xp,games_won FROM players
                WHERE platform='discord' AND user_id=?""", (uid,uid,uid,uid)).fetchall()
        def safe_name(value: object) -> str:
            return str(value or 'Игрок').replace('\r', ' ').replace('\n', ' ')[:36]
        ranking = [(str(row['user_id']), safe_name(row['display_name']),
                    int(row['xp']), int(row['games_won'])) for row in rows]
        key = 2 if metric == 'xp' else 3
        ranking.sort(key=lambda item: (-item[key], int(item[0])))
        return ranking

    def prune(self, days: int = 40) -> int:
        now = int(self.clock())
        with self.db.transaction() as con:
            count=con.execute("DELETE FROM discord_friend_links WHERE status='pending' AND expires_at<?", (now-86400,)).rowcount
            count+=con.execute("DELETE FROM discord_friend_links WHERE status IN ('declined','cancelled','removed') AND updated_at<?", (now-days*86400,)).rowcount
            con.execute('DELETE FROM discord_friend_request_quota WHERE utc_day<?', (now//86400-40,))
        return count
