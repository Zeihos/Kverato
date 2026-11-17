"""VK-first v0.3 features, pure domain logic with transactional SQLite updates.

Balances are virtual game currency only.  All debits and credits are atomic.
"""
from __future__ import annotations

from contextlib import closing

ACHIEVEMENTS = {
    'worker': ('💼 Первая зарплата', 'Выполнить первую работу'),
    'gamer': ('🎮 Начало пути', 'Сыграть первую мини-игру'),
    'winner': ('🏅 Первая победа', 'Победить в мини-игре'),
    'veteran': ('🎯 Бывалый игрок', 'Сыграть 10 мини-игр'),
    'quester': ('📜 Выполнено!', 'Получить награду за ежедневные задания'),
    'streak3': ('🔥 Постоянный гость', 'Получить бонус три раза подряд'),
    'career3': ('📈 Карьерист', 'Получить 3-й ранг профессии'),
    'entrepreneur': ('🏢 Предприниматель', 'Купить первое предприятие'),
    'industrialist': ('🏗 Индустриалист', 'Улучшить предприятие до 3-го уровня'),
    'first_contract': ('📦 Первый контракт', 'Выполнить заказ на поставку'),
    'explorer_first': ('🧭 Первый исследователь', 'Успешно завершить экспедицию'),
    'explorer_relic': ('🎖 Первопроходец', 'Найти первую редкую реликвию'),
    'city_builder': ('🏗 Созидатель', 'Получить награду за городской проект'),
    'collector': ('🎖 Коллекционер', 'Собрать и наградить коллекцию одежды'),
    'generous': ('🎁 Даритель', 'Отправить первый подарок игроку'),
    'trader': ('🛍 Торговец', 'Купить первый предмет на площадке'),
    'pet_friend': ('🐾 Друг города', 'Приобрести первого питомца'),
    'supply_opener': ('🎁 Открыватель', 'Открыть первый контейнер'),
    'workshop5': ('🛠 Мастер пяти форм', 'Изготовить пять различных предметов'),
    'industry10': ('🏭 Мастер снабжения', 'Выполнить 10 комплексных производственных заказов'),
    'delivery_first': ('🚚 Первая доставка', 'Завершить первый транспортный рейс'),
    'clan_pathfinder': ('🧭 Командный первопроходец', 'Получить награду за клановую экспедицию'),
}
JOBS = {
    'courier': ('Курьер', 40, 120),
    'driver': ('Водитель', 50, 130),
    'programmer': ('Программист', 65, 145),
    'technician': ('Техник', 55, 135),
    'barista': ('Бариста', 48, 125),
    'mechanic': ('Механик', 60, 140),
    'designer': ('Дизайнер', 65, 148),
    'researcher': ('Исследователь', 70, 155),
    'dispatcher': ('Диспетчер', 58, 138),
    'engineer': ('Инженер', 75, 160),
}
JOB_ALIASES = {'курьер': 'courier', 'водитель': 'driver', 'программист': 'programmer', 'техник': 'technician', 'бариста': 'barista', 'механик': 'mechanic', 'дизайнер': 'designer', 'исследователь': 'researcher', 'диспетчер': 'dispatcher', 'инженер': 'engineer', **{key: key for key in JOBS}}


def amount_arg(args: list[str], limit: int = 1_000_000) -> int | None:
    if len(args) != 1 or len(args[0]) > 10 or not args[0].isascii() or not args[0].isdecimal():
        return None
    number = int(args[0])
    return number if 1 <= number <= limit else None


def unlock(con, ctx, key: str, now: int) -> bool:
    return bool(con.execute('''INSERT OR IGNORE INTO achievements
        (platform, user_id, achievement_key, earned_at) VALUES (?,?,?,?)''',
        (ctx.platform, ctx.user_id, key, now)).rowcount)


def record_game(con, ctx, won: bool, now: int) -> list[str]:
    con.execute('''UPDATE players SET games_played=games_played+1, games_won=games_won+?
        WHERE platform=? AND user_id=?''', (int(won), ctx.platform, ctx.user_id))
    p = con.execute('SELECT games_played FROM players WHERE platform=? AND user_id=?',
                    (ctx.platform, ctx.user_id)).fetchone()
    from .v04 import record_quest_event
    record_quest_event(con, ctx, now, 'game', won)
    new = []
    for key, eligible in (('gamer', True), ('winner', won), ('veteran', p['games_played'] >= 10)):
        if eligible and unlock(con, ctx, key, now):
            new.append(ACHIEVEMENTS[key][0])
    return new


