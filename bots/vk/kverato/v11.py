""" explorable districts, one-step adventures, equipment and city efforts.

Virtual resources only. All mutating paths run in BEGIN IMMEDIATE transactions,
with UTC day limits, bounded rewards and idempotent claims.
"""
from __future__ import annotations

from contextlib import closing

from .features import unlock
from .v07 import MAX_WALLET
from .v08 import MATERIALS, MATERIAL_ALIASES, _materials
from .v09 import _material, _take_materials, _coins

# Title, minimum XP, primary reward material. Arenas are navigation only.
DISTRICTS = {
    'центр': ('Центр «Меридиан»', 0, 'supplies'),
    'мастерские': ('Квартал «Контур»', 150, 'parts'),
    'док': ('Транспортный док', 400, 'supplies'),
    'периметр': ('Внешний периметр', 900, 'chips'),
}
OTHER_DISTRICTS = {'арена': 'Арена — гонки и турниры',
                   'цитадель': 'Цитадель — кланы и совместные миссии'}
EVENTS = ('Заброшенная станция', 'Скрытый тайник', 'Неизвестный сигнал')
RELICS = {'signal': 'Сигнальный кристалл',
          'coil': 'Энергетическое кольцо',
          'map': 'Карта старого города',
          'beacon': 'Маяк странника',
          'anchor': 'Якорь дока',
          'badge': 'Знак старого мастера',
          'chip': 'Архивный чип',
          'lens': 'Линза Авроры',
          'tablet': 'Табличка Сферы',
          'totem': 'Тотем Периметра'}
MAX_RUNS_DAY = 3
RUN_TTL = 3600
MAX_MATERIAL = 1000
MAX_RELICS = 30
PROJECT_GOAL = 45
PROJECT_PER_USER = 20
PROJECT_MIN_USERS = 3
PROJECT_REWARD = 150
EQUIPMENT_RECIPES = {
    1: ({'parts': 5, 'chips': 2}, 15),
    2: ({'parts': 10, 'chips': 5}, 30),
    3: ({'parts': 20, 'chips': 10}, 60),
}


def _xp(con, ctx) -> int:
    row = con.execute('SELECT xp FROM players WHERE platform=? AND user_id=?',
                      (ctx.platform, ctx.user_id)).fetchone()
    return int(row[0]) if row else 0


def _gear(con, ctx) -> int:
    row = con.execute('SELECT tier FROM explorer_gear WHERE platform=? AND user_id=?',
                      (ctx.platform,ctx.user_id)).fetchone()
    return row['tier'] if row else 0


def _active(con, ctx):
    return con.execute('''SELECT * FROM expedition_runs WHERE platform=? AND user_id=?
        AND status='active' ORDER BY id DESC LIMIT 1''', (ctx.platform,ctx.user_id)).fetchone()


def _retire_expired(con, ctx, now: int) -> bool:
    return bool(con.execute('''UPDATE expedition_runs SET status='expired', finished_at=?
        WHERE platform=? AND user_id=? AND status='active' AND expires_at<=?''',
        (now,ctx.platform,ctx.user_id,now)).rowcount)


def _read_materials(con, ctx) -> str:
    inv = _materials(con,ctx)
    return ', '.join(f'{MATERIALS[key]}: {inv.get(key,0)}' for key in MATERIALS)


def city_command(service,ctx,args:list[str],now:int)->str:
    district = args[0].casefold() if args else ''
    with closing(service.db.connect()) as con:
        experience = _xp(con,ctx)
    if district:
        if district in OTHER_DISTRICTS:
            return '🌆 ' + OTHER_DISTRICTS[district] + '. Используй «гонка», «турнир» или «клан».'
        if district not in DISTRICTS:
            return '❌ Неизвестный район. Введи «город» для списка.'
        title,min_xp,material=DISTRICTS[district]
        if experience<min_xp:
            return f'🔒 {title} открывается при {min_xp} XP. Сейчас {experience} XP.'
        return (f'🌆 {title}\nДоступно для исследований. Основная находка: {MATERIALS[material]}.\n'
                f'Выход: «экспедиция начать {district}». Положено до {MAX_RUNS_DAY} экспедиций за сутки UTC.')
    rows=[]
    for key,(title,min_xp,_) in DISTRICTS.items():
        flag='✅' if experience>=min_xp else f'🔒 {min_xp} XP'
        rows.append(f'{flag} {title} — район {key}')
    rows.extend('🏟 '+name for name in OTHER_DISTRICTS.values())
    return ('🌆 КВЕРАТО | ГОРОД\n'+'\n'.join(rows)+'\n\n'
            'Команды: район центр / экспедиция / снаряжение / коллекция / проект.')


