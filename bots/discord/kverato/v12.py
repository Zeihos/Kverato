"""КВЕРАТО v0.12: collectible wardrobe, gifts and escrowed player market.

All money and item movement is inside SQLite BEGIN IMMEDIATE transactions.
Listings hold their inventory in escrow until a purchase or cancellation. There
is no real-money trade, withdrawal, external payment, or player chat moderation.
"""
from __future__ import annotations

from contextlib import closing

from .features import unlock
from .v07 import MAX_WALLET

# key: name, slot, catalogue price, set id
CATALOG = {
    'visor': ('Визор «Контур»', 'head', 700, 'contour'),
    'jacket': ('Куртка «Контур»', 'body', 1100, 'contour'),
    'bracelet': ('Браслет «Контур»', 'accessory', 500, 'contour'),
    'helmet': ('Шлем «Периметр»', 'head', 1900, 'perimeter'),
    'cloak': ('Плащ «Периметр»', 'body', 2700, 'perimeter'),
    'compass': ('Компас «Периметр»', 'accessory', 1450, 'perimeter'),
    'halo': ('Обруч «Аврора»', 'head', 2300, 'aurora'),
    'coat': ('Плащ «Аврора»', 'body', 2800, 'aurora'),
    'pendant': ('Кулон «Аврора»', 'accessory', 1600, 'aurora'),
    'mask': ('Маска «Синтез»', 'head', 3500, 'synthesis'),
    'armor': ('Куртка «Синтез»', 'body', 4300, 'synthesis'),
    'ring': ('Кольцо «Синтез»', 'accessory', 2000, 'synthesis'),
}
SETS = {'contour': ('Контур', 300), 'perimeter': ('Периметр', 600)}
SETS.update({'aurora': ('Аврора', 900), 'synthesis': ('Синтез', 1300)})
SLOTS = {'head': 'Голова', 'body': 'Одежда', 'accessory': 'Аксессуар'}
MAX_STACK = 50
MAX_INVENTORY = 50
MAX_LISTINGS = 5
GIFT_DAILY_LIMIT = 3
MARKET_FEE_PERCENT = 5
MARKET_MIN_PRICE = 10
MARKET_MAX_PRICE = 100_000


def _valid_number(s: str, min_value=1, max_value=1_000_000):
    if not s.isascii() or not s.isdecimal() or not (1 <= len(s) <= 12):
        return None
    value = int(s)
    return value if min_value <= value <= max_value else None


def _stock(con, platform: str, user_id: str, key: str) -> int:
    row = con.execute('''SELECT amount FROM wardrobe_items
        WHERE platform=? AND user_id=? AND item_key=?''', (platform, user_id, key)).fetchone()
    return row['amount'] if row else 0


def _sum_stock(con, platform: str, user_id: str) -> int:
    return int(con.execute('SELECT COALESCE(SUM(amount),0) FROM wardrobe_items WHERE platform=? AND user_id=?',
                           (platform, user_id)).fetchone()[0])


def _take(con, platform: str, user_id: str, key: str, amount: int) -> bool:
    cur = con.execute('''UPDATE wardrobe_items SET amount=amount-?
        WHERE platform=? AND user_id=? AND item_key=? AND amount>=?''',
        (amount, platform, user_id, key, amount))
    if not cur.rowcount:
        return False
    # No item can remain equipped after its last copy has been given away or escrowed.
    if not _stock(con, platform, user_id, key):
        con.execute('DELETE FROM wardrobe_equipped WHERE platform=? AND user_id=? AND item_key=?',
                    (platform, user_id, key))
        con.execute('DELETE FROM wardrobe_items WHERE platform=? AND user_id=? AND item_key=? AND amount=0',
                    (platform, user_id, key))
    return True


def _give(con, platform: str, user_id: str, key: str, amount: int) -> None:
    con.execute('''INSERT INTO wardrobe_items(platform,user_id,item_key,amount) VALUES(?,?,?,?)
        ON CONFLICT(platform,user_id,item_key) DO UPDATE SET amount=amount+excluded.amount''',
        (platform, user_id, key, amount))


def _log(con, platform: str, actor_id: str, counterparty_id: str, item_key: str,
         kind: str, amount: int, coins: int, now: int, listing_id: int | None = None) -> None:
    con.execute('''INSERT INTO wardrobe_ledger(platform,actor_id,counterparty_id,item_key,kind,amount,coins,created_at,listing_id)
        VALUES(?,?,?,?,?,?,?,?,?)''',
        (platform, actor_id, counterparty_id, item_key, kind, amount, coins, now, listing_id))


