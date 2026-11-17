""" virtual pets, supply crates, collectible titles, advanced materials.

All writes use BEGIN IMMEDIATE. Boxes cannot produce coins or real money.
Only existing players are eligible; BotService._award_activity registers them.
"""
from __future__ import annotations

from contextlib import closing
from .v07 import MAX_WALLET
from .v08 import MATERIALS, MAX_MATERIALS
from .v09 import RECIPES, RECIPE_NAMES
from .features import unlock

# Pet: display name, price. Pets are not real animals; care is virtual.
PETS = {
    'ember': ('Лис «Искра»', 2600),
    'pixel': ('Кот «Пиксель»', 3000),
    'orbit': ('Сова «Орбита»', 3400),
    'relay': ('Пёс «Реле»', 3800),
    'moss': ('Ёж «Мох»', 4200),
    'glint': ('Хорёк «Блик»', 4800),
    'rune': ('Ворон «Руна»', 5300),
    'nebby': ('Кролик «Небби»', 6000),
    'north': ('Волк «Север»', 7000),
    'mist': ('Рысь «Дымка»', 8600),
    'nova': ('Фенек «Нова»', 10000),
    'auric': ('Дракончик «Аурик»', 14000),
}
# name, price, prize resources (one randomly selected on opening), quantity range.
BOXES = {
    'starter': ('Набор «Старт»', 400, ('parts','supplies','chips'), 1, 3),
    'courier': ('Посылка «Транзит»', 600, ('supplies','fiber','parts'), 2, 4),
    'garage': ('Ящик «Гараж»', 850, ('parts','alloy','rubber'), 2, 5),
    'botanic': ('Бокс «Рост»', 1000, ('supplies','crystal','fiber'), 2, 4),
    'signal': ('Контейнер «Сигнал»', 1200, ('chips','circuit','optic'), 2, 5),
    'field': ('Кейс «Периметр»', 1400, ('alloy','crystal','ceramic'), 3, 5),
    'fabric': ('Посылка «Ателье»', 1650, ('fiber','resin','rubber'), 3, 6),
    'reactor': ('Модуль «Пульс»', 1950, ('circuit','alloy','battery'), 3, 7),
    'archive': ('Архивный бокс', 2400, ('optic','ceramic','crystal'), 4, 7),
    'atlas': ('Экспедиционный кейс', 3200, ('battery','optic','resin'), 4, 8),
}
TITLES = {
    'wanderer': ('Странник города', 700),
    'builder': ('Мастер квартала', 1100),
    'spark': ('Искра Периметра', 1500),
    'artisan': ('Создатель форм', 1900),
    'navigator': ('Навигатор', 2300),
    'architect': ('Архитектор', 2700),
    'racer': ('Пилот трассы', 3200),
    'merchant': ('Мастер торговли', 3700),
    'explorer': ('Исследователь', 4300),
    'keeper': ('Хранитель артефактов', 5000),
    'founder': ('Основатель', 5600),
    'navigator_plus': ('Покоритель маршрутов', 6500),
    'luminary': ('Светоч города', 7600),
    'legendary': ('Легенда Контуров', 9200),
    'sovereign': ('Властелин горизонта', 12000),
}
# Nine new materials (three established materials remain unchanged).
REFINING = {
    'fiber': ('Волокно', {'supplies': 3}, 2),
    'alloy': ('Сплав', {'parts': 3}, 3),
    'circuit': ('Печатная плата', {'chips': 3}, 4),
    'rubber': ('Полимер', {'supplies': 2,'parts': 1}, 3),
    'optic': ('Оптоволокно', {'chips': 2,'supplies': 1}, 4),
    'crystal': ('Кристаллическая пыль', {'chips': 2,'parts': 1}, 4),
    'resin': ('Смола', {'supplies': 2,'chips': 1}, 4),
    'ceramic': ('Керамика', {'parts': 2,'supplies': 1}, 4),
    'battery': ('Аккумулятор', {'parts': 2,'chips': 2}, 5),
}
MATERIALS.update({key: val[0] for key,val in REFINING.items()})
# Thirteen new recipes plus the legacy module/decor = 15 total.
ADVANCED_RECIPES = {
    'filter': ('Защитный фильтр', {'fiber': 2,'parts': 1}, 6),
    'case': ('Прочный футляр', {'alloy': 2,'rubber': 2}, 8),
    'scanner': ('Портативный сканер', {'circuit': 2,'optic': 1}, 10),
    'lamp': ('Фонарь «Аура»', {'battery': 1,'optic': 2}, 9),
    'emblem': ('Сувенирный герб', {'ceramic': 2,'resin': 1}, 8),
    'probe': ('Разведзонд', {'circuit': 2,'alloy': 2}, 12),
    'amulet': ('Амулет «Меридиан»', {'crystal': 3,'fiber': 2}, 10),
    'panel': ('Панель интерфейса', {'optic': 2,'circuit': 2}, 13),
    'case_plus': ('Экспедиционный кофр', {'alloy': 3,'fiber': 2}, 12),
    'generator': ('Карманный генератор', {'battery': 2,'parts': 3}, 15),
    'badgeframe': ('Рамка коллекционера', {'resin': 2,'crystal': 2}, 11),
    'beacon': ('Маяк исследователя', {'battery': 2,'optic': 3}, 17),
    'modelcity': ('Макет «КВЕРАТО»', {'ceramic': 3,'alloy': 2,'crystal': 1}, 18),
}
RECIPES.update({key: (name, ingredients, power) for key,(name,ingredients,power) in ADVANCED_RECIPES.items()})
RECIPE_NAMES.update({key: key for key in ADVANCED_RECIPES})
RECIPE_NAMES.update({
    'фильтр':'filter','футляр':'case','сканер':'scanner','фонарь':'lamp',
    'герб':'emblem','зонд':'probe','амулет':'amulet','панель':'panel',
    'кофр':'case_plus','генератор':'generator','рамка':'badgeframe',
    'маяк':'beacon','макет':'modelcity'})
