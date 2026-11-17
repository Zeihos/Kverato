"""Read-only Discord player comparison, without bank or inventory disclosures.

No inserts, updates or auto-registration are performed. The game core and
transactional economy remain untouched; only registered player rows are read.
"""
from __future__ import annotations
from contextlib import closing
from dataclasses import dataclass

from .discord_friends import valid_uid
from .service import level_from_xp


@dataclass(frozen=True)
class PublicStats:
    user_id: str
    name: str
    level: int
    xp: int
    played: int
    won: int
    reputation: int

    @property
    def win_percent(self) -> int:
        return round(100 * self.won / self.played) if self.played else 0


def compare_players(db, left: int, right: int) -> tuple[PublicStats | None, PublicStats | None]:
    """Return public information for two *existing* Discord accounts only."""
    a, b = valid_uid(left), valid_uid(right)
    if not a or not b:
        raise ValueError('Invalid Discord user ID')
    if a == b:
        raise ValueError('Cannot compare a user against themselves')
    with closing(db.connect()) as con:
        rows = con.execute("SELECT user_id,display_name,xp,games_played,games_won,reputation "
                "FROM players WHERE platform=? AND user_id IN (?,?)", ('discord', a, b)).fetchall()
    by_id = {}
    for row in rows:
        xp, played, won = int(row['xp']), int(row['games_played']), int(row['games_won'])
        by_id[row['user_id']] = PublicStats(
            row['user_id'], str(row['display_name'])[:60], level_from_xp(xp), xp,
            played, won, int(row['reputation']))
    return by_id.get(a), by_id.get(b)


def display_comparison(left: PublicStats, right: PublicStats, escape=lambda x: x) -> str:
    """Format side-by-side comparisons without private values or mentions."""
    first = escape(left.name)[:120]
    second = escape(right.name)[:120]
    def row(icon: str, title: str, key: str) -> str:
        a, b = getattr(left, key), getattr(right, key)
        marker = ' ◀' if a > b else (' ▶' if b > a else ' =')
        return f'{icon} {title}: **{a:,}** / **{b:,}**{marker}'
    return ('👑 **КВЕРАТО — сравнение игроков**\n'
            f'🔹 **{first}**  /  🔸 **{second}**\n\n'
            + '\n'.join([
                row('🌟', 'Уровень', 'level'),
                row('⭐', 'Опыт', 'xp'),
                row('🎮', 'Сыграно игр', 'played'),
                row('🏆', 'Победы', 'won'),
                row('📊', 'Процент побед', 'win_percent'),
                row('🤝', 'Репутация', 'reputation'),
            ]) + '\n\nСравнение основано на открытой игровой статистике. '
            'Балансы и покупки игроков не показываются.')
