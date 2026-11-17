"""v1.4.0 — delivery runs and cooperative clan expeditions.

All virtual-currency, resource and experience writes use a single BEGIN IMMEDIATE
SQLite transaction; rewards are idempotent and limited to a UTC day.
"""
from __future__ import annotations

from contextlib import closing

from .v06 import _member as clan_member
from .v07 import MAX_WALLET
from .v09 import VEHICLES, VEHICLE_NAMES, _material, _take_materials, _coins
from .v10 import VEHICLE_POWER, TRACKS
from .v08 import MATERIALS
from .v11 import _gear
from .features import unlock

# key -> (title, track, minimum track rating, cost, payout, XP)
ROUTES = {
    'центр': ('Доставка в Меридиан', 'кольцо', 0, {'supplies': 2}, 160, 15),
    'док': ('Срочная доставка в док', 'кольцо', 70, {'supplies': 3, 'parts': 2}, 320, 25),
    'периметр': ('Конвой в Периметр', 'периметр', 80, {'parts': 3, 'chips': 2}, 480, 40),
}
# key -> (title, cost, min explorer gear tier, points, energy cost)
CLAN_RUNS = {
    'док': ('Осмотр транспортного дока', {'parts': 2}, 0, 16, 0),
    'мастерские': ('Экспедиция в старые мастерские', {'parts': 3}, 1, 19, 0),
    'периметр': ('Патруль за внешним кольцом', {'chips': 2}, 2, 22, 5),
}
CLAN_TARGET = 48
CLAN_PARTICIPANTS = 3
CLAN_REWARD = 220
CLAN_XP = 60


def _clan_progress(con, platform: str, clan_id: int, day: int):
    return con.execute('''SELECT COUNT(*) AS participants, COALESCE(SUM(points),0) AS total
        FROM clan_expeditions WHERE platform=? AND clan_id=? AND utc_day=?''',
        (platform, clan_id, day)).fetchone()


def delivery(service, ctx, args: list[str], now: int) -> str:
    day = now // 86400
    if not args:
        with closing(service.db.connect()) as con:
            done = con.execute('SELECT route_key FROM transport_deliveries WHERE platform=? AND user_id=? AND utc_day=?',
                               (ctx.platform,ctx.user_id,day)).fetchone()
        lines = ['🚚 КВЕРАТО | ТРАНСПОРТНЫЕ РЕЙСЫ', 'Не более одного рейса за сутки UTC. Нужна собственная машина.']
        for key,(name,track,minimum,needs,reward,_xp) in ROUTES.items():
            lines.append(f'• {name} [{key}]: {reward} монет, требуется ' +
                         ', '.join(f'{v} {MATERIALS[k]}' for k,v in needs.items()) +
                         (f', рейтинг на трассе «{track}» от {minimum}' if minimum else ''))
        lines.append('Пример: рейс выполнить центр скутер')
        lines.append('Сегодня: ' + ('выполнен ✅' if done else 'доступен'))
        return '\n'.join(lines)
    if len(args)!=3 or args[0].casefold()!='выполнить':
        return '🚚 Формат: рейс выполнить центр|док|периметр МАШИНА. Список: рейс.'
    route = args[1].casefold()
    vehicle = VEHICLE_NAMES.get(args[2].casefold())
    if route not in ROUTES or vehicle not in VEHICLES:
        return '❌ Неизвестный маршрут или машина. Посмотри «рейс» и «транспорт каталог».'
    title,track,minimum,needs,reward,xp = ROUTES[route]
    with service.db.transaction() as con:
        already = con.execute('''SELECT 1 FROM transport_deliveries WHERE platform=? AND user_id=? AND utc_day=?''',
                              (ctx.platform,ctx.user_id,day)).fetchone()
        if already:
            return '⌛ Один рейс в сутки уже выполнен. Попробуй завтра (UTC).'
        owned = con.execute('''SELECT tuned FROM player_vehicles WHERE platform=? AND user_id=? AND vehicle_key=?''',
                            (ctx.platform,ctx.user_id,vehicle)).fetchone()
        if not owned:
            return '❌ Сначала купи и поставь машину в гараж.'
        rating = VEHICLE_POWER[vehicle][TRACKS[track][0]] + (12 if owned['tuned'] else 0)
        if rating < minimum:
            return f'🔒 Для маршрута нужен рейтинг «{track}» от {minimum}. У твоей машины: {rating}.'
        if any(_material(con,ctx,k)<count for k,count in needs.items()):
            return '📦 Не хватает ресурсов для доставки. Посмотри «ресурсы».'
        coins = con.execute('SELECT coins FROM players WHERE platform=? AND user_id=?',
                            (ctx.platform,ctx.user_id)).fetchone()['coins']
        if coins > MAX_WALLET - reward:
            return '⚠️ Кошелёк заполнен. Освободи место перед доставкой.'
        if not _take_materials(con,ctx,needs):
            return '📦 Не удалось забрать ресурсы.'
        if not _coins(con,ctx,reward,now,'delivery:'+route):
            raise RuntimeError('Delivery payout invariant')
        con.execute('UPDATE players SET xp=xp+? WHERE platform=? AND user_id=?',
                    (xp,ctx.platform,ctx.user_id))
        con.execute('''INSERT INTO transport_deliveries
            (platform,user_id,utc_day,route_key,vehicle_key,reward,completed_at) VALUES(?,?,?,?,?,?,?)''',
            (ctx.platform,ctx.user_id,day,route,vehicle,reward,now))
        unlock(con,ctx,'delivery_first',now)
    return f'🚚 {title} завершена на машине «{VEHICLES[vehicle].title}». +{reward} монет, +{xp} XP!'