MAX_PETS = 3
MAX_BOXES = 30


def _money(con, ctx, delta, now, action):
    if delta < 0:
        result = con.execute('UPDATE players SET coins=coins-? WHERE platform=? AND user_id=? AND coins>=?',
                             (-delta,ctx.platform,ctx.user_id,-delta)).rowcount
    else:
        result = con.execute('UPDATE players SET coins=coins+? WHERE platform=? AND user_id=? AND coins<=?',
                             (delta,ctx.platform,ctx.user_id,MAX_WALLET-delta)).rowcount
    if result:
        con.execute('INSERT INTO bot_economy_ledger(platform,actor_id,target_id,delta,source,created_at) VALUES(?,?,?,?,?,?)',
                    (ctx.platform,ctx.user_id,ctx.user_id,delta,'shop:'+action,now))
    return bool(result)


def _parse_key(args, catalog):
    if len(args)!=2 or args[1].casefold() not in catalog:
        return None
    return args[1].casefold()


def active_pet_work_bonus(con, ctx):
    row = con.execute('''SELECT p.bond FROM pet_selected s JOIN pet_owned p
        ON s.platform=p.platform AND s.user_id=p.user_id AND s.pet_key=p.pet_key
        WHERE s.platform=? AND s.user_id=?''', (ctx.platform,ctx.user_id)).fetchone()
    return min(10,int(row['bond'])) if row else 0


def active_title(con, ctx):
    row = con.execute('SELECT title_key FROM title_selected WHERE platform=? AND user_id=?',
                      (ctx.platform,ctx.user_id)).fetchone()
    return TITLES[row['title_key']][0] if row and row['title_key'] in TITLES else None