def equipment_command(service,ctx,args:list[str],now:int)->str:
    action = args[0].casefold() if args else ''
    if action not in ('','улучшить') or len(args)>1:
        return '🧰 Формат: снаряжение / снаряжение улучшить.'
    if not action:
        with closing(service.db.connect()) as con:
            tier=_gear(con,ctx)
            next_cost=EQUIPMENT_RECIPES.get(tier+1)
        msg=f'🧰 КВЕРАТО | ИССЛЕДОВАТЕЛЬ\nКомплект: {tier}/3\nШанс рискованной находки: {48+12*tier}%.'
        if next_cost:
            needs,energy=next_cost
            msg+=f'\nСледующее улучшение: {needs["parts"]} деталей, {needs["chips"]} микросхем и {energy} энергии.'
        return msg+'\nКоманда: снаряжение улучшить.'
    with service.db.transaction() as con:
        tier=_gear(con,ctx)
        if tier>=3:
            return '🧰 Снаряжение уже максимального уровня.'
        needs,energy=EQUIPMENT_RECIPES[tier+1]
        power=con.execute('SELECT stored FROM player_energy WHERE platform=? AND user_id=?',
                          (ctx.platform,ctx.user_id)).fetchone()
        if not power or power['stored']<energy or any(_material(con,ctx,k)<v for k,v in needs.items()):
            return f'❌ Для улучшения нужны {needs["parts"]} деталей, {needs["chips"]} микросхем и {energy} энергии.'
        if not _take_materials(con,ctx,needs):
            return '❌ Недостаточно ресурсов.'
        con.execute('UPDATE player_energy SET stored=stored-? WHERE platform=? AND user_id=?',
                    (energy,ctx.platform,ctx.user_id))
        con.execute('''INSERT INTO explorer_gear(platform,user_id,tier) VALUES(?,?,?)
            ON CONFLICT(platform,user_id) DO UPDATE SET tier=excluded.tier''',
            (ctx.platform,ctx.user_id,tier+1))
    return f'✅ Исследовательский комплект улучшен до уровня {tier+1}/3.'


def _run_text(run, gear: int) -> str:
    title = DISTRICTS[run['district']][0]
    return (f'🧭 ЭКСПЕДИЦИЯ #{run["id"]}\nРайон: {title}\n'
            f'Событие: {EVENTS[run["event_id"]]}\n'
            f'🧰 Комплект: {gear}/3\n'
            'Выбери действие:\n'
            '• экспедиция осмотреть — надёжные материалы и 15 XP\n'
            '• экспедиция рискнуть — шанс на редкую находку и 30 XP\n'
            '• экспедиция запитать — 10 энергии, усиленная добыча и 35 XP\n'
            '• экспедиция уйти — завершить без наград\n'
            'Время на выбор: 60 минут после старта.')


