"""КВЕРАТО v0.6: persistent open clans, clan treasury and daily cooperative mission.

Each mutation is serialized using Database.transaction/BEGIN IMMEDIATE. Clan
funds buy clan cosmetics only; they cannot be withdrawn as personal currency.
"""
from __future__ import annotations

from contextlib import closing
import re
import unicodedata

CLAN_CREATE_COST = 500
CLAN_MEMBER_LIMIT = 30
CLAN_MISSION_REWARD = 50
CLAN_MILESTONE_BONUS = 200
CLAN_MILESTONE_TARGET = 3
CLAN_PURCHASES = {'герб': (400, '🎨 Герб'), 'зал': (1500, '🏰 Зал собраний')}


def _clan_key(raw: str) -> tuple[str, str] | None:
    display = ' '.join(unicodedata.normalize('NFKC', raw).split())
    if not 3 <= len(display) <= 24 or not re.fullmatch(r'[а-яА-ЯёЁa-zA-Z0-9_ -]+', display):
        return None
    return display, display.casefold()


def _member(con, ctx):
    return con.execute('''SELECT c.*, m.joined_at FROM clan_members m
        JOIN clans c ON c.id=m.clan_id
        WHERE m.platform=? AND m.user_id=?''', (ctx.platform, ctx.user_id)).fetchone()


def _log(con, clan_id: int, ctx, delta: int, action: str, now: int):
    con.execute('''INSERT INTO clan_ledger(clan_id,platform,actor_id,delta,action,created_at)
                   VALUES (?,?,?,?,?,?)''', (clan_id, ctx.platform, ctx.user_id, delta, action, now))


def _id(text: str) -> bool:
    return bool(re.fullmatch(r'[1-9][0-9]{0,18}', text))


def _amount(text: str) -> int | None:
    if not re.fullmatch(r'[0-9]{1,6}', text, re.ASCII):
        return None
    result = int(text)
    return result if 1 <= result <= 10000 else None


