"""Read-only, source-backed catalog for all principal KVERATO systems.

Data is imported from game definitions, so prices and titles cannot drift out
of sync with the purchasing commands. VK output stays well below 4096 chars.
"""
from __future__ import annotations

from .features import ACHIEVEMENTS, JOBS
from .v04 import EQUIP
from .v06 import CLAN_PURCHASES
from .v08 import BUSINESSES, MATERIALS, CONTRACTS
from .v09 import HOMES, VEHICLES, RECIPES, ENERGY_PRICE
from .v10 import TRACKS, TOURNAMENT_ENTRY, RACE_ENTRY
from .v11 import DISTRICTS, OTHER_DISTRICTS, RELICS, EQUIPMENT_RECIPES
from .v12 import CATALOG, SETS
from .v13 import TIERS, EVENT_GOODS, PODIUM_COINS
from .v14 import PETS, BOXES, TITLES, REFINING
from .v15 import ORDERS
from .v16 import ROUTES, CLAN_RUNS

TOPICS = (
    'достижения', 'профессии', 'бизнесы', 'транспорт', 'магазин',
    'недвижимость', 'гардероб', 'ресурсы', 'контракты', 'крафт',
    'районы', 'находки', 'коллекции', 'гонки', 'кланы',
    'снаряжение', 'сезон', 'игры', 'питомцы', 'контейнеры', 'титулы', 'переработка', 'заказы', 'рейсы', 'кланпоходы',
)