def _owner(con, ctx):
    return (ctx.platform, ctx.user_id)


def wardrobe_command(service, ctx, args: list[str], now: int) -> str:
    cmd = args[0].casefold() if args else ''
    if cmd in ('', 'каталог') and len(args) <= 1:
        lines = [f'👕 ГАРДЕРОБ КВЕРАТО (свободных мест: до {MAX_INVENTORY})']
        for key, (name, slot, price, collection) in CATALOG.items():
            lines.append(f'• {name} [{key}] — {price} монет, {SLOTS[slot]}')
        lines.append('Купить: гардероб купить visor; надеть: гардероб надеть visor; снять: гардероб снять head.')
        lines.append('Облик: облик; коллекции: коллекции; торговля: рынок.')
        return '\n'.join(lines)
    if cmd == 'купить':
        if len(args) != 2 or args[1].casefold() not in CATALOG:
            return '👕 Формат: гардероб купить visor. Открой «гардероб» для каталога.'
        key = args[1].casefold()
        name, _slot, price, _set = CATALOG[key]
        with service.db.transaction() as con:
            if _sum_stock(con,*_owner(con,ctx)) >= MAX_INVENTORY or _stock(con,ctx.platform,ctx.user_id,key) >= MAX_STACK:
                return '📦 Гардероб заполнен (максимум 50 вещей).'
            taken = con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND user_id=? AND coins>=?''',
                                (price, ctx.platform, ctx.user_id, price))
            if not taken.rowcount:
                return f'❌ Недостаточно монет: требуется {price}.'
            _give(con,ctx.platform,ctx.user_id,key,1)
            _log(con,ctx.platform,ctx.user_id,'',key,'shop',1,-price,now)
        return f'✅ Куплено: {name}. Списано {price} монет.'
    if cmd == 'надеть':
        if len(args) != 2 or args[1].casefold() not in CATALOG:
            return '👕 Формат: гардероб надеть visor.'
        key = args[1].casefold()
        name, slot, _price, _set = CATALOG[key]
        with service.db.transaction() as con:
            if not _stock(con,ctx.platform,ctx.user_id,key):
                return '❌ Этого предмета нет в твоём гардеробе.'
            con.execute('''INSERT INTO wardrobe_equipped(platform,user_id,slot,item_key)
                VALUES(?,?,?,?) ON CONFLICT(platform,user_id,slot)
                DO UPDATE SET item_key=excluded.item_key''', (ctx.platform,ctx.user_id,slot,key))
        return f'✅ Надето: {name} ({SLOTS[slot]}).'
    if cmd == 'снять':
        if len(args) != 2 or args[1].casefold() not in SLOTS:
            return '👕 Формат: гардероб снять head/body/accessory.'
        slot = args[1].casefold()
        with service.db.transaction() as con:
            con.execute('DELETE FROM wardrobe_equipped WHERE platform=? AND user_id=? AND slot=?',
                        (ctx.platform,ctx.user_id,slot))
        return f'✅ Слот «{SLOTS[slot]}» освобождён.'
    return '👕 Команды: гардероб / гардероб купить visor / гардероб надеть visor / гардероб снять head.'


def outfit_command(service, ctx, args: list[str], now: int) -> str:
    if args:
        return '👕 Используй «облик» без аргументов.'
    with closing(service.db.connect()) as con:
        worn = {row['slot']: row['item_key'] for row in con.execute('''SELECT slot,item_key
            FROM wardrobe_equipped WHERE platform=? AND user_id=?''', _owner(con,ctx))}
        items = [(row['item_key'], row['amount']) for row in con.execute('''SELECT item_key,amount
            FROM wardrobe_items WHERE platform=? AND user_id=? AND amount>0 ORDER BY item_key''',_owner(con,ctx))]
    lines = ['👕 КВЕРАТО | МОЙ ОБЛИК']
    lines.extend(f'{name}: {CATALOG[worn[slot]][0] if slot in worn else "не выбран"}' for slot,name in SLOTS.items())
    lines.append(f'🎒 В гардеробе: {sum(q for _,q in items)}/{MAX_INVENTORY}')
    lines.extend(f'• {CATALOG[k][0]} ×{q} [{k}]' for k,q in items)
    return '\n'.join(lines)


def gift_command(service, ctx, args: list[str], now: int) -> str:
    if len(args) != 2 or _valid_number(args[0],1,10**19-1) is None or args[1].casefold() not in CATALOG:
        return '🎁 Формат: подарить Telegram_ID visor (один предмет за раз).'
    recipient, key = args[0], args[1].casefold()
    if recipient == ctx.user_id:
        return '❌ Нельзя подарить предмет самому себе.'
    with service.db.transaction() as con:
        if not con.execute('SELECT 1 FROM players WHERE platform=? AND user_id=?',
                           (ctx.platform,recipient)).fetchone():
            return '❌ Получатель должен сначала начать играть в КВЕРАТО.'
        # Recipients with active bot restrictions cannot receive gifts.
        ban=con.execute('SELECT blocked_until FROM bot_access WHERE platform=? AND user_id=?',
                        (ctx.platform,recipient)).fetchone()
        if ban and (ban['blocked_until'] == 0 or ban['blocked_until'] > now):
            return '❌ Получатель временно не может получать подарки.'
        if not _stock(con,ctx.platform,ctx.user_id,key):
            return '❌ В инвентаре нет такого предмета.'
        if _sum_stock(con,ctx.platform,recipient) >= MAX_INVENTORY or _stock(con,ctx.platform,recipient,key) >= MAX_STACK:
            return '📦 Гардероб получателя заполнен.'
        day=now//86400
        sent=con.execute('''SELECT sent FROM wardrobe_gifts_daily WHERE platform=? AND user_id=? AND utc_day=?''',
                         (ctx.platform,ctx.user_id,day)).fetchone()
        if sent and sent['sent']>=GIFT_DAILY_LIMIT:
            return '⏳ Сегодня уже отправлено три подарка (сутки UTC).'
        if not _take(con,ctx.platform,ctx.user_id,key,1):
            return '❌ В инвентаре нет такого предмета.'
        _give(con,ctx.platform,recipient,key,1)
        con.execute('''INSERT INTO wardrobe_gifts_daily(platform,user_id,utc_day,sent)
            VALUES(?,?,?,1) ON CONFLICT(platform,user_id,utc_day) DO UPDATE SET sent=sent+1''',
            (ctx.platform,ctx.user_id,day))
        _log(con,ctx.platform,ctx.user_id,recipient,key,'gift',1,0,now)
        unlock(con,ctx,'generous',now)
    return f'🎁 Подарено игроку {recipient}: {CATALOG[key][0]}.'


def market_command(service, ctx, args: list[str], now: int) -> str:
    action = args[0].casefold() if args else ''
    if action in ('','список') and len(args) <= 1:
        with closing(service.db.connect()) as con:
            results=con.execute('''SELECT id,seller_id,item_key,price FROM wardrobe_listings
                WHERE platform=? AND status='open' ORDER BY id DESC LIMIT 12''',(ctx.platform,)).fetchall()
        lines=['🛍 КВЕРАТО | ТОРГОВАЯ ПЛОЩАДКА']
        lines.extend(f'#{r["id"]} • {CATALOG[r["item_key"]][0]} — {r["price"]} монет (игрок {r["seller_id"]})'
                     for r in results)
        if not results:
            lines.append('Пока нет предложений.')
        lines.append('рынок выставить visor 850 / рынок купить 1 / рынок снять 1 / рынок история')
        return '\n'.join(lines)
    if action == 'выставить':
        if len(args)!=3 or args[1].casefold() not in CATALOG or _valid_number(args[2],MARKET_MIN_PRICE,MARKET_MAX_PRICE) is None:
            return f'🛍 Формат: рынок выставить visor 850 (цена {MARKET_MIN_PRICE}–{MARKET_MAX_PRICE}).'
        key,price=args[1].casefold(),int(args[2])
        with service.db.transaction() as con:
            active=con.execute('''SELECT COUNT(*) FROM wardrobe_listings
                WHERE platform=? AND seller_id=? AND status='open' ''',(ctx.platform,ctx.user_id)).fetchone()[0]
            if active>=MAX_LISTINGS:
                return f'⏳ Одновременно можно выставить не более {MAX_LISTINGS} товаров.'
            if not _take(con,ctx.platform,ctx.user_id,key,1):
                return '❌ Такого предмета нет в свободном инвентаре.'
            cur=con.execute('''INSERT INTO wardrobe_listings(platform,seller_id,item_key,price,status,created_at)
                VALUES(?,?,?,?,'open',?)''',(ctx.platform,ctx.user_id,key,price,now))
            _log(con,ctx.platform,ctx.user_id,'',key,'list',1,0,now,cur.lastrowid)
        return f'✅ Лот #{cur.lastrowid}: {CATALOG[key][0]} выставлен за {price} монет.'
    if action == 'снять':
        if len(args)!=2 or _valid_number(args[1]) is None:
            return '🛍 Формат: рынок снять НОМЕР.'
        listing_id=int(args[1])
        with service.db.transaction() as con:
            row=con.execute('''SELECT * FROM wardrobe_listings WHERE id=? AND platform=?''',
                            (listing_id,ctx.platform)).fetchone()
            if not row or row['seller_id']!=ctx.user_id:
                return '❌ Такой лот не найден среди твоих предложений.'
            if row['status']!='open':
                return '⏳ Лот уже закрыт.'
            if _sum_stock(con,ctx.platform,ctx.user_id)>=MAX_INVENTORY:
                return '📦 Освободи место в гардеробе, затем сними лот.'
            if _stock(con,ctx.platform,ctx.user_id,row['item_key'])>=MAX_STACK:
                return '📦 Слишком много одинаковых предметов в гардеробе.'
            con.execute('''UPDATE wardrobe_listings SET status='cancelled',closed_at=?
                WHERE id=? AND status='open' ''',(now,listing_id))
            _give(con,ctx.platform,ctx.user_id,row['item_key'],1)
            _log(con,ctx.platform,ctx.user_id,'',row['item_key'],'cancel',1,0,now,listing_id)
        return f'✅ Лот #{listing_id} снят; предмет возвращён в гардероб.'
    if action == 'купить':
        if len(args)!=2 or _valid_number(args[1]) is None:
            return '🛍 Формат: рынок купить НОМЕР.'
        listing_id=int(args[1])
        with service.db.transaction() as con:
            row=con.execute('''SELECT * FROM wardrobe_listings WHERE id=? AND platform=?''',
                            (listing_id,ctx.platform)).fetchone()
            if not row or row['status']!='open':
                return '❌ Лот недоступен или уже продан.'
            if row['seller_id']==ctx.user_id:
                return '❌ Нельзя покупать собственный лот.'
            seller_ban=con.execute('SELECT blocked_until FROM bot_access WHERE platform=? AND user_id=?',
                                   (ctx.platform,row['seller_id'])).fetchone()
            if (seller_ban and row['seller_id'] not in service.owner_ids
                    and (seller_ban['blocked_until']==0 or seller_ban['blocked_until']>now)):
                return '🚫 Продавец ограничен в доступе к КВЕРАТО; этот лот временно недоступен.'
            if _sum_stock(con,ctx.platform,ctx.user_id)>=MAX_INVENTORY or _stock(con,ctx.platform,ctx.user_id,row['item_key'])>=MAX_STACK:
                return '📦 Не хватает места в гардеробе.'
            price=row['price']
            fee=max(1,price*MARKET_FEE_PERCENT//100)
            payout=price-fee
            seller=con.execute('''SELECT coins FROM players WHERE platform=? AND user_id=?''',
                               (ctx.platform,row['seller_id'])).fetchone()
            if not seller:
                return '❌ Продавец больше не зарегистрирован.'
            if seller['coins']>MAX_WALLET-payout:
                return '❌ Продавец достиг лимита кошелька. Покупка пока невозможна.'
            taken=con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND user_id=? AND coins>=?''',
                              (price,ctx.platform,ctx.user_id,price))
            if not taken.rowcount:
                return '❌ Недостаточно монет для покупки.'
            con.execute('UPDATE players SET coins=coins+? WHERE platform=? AND user_id=?',
                        (payout,ctx.platform,row['seller_id']))
            _give(con,ctx.platform,ctx.user_id,row['item_key'],1)
            con.execute('''UPDATE wardrobe_listings SET status='sold',buyer_id=?,closed_at=?
                WHERE id=? AND status='open' ''',(ctx.user_id,now,listing_id))
            _log(con,ctx.platform,ctx.user_id,row['seller_id'],row['item_key'],'buy',1,-price,now,listing_id)
            _log(con,ctx.platform,row['seller_id'],ctx.user_id,row['item_key'],'sell',1,payout,now,listing_id)
            unlock(con,ctx,'trader',now)
        return (f'✅ Лот #{listing_id} куплен: {CATALOG[row["item_key"]][0]} за {price} монет. '
                f'Продавцу перечислено {payout}; комиссия {fee} монет.')
    if action=='история' and len(args)==1:
        with closing(service.db.connect()) as con:
            rows=con.execute('''SELECT * FROM wardrobe_ledger WHERE platform=? AND actor_id=?
                ORDER BY id DESC LIMIT 10''',(ctx.platform,ctx.user_id)).fetchall()
        if not rows:
            return '📜 Пока нет операций с гардеробом.'
        return '📜 ПОСЛЕДНИЕ ОПЕРАЦИИ\n'+'\n'.join(
            f'{r["kind"]}: {CATALOG[r["item_key"]][0]}, {r["coins"]:+} монет' for r in rows)
    return '🛍 Команды: рынок / рынок выставить visor 850 / рынок купить 1 / рынок снять 1 / рынок история.'


