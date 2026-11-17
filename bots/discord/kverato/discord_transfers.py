"""Read-only transfer history for the authenticated Discord player.

No account discovery, balance data, or cross-platform rows are exposed.
Every SQL predicate is server-side and scoped to the requesting player.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone

from .discord_friends import valid_uid

PAGE_SIZE = 5
FILTERS = ('all', 'incoming', 'outgoing')
LABELS = {'all': 'Все', 'incoming': 'Входящие', 'outgoing': 'Исходящие'}


class TransferHistory:
    def __init__(self, db):
        self.db = db
        # Speeds up receiving-side history on large databases; no stored values change.
        with db.transaction() as con:
            con.execute("CREATE INDEX IF NOT EXISTS idx_discord_transfer_receiver ON transfers(platform,to_user_id,id DESC)")

    def page(self, player_id: int | str, tab: str = 'all', number: int = 0) -> dict:
        uid = valid_uid(player_id)
        if not uid:
            raise ValueError('Invalid user ID')
        if tab not in FILTERS:
            tab = 'all'
        try:
            number = int(number)
        except (TypeError, ValueError, OverflowError):
            number = 0
        number = max(0, min(number, 100_000))
        where = "platform='discord' AND (from_user_id=? OR to_user_id=?)"
        values = (uid, uid)
        if tab == 'incoming':
            where = "platform='discord' AND to_user_id=?"
            values = (uid,)
        elif tab == 'outgoing':
            where = "platform='discord' AND from_user_id=?"
            values = (uid,)
        with closing(self.db.connect()) as con:
            total = int(con.execute(f'SELECT COUNT(*) FROM transfers WHERE {where}', values).fetchone()[0])
            pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            selected = min(number, pages - 1)
            rows = con.execute(f"SELECT id,from_user_id,to_user_id,amount,created_at FROM transfers WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*values, PAGE_SIZE, PAGE_SIZE * selected)).fetchall()
        lines = [f'🧾 **КВЕРАТО · {LABELS[tab]} переводы**',
                 f'Страница **{selected + 1}/{pages}** · операций: **{total}**', '']
        if not rows:
            lines.append('У тебя пока нет переводов в этом разделе.')
        else:
            for record in rows:
                incoming = str(record['to_user_id']) == uid
                other = record['from_user_id'] if incoming else record['to_user_id']
                moment = datetime.fromtimestamp(int(record['created_at']), tz=timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
                symbol = '+' if incoming else '−'
                verb = 'от' if incoming else 'для'
                lines.append(f'{symbol} **{int(record["amount"]):,}** монет {verb} ID `{other}` · {moment}'.replace(',', ' '))
        lines.append('\n🔒 История доступна только тебе. Просмотр не изменяет монеты.')
        return {'text': '\n'.join(lines), 'page': selected, 'pages': pages, 'total': total, 'tab': tab}