def catalog_command(section: str = '') -> str:
    from .service import SHOP
    key = section.casefold().strip()
    if not key:
        return ('📚 КВЕРАТО | ПОЛНЫЙ КАТАЛОГ\n'
                + '\n'.join('• каталог ' + name for name in TOPICS)
                + '\n\nЭто справочник. Для управления используй команды соответствующего раздела.')
    if key in ('достижения', 'ачивки'):
        entries = (f'• {name} — {goal} [{ident}]' for ident, (name, goal) in ACHIEVEMENTS.items())
    elif key in ('профессии', 'работы'):
        entries = (f'• {name} [{ident}] — {start}–{end} монет за работу, 5 карьерных рангов'
                   for ident, (name, start, end) in JOBS.items())
    elif key in ('бизнесы', 'предприятия'):
        entries = (f'• {b.name} [{ident}] — {b.price:,} монет, {b.hourly_units} {MATERIALS[b.material]}/ч'
                   for ident, b in BUSINESSES.items())
    elif key in ('транспорт', 'машины'):
        entries = (f'• {item.title} [{ident}] — {item.price:,} монет' for ident, item in VEHICLES.items())
    elif key in ('магазин', 'значки'):
        entries = (f'• {name} [{ident}] — {price:,} монет' for ident, (name, price) in SHOP.items())
    elif key in ('недвижимость', 'жильё'):
        entries = (f'• {item.title} [{ident}] — {item.price:,} монет, базовый гараж {item.parking}'
                   for ident, item in HOMES.items())
    elif key in ('гардероб', 'одежда'):
        entries = (f'• {title} [{ident}] — {price:,} монет, слот {slot}'
                   for ident, (title, slot, price, _set) in CATALOG.items())
    elif key in ('питомцы', 'питомец'):
        entries = (f'• {name} [{ident}] — {price:,} монет' for ident, (name,price) in PETS.items())
    elif key in ('контейнеры', 'кейсы'):
        entries = (f'• {box[0]} [{ident}] — {box[1]:,} монет' for ident,box in BOXES.items())
    elif key in ('титулы', 'титул'):
        entries = (f'• {name} [{ident}] — {price:,} монет' for ident,(name,price) in TITLES.items())
    elif key == 'переработка':
        entries = (f'• {name} [{ident}] — '+', '.join(f'{MATERIALS[k]} ×{v}' for k,v in parts.items()) + f'; энергия {energy}'
                   for ident,(name,parts,energy) in REFINING.items())
    elif key in ('ресурсы', 'материалы'):
        entries = (f'• {name} [{ident}]' for ident, name in MATERIALS.items())
    elif key in ('контракты', 'поставки'):
        entries = (f'• {MATERIALS[ident]} — сдать {amount} ед. за {coins:,} монет'
                   for ident, (amount, coins) in CONTRACTS.items())
    elif key == 'заказы':
        entries = (f'• {o.name} [{ident}]: '+', '.join(f'{MATERIALS[k]} ×{n}' for k,n in o.materials.items())
                   +f' → {o.payout} монет, ранг {o.min_rank}+' for ident,o in ORDERS.items())
    elif key in ('крафт', 'рецепты'):
        entries = (f'• {title} [{ident}]: ' + ', '.join(f'{MATERIALS[k]} ×{v}' for k, v in parts.items())
                   + f', энергия {energy}' for ident, (title, parts, energy) in RECIPES.items())
    elif key in ('районы', 'город'):
        entries = [*(f'• {title} [{ident}] — от {xp} XP, ресурс {MATERIALS[mat]}'
                     for ident, (title, xp, mat) in DISTRICTS.items()),
                   *(f'• {title} [{ident}] — социальная зона' for ident, title in OTHER_DISTRICTS.items())]
    elif key in ('находки', 'реликвии'):
        entries = (f'• {title} [{ident}]' for ident, title in RELICS.items())
    elif key in ('коллекции', 'наборы'):
        entries = (f'• {name} [{ident}] — награда {reward} монет, 3 предмета'
                   for ident, (name, reward) in SETS.items())
    elif key in ('рейсы', 'доставка'):
        entries = (f'• {title} [{ident}]: {reward} монет, требует ' + ', '.join(f'{amount} ×{MATERIALS[k]}' for k,amount in needs.items()) for ident,(title,track,minimum,needs,reward,xp) in ROUTES.items())
    elif key in ('кланпоходы', 'кланэкспедиции'):
        entries = (f'• {title} [{ident}]: {points} очков, комплект {tier}+, ' + ', '.join(f'{amount} ×{MATERIALS[k]}' for k,amount in needs.items()) for ident,(title,needs,tier,points,energy) in CLAN_RUNS.items())
    elif key in ('гонки', 'трассы'):
        entries = [*(f'• {name} — {details[1]}' for name, details in TRACKS.items()),
                   f'• Одиночная гонка: взнос {RACE_ENTRY} монет',
                   f'• Турнир: взнос {TOURNAMENT_ENTRY} монет']
    elif key in ('кланы', 'улучшения'):
        entries = (f'• {title} [{ident}] — {price} монет из казны'
                   for ident, (price, title) in CLAN_PURCHASES.items())
    elif key in ('снаряжение', 'исследования'):
        entries = (f'• Уровень {level}: ' + ', '.join(f'{MATERIALS[k]} ×{v}' for k, v in need.items())
                   + f', энергия {energy}' for level, (need, energy) in EQUIPMENT_RECIPES.items())
    elif key in ('сезон', 'награды'):
        entries = [*(f'• Уровень {level}: {points} очков — {reward} монет'
                     for level, (points, reward) in TIERS.items()),
                   *(f'• {name} [{ident}] — {price} огней' for ident, (name, price) in EVENT_GOODS.items()),
                   *(f'• Место {position}: {reward} монет' for position, reward in PODIUM_COINS.items())]
    elif key in ('игры', 'мини-игры'):
        entries = ('• Монетка (ставки)', '• Кубик (ставки)', '• Угадай число 1–5 (ставки)',
                   '• Камень-ножницы-бумага (без ставок)', '• Слоты (ставки)',
                   '• Дуэли (ставки)', '• Заезды (ставки)', '• Гонки и турниры')
    else:
        return '❌ Нет такого раздела. Напиши «каталог» для полного списка.'
    return ('📚 КВЕРАТО | ' + key.upper() + '\n'
            + '\n'.join(entries).replace(',', ' ')
            + '\n\nВсе цены — в виртуальных монетах, если не указано иное.')