def _clan_details(con, clan, now: int):
    members = con.execute('SELECT COUNT(*) AS total FROM clan_members WHERE clan_id=?', (clan['id'],)).fetchone()['total']
    progress = con.execute('''SELECT completed,rewarded FROM clan_daily_progress
        WHERE clan_id=? AND utc_day=?''', (clan['id'], now // 86400)).fetchone()
    done = progress['completed'] if progress else 0
    return (f'👑 КЛАН «{clan["name"]}»\n'
            f'👥 Участников: {members}/{CLAN_MEMBER_LIMIT}\n'
            f'💰 Казна: {clan["treasury"]} монет\n'
            f'🏅 Очки активности: {clan["score"]}\n'
            f'👤 Лидер: [id{clan["owner_id"]}|игрок]\n'
            f'🎨 Герб: {"приобретён" if clan["has_emblem"] else "не приобретён"}\n'
            f'🏰 Зал: {"построен" if clan["has_hall"] else "не построен"}\n'
            f'📜 Совместная миссия (UTC): {min(done, CLAN_MILESTONE_TARGET)}/{CLAN_MILESTONE_TARGET}'
            + (' ✅' if progress and progress['rewarded'] else '')
            + '\nКоманды: клан участники / миссия / вклад 50 / магазин / журнал')


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    if cmd not in ('clan', 'clantop', 'clanmission'):
        return None
    db = service.db
    if cmd == 'clantop':
        with closing(db.connect()) as con:
            rows = con.execute('''SELECT name,score,treasury FROM clans WHERE platform=?
                ORDER BY score DESC,treasury DESC,id ASC LIMIT 10''', (ctx.platform,)).fetchall()
        if not rows:
            return '🏆 Пока нет кланов. Создай первый: клан создать Название.'
        return '🏆 ТОП КЛАНОВ\n' + '\n'.join(
            f'{i}. {row["name"]} — {row["score"]} очк. • казна {row["treasury"]}'
            for i, row in enumerate(rows, 1))

    sub = ('миссия' if cmd == 'clanmission' else args[0].casefold() if args else 'инфо')
    tail = ([] if cmd == 'clanmission' else args[1:])
    if sub in ('помощь', 'help'):
        return ('👑 КЛАНЫ КВЕРАТО\n'
                'клан создать Название — 500 монет\n'
                'клан вступить Название — открытые кланы, до 30 участников\n'
                'клан / клан участники / топкланов\n'
                'клан вклад 50 — пожертвование в казну\n'
                'клан миссия — раз в сутки UTC; +50 в казну, бонус +200 за 3 участников\n'
                'клан магазин / клан купить герб|зал — улучшения за средства казны\n'
                'клан передать ID / клан выйти / клан распустить (только лидер)\n'
                'клан журнал — последние события. Казна не обменивается на личные монеты.')

    if sub in ('создать', 'вступить'):
        parsed = _clan_key(' '.join(tail))
        if parsed is None:
            return '⚠ Название клана: 3–24 символа, только буквы, цифры, пробел, _ или дефис.'
        name, name_key = parsed
        with db.transaction() as con:
            if _member(con, ctx):
                return '⛔ Ты уже состоишь в клане. Сначала выйди из текущего.'
            if sub == 'создать':
                if con.execute('SELECT id FROM clans WHERE platform=? AND name_key=?',
                               (ctx.platform, name_key)).fetchone():
                    return '⛔ Клан с таким названием уже существует.'
                if not con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND
                    user_id=? AND coins>=?''', (CLAN_CREATE_COST, ctx.platform, ctx.user_id,
                                              CLAN_CREATE_COST)).rowcount:
                    return f'❌ Для создания клана нужно {CLAN_CREATE_COST} монет в кошельке.'
                result = con.execute('''INSERT INTO clans(platform,name,name_key,owner_id,created_at)
                    VALUES (?,?,?,?,?)''', (ctx.platform, name, name_key, ctx.user_id, now))
                clan_id = result.lastrowid
                con.execute('''INSERT INTO clan_members(platform,user_id,clan_id,joined_at)
                    VALUES (?,?,?,?)''', (ctx.platform, ctx.user_id, clan_id, now))
                _log(con, clan_id, ctx, 0, 'create', now)
                return f'👑 Клан «{name}» создан! Списано {CLAN_CREATE_COST} монет. Ты — лидер.'
            clan = con.execute('SELECT * FROM clans WHERE platform=? AND name_key=?',
                               (ctx.platform, name_key)).fetchone()
            if not clan:
                return '❌ Такой клан не найден. Посмотри: топкланов.'
            members = con.execute('SELECT COUNT(*) FROM clan_members WHERE clan_id=?',
                                  (clan['id'],)).fetchone()[0]
            if members >= CLAN_MEMBER_LIMIT:
                return '❌ В клане нет свободных мест.'
            con.execute('''INSERT INTO clan_members(platform,user_id,clan_id,joined_at)
                VALUES (?,?,?,?)''', (ctx.platform, ctx.user_id, clan['id'], now))
            _log(con, clan['id'], ctx, 0, 'join', now)
            return f'👑 Ты вступил в клан «{clan["name"]}»!'

    if sub in ('инфо', 'профиль', 'участники', 'журнал', 'лог', 'магазин'):
        with closing(db.connect()) as con:
            clan = _member(con, ctx)
            if not clan:
                return '👑 Пока ты не в клане. Команды: клан помощь / клан создать Название / топкланов.'
            if sub in ('инфо', 'профиль'):
                return _clan_details(con, clan, now)
            if sub == 'участники':
                rows = con.execute('''SELECT m.user_id,p.display_name FROM clan_members m
                    JOIN players p ON p.platform=m.platform AND p.user_id=m.user_id
                    WHERE m.clan_id=? ORDER BY m.joined_at,m.rowid LIMIT 30''', (clan['id'],)).fetchall()
                return '👥 УЧАСТНИКИ «' + clan['name'] + '»\n' + '\n'.join(
                    f'{i}. {r["display_name"]}' + (' 👑' if r['user_id'] == clan['owner_id'] else '')
                    for i, r in enumerate(rows, 1))
            if sub == 'магазин':
                return ('🛍 МАГАЗИН КЛАНА\n'
                        f'🎨 Герб: 400 монет ({"куплен" if clan["has_emblem"] else "доступен"})\n'
                        f'🏰 Зал: 1500 монет ({"куплен" if clan["has_hall"] else "доступен"})\n'
                        'Покупку делает лидер: клан купить герб / клан купить зал.\n'
                        'Оплата из общей казны. Улучшения косметические.')
            rows = con.execute('''SELECT actor_id,delta,action FROM clan_ledger
                WHERE clan_id=? ORDER BY id DESC LIMIT 7''', (clan['id'],)).fetchall()
            return '📜 ИСТОРИЯ КЛАНА\n' + '\n'.join(
                f'{r["actor_id"]}: {r["action"]} ({r["delta"]:+d} казна)' for r in rows)

    if sub in ('выйти', 'распустить', 'передать', 'вклад', 'пожертвовать', 'миссия', 'купить'):
        with db.transaction() as con:
            clan = _member(con, ctx)
            if not clan:
                return '⛔ Сначала вступи в клан: клан вступить Название.'
            clan_id = clan['id']
            leader = clan['owner_id'] == ctx.user_id
            if sub == 'вклад' or sub == 'пожертвовать':
                amount = _amount(tail[0]) if len(tail) == 1 else None
                if amount is None:
                    return '💰 Формат: клан вклад 50 (от 1 до 10000 монет из кошелька).'
                if not con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND
                    user_id=? AND coins>=?''', (amount, ctx.platform, ctx.user_id, amount)).rowcount:
                    return '❌ Недостаточно монет на личном кошельке.'
                con.execute('UPDATE clans SET treasury=treasury+? WHERE id=?', (amount, clan_id))
                _log(con, clan_id, ctx, amount, 'donation', now)
                return f'💰 В казну клана внесено {amount} монет.'
            if sub == 'миссия':
                day = now // 86400
                exists = con.execute('''SELECT 1 FROM clan_quest_claims WHERE platform=? AND
                    user_id=? AND utc_day=?''', (ctx.platform, ctx.user_id, day)).fetchone()
                if exists:
                    return '📜 Ты уже выполнил клановую миссию сегодня (UTC).'
                con.execute('''INSERT INTO clan_quest_claims(platform,user_id,utc_day,clan_id,claimed_at)
                    VALUES (?,?,?,?,?)''', (ctx.platform, ctx.user_id, day, clan_id, now))
                con.execute('''INSERT INTO clan_daily_progress(clan_id,utc_day,completed)
                    VALUES (?,?,1) ON CONFLICT(clan_id,utc_day)
                    DO UPDATE SET completed=completed+1''', (clan_id, day))
                progress = con.execute('''SELECT completed,rewarded FROM clan_daily_progress
                    WHERE clan_id=? AND utc_day=?''', (clan_id, day)).fetchone()
                milestone = progress['completed'] >= CLAN_MILESTONE_TARGET and not progress['rewarded']
                bonus = CLAN_MILESTONE_BONUS if milestone else 0
                if milestone:
                    con.execute('''UPDATE clan_daily_progress SET rewarded=1
                        WHERE clan_id=? AND utc_day=?''', (clan_id, day))
                con.execute('''UPDATE clans SET treasury=treasury+?,score=score+1 WHERE id=?''',
                            (CLAN_MISSION_REWARD + bonus, clan_id))
                _log(con, clan_id, ctx, CLAN_MISSION_REWARD + bonus, 'mission', now)
                return (f'📜 Совместная миссия выполнена! +{CLAN_MISSION_REWARD} в казну. '
                        f'Прогресс: {min(progress["completed"], CLAN_MILESTONE_TARGET)}/{CLAN_MILESTONE_TARGET}.'
                        + (f' 🎉 Цель дня! Ещё +{bonus} в казну.' if milestone else ''))
            if sub == 'выйти':
                other = con.execute('''SELECT user_id FROM clan_members WHERE clan_id=? AND user_id<>?
                    ORDER BY joined_at,rowid LIMIT 1''', (clan_id, ctx.user_id)).fetchone()
                if leader and other:
                    con.execute('UPDATE clans SET owner_id=? WHERE id=?', (other['user_id'], clan_id))
                    _log(con, clan_id, ctx, 0, 'leader_succession', now)
                con.execute('DELETE FROM clan_members WHERE platform=? AND user_id=?',
                            (ctx.platform, ctx.user_id))
                if leader and not other:
                    con.execute('DELETE FROM clans WHERE id=?', (clan_id,))
                    return '👑 Ты вышел из клана. Пустой клан распущен; его казна не возвращается.'
                _log(con, clan_id, ctx, 0, 'leave', now)
                return ('✅ Ты вышел из клана. Лидерство передано следующему участнику.'
                        if leader else '✅ Ты вышел из клана.')
            if not leader:
                return '⛔ Эту команду может выполнить только лидер клана.'
            if sub == 'распустить':
                con.execute('DELETE FROM clans WHERE id=?', (clan_id,))
                return '🗑 Клан распущен. Средства казны не возвращаются.'
            if sub == 'передать':
                recipient = tail[0] if len(tail) == 1 else ''
                if not _id(recipient) or recipient == ctx.user_id:
                    return '👑 Формат: клан передать 123456 (ID другого участника).'
                exists = con.execute('''SELECT 1 FROM clan_members WHERE clan_id=? AND
                    platform=? AND user_id=?''', (clan_id, ctx.platform, recipient)).fetchone()
                if not exists:
                    return '⛔ Новый лидер должен состоять в том же клане.'
                con.execute('UPDATE clans SET owner_id=? WHERE id=?', (recipient, clan_id))
                _log(con, clan_id, ctx, 0, 'leader_transfer', now)
                return f'👑 Новый лидер клана: [id{recipient}|игрок].'
            if sub == 'купить':
                choice = tail[0].lower() if len(tail) == 1 else ''
                if choice not in CLAN_PURCHASES:
                    return '🛍 Формат: клан купить герб / клан купить зал.'
                price, title = CLAN_PURCHASES[choice]
                column = 'has_emblem' if choice == 'герб' else 'has_hall'
                if clan[column]:
                    return '✅ Улучшение уже приобретено.'
                if clan['treasury'] < price:
                    return f'❌ В казне нужно {price} монет для улучшения.'
                # Only fixed SQL identifiers from the static mapping above.
                con.execute(f'UPDATE clans SET treasury=treasury-?, {column}=1 WHERE id=?',
                            (price, clan_id))
                _log(con, clan_id, ctx, -price, 'buy_' + choice, now)
                return f'✅ {title} приобретён за {price} монет из казны!'

    return '👑 Неизвестная команда клана. Напиши: клан помощь.'
