"""v1.3: complex industrial orders, using existing inventory and career systems.

All balance, career, quota and resource changes happen in one SQLite BEGIN IMMEDIATE.
The order board rotates daily in UTC and is deterministic across process restarts.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass

from .features import unlock
from .v07 import MAX_WALLET
from .v08 import MATERIALS, MAX_CONTRACTS_DAY, career_rank


@dataclass(frozen=True)
class IndustryOrder:
    name: str
    materials: dict[str, int]
    payout: int
    min_rank: int = 1


ORDERS = {
    'supply_run': IndustryOrder('Городские поставки', {'parts': 6, 'supplies': 5}, 1350),
    'circuit_run': IndustryOrder('Техническая сборка', {'parts': 6, 'chips': 4}, 1450),
    'city_food': IndustryOrder('Умный кейтеринг', {'supplies': 7, 'chips': 3}, 1500),
    'repair_kits': IndustryOrder('Ремонтные комплекты', {'parts': 5, 'supplies': 5, 'chips': 2}, 1550),
    'armor': IndustryOrder('Каркасный заказ', {'alloy': 2, 'parts': 4}, 1250, 2),
    'textile': IndustryOrder('Индустриальная ткань', {'fiber': 3, 'supplies': 5}, 1950, 2),
    'circuits': IndustryOrder('Печатные контроллеры', {'circuit': 2, 'chips': 3}, 2300, 2),
    'optics': IndustryOrder('Оптические датчики', {'optic': 2, 'circuit': 1}, 2150, 3),
    'power_cell': IndustryOrder('Запасные энергоблоки', {'battery': 2, 'parts': 3}, 2050, 3),
    'city_beacon': IndustryOrder('Городской маяк', {'crystal': 3, 'alloy': 2, 'supplies': 3}, 3100, 4),
}
BASIC_KEYS = tuple(list(ORDERS)[:4])
ADVANCED_KEYS = tuple(list(ORDERS)[4:])
ORDER_XP = 25


def today_orders(utc_day: int) -> tuple[str, ...]:
    """Two accessible beginner orders and two higher-skill offers each UTC day."""
    return (
        BASIC_KEYS[utc_day % 4], BASIC_KEYS[(utc_day + 2) % 4],
        ADVANCED_KEYS[utc_day % 6], ADVANCED_KEYS[(utc_day + 3) % 6],
    )


def _used(con, ctx, day: int) -> int:
    row = con.execute('''SELECT completed FROM daily_contracts
        WHERE platform=? AND user_id=? AND utc_day=?''',
        (ctx.platform, ctx.user_id, day)).fetchone()
    return row['completed'] if row else 0


def _rank(con, ctx) -> tuple[str, int]:
    player = con.execute('''SELECT job_key FROM players WHERE platform=? AND user_id=?''',
                         (ctx.platform, ctx.user_id)).fetchone()
    job = player['job_key']
    row = con.execute('''SELECT xp FROM career_progress WHERE platform=? AND user_id=? AND job_key=?''',
                      (ctx.platform, ctx.user_id, job)).fetchone()
    return job, career_rank(row['xp'] if row else 0)


def order_command(service, ctx, args: list[str], now: int) -> str:
    day = now // 86400
    if not args or args[0].casefold() in ('список', 'доска', 'каталог'):
        with closing(service.db.connect()) as con:
            used = _used(con, ctx, day)
            job, rank = _rank(con, ctx)
            completed = {r['order_key'] for r in con.execute('''SELECT order_key FROM industry_order_log
                WHERE platform=? AND user_id=? AND utc_day=?''', (ctx.platform, ctx.user_id, day))}
        lines = [f'🏭 КВЕРАТО | ПРОИЗВОДСТВЕННЫЕ ЗАКАЗЫ ({used}/{MAX_CONTRACTS_DAY} за сутки UTC)',
                 f'💼 Ранг текущей профессии: {rank}/5. Лимит общий с обычными контрактами.']
        for key in today_orders(day):
            order = ORDERS[key]
            requirements = ', '.join(f'{MATERIALS[k]} ×{amount}' for k, amount in order.materials.items())
            state = '✅ Выполнено' if key in completed else ('🔒' if rank < order.min_rank else '📦')
            lines.append(f'{state} {order.name} [{key}] — {requirements} → {order.payout} монет; ранг {order.min_rank}+')
        lines.append('Команда: заказ выполнить supply_run. Новые предложения завтра по UTC.')
        lines.append('За сложный заказ: +25 карьерного XP. Производственные цепочки: цепочки.')
        return '\n'.join(lines)
    if len(args) != 2 or args[0].casefold() != 'выполнить':
        return '⚠ Формат: заказ выполнить supply_run. Доступные предложения: заказы.'
    key = args[1].casefold()
    if key not in ORDERS or key not in today_orders(day):
        return '❌ Заказ недоступен сегодня. Посмотри «заказы».'
    order = ORDERS[key]
    with service.db.transaction() as con:
        used = _used(con, ctx, day)
        if used >= MAX_CONTRACTS_DAY:
            return '⏳ Лимит 3 контракта/заказа за сутки UTC исчерпан.'
        job, rank = _rank(con, ctx)
        if rank < order.min_rank:
            return f'🔒 Требуется ранг {order.min_rank} активной профессии. Сейчас: {rank}/5.'
        prior = con.execute('''SELECT 1 FROM industry_order_log
            WHERE platform=? AND user_id=? AND utc_day=? AND order_key=?''',
            (ctx.platform, ctx.user_id, day, key)).fetchone()
        if prior:
            return '✅ Этот заказ уже выполнен сегодня.'
        player = con.execute('''SELECT coins FROM players WHERE platform=? AND user_id=?''',
                             (ctx.platform, ctx.user_id)).fetchone()
        if player['coins'] > MAX_WALLET - order.payout:
            return '⚠ Кошелёк переполнен. Освободи место: материалы не списаны.'
        # Pre-check all resources before touching any balance, then a single transaction commits all.
        owned = {r['material_key']: r['amount'] for r in con.execute('''SELECT material_key,amount
            FROM material_balances WHERE platform=? AND user_id=?''', (ctx.platform, ctx.user_id))}
        missing = [f'{MATERIALS[mat]}: {amount-owned.get(mat, 0)}' for mat, amount in order.materials.items()
                   if owned.get(mat, 0) < amount]
        if missing:
            return '❌ Не хватает ресурсов: ' + ', '.join(missing) + '.'
        for mat, amount in order.materials.items():
            con.execute('''UPDATE material_balances SET amount=amount-?
                WHERE platform=? AND user_id=? AND material_key=? AND amount>=?''',
                (amount, ctx.platform, ctx.user_id, mat, amount))
        con.execute('''UPDATE players SET coins=coins+? WHERE platform=? AND user_id=?''',
                    (order.payout, ctx.platform, ctx.user_id))
        con.execute('''INSERT INTO daily_contracts(platform,user_id,utc_day,completed) VALUES(?,?,?,1)
            ON CONFLICT(platform,user_id,utc_day) DO UPDATE SET completed=completed+1''',
            (ctx.platform, ctx.user_id, day))
        con.execute('''INSERT INTO industry_order_log(platform,user_id,utc_day,order_key,reward,job_key,completed_at)
            VALUES(?,?,?,?,?,?,?)''', (ctx.platform, ctx.user_id, day, key, order.payout, job, now))
        con.execute('''INSERT INTO career_progress(platform,user_id,job_key,xp,completed) VALUES(?,?,?, ?,0)
            ON CONFLICT(platform,user_id,job_key) DO UPDATE SET xp=xp+excluded.xp''',
            (ctx.platform, ctx.user_id, job, ORDER_XP))
        con.execute('''INSERT INTO bot_economy_ledger(platform,actor_id,target_id,delta,source,created_at)
            VALUES(?,?,?,?,?,?)''', (ctx.platform, ctx.user_id, ctx.user_id, order.payout, 'industry:order:'+key, now))
        unlock(con, ctx, 'first_contract', now)
        total = con.execute('''SELECT COUNT(*) AS c FROM industry_order_log
            WHERE platform=? AND user_id=?''', (ctx.platform, ctx.user_id)).fetchone()['c']
        achieved = unlock(con, ctx, 'industry10', now) if total >= 10 else False
        from .v13 import activity as season_activity
        season_activity(con, ctx, 'contract', now)
    return (f'✅ Заказ «{order.name}» выполнен! +{order.payout} монет, +{ORDER_XP} карьерного XP. '
            f'Сегодня {used+1}/{MAX_CONTRACTS_DAY}.'
            + (' 🏆 Достижение: Мастер снабжения.' if achieved else ''))


def chains_command(service, ctx, now: int) -> str:
    from .v14 import REFINING
    from .v08 import BUSINESSES
    from .v09 import RECIPES
    lines = ['⚙️ КВЕРАТО | ПРОИЗВОДСТВЕННЫЕ ЦЕПОЧКИ',
             '1. Предприятия производят: '+', '.join(dict.fromkeys(MATERIALS[b.material] for b in BUSINESSES.values()))+'.',
             '2. Переработка базовых материалов расходует энергию:',
             *('• '+name+': '+', '.join(f'{MATERIALS[k]} ×{v}' for k,v in mats.items())+f'; энергия {energy}'
               for name,mats,energy in REFINING.values()),
             '3. Используй готовые материалы для крафта (15 рецептов) или заказов.',
             f'4. Заказы: {len(ORDERS)} видов, четыре предложения в день, не более 3 выполнений вместе с контрактами.',
             f'Рецептов в мастерской: {len(RECIPES)}. Команды: бизнесы / станция / переработка каталог / крафт каталог / заказы.']
    return '\n'.join(lines)


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    if cmd == 'orders':
        return order_command(service, ctx, args, now)
    if cmd == 'chains':
        return chains_command(service, ctx, now)
    return None
