"""KVERATO v0.9: player homes, garages, transport, energy and useful crafting.

No background farming of money. Every state-changing operation takes one SQLite
BEGIN IMMEDIATE transaction; equipment and crafting consume existing resources.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass

from .v07 import MAX_WALLET
from .v08 import MATERIALS, MAX_MATERIALS


@dataclass(frozen=True)
class Home:
    title: str
    price: int
    parking: int


@dataclass(frozen=True)
class Vehicle:
    title: str
    price: int


HOMES = {
    'flat': Home('Квартира «Искра»', 4000, 1),
    'loft': Home('Лофт «Линия»', 17000, 2),
    'villa': Home('Резиденция «Горизонт»', 60000, 3),
    'studio': Home('Студия «Нить»', 2500, 0),
    'room': Home('Апартаменты «Пульс»', 6500, 1),
    'duplex': Home('Дуплекс «Лист»', 11000, 1),
    'penthouse': Home('Пентхаус «Вершина»', 26500, 2),
    'cottage': Home('Коттедж «Маяк»', 35000, 2),
    'townhouse': Home('Таунхаус «Ритм»', 42000, 2),
    'chalet': Home('Шале «Север»', 51000, 3),
    'mansion': Home('Особняк «Атлас»', 75000, 3),
    'estate': Home('Усадьба «Рассвет»', 98000, 4),
    'tower': Home('Башня «Вертикаль»', 130000, 4),
    'palace': Home('Дворец «Сфера»', 175000, 5),
}
HOME_NAMES = {'квартира': 'flat', 'flat': 'flat', 'лофт': 'loft', 'loft': 'loft',
              'резиденция': 'villa', 'villa': 'villa'}
HOME_NAMES.update({'студия': 'studio', 'studio': 'studio', 'апартаменты': 'room', 'room': 'room', 'дуплекс': 'duplex', 'duplex': 'duplex', 'пентхаус': 'penthouse', 'penthouse': 'penthouse', 'коттедж': 'cottage', 'cottage': 'cottage', 'таунхаус': 'townhouse', 'townhouse': 'townhouse', 'шале': 'chalet', 'chalet': 'chalet', 'особняк': 'mansion', 'mansion': 'mansion', 'усадьба': 'estate', 'estate': 'estate', 'башня': 'tower', 'tower': 'tower', 'дворец': 'palace', 'palace': 'palace'})
VEHICLES = {
    'scooter': Vehicle('Скутер «Луч»', 2000),
    'coupe': Vehicle('Купе «Вектор»', 10000),
    'rover': Vehicle('Вездеход «Орион»', 35000),
    'moped': Vehicle('Мопед «Пиксель»', 3200),
    'hatch': Vehicle('Хэтчбек «Спарк»', 5100),
    'sedan': Vehicle('Седан «Астра»', 7500),
    'wagon': Vehicle('Универсал «Вояж»', 9200),
    'rally': Vehicle('Ралли-кар «Искра»', 15500),
    'bike': Vehicle('Мотоцикл «Поток»', 18000),
    'pickup': Vehicle('Пикап «Гранит»', 21500),
    'muscle': Vehicle('Маслкар «Шторм»', 27000),
    'suv': Vehicle('Кроссовер «Парус»', 31500),
    'sport': Vehicle('Спорткар «Фантом»', 47000),
    'buggy': Vehicle('Багги «Пыль»', 54000),
    'electric': Vehicle('Электрокар «Эхо»', 64000),
    'super': Vehicle('Суперкар «Квант»', 85000),
    'hyper': Vehicle('Гиперкар «Зенит»', 120000),
}
VEHICLE_NAMES = {'скутер': 'scooter', 'scooter': 'scooter',
                 'купе': 'coupe', 'coupe': 'coupe',
                 'вездеход': 'rover', 'rover': 'rover'}
VEHICLE_NAMES.update({'мопед': 'moped', 'moped': 'moped', 'хэтчбек': 'hatch', 'hatch': 'hatch', 'седан': 'sedan', 'sedan': 'sedan', 'универсал': 'wagon', 'wagon': 'wagon', 'ралли': 'rally', 'rally': 'rally', 'мотоцикл': 'bike', 'bike': 'bike', 'пикап': 'pickup', 'pickup': 'pickup', 'маслкар': 'muscle', 'muscle': 'muscle', 'кроссовер': 'suv', 'suv': 'suv', 'спорткар': 'sport', 'sport': 'sport', 'багги': 'buggy', 'buggy': 'buggy', 'электрокар': 'electric', 'electric': 'electric', 'суперкар': 'super', 'super': 'super', 'гиперкар': 'hyper', 'hyper': 'hyper'})
RECIPES = {
    'module': ('Модуль тюнинга', {'parts': 4, 'chips': 2}, 12),
    'decor': ('Декор интерьера', {'supplies': 5, 'parts': 2}, 10),
}
RECIPE_NAMES = {'модуль': 'module', 'module': 'module',
                'декор': 'decor', 'decor': 'decor'}
ENERGY_PRICE = 6500
ENERGY_MAX = 200
ENERGY_HOURS_CAP = 8
ENERGY_MAX_TIER = 3
MAX_CRAFTED_PER_TYPE = 100


def _coins(con, ctx, delta: int, now: int, label: str) -> bool:
    if delta < 0:
        rc = con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND user_id=? AND coins>=?''',
                         (-delta, ctx.platform, ctx.user_id, -delta)).rowcount
    else:
        rc = con.execute('''UPDATE players SET coins=coins+? WHERE platform=? AND user_id=? AND coins<=?''',
                         (delta, ctx.platform, ctx.user_id, MAX_WALLET-delta)).rowcount
    if rc:
        con.execute('''INSERT INTO bot_economy_ledger(platform,actor_id,target_id,delta,source,created_at)
                        VALUES(?,?,?,?,?,?)''',
                    (ctx.platform,ctx.user_id,ctx.user_id,delta,'world:'+label,now))
    return bool(rc)


def _material(con, ctx, key: str) -> int:
    result = con.execute('''SELECT amount FROM material_balances WHERE platform=? AND user_id=? AND material_key=?''',
                         (ctx.platform,ctx.user_id,key)).fetchone()
    return int(result['amount']) if result else 0


def _take_materials(con, ctx, needs: dict[str, int]) -> bool:
    # Check every balance before mutating anything. SQLite transaction serializes buyers.
    if any(_material(con, ctx, key) < amount for key, amount in needs.items()):
        return False
    for key, amount in needs.items():
        rc = con.execute('''UPDATE material_balances SET amount=amount-?
                            WHERE platform=? AND user_id=? AND material_key=? AND amount>=?''',
                         (amount,ctx.platform,ctx.user_id,key,amount)).rowcount
        if rc != 1:
            raise RuntimeError('Concurrent material modification within SQLite transaction')
    return True


def _house(con, ctx):
    return con.execute('''SELECT * FROM player_homes WHERE platform=? AND user_id=?''',
                       (ctx.platform,ctx.user_id)).fetchone()


def _car_count(con, ctx) -> int:
    return con.execute('''SELECT COUNT(*) FROM player_vehicles WHERE platform=? AND user_id=?''',
                       (ctx.platform,ctx.user_id)).fetchone()[0]


def _garage_capacity(home) -> int:
    return 1 if home is None else 1 + HOMES[home['home_key']].parking + (home['tier']-1)


def home_command(service, ctx, args: list[str], now: int) -> str:
    if not args or args[0] in ('мой', 'статус'):
        with closing(service.db.connect()) as con:
            home = _house(con,ctx)
            vehicles = _car_count(con,ctx)
        if home is None:
            return f'🏠 ЖИЛЬЁ КВЕРАТО\nСвоего жилья нет. Гараж: {vehicles}/1. Каталог: дом каталог.'
        title = HOMES[home['home_key']].title
        return (f'🏠 {title}\nУровень: {home["tier"]}/3\n'
                f'Декор: {"установлен" if home["decorated"] else "не установлен"}\n'
                f'Гараж: {vehicles}/{_garage_capacity(home)}\n'
                'Действия: дом улучшить / дом украсить / дом продать подтвердить.')
    action = args[0].casefold()
    if action in ('каталог', 'магазин'):
        return '🏠 КАТАЛОГ НЕДВИЖИМОСТИ\n' + '\n'.join(
            f'• {home.title}: {home.price:,} монет, +{home.parking} мест в гараже. Команда: дом купить {key}'.replace(',', ' ', 1)
            for key,home in HOMES.items())
    if action == 'купить':
        if len(args) != 2 or args[1].casefold() not in HOME_NAMES:
            return '⚠ Формат: дом купить <ключ>. Смотри «дом каталог».'
        key = HOME_NAMES[args[1].casefold()]
        kind = HOMES[key]
        with service.db.transaction() as con:
            if _house(con,ctx):
                return '⚠ Уже есть жильё. Вначале продай старое.'
            if not _coins(con,ctx,-kind.price,now,'home-buy:'+key):
                return f'❌ Для покупки требуется {kind.price:,} монет.'.replace(',',' ')
            con.execute('''INSERT INTO player_homes(platform,user_id,home_key,tier,invested,decorated,bought_at)
                VALUES(?,?,?,1,?,0,?)''', (ctx.platform,ctx.user_id,key,kind.price,now))
        return f'✅ Твоя недвижимость: {kind.title}. Гараж расширен!'
    if action == 'продать' and args[1:] != ['подтвердить']:
        return '⚠ Для продажи введи: дом продать подтвердить (40% вложенных монет; декор исчезнет).'
    if action not in ('продать','улучшить','украсить'):
        return '🏠 Команды: дом / дом каталог / дом купить квартира / дом улучшить / дом украсить / дом продать подтвердить.'
    with service.db.transaction() as con:
        home = _house(con,ctx)
        if home is None:
            return '❌ У тебя ещё нет жилья.'
        if action == 'улучшить':
            if home['tier'] >= 3:
                return '✅ Жильё уже максимального уровня.'
            tier = home['tier']
            cost = HOMES[home['home_key']].price * tier // 2
            needs = {'parts': tier * 2}
            if _material(con,ctx,'parts') < needs['parts']:
                return f'❌ Для улучшения нужно {needs["parts"]} деталей.'
            if not _coins(con,ctx,-cost,now,'home-upgrade'):
                return f'❌ Для улучшения нужно {cost:,} монет.'.replace(',',' ')
            if not _take_materials(con,ctx,needs):
                raise RuntimeError('Material balance changed unexpectedly')
            con.execute('''UPDATE player_homes SET tier=tier+1,invested=invested+?
                WHERE platform=? AND user_id=?''', (cost,ctx.platform,ctx.user_id))
            return f'✅ Жильё улучшено до уровня {tier+1}. Добавлено место в гараже.'
        if action == 'украсить':
            if home['decorated']:
                return '✅ Декор уже установлен.'
            rc = con.execute('''UPDATE crafted_inventory SET amount=amount-1 WHERE
                platform=? AND user_id=? AND item_key='decor' AND amount>=1''',
                (ctx.platform,ctx.user_id)).rowcount
            if not rc:
                return '❌ Нужен предмет «Декор интерьера». Создай его: создать декор.'
            con.execute('''UPDATE player_homes SET decorated=1 WHERE platform=? AND user_id=?''',
                        (ctx.platform,ctx.user_id))
            return '✅ Декор установлен в твоём доме!'
        if _car_count(con,ctx) > 1:
            return '❌ Перед продажей жилья освободи гараж: без дома доступно только 1 место.'
        refund = home['invested'] * 40 // 100
        if not _coins(con,ctx,refund,now,'home-sale'):
            return '⚠ Кошелёк заполнен. Освободи место для возврата.'
        con.execute('DELETE FROM player_homes WHERE platform=? AND user_id=?',
                    (ctx.platform,ctx.user_id))
        return f'✅ Жильё продано. Возвращено {refund:,} монет.'.replace(',',' ')


def vehicle_command(service, ctx, args: list[str], now: int) -> str:
    if not args or args[0].casefold() in ('гараж','мой'):
        with closing(service.db.connect()) as con:
            rows = con.execute('''SELECT * FROM player_vehicles WHERE platform=? AND user_id=?
                ORDER BY bought_at''', (ctx.platform,ctx.user_id)).fetchall()
            capacity = _garage_capacity(_house(con,ctx))
        return ('🚗 КВЕРАТО | ГАРАЖ ' + f'{len(rows)}/{capacity}\n' +
                ('\n'.join(f'• {VEHICLES[r["vehicle_key"]].title}' +
                  (' · тюнинг установлен' if r['tuned'] else '') for r in rows) if rows else 'Транспорт отсутствует.') +
                '\nКаталог: транспорт каталог.')
    action = args[0].casefold()
    if action in ('каталог','магазин'):
        return '🚗 КАТАЛОГ ТРАНСПОРТА\n' + '\n'.join(
            f'• {car.title} — {car.price:,} монет, транспорт купить {key}'.replace(',', ' ', 1)
            for key,car in VEHICLES.items())
    if action not in ('купить','тюнинг','продать') or len(args) < 2:
        return '🚗 Команды: гараж / транспорт каталог / транспорт купить скутер / транспорт тюнинг скутер / транспорт продать скутер подтвердить.'
    key = VEHICLE_NAMES.get(args[1].casefold())
    if key is None:
        return '⚠ Выбери транспорт из «транспорт каталог».'
    if action == 'продать' and (len(args)<3 or args[2].casefold() != 'подтвердить'):
        return '⚠ Подтверди: транспорт продать '+args[1]+' подтвердить (40% цены, тюнинг исчезнет).'
    if (action != 'продать' and len(args) != 2) or (action == 'продать' and len(args) != 3):
        return '⚠ Неверный формат команды.'
    vehicle = VEHICLES[key]
    with service.db.transaction() as con:
        current = con.execute('''SELECT * FROM player_vehicles WHERE platform=? AND user_id=? AND vehicle_key=?''',
                              (ctx.platform,ctx.user_id,key)).fetchone()
        if action == 'купить':
            if current:
                return '✅ Этот транспорт уже есть в гараже.'
            if _car_count(con,ctx) >= _garage_capacity(_house(con,ctx)):
                return '❌ Нет свободных мест в гараже. Приобрети или улучши жильё.'
            if not _coins(con,ctx,-vehicle.price,now,'vehicle-buy:'+key):
                return f'❌ Для покупки нужно {vehicle.price:,} монет.'.replace(',',' ')
            con.execute('''INSERT INTO player_vehicles(platform,user_id,vehicle_key,tuned,bought_at)
                VALUES(?,?,?,0,?)''',(ctx.platform,ctx.user_id,key,now))
            return f'✅ Транспорт {vehicle.title} добавлен в гараж.'
        if current is None:
            return '❌ Этого транспорта нет в твоём гараже.'
        if action == 'тюнинг':
            if current['tuned']:
                return '✅ Тюнинг уже установлен.'
            rc = con.execute('''UPDATE crafted_inventory SET amount=amount-1 WHERE
                platform=? AND user_id=? AND item_key='module' AND amount>=1''',
                (ctx.platform,ctx.user_id)).rowcount
            if not rc:
                return '❌ Для тюнинга создай модуль: создать модуль.'
            con.execute('''UPDATE player_vehicles SET tuned=1 WHERE platform=? AND user_id=? AND vehicle_key=?''',
                        (ctx.platform,ctx.user_id,key))
            return f'✅ Тюнинг установлен: {vehicle.title}.'
        refund = vehicle.price * 40 // 100
        if not _coins(con,ctx,refund,now,'vehicle-sale:'+key):
            return '⚠ Кошелёк заполнен. Освободи место для возврата.'
        con.execute('''DELETE FROM player_vehicles WHERE platform=? AND user_id=? AND vehicle_key=?''',
                    (ctx.platform,ctx.user_id,key))
        return f'✅ {vehicle.title} продан. Возвращено {refund:,} монет.'.replace(',',' ')


def _station(con,ctx):
    return con.execute('''SELECT * FROM player_energy WHERE platform=? AND user_id=?''',
                       (ctx.platform,ctx.user_id)).fetchone()


def _production(station,now):
    hours = min(ENERGY_HOURS_CAP,max(0,now-station['collected_at'])//3600)
    return hours*(4+station['tier']*2),hours


def station_command(service,ctx,args:list[str],now:int)->str:
    action = args[0].casefold() if args else 'статус'
    if action in ('помощь','каталог'):
        return (f'⚡ Энергостанция — {ENERGY_PRICE:,} монет и 5 деталей. '
                f'Генерация: 6 ед./ч, максимум {ENERGY_HOURS_CAP} часов ожидания. '
                'Команды: станция купить / станция / станция собрать / станция улучшить.').replace(',',' ')
    if action not in ('статус','купить','собрать','улучшить') or len(args)>1:
        return '⚡ Команды: станция каталог / станция купить / станция собрать / станция улучшить.'
    if action == 'статус':
        with closing(service.db.connect()) as con:
            st = _station(con,ctx)
        if st is None:
            return '⚡ Энергостанция не построена. Используй: станция каталог.'
        ready,hours = _production(st,now)
        return f'⚡ СТАНЦИЯ ур. {st["tier"]}/3\nНакоплено: {st["stored"]}/{ENERGY_MAX}\nК сбору: {ready} за {hours} ч.'
    with service.db.transaction() as con:
        st = _station(con,ctx)
        if action == 'купить':
            if st:
                return '✅ У тебя уже есть энергостанция.'
            if _material(con,ctx,'parts') < 5:
                return '❌ Для строительства нужно 5 деталей.'
            if not _coins(con,ctx,-ENERGY_PRICE,now,'station-build'):
                return f'❌ Для станции требуется {ENERGY_PRICE} монет.'
            if not _take_materials(con,ctx,{'parts':5}):
                raise RuntimeError('Material balance changed unexpectedly')
            con.execute('''INSERT INTO player_energy(platform,user_id,tier,collected_at,stored)
                VALUES(?,?,1,?,0)''',(ctx.platform,ctx.user_id,now))
            return '✅ Энергостанция построена. Производство началось!'
        if st is None:
            return '❌ Сначала построй станцию: станция купить.'
        ready,hours = _production(st,now)
        if action == 'собрать':
            if hours == 0:
                return '⏳ Энергия ещё не готова. Нужно дождаться полного часа.'
            if st['stored']+ready > ENERGY_MAX:
                return '⚠ Хранилище энергии переполнится. Сначала потрать энергию на создание предметов.'
            con.execute('''UPDATE player_energy SET stored=stored+?, collected_at=?
                WHERE platform=? AND user_id=?''',
                (ready,st['collected_at']+hours*3600 if hours < ENERGY_HOURS_CAP else now,ctx.platform,ctx.user_id))
            return f'⚡ Собрано +{ready} энергии. В запасе {st["stored"]+ready}/{ENERGY_MAX}.'
        if st['tier']>=ENERGY_MAX_TIER:
            return '✅ Энергостанция максимального уровня.'
        if hours:
            return '⚡ Сначала собери готовую энергию перед улучшением.'
        price = 3000*st['tier']
        needs = {'parts':3*st['tier'],'chips':st['tier']}
        if any(_material(con,ctx,k)<v for k,v in needs.items()):
            return '❌ Не хватает деталей или микросхем для улучшения.'
        if not _coins(con,ctx,-price,now,'station-upgrade'):
            return f'❌ Нужно {price} монет для улучшения.'
        if not _take_materials(con,ctx,needs):
            raise RuntimeError('Material balance changed unexpectedly')
        con.execute('''UPDATE player_energy SET tier=tier+1 WHERE platform=? AND user_id=?''',
                    (ctx.platform,ctx.user_id))
        return f'✅ Станция улучшена до уровня {st["tier"]+1}. '


def crafting_command(service,ctx,args:list[str],now:int)->str:
    action = args[0].casefold() if args else ''
    if action in ('каталог','рецепты','помощь',''):
        return ('🛠 МАСТЕРСКАЯ КВЕРАТО\n'
                + '\n'.join('• '+title+' ['+key+']: '+', '.join(
                    f'{MATERIALS[mat]} ×{qty}' for mat,qty in materials.items())
                    + f', энергия {energy}' for key,(title,materials,energy) in RECIPES.items())
                + '\nКоманда: создать <ключ>. Модуль и декор можно установить; прочие предметы — коллекционные.')
    key = RECIPE_NAMES.get(action)
    if key is None or len(args)!=1:
        return '⚠ Используй ключ предмета из «крафт каталог».'
    title,needs,energy = RECIPES[key]
    with service.db.transaction() as con:
        station = _station(con,ctx)
        if station is None:
            return '❌ Для создания предметов нужна энергостанция.'
        if station['stored']<energy:
            return f'❌ Для создания нужно {energy} ед. энергии. Собери энергию на станции.'
        stock = con.execute('''SELECT amount FROM crafted_inventory WHERE platform=? AND user_id=? AND item_key=?''',
                            (ctx.platform,ctx.user_id,key)).fetchone()
        if stock and stock['amount']>=MAX_CRAFTED_PER_TYPE:
            return '⚠ Лимит предметов данного типа: 100.'
        if any(_material(con,ctx,k)<v for k,v in needs.items()):
            return '❌ Не хватает ресурсов: ' + ', '.join(f'{MATERIALS[k]} ×{v}' for k,v in needs.items())
        if not _take_materials(con,ctx,needs):
            raise RuntimeError('Material balance changed unexpectedly')
        con.execute('''UPDATE player_energy SET stored=stored-? WHERE platform=? AND user_id=? AND stored>=?''',
                    (energy,ctx.platform,ctx.user_id,energy))
        con.execute('''INSERT INTO crafted_inventory(platform,user_id,item_key,amount) VALUES(?,?,?,1)
            ON CONFLICT(platform,user_id,item_key) DO UPDATE SET amount=amount+1''',
            (ctx.platform,ctx.user_id,key))
        crafted_types=con.execute('SELECT COUNT(*) FROM crafted_inventory WHERE platform=? AND user_id=? AND amount>0',
                                (ctx.platform,ctx.user_id)).fetchone()[0]
        from .features import unlock
        new=unlock(con,ctx,'workshop5',now) if crafted_types>=5 else False
        return f'🛠 Создан предмет «{title}». Списано {energy} энергии.' + (' 🏆 Достижение: Мастер пяти форм.' if new else '')


def crafted_summary(service,ctx)->str:
    with closing(service.db.connect()) as con:
        rows = con.execute('''SELECT item_key,amount FROM crafted_inventory WHERE platform=? AND user_id=? AND amount>0''',
                           (ctx.platform,ctx.user_id)).fetchall()
    return ('\n🛠 Изготовленные предметы: ' +
            (', '.join(f'{RECIPES[r["item_key"]][0]} ×{r["amount"]}' for r in rows)
             if rows else 'нет'))


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    if cmd == 'home':
        return home_command(service,ctx,args,now)
    if cmd == 'vehicle':
        return vehicle_command(service,ctx,args,now)
    if cmd == 'garage':
        return vehicle_command(service,ctx,[],now)
    if cmd == 'station':
        return station_command(service,ctx,args,now)
    if cmd == 'craft':
        return crafting_command(service,ctx,args,now)
    return None