def clan_expedition(service,ctx,args:list[str],now:int) -> str:
    day=now//86400
    action=args[0].casefold() if args else ''
    if action in ('','статус') and len(args)<=1:
        with closing(service.db.connect()) as con:
            clan=clan_member(con,ctx)
            if not clan:
                return '👑 Для совместной экспедиции нужно вступить в клан. «клан помощь».'
            score=_clan_progress(con,ctx.platform,clan['id'],day)
            own=con.execute('''SELECT route_key,claimed FROM clan_expeditions
                WHERE platform=? AND user_id=? AND utc_day=?''', (ctx.platform,ctx.user_id,day)).fetchone()
        routes='\n'.join(f'• {title} [{key}]: {points} очков, комплект {tier}+, расход '+
                          ', '.join(f'{v} {MATERIALS[k]}' for k,v in cost.items())+
                          (f', {energy} энергии' if energy else '')
                          for key,(title,cost,tier,points,energy) in CLAN_RUNS.items())
        return (f'🧭 КЛАНОВАЯ ЭКСПЕДИЦИЯ | {clan["name"]}\n'
                f'Прогресс за день: {score["total"]}/{CLAN_TARGET} очков, '
                f'{score["participants"]}/{CLAN_PARTICIPANTS} участников.\n'
                f'Твой вклад: {own["route_key"] if own else "ещё не участвовал"}.\n'
                f'Награда при завершении: {CLAN_REWARD} монет, {CLAN_XP} XP каждому участнику.\n'
                +routes+'\nНачать: кланпоход идти док. Получить: кланпоход награда.')
    if action=='топ' and len(args)==1:
        # Read-only, current UTC day ranking.
        with closing(service.db.connect()) as con:
            rows=con.execute('''SELECT c.name, SUM(e.points) AS score, COUNT(*) AS members
                FROM clan_expeditions e JOIN clans c ON c.id=e.clan_id AND c.platform=e.platform
                WHERE e.platform=? AND e.utc_day=? GROUP BY e.clan_id
                ORDER BY score DESC,members DESC,c.id LIMIT 10''', (ctx.platform,day)).fetchall()
        return ('🏆 КЛАНОВЫЕ ЭКСПЕДИЦИИ | ТОП ЗА СУТКИ\n' +
                ('\n'.join(f'{i}. {r["name"]}: {r["score"]} очков ({r["members"]} участников)' for i,r in enumerate(rows,1)) if rows else 'Пока никто не участвовал.'))
    if action=='идти' and len(args)==2:
        key=args[1].casefold()
        if key not in CLAN_RUNS:
            return '🧭 Районы: док / мастерские / периметр.'
        title,needs,tier,points,energy=CLAN_RUNS[key]
        with service.db.transaction() as con:
            clan=clan_member(con,ctx)
            if not clan:
                return '⛔ Вступи в клан перед совместной экспедицией.'
            own=con.execute('SELECT 1 FROM clan_expeditions WHERE platform=? AND user_id=? AND utc_day=?',
                            (ctx.platform,ctx.user_id,day)).fetchone()
            if own:
                return '⌛ Ты уже участвовал в клановой экспедиции сегодня (UTC).'
            score=_clan_progress(con,ctx.platform,clan['id'],day)
            if score['total']>=CLAN_TARGET and score['participants']>=CLAN_PARTICIPANTS:
                return '✅ Твой клан уже завершил экспедицию сегодня. Попробуй завтра.'
            if _gear(con,ctx)<tier:
                return f'🔒 Нужен исследовательский комплект уровня {tier}+. Команда «снаряжение».'
            if any(_material(con,ctx,k)<count for k,count in needs.items()):
                return '📦 Не хватает ресурсов на подготовку экспедиции.'
            if energy:
                power=con.execute('SELECT stored FROM player_energy WHERE platform=? AND user_id=?',
                                  (ctx.platform,ctx.user_id)).fetchone()
                if not power or power['stored']<energy:
                    return f'⚡ Нужно {energy} энергии.'
            if not _take_materials(con,ctx,needs):
                return '📦 Недостаточно ресурсов.'
            if energy:
                con.execute('UPDATE player_energy SET stored=stored-? WHERE platform=? AND user_id=?',
                            (energy,ctx.platform,ctx.user_id))
            con.execute('''INSERT INTO clan_expeditions
                (platform,user_id,utc_day,clan_id,route_key,points,created_at) VALUES(?,?,?,?,?,?,?)''',
                (ctx.platform,ctx.user_id,day,clan['id'],key,points,now))
            score=_clan_progress(con,ctx.platform,clan['id'],day)
        suffix = ' 🎉 Цель достигнута! Участники могут забрать награду.' if score['total']>=CLAN_TARGET and score['participants']>=CLAN_PARTICIPANTS else ''
        return (f'🧭 {title}: +{points} очков клану «{clan["name"]}»! '
                f'Всего {score["total"]}/{CLAN_TARGET}, участников {score["participants"]}/{CLAN_PARTICIPANTS}.{suffix}')
    if action=='награда' and len(args)==1:
        with service.db.transaction() as con:
            clan=clan_member(con,ctx)
            if not clan:
                return '⛔ Награда выдаётся только участникам текущего клана.'
            own=con.execute('''SELECT clan_id,claimed FROM clan_expeditions
                WHERE platform=? AND user_id=? AND utc_day=?''', (ctx.platform,ctx.user_id,day)).fetchone()
            if not own or own['clan_id']!=clan['id']:
                return '❌ Сегодня ты не участвовал в экспедиции этого клана.'
            if own['claimed']:
                return '✅ Награда уже получена.'
            score=_clan_progress(con,ctx.platform,clan['id'],day)
            if score['total']<CLAN_TARGET or score['participants']<CLAN_PARTICIPANTS:
                return f'⏳ Не хватает участников или очков: {score["total"]}/{CLAN_TARGET}, {score["participants"]}/{CLAN_PARTICIPANTS}.'
            wallet=con.execute('SELECT coins FROM players WHERE platform=? AND user_id=?',
                               (ctx.platform,ctx.user_id)).fetchone()['coins']
            if wallet>MAX_WALLET-CLAN_REWARD:
                return '⚠️ Кошелёк переполнен. Освободи место, награда сохранится.'
            if not _coins(con,ctx,CLAN_REWARD,now,'clan-expedition'):
                raise RuntimeError('Clan expedition payout invariant')
            con.execute('UPDATE players SET xp=xp+? WHERE platform=? AND user_id=?',
                        (CLAN_XP,ctx.platform,ctx.user_id))
            con.execute('''UPDATE clan_expeditions SET claimed=1 WHERE platform=? AND user_id=? AND utc_day=? AND claimed=0''',
                        (ctx.platform,ctx.user_id,day))
            unlock(con,ctx,'clan_pathfinder',now)
        return f'🎉 Клановая экспедиция завершена! +{CLAN_REWARD} монет и +{CLAN_XP} XP.'
    return '🧭 Кланпоход: кланпоход / кланпоход идти док / кланпоход награда / кланпоход топ.'


def extra_command(service,ctx,cmd,args,now):
    if cmd=='delivery':
        return delivery(service,ctx,args,now)
    if cmd=='clanexpedition':
        return clan_expedition(service,ctx,args,now)
    return None
