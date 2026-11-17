""" careers, player businesses, production and limited daily contracts.

No paid currency, background scheduler, or network needed. SQLite transactions enforce
inventory, wallet and claim bounds even across concurrent bot processes.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass

from .features import JOBS, unlock
from .v07 import MAX_WALLET

# Names are original KVERATO game entities; values are balancing parameters.
@dataclass(frozen=True)
class BusinessType:
    name: str
    price: int
    material: str
    hourly_units: int


BUSINESSES = {
    'workshop': BusinessType('Мастерская «Контур»', 5000, 'parts', 2),
    'cafe': BusinessType('Кафе «Пульс»', 12000, 'supplies', 3),
    'lab': BusinessType('Технолаборатория «Спектр»', 30000, 'chips', 2),
    'bakery': BusinessType('Пекарня «Заря»', 6500, 'supplies', 2),
    'repair': BusinessType('Ремонтный центр «Импульс»', 8500, 'parts', 3),
    'market': BusinessType('Минимаркет «Ритм»', 16000, 'supplies', 4),
    'atelier': BusinessType('Ателье «Форма»', 19000, 'parts', 3),
    'logistics': BusinessType('Логистический узел «Транзит»', 24000, 'parts', 4),
    'greenhouse': BusinessType('Теплица «Рост»', 28000, 'supplies', 5),
    'chipplant': BusinessType('Цех «Синапс»', 42000, 'chips', 3),
    'techpark': BusinessType('Технопарк «Атлас»', 65000, 'chips', 4),
}
BUSINESS_DISPLAY = {'workshop':'мастерская','cafe':'кафе','lab':'лаборатория'}
BUSINESS_DISPLAY.update({'bakery': 'пекарня', 'repair': 'ремонт', 'market': 'минимаркет', 'atelier': 'ателье', 'logistics': 'логистика', 'greenhouse': 'теплица', 'chipplant': 'чипцех', 'techpark': 'технопарк'})
BUSINESS_ALIASES = {
    'мастерская': 'workshop', 'контур': 'workshop', 'workshop': 'workshop',
    'кафе': 'cafe', 'пульс': 'cafe', 'cafe': 'cafe',
    'лаборатория': 'lab', 'спектр': 'lab', 'lab': 'lab',
    'пекарня': 'bakery', 'bakery': 'bakery',
    'ремонт': 'repair', 'repair': 'repair',
    'минимаркет': 'market', 'market': 'market',
    'ателье': 'atelier', 'atelier': 'atelier',
    'логистика': 'logistics', 'logistics': 'logistics',
    'теплица': 'greenhouse', 'greenhouse': 'greenhouse',
    'чипцех': 'chipplant', 'chipplant': 'chipplant',
    'технопарк': 'techpark', 'techpark': 'techpark',
}
MATERIALS = {'parts': 'Детали', 'supplies': 'Продукты', 'chips': 'Микросхемы'}
MATERIAL_ALIASES = {
    'детали': 'parts', 'деталей': 'parts', 'parts': 'parts',
    'продукты': 'supplies', 'продуктов': 'supplies', 'supplies': 'supplies',
    'микросхемы': 'chips', 'микросхем': 'chips', 'chips': 'chips',
}
# resource, required units, virtual coins. Contracts limited to 3 per UTC day.
MATERIAL_DISPLAY = {'parts':'детали','supplies':'продукты','chips':'микросхемы'}
CONTRACTS = {
    'parts': (5, 450),
    'supplies': (6, 650),
    'chips': (5, 900),
}
MAX_TIER = 5
MAX_PRODUCTION_HOURS = 8
MAX_MATERIALS = 1000
MAX_CONTRACTS_DAY = 3


def career_rank(xp: int) -> int:
    return min(5, 1 + max(0, xp) // 100)


def career_progress(con, ctx, key: str):
    return con.execute('''SELECT xp, completed FROM career_progress
        WHERE platform=? AND user_id=? AND job_key=?''', (ctx.platform, ctx.user_id, key)).fetchone()


def record_career_work(con, ctx, key: str, now: int) -> tuple[int, bool]:
    """Call from same transaction as salary. XP is deterministic, not random."""
    con.execute('''INSERT INTO career_progress(platform,user_id,job_key,xp,completed)
        VALUES (?,?,?,20,1) ON CONFLICT(platform,user_id,job_key)
        DO UPDATE SET xp=xp+20,completed=completed+1''', (ctx.platform, ctx.user_id, key))
    progress = career_progress(con, ctx, key)
    gained = unlock(con, ctx, 'career3', now) if career_rank(progress['xp']) >= 3 else False
    return career_rank(progress['xp']), gained


def _business_key(text: str) -> str | None:
    return BUSINESS_ALIASES.get(text.casefold())


def _materials(con, ctx) -> dict[str, int]:
    return {r['material_key']: r['amount'] for r in con.execute('''
        SELECT material_key,amount FROM material_balances WHERE platform=? AND user_id=?''',
        (ctx.platform, ctx.user_id))}


def _businesses(con, ctx):
    return con.execute('''SELECT * FROM player_businesses WHERE platform=? AND user_id=?
        ORDER BY purchased_at,business_key''', (ctx.platform, ctx.user_id)).fetchall()


def _pending(row, now: int) -> tuple[int,int]:
    elapsed = max(0, now - row['collected_at'])
    hours = min(MAX_PRODUCTION_HOURS, elapsed // 3600)
    kind = BUSINESSES[row['business_key']]
    return hours * (kind.hourly_units + row['tier'] - 1), hours


def _wallet(con, ctx, delta: int, now: int, action: str) -> bool:
    """SQL predicate prevents negatives/overflow without reading stale balances."""
    if delta > 0:
        rc = con.execute('''UPDATE players SET coins=coins+? WHERE platform=?
            AND user_id=? AND coins<=?''',
            (delta, ctx.platform, ctx.user_id, MAX_WALLET - delta)).rowcount
    else:
        rc = con.execute('''UPDATE players SET coins=coins-? WHERE platform=?
            AND user_id=? AND coins>=?''',
            (-delta, ctx.platform, ctx.user_id, -delta)).rowcount
    if rc:
        con.execute('''INSERT INTO bot_economy_ledger
            (platform,actor_id,target_id,delta,source,created_at) VALUES (?,?,?,?,?,?)''',
            (ctx.platform,ctx.user_id,ctx.user_id,delta,'business:'+action,now))
    return bool(rc)


def _business_details(row, now: int) -> str:
    kind = BUSINESSES[row['business_key']]
    ready, hours = _pending(row, now)
    return (f'{kind.name} — ур. {row["tier"]}/{MAX_TIER}; ' 
            f'{MATERIALS[kind.material]}: {ready} готово ({hours} ч.); ' 
            f'{kind.hourly_units + row["tier"]-1} ед./ч.')


def business_command(service, ctx, args: list[str], now: int) -> str:
    db = service.db
    if not args or args[0].casefold() in ('мои','мой','список'):
        with closing(db.connect()) as con:
            holdings = _businesses(con, ctx)
        return ('🏢 КВЕРАТО | МОИ ПРЕДПРИЯТИЯ\n' +
                ('\n'.join('• '+_business_details(row, now) for row in holdings)
                 if holdings else 'Предприятий нет.') +
                '\nКоманды: бизнесы / бизнес купить мастерская / бизнес собрать мастерская / бизнес улучшить мастерская')
    action = args[0].casefold()
    if action in ('магазин','каталог','помощь'):
        return ('🏢 КАТАЛОГ ПРЕДПРИЯТИЙ\n' + '\n'.join(
            f'• {kind.name}: {kind.price:,} монет; ресурс — {MATERIALS[kind.material]}, '
            f'{kind.hourly_units} ед./час; команда: бизнес купить {BUSINESS_DISPLAY[key]}'
            for key,kind in BUSINESSES.items()).replace(',', ' ') +
            '\nОбычно максимум 2 предприятия, третье — с 3-го карьерного ранга. '
            'Производство накапливается не более чем за 8 часов.')
    if action == 'история':
        with closing(db.connect()) as con:
            records = con.execute('''SELECT delta,source FROM bot_economy_ledger
                WHERE platform=? AND target_id=? AND source LIKE 'business:%'
                ORDER BY id DESC LIMIT 8''', (ctx.platform,ctx.user_id)).fetchall()
        return '📜 ИСТОРИЯ ПРЕДПРИЯТИЙ\n' + ('\n'.join(
            f'{row["delta"]:+} монет — {row["source"][9:]}' for row in records) if records else 'Пока нет операций.')
    if action not in ('купить','улучшить','собрать','продать') or len(args) < 2:
        return ('🏢 Доступно: бизнес, бизнес каталог, бизнес купить мастерская, '
                'бизнес собрать мастерская, бизнес улучшить мастерская, '
                'бизнес продать мастерская подтвердить, бизнес история.')
    key = _business_key(args[1])
    if not key or (len(args) != 2 and not (action == 'продать' and len(args) == 3 and args[2].casefold() == 'подтвердить')):
        return '⚠ Укажи название из «бизнес каталог». Используй ключ в квадратных скобках.'
    kind = BUSINESSES[key]
    if action == 'продать' and (len(args) != 3 or args[2].casefold() != 'подтвердить'):
        return '⚠ Подтверди продажу: бизнес продать '+args[1]+' подтвердить (возврат 40% затрат; несобранное производство исчезнет).'
    with db.transaction() as con:
        row = con.execute('''SELECT * FROM player_businesses WHERE platform=? AND user_id=?
            AND business_key=?''',(ctx.platform,ctx.user_id,key)).fetchone()
        if action == 'купить':
            if row:
                return '✅ Это предприятие уже принадлежит тебе.'
            total = len(_businesses(con,ctx))
            max_rank_row = con.execute('''SELECT MAX(xp) AS max_xp FROM career_progress
                WHERE platform=? AND user_id=?''', (ctx.platform,ctx.user_id)).fetchone()
            capacity = 3 if max_rank_row['max_xp'] is not None and career_rank(max_rank_row['max_xp']) >= 3 else 2
            if total >= capacity:
                return f'❌ Лимит предприятий: {capacity}. Для третьего достигни 3-го ранга карьеры.'
            if not _wallet(con,ctx,-kind.price,now,'purchase:'+key):
                return f'❌ Не хватает монет. Требуется {kind.price:,}.'.replace(',',' ')
            con.execute('''INSERT INTO player_businesses
                (platform,user_id,business_key,tier,invested,purchased_at,collected_at)
                VALUES (?,?,?,1,?,?,?)''', (ctx.platform,ctx.user_id,key,kind.price,now,now))
            unlocked = unlock(con, ctx, 'entrepreneur', now)
            return f'✅ Куплено: {kind.name} за {kind.price:,} монет.'.replace(',',' ') + (' 🏆 Достижение: Предприниматель.' if unlocked else '')
        if row is None:
            return '❌ У тебя нет такого предприятия. Список: бизнес каталог.'
        if action == 'улучшить':
            if _pending(row,now)[1]:
                return '📦 Сначала собери готовую продукцию, затем улучшай предприятие.'
            if row['tier'] >= MAX_TIER:
                return f'✅ Достигнут максимальный уровень {MAX_TIER}.'
            cost = kind.price * row['tier'] // 2
            if not _wallet(con,ctx,-cost,now,'upgrade:'+key):
                return f'❌ Для улучшения требуется {cost:,} монет.'.replace(',',' ')
            con.execute('''UPDATE player_businesses SET tier=tier+1, invested=invested+?
                WHERE platform=? AND user_id=? AND business_key=?''',
                (cost,ctx.platform,ctx.user_id,key))
            # No full hours remain unclaimed; future hours use the new rate.
            upgraded = row['tier']+1
            unlocked = unlock(con,ctx,'industrialist',now) if upgraded >= 3 else False
            return f'✅ {kind.name}: уровень {upgraded}/{MAX_TIER}. Уплачено {cost:,} монет.'.replace(',',' ') + (' 🏆 Достижение: Индустриалист.' if unlocked else '')
        if action == 'продать':
            refund = row['invested'] * 40 // 100
            if not _wallet(con,ctx,refund,now,'sell:'+key):
                return '⚠ Лимит кошелька. Сначала освободи место для возврата монет.'
            con.execute('''DELETE FROM player_businesses WHERE platform=? AND user_id=?
                AND business_key=?''', (ctx.platform,ctx.user_id,key))
            return f'✅ {kind.name} продано. Возвращено {refund:,} монет (40% затрат).'.replace(',',' ')
        # Collect full hours only; fractional hours carry forward. Capped at 8h even
        # if the user was offline for days. Never allow negative or overflowed stacks.
        ready,hours = _pending(row,now)
        if not hours:
            return '⏳ Производство ещё не готово. Первая партия появится через час после покупки или сбора.'
        inventory = _materials(con,ctx)
        quantity = min(ready, MAX_MATERIALS-inventory.get(kind.material,0))
        if quantity <= 0:
            return f'📦 Хранилище переполнено ({MAX_MATERIALS} ед.). Выполни контракты, чтобы освободить место.'
        con.execute('''INSERT INTO material_balances(platform,user_id,material_key,amount)
            VALUES(?,?,?,?) ON CONFLICT(platform,user_id,material_key)
            DO UPDATE SET amount=amount+excluded.amount''',
            (ctx.platform,ctx.user_id,kind.material,quantity))
        # Cap elapsed time; no replaying unclaimed hours via repeated collection.
        collected_at = now - max(0,now-row['collected_at'])%3600
        con.execute('''UPDATE player_businesses SET collected_at=? WHERE platform=?
            AND user_id=? AND business_key=?''', (collected_at,ctx.platform,ctx.user_id,key))
        return f'📦 Склад пополнен: +{quantity} ед. «{MATERIALS[kind.material]}». Всего: {inventory.get(kind.material,0)+quantity}/{MAX_MATERIALS}.'


def contract_command(service, ctx, args: list[str], now: int) -> str:
    if not args:
        with closing(service.db.connect()) as con:
            counts = con.execute('''SELECT completed FROM daily_contracts
                WHERE platform=? AND user_id=? AND utc_day=?''',
                (ctx.platform,ctx.user_id,now//86400)).fetchone()
        used = counts['completed'] if counts else 0
        return ('📜 КВЕРАТО | КОНТРАКТЫ ('+str(used)+'/'+str(MAX_CONTRACTS_DAY)+' за сутки UTC)\n' +
                '\n'.join(f'• {MATERIALS[k]}: {amount} ед. → {reward} монет. '
                          f'Команда: контракт выполнить {MATERIAL_DISPLAY[k]}' for k,(amount,reward) in CONTRACTS.items()))
    key = MATERIAL_ALIASES.get(args[-1].casefold())
    if len(args) == 2 and args[0].casefold() != 'выполнить':
        key = None
    if len(args) not in (1,2) or not key:
        return '⚠ Формат: контракт выполнить детали (или продукты, микросхемы).'
    need,reward = CONTRACTS[key]
    with service.db.transaction() as con:
        row = con.execute('''SELECT completed FROM daily_contracts WHERE platform=?
            AND user_id=? AND utc_day=?''', (ctx.platform,ctx.user_id,now//86400)).fetchone()
        if row and row['completed'] >= MAX_CONTRACTS_DAY:
            return '⏳ Дневной лимит: 3 контракта. Новые заказы завтра по UTC.'
        used = con.execute('''UPDATE material_balances SET amount=amount-?
            WHERE platform=? AND user_id=? AND material_key=? AND amount>=?''',
            (need,ctx.platform,ctx.user_id,key,need)).rowcount
        if not used:
            return f'❌ Недостаточно ресурсов «{MATERIALS[key]}»: нужно {need} ед.'
        if not _wallet(con,ctx,reward,now,'contract:'+key):
            # early return commits transaction! Restore resources before returning.
            con.execute('''UPDATE material_balances SET amount=amount+?
                WHERE platform=? AND user_id=? AND material_key=?''',
                (need,ctx.platform,ctx.user_id,key))
            return '⚠ Достигнут лимит кошелька. Продажа отменена, ресурсы сохранены.'
        con.execute('''INSERT INTO daily_contracts(platform,user_id,utc_day,completed)
            VALUES(?,?,?,1) ON CONFLICT(platform,user_id,utc_day)
            DO UPDATE SET completed=completed+1''',
            (ctx.platform,ctx.user_id,now//86400))
        total = (row['completed'] if row else 0)+1
        achievement = unlock(con,ctx,'first_contract',now)
        from .v13 import activity as season_activity
        season_activity(con,ctx,'contract',now)
    return f'✅ Контракт выполнен: −{need} «{MATERIALS[key]}», +{reward} монет. Сегодня {total}/{MAX_CONTRACTS_DAY}.' + (' 🏆 Достижение: Первый контракт.' if achievement else '')


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    if cmd == 'career':
        with closing(service.db.connect()) as con:
            p = service._player(con,ctx)
            history = {row['job_key']:row for row in con.execute('''SELECT job_key,xp,completed
                FROM career_progress WHERE platform=? AND user_id=?''', (ctx.platform,ctx.user_id))}
        return ('💼 КВЕРАТО | КАРЬЕРА\n' + '\n'.join(
            f'{"➡️" if key == p["job_key"] else "•"} {name}: ранг {career_rank(history[key]["xp"] if key in history else 0)}/5; '
            f'XP {history[key]["xp"] if key in history else 0}; работ {history[key]["completed"] if key in history else 0}'
            for key,(name,_,_) in JOBS.items()) + '\nКаждая работа даёт 20 карьерного XP. Новый ранг каждые 100 XP, до ранга 5.\n'
            'Бонус к заработку: +10 монет за ранг сверх первого (до +40).')
    if cmd == 'businesses':
        return business_command(service,ctx,['каталог'],now)
    if cmd == 'business':
        return business_command(service,ctx,args,now)
    if cmd == 'materials':
        with closing(service.db.connect()) as con:
            amounts = _materials(con,ctx)
        return '📦 КВЕРАТО | РЕСУРСЫ\n' + '\n'.join(
            f'{name}: {amounts.get(key,0)}/{MAX_MATERIALS}' for key,name in MATERIALS.items())
    if cmd == 'contracts':
        return contract_command(service,ctx,args,now)
    return None