def pet_command(service,ctx,args,now):
    action = args[0].casefold() if args else ''
    if action in ('каталог','магазин'):
        return '🐾 ПИТОМЦЫ (макс. 3)\n'+'\n'.join(f'• {name} [{key}] — {price} монет' for key,(name,price) in PETS.items()) + '\nПокупка: питомец купить ember'
    if action in ('','мои','мой'):
        with closing(service.db.connect()) as con:
            rows=con.execute('''SELECT p.pet_key,p.bond,s.pet_key AS current FROM pet_owned p
                LEFT JOIN pet_selected s ON s.platform=p.platform AND s.user_id=p.user_id AND s.pet_key=p.pet_key
                WHERE p.platform=? AND p.user_id=? ORDER BY p.purchased_at''',(ctx.platform,ctx.user_id)).fetchall()
        return ('🐾 МОИ ПИТОМЦЫ\n'+'\n'.join(f'• {PETS[r["pet_key"]][0]} [{r["pet_key"]}] — связь {r["bond"]}/10'+ (' ⭐' if r['current'] else '') for r in rows) if rows else '🐾 У тебя пока нет питомцев. Каталог: питомец каталог.')+'\nКормление: питомец покормить ember (2 продукта/день)'
    if action not in ('купить','выбрать','покормить','отпустить'):
        return '🐾 Команды: питомец каталог / питомец купить KEY / питомец выбрать KEY / питомец покормить KEY / питомец отпустить KEY подтвердить.'
    if action=='отпустить':
        if len(args)!=3 or args[2].casefold()!='подтвердить' or args[1] not in PETS:
            return '⚠ Для удаления: питомец отпустить KEY подтвердить. Монеты и связь не возвращаются.'
        key=args[1]
        with service.db.transaction() as con:
            con.execute('DELETE FROM pet_selected WHERE platform=? AND user_id=? AND pet_key=?', (ctx.platform,ctx.user_id,key))
            result=con.execute('DELETE FROM pet_owned WHERE platform=? AND user_id=? AND pet_key=?', (ctx.platform,ctx.user_id,key))
            return ('🐾 Питомец отпущен, связь утрачена. Монеты не возвращаются.' if result.rowcount else '❌ У тебя нет такого питомца.')
    key=_parse_key(args,PETS)
    if key is None:
        return '❌ Используй точный ключ из «питомец каталог».'
    with service.db.transaction() as con:
        owned=con.execute('SELECT bond,last_fed_day FROM pet_owned WHERE platform=? AND user_id=? AND pet_key=?',
                          (ctx.platform,ctx.user_id,key)).fetchone()
        if action=='купить':
            if owned: return '✅ Этот питомец уже у тебя.'
            count=con.execute('SELECT COUNT(*) FROM pet_owned WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id)).fetchone()[0]
            if count>=MAX_PETS: return '❌ Лимит — три питомца на игрока.'
            price=PETS[key][1]
            if not _money(con,ctx,-price,now,'pet:'+key): return f'❌ Не хватает монет: требуется {price}.'
            con.execute('INSERT INTO pet_owned(platform,user_id,pet_key,purchased_at) VALUES(?,?,?,?)',
                        (ctx.platform,ctx.user_id,key,now))
            new = unlock(con,ctx,'pet_friend',now)
            return f'✅ Питомец «{PETS[key][0]}» приобретён.' + (' 🏆 Достижение: Друг города.' if new else '')
        if not owned: return '❌ Сначала купи этого питомца.'
        if action=='выбрать':
            con.execute('''INSERT INTO pet_selected(platform,user_id,pet_key) VALUES(?,?,?)
                ON CONFLICT(platform,user_id) DO UPDATE SET pet_key=excluded.pet_key''',
                (ctx.platform,ctx.user_id,key))
            return f'🐾 {PETS[key][0]} теперь сопровождает тебя. Бонус к зарплате: +{owned["bond"]}.'
        day=now//86400
        if owned['last_fed_day']==day: return '⏳ Сегодня этот питомец уже получал корм.'
        if owned['bond']>=10: return '✅ Связь с питомцем уже максимальна.'
        consumed=con.execute('''UPDATE material_balances SET amount=amount-2 WHERE platform=? AND user_id=?
            AND material_key='supplies' AND amount>=2''',(ctx.platform,ctx.user_id)).rowcount
        if not consumed: return '❌ Для кормления нужны 2 продукта на складе.'
        con.execute('''UPDATE pet_owned SET bond=bond+1,last_fed_day=? WHERE platform=? AND user_id=? AND pet_key=?''',
                    (day,ctx.platform,ctx.user_id,key))
        return f'🐾 {PETS[key][0]} накормлен. Связь: {owned["bond"]+1}/10.'


def box_command(service,ctx,args,now):
    action=args[0].casefold() if args else ''
    if action in ('каталог','магазин'):
        return '🎁 КОНТЕЙНЕРЫ\n'+'\n'.join(f'• {title} [{key}] — {price} монет' for key,(title,price,_,_,_) in BOXES.items())+'\nКоманды: контейнер купить starter / контейнер открыть starter'
    if action in ('','мои'):
        with closing(service.db.connect()) as con:
            rows=con.execute('SELECT box_key,amount FROM box_items WHERE platform=? AND user_id=? AND amount>0',
                             (ctx.platform,ctx.user_id)).fetchall()
        return '🎁 МОИ КОНТЕЙНЕРЫ\n'+('\n'.join(f'• {BOXES[r["box_key"]][0]} ×{r["amount"]} [{r["box_key"]}]' for r in rows) if rows else 'Пока нет контейнеров. Каталог: контейнер каталог.')
    if action not in ('купить','открыть'):
        return '🎁 Команды: контейнер каталог / контейнер купить KEY / контейнер открыть KEY.'
    key=_parse_key(args,BOXES)
    if key is None: return '❌ Используй ключ из каталога контейнеров.'
    title,price,options,low,high=BOXES[key]
    with service.db.transaction() as con:
        owned=con.execute('SELECT amount FROM box_items WHERE platform=? AND user_id=? AND box_key=?',
                          (ctx.platform,ctx.user_id,key)).fetchone()
        if action=='купить':
            if owned and owned['amount']>=MAX_BOXES: return '❌ Лимит контейнеров одного вида: 30.'
            if not _money(con,ctx,-price,now,'crate:'+key): return '❌ Недостаточно монет.'
            con.execute('''INSERT INTO box_items(platform,user_id,box_key,amount) VALUES(?,?,?,1)
                ON CONFLICT(platform,user_id,box_key) DO UPDATE SET amount=amount+1''',
                (ctx.platform,ctx.user_id,key))
            return f'🎁 Контейнер «{title}» добавлен в инвентарь.'
        if owned is None or owned['amount']<=0: return '❌ Нет такого контейнера в инвентаре.'
        # RNG evaluated inside the transaction; serialisation prevents a double opening.
        material=options[service.randbelow(len(options))]
        quantity=low+service.randbelow(high-low+1)
        row=con.execute('''SELECT amount FROM material_balances WHERE platform=? AND user_id=? AND material_key=?''',
                        (ctx.platform,ctx.user_id,material)).fetchone()
        if row and row['amount']>MAX_MATERIALS-quantity:
            return '⚠ На складе недостаточно места для награды. Контейнер сохранён.'
        changed=con.execute('''UPDATE box_items SET amount=amount-1 WHERE platform=? AND user_id=?
            AND box_key=? AND amount>=1''',(ctx.platform,ctx.user_id,key)).rowcount
        if not changed: return '❌ Контейнер уже открыт.'
        con.execute('''INSERT INTO material_balances(platform,user_id,material_key,amount) VALUES(?,?,?,?)
            ON CONFLICT(platform,user_id,material_key) DO UPDATE SET amount=amount+excluded.amount''',
            (ctx.platform,ctx.user_id,material,quantity))
        con.execute('INSERT INTO box_ledger(platform,user_id,box_key,material_key,amount,opened_at) VALUES(?,?,?,?,?,?)',
                    (ctx.platform,ctx.user_id,key,material,quantity,now))
        new=unlock(con,ctx,'supply_opener',now)
        return f'🎁 Открыт «{title}»: {MATERIALS[material]} ×{quantity}. Награда зачислена на склад.' + (' 🏆 Достижение: Открыватель.' if new else '')


def title_command(service,ctx,args,now):
    action=args[0].casefold() if args else ''
    if action in ('каталог','магазин'):
        return '🏷 ТИТУЛЫ\n'+'\n'.join(f'• {name} [{key}] — {price} монет' for key,(name,price) in TITLES.items())
    if action in ('','мои'):
        with closing(service.db.connect()) as con:
            owned=con.execute('''SELECT t.title_key,s.title_key AS equipped FROM title_owned t
                LEFT JOIN title_selected s ON s.platform=t.platform AND s.user_id=t.user_id AND s.title_key=t.title_key
                WHERE t.platform=? AND t.user_id=?''',(ctx.platform,ctx.user_id)).fetchall()
        return '🏷 МОИ ТИТУЛЫ\n'+('\n'.join(f'• {TITLES[r["title_key"]][0]} [{r["title_key"]}]'+(' ⭐' if r['equipped'] else '') for r in owned) if owned else 'Титулов нет. Каталог: титул каталог.')
    if action not in ('купить','выбрать','снять'):
        return '🏷 Используй: титул каталог / титул купить KEY / титул выбрать KEY / титул снять.'
    with service.db.transaction() as con:
        if action=='снять' and len(args)==1:
            con.execute('DELETE FROM title_selected WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id))
            return '✅ Покупной титул снят.'
        key=_parse_key(args,TITLES)
        if key is None: return '❌ Неверный ключ титула.'
        owned=con.execute('SELECT 1 FROM title_owned WHERE platform=? AND user_id=? AND title_key=?',
                          (ctx.platform,ctx.user_id,key)).fetchone()
        if action=='купить':
            if owned: return '✅ Этот титул уже приобретён.'
            if not _money(con,ctx,-TITLES[key][1],now,'title:'+key): return '❌ Не хватает монет.'
            con.execute('INSERT INTO title_owned(platform,user_id,title_key,bought_at) VALUES(?,?,?,?)',
                        (ctx.platform,ctx.user_id,key,now))
            return f'✅ Титул «{TITLES[key][0]}» приобретён.'
        if not owned: return '❌ Сначала купи титул.'
        con.execute('''INSERT INTO title_selected(platform,user_id,title_key) VALUES(?,?,?)
            ON CONFLICT(platform,user_id) DO UPDATE SET title_key=excluded.title_key''',
            (ctx.platform,ctx.user_id,key))
        return f'🏷 Выбран титул «{TITLES[key][0]}».'


def refine_command(service,ctx,args,now):
    if not args or args[0].casefold() in ('каталог','рецепты'):
        return ('🧪 ПЕРЕРАБОТКА РЕСУРСОВ\n'+'\n'.join(
            f'• {name} [{key}]: '+', '.join(f'{MATERIALS[mat]} ×{amount}' for mat,amount in need.items())+f'; энергия {energy}'
            for key,(name,need,energy) in REFINING.items())+'\nКоманда: переработка fiber')
    key=args[0].casefold()
    if len(args)!=1 or key not in REFINING:
        return '❌ Выбери ключ из «переработка каталог».'
    name,needs,power=REFINING[key]
    with service.db.transaction() as con:
        station=con.execute('SELECT stored FROM player_energy WHERE platform=? AND user_id=?',
                            (ctx.platform,ctx.user_id)).fetchone()
        if station is None: return '❌ Сначала построй энергостанцию.'
        if station['stored']<power: return '❌ Недостаточно энергии.'
        material_rows={r['material_key']:r['amount'] for r in con.execute('SELECT material_key,amount FROM material_balances WHERE platform=? AND user_id=?',
                                                                          (ctx.platform,ctx.user_id))}
        if material_rows.get(key,0)>=MAX_MATERIALS: return '❌ Склад этого ресурса заполнен.'
        if any(material_rows.get(mat,0)<count for mat,count in needs.items()):
            return '❌ Не хватает сырья: '+', '.join(f'{MATERIALS[k]} ×{v}' for k,v in needs.items())
        for mat,count in needs.items():
            changed=con.execute('UPDATE material_balances SET amount=amount-? WHERE platform=? AND user_id=? AND material_key=? AND amount>=?',
                                (count,ctx.platform,ctx.user_id,mat,count)).rowcount
            if changed!=1: raise RuntimeError('Atomic resource debit failed')
        con.execute('UPDATE player_energy SET stored=stored-? WHERE platform=? AND user_id=? AND stored>=?',
                    (power,ctx.platform,ctx.user_id,power))
        con.execute('''INSERT INTO material_balances(platform,user_id,material_key,amount) VALUES(?,?,?,1)
            ON CONFLICT(platform,user_id,material_key) DO UPDATE SET amount=amount+1''',
            (ctx.platform,ctx.user_id,key))
        return f'🧪 Получен ресурс «{name}» ×1. Затрачено {power} энергии.'


def inventory_summary(service,ctx):
    with closing(service.db.connect()) as con:
        pets=con.execute('SELECT COUNT(*) FROM pet_owned WHERE platform=? AND user_id=?',
                         (ctx.platform,ctx.user_id)).fetchone()[0]
        crates=con.execute('SELECT COALESCE(SUM(amount),0) FROM box_items WHERE platform=? AND user_id=?',
                           (ctx.platform,ctx.user_id)).fetchone()[0]
        titles=con.execute('SELECT COUNT(*) FROM title_owned WHERE platform=? AND user_id=?',
                           (ctx.platform,ctx.user_id)).fetchone()[0]
    return f'\n🐾 Питомцы: {pets}/{MAX_PETS} • 🎁 Контейнеры: {crates} • 🏷 Титулы: {titles}'


def extra_command(service,ctx,cmd,args,now):
    if cmd=='pets': return pet_command(service,ctx,args,now)
    if cmd=='boxes': return box_command(service,ctx,args,now)
    if cmd=='titles': return title_command(service,ctx,args,now)
    if cmd=='refine': return refine_command(service,ctx,args,now)
    return None