def sets_command(service,ctx,args:list[str],now:int)->str:
    if not args:
        with closing(service.db.connect()) as con:
            stock={r['item_key']:r['amount'] for r in con.execute('''SELECT item_key,amount FROM wardrobe_items
                WHERE platform=? AND user_id=? AND amount>0''',(ctx.platform,ctx.user_id))}
            claimed={r['set_key'] for r in con.execute('''SELECT set_key FROM wardrobe_set_claims
                WHERE platform=? AND user_id=?''',(ctx.platform,ctx.user_id))}
        lines=['🎖 КОЛЛЕКЦИИ КВЕРАТО']
        for set_key,(name,reward) in SETS.items():
            owned=sum(1 for key,(_,_,_,sid) in CATALOG.items() if sid==set_key and stock.get(key,0))
            lines.append(f'{name}: {owned}/3, награда {reward} монет. {"✅ Получена" if set_key in claimed else "Команда: коллекции награда "+set_key}')
        return '\n'.join(lines)
    if len(args)!=2 or args[0].casefold()!='награда' or args[1].casefold() not in SETS:
        return '🎖 Формат: коллекции награда contour/perimeter.'
    key=args[1].casefold()
    with service.db.transaction() as con:
        if con.execute('SELECT 1 FROM wardrobe_set_claims WHERE platform=? AND user_id=? AND set_key=?',
                       (ctx.platform,ctx.user_id,key)).fetchone():
            return '✅ Награда этой коллекции уже получена.'
        required=[k for k,(_name,_slot,_price,set_id) in CATALOG.items() if set_id==key]
        if any(_stock(con,ctx.platform,ctx.user_id,k)<1 for k in required):
            return '🔒 Для награды нужно иметь все три предмета коллекции одновременно.'
        title,reward=SETS[key]
        success=con.execute('''UPDATE players SET coins=coins+? WHERE platform=? AND user_id=? AND coins<=?''',
                            (reward,ctx.platform,ctx.user_id,MAX_WALLET-reward))
        if not success.rowcount:
            return '❌ Кошелёк переполнен, освободи место и повтори.'
        con.execute('INSERT INTO wardrobe_set_claims(platform,user_id,set_key,claimed_at) VALUES(?,?,?,?)',
                    (ctx.platform,ctx.user_id,key,now))
        unlock(con,ctx,'collector',now)
    return f'🏆 Коллекция «{title}» собрана! +{reward} монет и достижение «Коллекционер».'


def inventory_summary(service,ctx) -> str:
    with closing(service.db.connect()) as con:
        items=con.execute('''SELECT item_key,amount FROM wardrobe_items WHERE platform=? AND user_id=? AND amount>0
            ORDER BY item_key''',(ctx.platform,ctx.user_id)).fetchall()
    if not items:
        return '\n👕 Гардероб: пока пуст. Команда: гардероб.'
    return '\n👕 Гардероб: '+'; '.join(f'{CATALOG[r["item_key"]][0]} ×{r["amount"]}' for r in items)


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    if cmd=='wardrobe':
        return wardrobe_command(service,ctx,args,now)
    if cmd=='outfit':
        return outfit_command(service,ctx,args,now)
    if cmd=='giftitem':
        return gift_command(service,ctx,args,now)
    if cmd=='market':
        return market_command(service,ctx,args,now)
    if cmd=='sets':
        return sets_command(service,ctx,args,now)
    return None