def suffix(new: list[str]) -> str:
    return '\n🏆 Новое достижение: ' + ', '.join(new) if new else ''


def _wardrobe_inventory(service,ctx):
    from .v12 import inventory_summary
    from .v14 import inventory_summary as shop_inventory
    return inventory_summary(service,ctx) + shop_inventory(service,ctx)


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    db = service.db
    if cmd in ('bank', 'deposit', 'withdraw'):
        if cmd == 'bank':
            with closing(db.connect()) as con:
                p = service._player(con, ctx)
            return (f'🏦 БАНК КВЕРАТО\n💰 Кошелёк: {p["coins"]:,}\n🏦 На счёте: {p["bank_coins"]:,}\n'
                    'Вклад: /вклад 100\nСнятие: /снять 100\n'
                    'Банк только хранит виртуальные монеты, проценты не начисляются.').replace(',', ' ')
        amount = amount_arg(args)
        if amount is None:
            return f'🏦 Формат: /{"вклад" if cmd == "deposit" else "снять"} 100 (1–1 000 000).'
        from .v07 import MAX_WALLET
        with db.transaction() as con:
            src, dst = ('coins', 'bank_coins') if cmd == 'deposit' else ('bank_coins', 'coins')
            result = con.execute(f'''UPDATE players SET {src}={src}-?, {dst}={dst}+?
                WHERE platform=? AND user_id=? AND {src}>=? AND {dst}<=?''',
                (amount, amount, ctx.platform, ctx.user_id, amount, MAX_WALLET - amount))
            if not result.rowcount:
                return '❌ Недостаточно монет или превышен лимит счёта.'
        return f'✅ {"Внесено на счёт" if cmd == "deposit" else "Снято со счёта"}: {amount:,} монет.'.replace(',', ' ')
    if cmd == 'jobs':
        return ('💼 ПРОФЕССИИ (работа каждые 30 минут)\n' + '\n'.join(
            f'{name}: {low}–{high} монет — /профессия {key}'
            for key, (name, low, high) in JOBS.items()) + '\nВыбор профессии бесплатный. Карьерные ранги: /карьера.')
    if cmd == 'job':
        key = JOB_ALIASES.get(args[0].lower()) if len(args) == 1 else None
        if key not in JOBS:
            return '💼 Формат: /профессия курьер, водитель или программист.'
        with db.transaction() as con:
            con.execute('UPDATE players SET job_key=? WHERE platform=? AND user_id=?',
                        (key, ctx.platform, ctx.user_id))
        return f'✅ Теперь твоя профессия — {JOBS[key][0]}. Используй /работа.'
    if cmd in ('stats', 'inventory', 'achievements'):
        with closing(db.connect()) as con:
            p = service._player(con, ctx)
            if cmd == 'stats':
                return (f'📊 СТАТИСТИКА\n🎮 Игр: {p["games_played"]} (побед: {p["games_won"]})\n'
                        f'💼 Выполнено работ: {p["work_count"]}\n'
                        f'🧰 Профессия: {JOBS.get(p["job_key"], JOBS["courier"])[0]}')
            if cmd == 'inventory':
                items = [r['item_key'] for r in con.execute('''SELECT item_key FROM purchases
                    WHERE platform=? AND user_id=? ORDER BY purchased_at''', (ctx.platform, ctx.user_id))]
                from .v09 import crafted_summary
                return ('🎒 ИНВЕНТАРЬ\nЗначки: ' + (', '.join(items) if items else 'пока пусто') +
                        f'\nАктивный значок: {p["badge"]}' + crafted_summary(service,ctx) + _wardrobe_inventory(service,ctx))
            earned = {r['achievement_key'] for r in con.execute('''SELECT achievement_key FROM achievements
                WHERE platform=? AND user_id=?''', (ctx.platform, ctx.user_id))}
        return '🏆 ДОСТИЖЕНИЯ\n' + '\n'.join(
            f'{"✅" if key in earned else "🔒"} {name} — {desc}'
            for key, (name, desc) in ACHIEVEMENTS.items())
    return None
