# features. No external API dependencies; transactional and platform isolated.
from __future__ import annotations

from contextlib import closing
import re

from .features import record_game, suffix, unlock

QUEST_REWARD = 250  # Virtual currency; never real money.
SLOT_SYMBOLS = ('🍒', '🍋', '🔔', '⭐')
EQUIP = {'vip': 'VIP', 'legend': 'Легенда', 'crown': 'Корона 👑',
         'star': 'Звезда ⭐', 'phoenix': 'Феникс 🔥',
         'игрок': 'Игрок', 'player': 'Игрок'}
EQUIP.update({'nebula': 'Туманность 🌌', 'circuit': 'Контур ⚙️', 'voyager': 'Странник 🧭', 'aurora': 'Аврора 🌅', 'prism': 'Призма 🔷', 'comet': 'Комета ☄️', 'atlas': 'Атлас 🏛'})


def utc_day(now: int) -> int:
    # Integer UTC day, independent of the server's local timezone.
    return now // 86400


def record_quest_event(con, ctx, now: int, kind: str, won: bool = False) -> None:
    # Call inside the same transaction that grants a successful work/game action.
    if kind not in ('work', 'game'):
        raise ValueError('Unknown quest event')
    day = utc_day(now)
    con.execute('''INSERT OR IGNORE INTO quest_progress(platform,user_id,utc_day)
        VALUES(?,?,?)''', (ctx.platform, ctx.user_id, day))
    if kind == 'work':
        con.execute('''UPDATE quest_progress SET work_done=work_done+1
            WHERE platform=? AND user_id=? AND utc_day=?''',
            (ctx.platform, ctx.user_id, day))
    else:
        con.execute('''UPDATE quest_progress
            SET games_done=games_done+1, wins_done=wins_done+?
            WHERE platform=? AND user_id=? AND utc_day=?''',
            (int(won), ctx.platform, ctx.user_id, day))


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    db = service.db
    if cmd == 'streak':
        with closing(db.connect()) as con:
            p = service._player(con, ctx)
        streak = p['daily_streak']
        return (f'🔥 Серия ежедневных бонусов: {streak}.\n'
                'Награда: 200 монет +25 за каждый дополнительный день серии '
                '(не более +150).\n'
                'Получай следующий бонус через 24–48 часов после предыдущего, '
                'чтобы сохранить серию.')
    if cmd == 'quests':
        with closing(db.connect()) as con:
            row = con.execute('''SELECT work_done,games_done,wins_done,claimed
                FROM quest_progress WHERE platform=? AND user_id=? AND utc_day=?''',
                (ctx.platform, ctx.user_id, utc_day(now))).fetchone()
        works, games, wins, claimed = tuple(row) if row else (0, 0, 0, 0)
        return (f'📜 ЕЖЕДНЕВНЫЕ ЗАДАНИЯ (UTC)\n'
                f'{"✅" if works >= 1 else "▫️"} Поработать: {min(works,1)}/1\n'
                f'{"✅" if games >= 3 else "▫️"} Сыграть 3 игры: {min(games,3)}/3\n'
                f'{"✅" if wins >= 1 else "▫️"} Победить 1 раз: {min(wins,1)}/1\n'
                f'🎁 Награда: {QUEST_REWARD} монет. '
                + ('уже получена.' if claimed else 'Команда: /награда')
                + '\nПрогресс сбрасывается в 00:00 UTC.')
    if cmd == 'claim':
        with db.transaction() as con:
            row = con.execute('''SELECT work_done,games_done,wins_done,claimed FROM quest_progress
                WHERE platform=? AND user_id=? AND utc_day=?''',
                (ctx.platform, ctx.user_id, utc_day(now))).fetchone()
            if row is not None and row['claimed']:
                return '🎁 Сегодня награда уже получена. Новые задания завтра (UTC).'
            if row is None or row['work_done'] < 1 or row['games_done'] < 3 or row['wins_done'] < 1:
                return '📜 Сначала выполни все три задания. Проверить: /задания.'
            from .v07 import MAX_WALLET
            wallet = con.execute('SELECT coins FROM players WHERE platform=? AND user_id=?',
                                 (ctx.platform, ctx.user_id)).fetchone()[0]
            if wallet > MAX_WALLET - QUEST_REWARD:
                return '❌ Кошелёк заполнен. Перенеси часть монет в банк.'
            updated = con.execute('''UPDATE quest_progress SET claimed=1
                WHERE platform=? AND user_id=? AND utc_day=? AND claimed=0''',
                (ctx.platform, ctx.user_id, utc_day(now)))
            if not updated.rowcount:
                return '🎁 Награда уже получена.'
            con.execute('''UPDATE players SET coins=coins+?
                WHERE platform=? AND user_id=?''',
                (QUEST_REWARD, ctx.platform, ctx.user_id))
            ach = unlock(con, ctx, 'quester', now)
        return f'🎁 Все задания выполнены! +{QUEST_REWARD} монет.' + (suffix(['📜 Выполнено!']) if ach else '')
    if cmd == 'equip':
        if len(args) != 1 or args[0].lower() not in EQUIP:
            return '🎖 Формат: /надеть vip, legend, crown, star, phoenix или игрок. Значок нужно сначала купить.'
        key = args[0].lower()
        badge = EQUIP[key]
        with db.transaction() as con:
            if badge != 'Игрок':
                owned = con.execute('''SELECT 1 FROM purchases
                    WHERE platform=? AND user_id=? AND item_key=?''',
                    (ctx.platform, ctx.user_id, key)).fetchone()
                if not owned:
                    return '❌ Значок ещё не куплен. Используй /магазин.'
            con.execute('''UPDATE players SET badge=? WHERE platform=? AND user_id=?''',
                        (badge, ctx.platform, ctx.user_id))
        return f'🎖 Теперь активный значок — {badge}.'
    if cmd == 'slots':
        if len(args) != 1 or not (1 <= len(args[0]) <= 8) or not args[0].isascii() or not args[0].isdecimal():
            return '🎰 Формат: /слоты 50 (ставка 1–5000 виртуальных монет).'
        bet = int(args[0])
        if not 1 <= bet <= 5000:
            return '⚠ Ставка должна быть от 1 до 5000 монет.'
        with db.transaction() as con:
            player = service._player(con, ctx)
            if player['coins'] < bet:
                return '❌ На балансе недостаточно монет.'
            # Three matching: net +5x; two matching: refund; all different: -1x.
            indexes = [service.randbelow(len(SLOT_SYMBOLS)) for _ in range(3)]
            count = len(set(indexes))
            delta = (5 * bet if count == 1 else 0 if count == 2 else -bet)
            from .v07 import MAX_WALLET
            if delta > 0 and player['coins'] > MAX_WALLET - delta:
                return '❌ Кошелёк заполнен. Перенеси часть монет в банк.'
            con.execute('''UPDATE players SET coins=coins+? WHERE platform=? AND user_id=?''',
                        (delta, ctx.platform, ctx.user_id))
            new = record_game(con, ctx, delta > 0, now)
        result = ('🎉 Джекпот! +' + str(delta) + ' монет.' if delta > 0 else
                  '🤝 Два одинаковых: ставка возвращена.' if delta == 0 else
                  f'😔 Все разные: −{bet} монет.')
        return '🎰 ' + ' '.join(SLOT_SYMBOLS[i] for i in indexes) + '\n' + result + suffix(new)
    if cmd == 'topwins':
        with closing(db.connect()) as con:
            rows = con.execute('''SELECT display_name, games_won FROM players
                WHERE platform=? ORDER BY games_won DESC, created_at ASC LIMIT 10''',
                (ctx.platform,)).fetchall()
        return '🥇 ТОП ПО ПОБЕДАМ\n' + '\n'.join(
            f'{i}. {r["display_name"]}: {r["games_won"]}' for i, r in enumerate(rows, 1))
    return None
