""" deterministic 28-day seasons and final-week events.

All progression is server-clock based and persisted in SQLite. Rewards are claimed
atomically. No real currency or external services are used. Closing seasons and
hall-of-fame snapshots is lazy (on season command), without a background worker.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone

from .v07 import MAX_WALLET

EPOCH = 1790812800  # 2026-10-01 00:00:00 UTC
DAYS = 28
SECONDS = DAYS * 86400
EVENT_DAYS = 7
DAILY_TYPES = ('work', 'contract', 'expedition', 'race')
TYPE_RU = {'work': 'Работа', 'contract': 'Контракты', 'expedition': 'Экспедиции', 'race': 'Гонки'}
BASE_XP = {'work': 15, 'contract': 20, 'expedition': 25, 'race': 20}
DAILY_MISSION_POINTS = 40
TIERS = {1: (50, 100), 2: (180, 250), 3: (450, 400), 4: (900, 1000)}
EVENT_GOODS = {'flare': ('Сияние Периметра', 30), 'crest': ('Герб Рассвета', 75)}
PODIUM_COINS = {1: 600, 2: 300, 3: 150}


def season_id(now: int) -> int:
    return max(0, (now - EPOCH) // SECONDS)


def bounds(sid: int) -> tuple[int, int]:
    start = EPOCH + sid * SECONDS
    return start, start + SECONDS


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')


def is_event(now: int) -> bool:
    sid = season_id(now)
    start, end = bounds(sid)
    return start + (DAYS - EVENT_DAYS) * 86400 <= now < end


def _score(con, platform: str, user_id: str, sid: int):
    return con.execute('''SELECT points, tokens FROM season_scores WHERE
        platform=? AND user_id=? AND season_id=?''', (platform, user_id, sid)).fetchone()


def activity(con, ctx, action: str, now: int) -> bool:
    """Called inside the SAME transaction as a successful game action.

    At most one qualifying action per category per UTC day. A repeated work,
    race or contract cannot farm unlimited season points or event tokens.
    """
    if action not in DAILY_TYPES or now < EPOCH:
        return False
    enabled=con.execute('SELECT enabled FROM bot_flags WHERE platform=? AND feature=?',
                        (ctx.platform,'seasons')).fetchone()
    if enabled and not enabled['enabled']:
        return False
    sid, day = season_id(now), now // 86400
    inserted = con.execute('''INSERT OR IGNORE INTO season_activity
        (platform,user_id,season_id,utc_day,kind,created_at) VALUES(?,?,?,?,?,?)''',
        (ctx.platform, ctx.user_id, sid, day, action, now)).rowcount
    if not inserted:
        return False
    points = BASE_XP[action]
    tokens = 10 if is_event(now) else 0
    con.execute('''INSERT INTO season_scores(platform,user_id,season_id,points,tokens,earned_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(platform,user_id,season_id) DO UPDATE SET
        points=points+excluded.points,tokens=tokens+excluded.tokens,
        earned_at=MIN(earned_at,excluded.earned_at)''',
        (ctx.platform, ctx.user_id, sid, points, tokens, now))
    return True


def _finalize(con, platform: str, sid: int) -> None:
    """Freeze a previous season leaderboard once, even under concurrent calls."""
    exists = con.execute('''SELECT 1 FROM season_finalized WHERE platform=? AND season_id=?''',
                         (platform, sid)).fetchone()
    if exists:
        return
    top = con.execute('''SELECT user_id,points FROM season_scores
        WHERE platform=? AND season_id=? AND points>0
        ORDER BY points DESC,earned_at ASC,user_id ASC LIMIT 3''', (platform, sid)).fetchall()
    for rank, row in enumerate(top, 1):
        con.execute('''INSERT INTO season_hall(platform,season_id,rank,user_id,points)
            VALUES(?,?,?,?,?)''', (platform, sid, rank, row['user_id'], row['points']))
    con.execute('INSERT INTO season_finalized(platform,season_id) VALUES(?,?)', (platform, sid))


def finalize_past(con, platform: str, now: int) -> None:
    if now < EPOCH:
        return
    current = season_id(now)
    seasons = con.execute('''SELECT DISTINCT season_id FROM season_scores WHERE
        platform=? AND season_id<?''', (platform, current)).fetchall()
    for row in seasons:
        _finalize(con, platform, row['season_id'])


def _wallet_reward(con, ctx, amount: int) -> bool:
    return bool(con.execute('''UPDATE players SET coins=coins+? WHERE platform=? AND user_id=?
        AND coins<=?''', (amount, ctx.platform, ctx.user_id, MAX_WALLET - amount)).rowcount)


def _rank_rows(con, platform: str, sid: int, limit: int = 10):
    return con.execute('''SELECT s.user_id,s.points,p.display_name FROM season_scores s
        JOIN players p ON p.platform=s.platform AND p.user_id=s.user_id
        WHERE s.platform=? AND s.season_id=?
        ORDER BY s.points DESC,s.earned_at ASC,s.user_id ASC LIMIT ?''',
        (platform, sid, limit)).fetchall()


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    if cmd not in ('season', 'seasonquests', 'seasontop', 'hall', 'event'):
        return None
    sid = season_id(now)
    if cmd == 'seasonquests':
        cmd, args = 'season', ['задания']
    elif cmd == 'seasontop':
        cmd, args = 'season', ['топ']
    elif cmd == 'hall':
        cmd, args = 'season', ['слава']
    elif cmd == 'event':
        cmd, args = 'season', ['событие'] + args
    action = args[0].casefold() if args else ''
    if action == 'событие':
        from .v07 import feature_enabled
        if not feature_enabled(service,ctx.platform,'events'):
            return '⏸ Временные события отключены администрацией КВЕРАТО.'
    if action in ('', 'статус') and len(args) <= 1:
        with closing(service.db.connect()) as con:
            score = _score(con, ctx.platform, ctx.user_id, sid)
            claimed = {r['tier'] for r in con.execute('''SELECT tier FROM season_tier_claims
                WHERE platform=? AND user_id=? AND season_id=?''', (ctx.platform, ctx.user_id, sid))}
        start, end = bounds(sid)
        points = score['points'] if score else 0
        tokens = score['tokens'] if score else 0
        return (f'🌠 КВЕРАТО | СЕЗОН №{sid+1}\n'
                f'📅 { _date(start) } — { _date(end) }\n'
                f'⭐ Сезонный прогресс: {points} очков\n'
                f'✨ Валюта события: {tokens} огней\n'
                f'🎖 Получено наград: {len(claimed)}/{len(TIERS)}\n'
                f'🎪 Событие: {"открыто" if is_event(now) else "откроется в последние 7 дней"}\n'
                'сезон задания / сезон награды / сезон топ / сезон событие / залславы')
    if action in ('задания', 'миссии') and len(args) == 1:
        with closing(service.db.connect()) as con:
            rows = con.execute('''SELECT kind FROM season_activity WHERE platform=? AND
                user_id=? AND season_id=? AND utc_day=?''',
                (ctx.platform, ctx.user_id, sid, now//86400)).fetchall()
            claimed = con.execute('''SELECT 1 FROM season_daily_claims WHERE platform=? AND
                user_id=? AND utc_day=?''', (ctx.platform, ctx.user_id, now//86400)).fetchone()
        done = {r['kind'] for r in rows}
        return ('📜 СЕЗОННЫЕ ЗАДАНИЯ (сутки UTC)\n' + '\n'.join(
            f'{"✅" if kind in done else "⬜"} {TYPE_RU[kind]} (+{BASE_XP[kind]} очков)'
            for kind in DAILY_TYPES) + '\n'
            f'🎁 Бонус за 2 разных действия: +{DAILY_MISSION_POINTS} очков. '
            f'{"Получен" if claimed else "Сезон забрать" if len(done)>=2 else f"Выполнено {len(done)}/2"}.')
    if action == 'забрать' and len(args) == 1:
        with service.db.transaction() as con:
            count=con.execute('''SELECT COUNT(*) FROM season_activity WHERE platform=? AND
                user_id=? AND season_id=? AND utc_day=?''',
                (ctx.platform, ctx.user_id, sid, now//86400)).fetchone()[0]
            if count < 2:
                return '⌛ Выполни два разных сезонных действия за сутки UTC.'
            inserted = con.execute('''INSERT OR IGNORE INTO season_daily_claims
                (platform,user_id,utc_day,season_id,claimed_at) VALUES(?,?,?,?,?)''',
                (ctx.platform, ctx.user_id, now//86400, sid, now)).rowcount
            if not inserted:
                return '✅ Сезонная дневная награда уже получена.'
            con.execute('''UPDATE season_scores SET points=points+? WHERE
                platform=? AND user_id=? AND season_id=?''',
                (DAILY_MISSION_POINTS,ctx.platform,ctx.user_id,sid))
        return f'🎉 +{DAILY_MISSION_POINTS} сезонных очков за два разных действия!'
    if action == 'награды' and len(args)==1:
        with closing(service.db.connect()) as con:
            score=_score(con,ctx.platform,ctx.user_id,sid)
            done = {r['tier'] for r in con.execute('''SELECT tier FROM season_tier_claims
                WHERE platform=? AND user_id=? AND season_id=?''', (ctx.platform, ctx.user_id, sid))}
        points = score['points'] if score else 0
        lines = [f'🎖 НАГРАДЫ СЕЗОНА №{sid+1} | {points} очков']
        lines.extend(f'{"✅" if tier in done else "🎁" if points>=threshold else "🔒"} '
                     f'Уровень {tier}: {threshold} очков → {coins} монет'+
                     (' и титул «Хранитель сезона»' if tier == 4 else '')
                     for tier,(threshold,coins) in TIERS.items())
        lines.append('Получить: сезон награда 1 (или 2/3/4).')
        return '\n'.join(lines)
    if action == 'награда' and len(args)==2:
        tier = int(args[1]) if args[1].isascii() and args[1] in ('1','2','3','4') else None
        if tier not in TIERS:
            return '🎖 Формат: сезон награда 1/2/3/4.'
        threshold,coins = TIERS[tier]
        with service.db.transaction() as con:
            score=_score(con,ctx.platform,ctx.user_id,sid)
            if not score or score['points']<threshold:
                return f'🔒 Для награды уровня {tier} нужно {threshold} сезонных очков.'
            if con.execute('''SELECT 1 FROM season_tier_claims WHERE
                platform=? AND user_id=? AND season_id=? AND tier=?''',
                (ctx.platform,ctx.user_id,sid,tier)).fetchone():
                return '✅ Ты уже получил эту награду.'
            if not _wallet_reward(con,ctx,coins):
                return '⚠ Кошелёк переполнен. Освободи место и повтори.'
            con.execute('''INSERT INTO season_tier_claims
                (platform,user_id,season_id,tier,claimed_at) VALUES(?,?,?,?,?)''',
                (ctx.platform,ctx.user_id,sid,tier,now))
            if tier==4:
                con.execute('''INSERT INTO season_titles
                    (platform,user_id,season_id,title,equipped) VALUES(?,?,?,?,0)''',
                    (ctx.platform,ctx.user_id,sid,f'Хранитель сезона №{sid+1}'))
        return f'🎉 Награда уровня {tier}: +{coins} монет!'+(' 🎖 Титул открыт: сезон титул.' if tier==4 else '')
    if action == 'титул' and len(args)==1:
        with service.db.transaction() as con:
            row=con.execute('''SELECT season_id,title FROM season_titles WHERE platform=? AND
                user_id=? ORDER BY season_id DESC LIMIT 1''', (ctx.platform,ctx.user_id)).fetchone()
            if not row:
                return '🔒 У тебя пока нет сезонного титула. Получи награду уровня 4.'
            con.execute('UPDATE season_titles SET equipped=0 WHERE platform=? AND user_id=?',
                        (ctx.platform,ctx.user_id))
            con.execute('''UPDATE season_titles SET equipped=1 WHERE platform=? AND user_id=?
                AND season_id=?''', (ctx.platform,ctx.user_id,row['season_id']))
        return f'🎖 Титул установлен: «{row["title"]}».'
    if action == 'топ' and len(args)==1:
        with closing(service.db.connect()) as con:
            rows=_rank_rows(con,ctx.platform,sid)
        return f'🏆 ТОП СЕЗОНА №{sid+1}\n'+ ('\n'.join(
            f'{i}. {r["display_name"][:40]} — {r["points"]} очков' for i,r in enumerate(rows,1)) if rows else 'Пока нет участников.')
    if action == 'слава' and len(args) == 1:
        with service.db.transaction() as con:
            finalize_past(con,ctx.platform,now)
            rows=con.execute('''SELECT h.season_id,h.rank,h.points,p.display_name FROM season_hall h
                LEFT JOIN players p ON h.platform=p.platform AND h.user_id=p.user_id
                WHERE h.platform=? ORDER BY h.season_id DESC,h.rank LIMIT 12''',
                (ctx.platform,)).fetchall()
        return '🏛 ЗАЛ СЛАВЫ КВЕРАТО\n'+ ('\n'.join(
            f'Сезон №{r["season_id"]+1}, место {r["rank"]}: '
            f'{(r["display_name"] or "Игрок")[:40]} — {r["points"]} очков'
            for r in rows) if rows else 'Зал славы пока пуст.')
    if action == 'приз' and len(args) == 1:
        with service.db.transaction() as con:
            finalize_past(con,ctx.platform,now)
            row=con.execute('''SELECT h.season_id,h.rank FROM season_hall h WHERE
                h.platform=? AND h.user_id=? AND h.season_id<? AND NOT EXISTS (
                  SELECT 1 FROM season_podium_claims c WHERE c.platform=h.platform
                  AND c.user_id=h.user_id AND c.season_id=h.season_id)
                ORDER BY h.season_id DESC LIMIT 1''',
                (ctx.platform,ctx.user_id,sid)).fetchone()
            if not row:
                return '🏛 Тебе пока не положен приз за прошлые сезоны.'
            previous = con.execute('''SELECT 1 FROM season_podium_claims WHERE
                platform=? AND user_id=? AND season_id=?''',
                (ctx.platform,ctx.user_id,row['season_id'])).fetchone()
            if previous:
                return '✅ Приз за этот сезон уже получен.'
            coins = PODIUM_COINS[row['rank']]
            if not _wallet_reward(con,ctx,coins):
                return '⚠ Кошелёк переполнен. Освободи место и повтори.'
            con.execute('''INSERT INTO season_podium_claims
                (platform,user_id,season_id,claimed_at) VALUES(?,?,?,?)''',
                (ctx.platform,ctx.user_id,row['season_id'],now))
        return f'🏆 Приз за сезон №{row["season_id"]+1}: +{coins} монет!'
    if action == 'событие':
        if len(args)==1:
            start,end=bounds(sid)
            with closing(service.db.connect()) as con:
                score=_score(con,ctx.platform,ctx.user_id,sid)
                bought = {r['item_key'] for r in con.execute('''SELECT item_key FROM season_collectibles
                    WHERE platform=? AND user_id=? AND season_id=?''', (ctx.platform,ctx.user_id,sid))}
            lines=[f'🌌 СОБЫТИЕ «ОГНИ ПЕРИМЕТРА» | СЕЗОН №{sid+1}',
                   f'Период: {_date(start+(DAYS-EVENT_DAYS)*86400)} — {_date(end)}',
                   f'Статус: {"активно" if is_event(now) else "ожидается"}',
                   f'✨ У тебя: {score["tokens"] if score else 0} огней',
                   'За каждую игровую активность (до 4 разных в день): +10 огней.']
            lines.extend(f'{"✅" if key in bought else "🎁"} {name} [{key}] — {price} огней'
                         for key,(name,price) in EVENT_GOODS.items())
            lines.append('Покупка: сезон событие купить flare/crest. Вещи остаются после сезона.')
            return '\n'.join(lines)
        if len(args)==3 and args[1].casefold()=='купить' and args[2].casefold() in EVENT_GOODS:
            key=args[2].casefold()
            if not is_event(now):
                return '⏳ Магазин события работает только в последнюю неделю сезона.'
            name,price=EVENT_GOODS[key]
            with service.db.transaction() as con:
                if con.execute('''SELECT 1 FROM season_collectibles WHERE platform=? AND
                    user_id=? AND season_id=? AND item_key=?''', (ctx.platform,ctx.user_id,sid,key)).fetchone():
                    return '✅ Этот коллекционный предмет уже приобретён.'
                updated=con.execute('''UPDATE season_scores SET tokens=tokens-? WHERE
                    platform=? AND user_id=? AND season_id=? AND tokens>=?''',
                    (price,ctx.platform,ctx.user_id,sid,price)).rowcount
                if not updated:
                    return f'❌ Нужно {price} огней события.'
                con.execute('''INSERT INTO season_collectibles(platform,user_id,season_id,item_key,bought_at)
                    VALUES(?,?,?,?,?)''', (ctx.platform,ctx.user_id,sid,key,now))
            return f'🌠 Предмет «{name}» добавлен в сезонную коллекцию!'
        return '🌌 Команды: сезон событие / сезон событие купить flare.'
    if action == 'коллекция' and len(args)==1:
        with closing(service.db.connect()) as con:
            rows=con.execute('''SELECT season_id,item_key FROM season_collectibles WHERE
                platform=? AND user_id=? ORDER BY season_id DESC,item_key''',
                (ctx.platform,ctx.user_id)).fetchall()
        return '🎖 СЕЗОННАЯ КОЛЛЕКЦИЯ\n'+ ('\n'.join(
            f'Сезон №{r["season_id"]+1}: {EVENT_GOODS[r["item_key"]][0]}' for r in rows)
            if rows else 'Предметов пока нет.')
    return ('🌠 Команды: сезон / сезон задания / сезон забрать / сезон награды / '
            'сезон награда 1 / сезон титул / сезон топ / сезон событие / '
            'сезон коллекция / сезон приз / залславы.')