def expedition_command(service,ctx,args:list[str],now:int)->str:
    action=args[0].casefold() if args else ''
    if action=='выбор' and len(args)==2:
        action=args[1].casefold()
        args=[action]
    if action=='начать':
        if len(args)!=2 or args[1].casefold() not in DISTRICTS:
            return '🧭 Формат: экспедиция начать центр/мастерские/док/периметр.'
        location=args[1].casefold()
        with service.db.transaction() as con:
            _retire_expired(con,ctx,now)
            if _active(con,ctx):
                return '⌛ Есть незавершённая экспедиция. Введи «экспедиция».'
            title, min_xp, mat = DISTRICTS[location]
            if _xp(con,ctx)<min_xp:
                return f'🔒 {title} доступен после {min_xp} XP.'
            day=now//86400
            row=con.execute('SELECT starts FROM expedition_daily WHERE platform=? AND user_id=? AND utc_day=?',
                            (ctx.platform,ctx.user_id,day)).fetchone()
            if row and row['starts']>=MAX_RUNS_DAY:
                return '⏳ Лимит: три экспедиции за сутки UTC. Попробуй завтра.'
            con.execute('''INSERT INTO expedition_daily(platform,user_id,utc_day,starts) VALUES(?,?,?,1)
                ON CONFLICT(platform,user_id,utc_day) DO UPDATE SET starts=starts+1''',
                (ctx.platform,ctx.user_id,day))
            event=service.randbelow(len(EVENTS))
            cur=con.execute('''INSERT INTO expedition_runs(platform,user_id,district,event_id,status,
                            created_at,expires_at) VALUES(?,?,?,?,'active',?,?)''',
                            (ctx.platform,ctx.user_id,location,event,now,now+RUN_TTL))
            run=con.execute('SELECT * FROM expedition_runs WHERE id=?',(cur.lastrowid,)).fetchone()
            return _run_text(run,_gear(con,ctx))
    elif action in ('','статус') and len(args)<=1:
        with service.db.transaction() as con:
            expired=_retire_expired(con,ctx,now)
            run=_active(con,ctx)
            day=now//86400
            progress=con.execute('SELECT starts FROM expedition_daily WHERE platform=? AND user_id=? AND utc_day=?',
                                 (ctx.platform,ctx.user_id,day)).fetchone()
            if run:
                return _run_text(run,_gear(con,ctx))
        return (('⌛ Прошлая экспедиция истекла.\n' if expired else '')+
                f'🧭 Экспедиций сегодня: {progress["starts"] if progress else 0}/{MAX_RUNS_DAY}.\n'
                'Начать: экспедиция начать центр. Посмотри другие районы: город.')
    elif action in ('осмотреть','рискнуть','запитать','уйти') and len(args) in (1,2):
        if len(args)==2 and args[0].casefold()!='выбор':
            return '❌ Лишние аргументы. Пример: экспедиция осмотреть.'
        with service.db.transaction() as con:
            if _retire_expired(con,ctx,now):
                return '⌛ Экспедиция истекла через 60 минут. Попробуй начать новую.'
            run=_active(con,ctx)
            if not run:
                return '❌ Нет активной экспедиции. Введи «экспедиция начать центр».'
            if action=='уйти':
                con.execute('UPDATE expedition_runs SET status=?,choice=?,finished_at=? WHERE id=?',
                            ('abandoned',action,now,run['id']))
                return '🚪 Экспедиция завершена без находок.'
            gear=_gear(con,ctx)
            if action=='запитать':
                power=con.execute('SELECT stored FROM player_energy WHERE platform=? AND user_id=?',
                                  (ctx.platform,ctx.user_id)).fetchone()
                if not power or power['stored']<10:
                    return '⚡ Нужно 10 единиц энергии с энергостанции.'
            material=DISTRICTS[run['district']][2]
            reward_xp={'осмотреть':15,'рискнуть':30,'запитать':35}[action]
            relic=None
            if action=='осмотреть':
                amount=1+service.randbelow(2)
                detail='Безопасный поиск'
            elif action=='рискнуть':
                success=service.randbelow(100)<48+12*gear
                amount=(3+service.randbelow(2)) if success else 1
                detail='Удачная вылазка' if success else 'Риск не оправдался'
                if success and service.randbelow(100)<25:
                    relic=tuple(RELICS)[service.randbelow(len(RELICS))]
            else:
                amount=4+service.randbelow(2)
                detail='Станция запитана'
                if service.randbelow(100)<15:
                    relic=tuple(RELICS)[service.randbelow(len(RELICS))]
            if _material(con,ctx,material)>MAX_MATERIAL-amount:
                return f'📦 Склад {MATERIALS[material].lower()} переполнен. Освободи место.'
            if relic:
                old=con.execute('''SELECT amount FROM explorer_relics WHERE platform=? AND user_id=? AND relic_key=?''',
                                (ctx.platform,ctx.user_id,relic)).fetchone()
                if old and old['amount']>=MAX_RELICS:
                    return '🎖 Коллекция переполнена. Не удалось забрать добычу.'
            # Preflight checks are above: now perform every write atomically.
            if action=='запитать':
                con.execute('UPDATE player_energy SET stored=stored-10 WHERE platform=? AND user_id=?',
                            (ctx.platform,ctx.user_id))
            con.execute('''INSERT INTO material_balances(platform,user_id,material_key,amount)
                VALUES(?,?,?,?) ON CONFLICT(platform,user_id,material_key)
                DO UPDATE SET amount=amount+excluded.amount''', (ctx.platform,ctx.user_id,material,amount))
            if relic:
                con.execute('''INSERT INTO explorer_relics(platform,user_id,relic_key,amount) VALUES(?,?,?,1)
                    ON CONFLICT(platform,user_id,relic_key) DO UPDATE SET amount=amount+1''',
                    (ctx.platform,ctx.user_id,relic))
                unlock(con,ctx,'explorer_relic',now)
            con.execute('UPDATE players SET xp=xp+? WHERE platform=? AND user_id=?',
                        (reward_xp,ctx.platform,ctx.user_id))
            con.execute('''INSERT INTO explorer_progress(platform,user_id,completed,finds)
                VALUES(?,?,1,?) ON CONFLICT(platform,user_id)
                DO UPDATE SET completed=completed+1,finds=finds+excluded.finds''',
                (ctx.platform,ctx.user_id,int(relic is not None)))
            unlock(con,ctx,'explorer_first',now)
            from .v13 import activity as season_activity
            season_activity(con,ctx,'expedition',now)
            con.execute('''UPDATE expedition_runs SET status='finished',choice=?,reward_material=?,
                reward_amount=?,reward_relic=?,finished_at=? WHERE id=? AND status='active' ''',
                (action,material,amount,relic,now,run['id']))
            return (f'✅ {detail}! Добыто: {MATERIALS[material]} ×{amount}; +{reward_xp} XP.'+
                    (f' 🎖 Редкая находка: {RELICS[relic]}.' if relic else ''))
    return ('🧭 Команды: экспедиция / экспедиция начать центр / экспедиция осмотреть / '
            'экспедиция рискнуть / экспедиция запитать / экспедиция уйти.')


