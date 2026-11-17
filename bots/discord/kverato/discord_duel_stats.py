"""Read-only personal Discord duel statistics; no writes to the game economy."""
from __future__ import annotations
from contextlib import closing
from dataclasses import dataclass
from .discord_friends import valid_uid

@dataclass(frozen=True)
class DuelSummary:
    total: int
    wins: int
    losses: int
    pending: int
    declined: int
    cancelled: int
    expired: int


def summarize_duels(db, user_id: int | str) -> DuelSummary:
    uid = valid_uid(user_id)
    if not uid:
        raise ValueError('Invalid Discord ID')
    with closing(db.connect()) as con:
        row = con.execute('''SELECT COUNT(*) total,
            COALESCE(SUM(CASE WHEN status='accepted' AND winner_id=? THEN 1 ELSE 0 END),0) wins,
            COALESCE(SUM(CASE WHEN status='accepted' AND winner_id<>? THEN 1 ELSE 0 END),0) losses,
            COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending,
            COALESCE(SUM(CASE WHEN status='declined' THEN 1 ELSE 0 END),0) declined,
            COALESCE(SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END),0) cancelled,
            COALESCE(SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END),0) expired
            FROM duels WHERE platform='discord' AND (challenger_id=? OR opponent_id=?)''',
            (uid, uid, uid, uid)).fetchone()
    return DuelSummary(*(int(row[key]) for key in (
        'total', 'wins', 'losses', 'pending', 'declined', 'cancelled', 'expired')))


def format_duel_summary(result: DuelSummary) -> str:
    finished = result.wins + result.losses
    rate = f'{result.wins * 100 / finished:.0f}%' if finished else '—'
    return ('⚔️ **КВЕРАТО — статистика дуэлей**\n'
            f'🏆 Победы: **{result.wins}**\n'
            f'💔 Поражения: **{result.losses}**\n'
            f'📊 Доля побед: **{rate}**\n'
            f'⏳ Активные вызовы: **{result.pending}**\n'
            f'🚫 Отклонены: {result.declined} · отменены: {result.cancelled} · истекли: {result.expired}\n'
            f'Всего вызовов с твоим участием: {result.total}.\n\n'
            'Учитываются только Discord-дуэли. Данные других игроков и балансы не раскрываются.')