def collection_command(service,ctx,args:list[str],now:int)->str:
    with closing(service.db.connect()) as con:
        relics={r['relic_key']:r['amount'] for r in con.execute('''SELECT relic_key,amount FROM explorer_relics
            WHERE platform=? AND user_id=?''',(ctx.platform,ctx.user_id))}
        progress=con.execute('SELECT completed,finds FROM explorer_progress WHERE platform=? AND user_id=?',
                             (ctx.platform,ctx.user_id)).fetchone()
    lines=[f'• {name}: {relics.get(key,0)}' for key,name in RELICS.items()]
    return ('🎖 КВЕРАТО | КОЛЛЕКЦИЯ\n'+'\n'.join(lines)+
            f'\nЭкспедиций завершено: {progress["completed"] if progress else 0}; редких находок: {progress["finds"] if progress else 0}.')


def _project_progress(con,platform,day):
    row=con.execute('''SELECT COALESCE(SUM(points),0) AS score,
        COALESCE(SUM(CASE WHEN points>=5 THEN 1 ELSE 0 END),0) AS participants
        FROM city_project_contributions WHERE platform=? AND utc_day=?''',
        (platform,day)).fetchone()
    return row['score'],row['participants']


def project_command(service,ctx,args:list[str],now:int)->str:
    action=args[0].casefold() if args else ''
    day=now//86400
    if action in ('','статус') and len(args)<=1:
        with closing(service.db.connect()) as con:
            total,participants=_project_progress(con,ctx.platform,day)
            contribution=con.execute('''SELECT points,claimed FROM city_project_contributions
                WHERE platform=? AND utc_day=? AND user_id=?''',(ctx.platform,day,ctx.user_id)).fetchone()
        return (f'🏗 ГОРОДСКОЙ ПРОЕКТ | Восстановление Меридиана\n'
                f'Прогресс: {min(total,PROJECT_GOAL)}/{PROJECT_GOAL} очков; участники: {participants}/{PROJECT_MIN_USERS}.\n'
                f'Твой вклад: {contribution["points"] if contribution else 0}/{PROJECT_PER_USER}.\n'
                f'Награда: {PROJECT_REWARD} монет и 75 XP участникам с вкладом от 5 очков.\n'
                'Вклад: проект вклад детали 5 (или продукты/микросхемы).\n'
                'Очки: детали ×1, продукты ×1, микросхемы ×2.\n'
                'После завершения: проект награда.')
    if action=='вклад':
        if len(args)!=3 or args[1].casefold() not in MATERIAL_ALIASES or not args[2].isascii() or not args[2].isdecimal() or len(args[2])>3:
            return '🏗 Формат: проект вклад детали 5 (1–20 единиц).'
        material=MATERIAL_ALIASES[args[1].casefold()]
        count=int(args[2])
        if count<1 or count>20:
            return '❌ Можно внести от 1 до 20 ресурсов за команду.'
        points=count*(2 if material=='chips' else 1)
        with service.db.transaction() as con:
            total,participants=_project_progress(con,ctx.platform,day)
            if total>=PROJECT_GOAL and participants>=PROJECT_MIN_USERS:
                return '✅ Общий проект завершён. Используй «проект награда».'
            past=con.execute('''SELECT points FROM city_project_contributions WHERE platform=? AND utc_day=? AND user_id=?''',
                             (ctx.platform,day,ctx.user_id)).fetchone()
            if (past['points'] if past else 0)+points>PROJECT_PER_USER:
                return f'❌ Лимит вклада: {PROJECT_PER_USER} очков в сутки UTC.'
            if _material(con,ctx,material)<count:
                return f'❌ Недостаточно ресурсов: {MATERIALS[material]}.'
            if not _take_materials(con,ctx,{material:count}):
                return '❌ Не удалось передать ресурсы.'
            con.execute('''INSERT INTO city_project_contributions(platform,utc_day,user_id,points)
                VALUES(?,?,?,?) ON CONFLICT(platform,utc_day,user_id)
                DO UPDATE SET points=points+excluded.points''',(ctx.platform,day,ctx.user_id,points))
            total,participants=_project_progress(con,ctx.platform,day)
        return (f'🏗 Передано: {MATERIALS[material]} ×{count}, вклад +{points} очков.\n'
                f'Общий прогресс: {total}/{PROJECT_GOAL}, участников: {participants}.'+
                (' 🎉 Проект завершён! Забирай награду.' if total>=PROJECT_GOAL and participants>=PROJECT_MIN_USERS else ''))
    if action=='награда' and len(args)==1:
        with service.db.transaction() as con:
            total,participants=_project_progress(con,ctx.platform,day)
            if total<PROJECT_GOAL or participants<PROJECT_MIN_USERS:
                return '⌛ Проект ещё не завершён (нужно 45 очков и 3 участника).'
            row=con.execute('''SELECT points,claimed FROM city_project_contributions
                WHERE platform=? AND utc_day=? AND user_id=?''',(ctx.platform,day,ctx.user_id)).fetchone()
            if not row or row['points']<5:
                return '❌ Награда положена только участникам, внёсшим минимум 5 очков.'
            if row['claimed']:
                return '✅ Награда за проект уже получена.'
            if not _coins(con,ctx,PROJECT_REWARD,now,'city-project'):
                return '⚠ Кошелёк переполнен. Освободи место, награда сохранится.'
            con.execute('UPDATE players SET xp=xp+75 WHERE platform=? AND user_id=?',
                        (ctx.platform,ctx.user_id))
            con.execute('''UPDATE city_project_contributions SET claimed=1 WHERE platform=? AND utc_day=? AND user_id=? AND claimed=0''',
                        (ctx.platform,day,ctx.user_id))
            unlock(con,ctx,'city_builder',now)
        return f'🎉 Награда за проект: +{PROJECT_REWARD} монет и +75 XP!'
    return '🏗 Команды: проект / проект вклад детали 5 / проект награда.'


def extra_command(service,ctx,cmd:str,args:list[str],now:int)->str|None:
    if cmd=='city':
        return city_command(service,ctx,args,now)
    if cmd=='district':
        return city_command(service,ctx,args,now)
    if cmd=='expedition':
        return expedition_command(service,ctx,args,now)
    if cmd=='equipment':
        return equipment_command(service,ctx,args,now)
    if cmd=='collection':
        return collection_command(service,ctx,args,now)
    if cmd=='project':
        return project_command(service,ctx,args,now)
    return None
