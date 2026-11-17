"""Dynamic, local-only KVERATO VK image cards.

All numbers come from SQLite or the unchanged BotService response. No image AI,
web site, fabricated player data, or network call is used during rendering.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from functools import lru_cache
import math
import re
import sys
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .features import ACHIEVEMENTS, JOBS
from .service import level_from_xp

WIDTH, HEIGHT = 1000, 1330
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'assets'
# Deliberately exclude actions that can mutate SQLite. Card generation is purely a
# presentation layer *after* the normal permission / command pipeline has run.
CARD_COMMANDS = frozenset({
    'start', 'profile', 'balance', 'level', 'stats', 'achievements', 'top', 'topxp', 'topwins', 'toprep',
    'racetop', 'clantop', 'seasontop', 'games', 'help', 'economy', 'shop',
    'catalog', 'jobs', 'businesses', 'materials', 'wardrobe', 'sets',
    'city', 'hall', 'inventory', 'chains', 'quests', 'race', 'delivery',
    'contracts', 'orders', 'pets', 'boxes', 'titles', 'vehicle', 'home',
    'station', 'craft', 'market', 'season', 'equipment', 'collection',
    'project', 'tournament', 'garage', 'outfit', 'career', 'bank', 'rep',
    'racestats', 'district', 'clan', 'clanexpedition',
})
# Commands with more arguments might PURCHASE, WITHDRAW, JOIN, CLAIM etc.
# The caller only switches to an image for a command *without extra arguments*.

HEADINGS = {
    'start': 'ДОБРО ПОЖАЛОВАТЬ', 'balance': 'БАЛАНС', 'level': 'УРОВЕНЬ',
    'profile': 'ПРОФИЛЬ', 'stats': 'СТАТИСТИКА', 'achievements': 'ДОСТИЖЕНИЯ',
    'top': 'ТОП ПО МОНЕТАМ', 'topxp': 'ТОП ПО ОПЫТУ',
    'topwins': 'ТОП ПО ПОБЕДАМ', 'toprep': 'ТОП ПО РЕПУТАЦИИ',
    'games': 'ИГРЫ', 'help': 'МЕНЮ', 'economy': 'ЭКОНОМИКА',
    'shop': 'МАГАЗИН', 'catalog': 'КАТАЛОГ', 'jobs': 'ПРОФЕССИИ',
    'businesses': 'ПРЕДПРИЯТИЯ', 'vehicle': 'ТРАНСПОРТ', 'home': 'НЕДВИЖИМОСТЬ',
    'season': 'СЕЗОН', 'inventory': 'ИНВЕНТАРЬ',
    'clantop': 'ТОП КЛАНОВ', 'racetop': 'ТОП ГОНЩИКОВ',
    'seasontop': 'ТОП СЕЗОНА',
}

@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    symbol: str = '✦'

@dataclass(frozen=True)
class CardData:
    command: str
    title: str
    name: str
    user_id: str
    metrics: tuple[tuple[str, tuple[Metric, ...]], ...] = ()
    rows: tuple[tuple[str, str], ...] = ()
    items: tuple[tuple[str, str, bool], ...] = ()
    lines: tuple[str, ...] = ()
    avatar: bytes | None = None


def _fmt(value) -> str:
    if isinstance(value, int):
        return f'{value:,}'.replace(',', ' ')
    return str(value)


@lru_cache(maxsize=4)
def _font_path(bold: bool = False, korean: bool = False) -> str | None:
    candidates = (
        ([r'C:\Windows\Fonts\malgunbd.ttf', r'C:\Windows\Fonts\malgun.ttf'] if korean else
         [r'C:\Windows\Fonts\segoeuib.ttf', r'C:\Windows\Fonts\arialbd.ttf'] if bold else
         [r'C:\Windows\Fonts\segoeui.ttf', r'C:\Windows\Fonts\arial.ttf'])
        + ([r'C:\Windows\Fonts\malgun.ttf'] if not korean else [])
        + (['/usr/share/fonts/truetype/nanum/NanumSquareB.ttf', '/usr/share/fonts/truetype/nanum/NanumSquareR.ttf'] if korean else [])
        + (['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'] if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    )
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


@lru_cache(maxsize=160)
def _cached_font(size: int, bold: bool, korean: bool):
    path = _font_path(bold, korean)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)


def _font(size: int, bold: bool = False, s: str = ''):
    ko = bool(re.search('[\uac00-\ud7a3\u3130-\u318f]', s))
    return _cached_font(size, bold, ko)


def _fit(draw, s: str, maximum: int, size: int = 28, minimum: int = 17, bold: bool = False):
    s = str(s).replace('\n',' ').strip()
    for n in range(size, minimum - 1, -1):
        font = _font(n, bold, s)
        if draw.textbbox((0, 0), s, font=font)[2] <= maximum:
            return s, font
    font = _font(minimum, bold, s)
    while s and draw.textbbox((0, 0), s + '…', font=font)[2] > maximum:
        s = s[:-1]
    return s + '…', font


@lru_cache(maxsize=1)
def _base_background() -> Image.Image:
    path=ASSETS/'wallpaper_glass.jpg'
    if path.is_file():
        with Image.open(path) as source:
            background=source.convert('RGBA').resize((WIDTH,HEIGHT),Image.Resampling.LANCZOS)
        # Keep title and metrics legible on top of the glossy background.
        shade=Image.new('RGBA',(WIDTH,HEIGHT),(9,10,28,100))
        background.alpha_composite(shade)
        return background
    base=Image.new('RGBA',(WIDTH,HEIGHT),(18,20,55,255))
    return base


def _bg() -> Image.Image:
    return _base_background().copy()

_ICON_NAMES={'◉':'coins','▣':'bank','★':'star','✧':'sparkles','◆':'gamepad',
             '♥':'heart','▥':'building','⌂':'home','◇':'car','↗':'flag',
             '▲':'chart','◷':'calendar','✦':'badge','?':'question',
             '✂':'scissors','⚔':'swords','▤':'slots','♛':'crown','◎':'coins'}
@lru_cache(maxsize=32)
def _icon_bitmap(key: str) -> Image.Image | None:
    path=ASSETS/'icons'/f'{key}.png'
    if not path.is_file():return None
    with Image.open(path) as icon:
        return icon.convert('RGBA')


def _icon(img: Image.Image, symbol: str, x:int, y:int, size:int=43) -> bool:
    bitmap=_icon_bitmap(_ICON_NAMES.get(symbol,''))
    if bitmap is None:return False
    img.alpha_composite(bitmap.resize((size,size),Image.Resampling.LANCZOS),(x,y))
    return True


def _round(d, box, fill, radius=25, outline=None, width=1):
    d.rounded_rectangle(tuple(map(int,box)), radius=radius, fill=fill, outline=outline, width=width)


def _glass(img: Image.Image, box: tuple[int,int,int,int], radius=30, bright=False):
    # Render only the small card tile rather than allocating a full 1000x1330
    # transparent image for every badge, row and metric.  Visually identical,
    # much faster and with vastly lower peak memory usage.
    x0,y0,x1,y1=map(int,box)
    w,h=x1-x0,y1-y0
    if w<=0 or h<=0:
        return
    overlay=Image.new('RGBA', (w,h),(0,0,0,0))
    d=ImageDraw.Draw(overlay)
    r=min(radius,w//2,h//2)
    _round(d,(0,0,w-1,h-1),(112,104,188,73 if bright else 47),radius=r,
           outline=(214,211,255,156 if bright else 98),width=2)
    d.line([(r,2),(w-r,2)], fill=(242,229,255,99),width=2)
    img.alpha_composite(overlay,(x0,y0))


def _text(img,x,y,string,size=27,color=(242,243,255),bold=False,maxw=None):
    d=ImageDraw.Draw(img)
    if maxw:
        string, font = _fit(d,string,maxw,size,bold=bold)
    else:
        font=_font(size,bold,string)
    d.text((int(x),int(y)),string,font=font,fill=color,stroke_width=0)


@lru_cache(maxsize=1)
def _mascot_bitmap():
    p=ASSETS/'mascot_glass.png'
    if not p.is_file():return None
    with Image.open(p) as original:
        return original.convert('RGBA').resize((340,267),Image.Resampling.LANCZOS)


def _mascot(img):
    mascot=_mascot_bitmap()
    if mascot is not None:
        img.alpha_composite(mascot,(WIDTH-385,53))


def _avatar(img, x=86,y=144,size=143, blob: bytes | None=None):
    source=None
    if blob:
        try:
            with Image.open(BytesIO(blob)) as remote:
                w,h=remote.size
                if w < 2 or h < 2 or w*h > 8_000_000:
                    raise ValueError('Avatar dimensions out of bounds')
                remote.load()
                source=ImageOps.fit(remote.convert('RGBA'),(size,size),method=Image.Resampling.LANCZOS)
        except Exception:
            source=None
    if source is None:
        p=ASSETS/'avatar_placeholder.png'
        if p.is_file():
            with Image.open(p) as orig:source=orig.convert('RGBA')
    if source is None:
        source=Image.new('RGBA',(size,size),(76,49,119,255))
    mask=Image.new('L',(size,size));d=ImageDraw.Draw(mask)
    d.ellipse((2,2,size-3,size-3),fill=255)
    source=ImageOps.fit(source,(size,size),method=Image.Resampling.LANCZOS)
    source.putalpha(mask)
    img.alpha_composite(source,(x,y))
    d=ImageDraw.Draw(img)
    d.ellipse((x,y,x+size,y+size),outline=(191,155,250,240),width=4)


def _frame(data: CardData, show_identity=True):
    img=_bg();d=ImageDraw.Draw(img)
    _glass(img,(30,27,WIDTH-30,HEIGHT-29),radius=49)
    _text(img,73,64,data.title,37,(239,235,255),bold=True,maxw=675)
    d.line((74,121,WIDTH-74,121),fill=(156,135,217,130),width=2)
    _mascot(img)
    bottom=132
    if show_identity:
        _glass(img,(67,137,WIDTH-67,310),radius=33)
        _avatar(img,87,155,133,data.avatar)
        _text(img,241,176,data.name,35,(255,253,255),bold=True,maxw=412)
        _text(img,242,229,f'ID: {data.user_id}',23,(192,176,237))
        bottom=327
    return img,bottom


def _section(img, y, title, metrics, max_bottom=1230):
    if not metrics:return y
    # Each row has 2 tiles, one or three lines, dynamic height.
    count=len(metrics); cols=2 if count>=2 else 1
    rows=math.ceil(count/cols)
    tile_h=80
    need=45+rows*89
    if y+need>1250:
        return -1
    _glass(img,(68,y,WIDTH-68,y+need),radius=27)
    _text(img,93,y+7,title.upper(),21,(194,181,241),bold=True,maxw=820)
    for i,m in enumerate(metrics):
        col=i%cols;row=i//cols
        gap=16
        tile_w=(WIDTH-160-gap*(cols-1))//cols
        x=80+col*(tile_w+gap); ty=y+35+row*89
        _glass(img,(x,ty,x+tile_w,ty+tile_h),radius=21,bright=(i==0 and y<410))
        if not _icon(img,m.symbol,x+17,ty+17,42):
            _text(img,x+20,ty+11,m.symbol,28,(207,176,255),bold=True)
        _text(img,x+68,ty+8,m.label,21,(199,193,229),maxw=tile_w-82)
        _text(img,x+68,ty+39,m.value,28,(248,246,255),bold=True,maxw=tile_w-82)
    return y+need+10


def _footer(img, text='Данные обновляются при запросе'):
    _text(img,85,HEIGHT-92,text,21,(191,180,227),maxw=WIDTH-150)


def _to_png(img):
    out=BytesIO();img.convert('RGB').save(out,format='PNG',compress_level=1)
    return out.getvalue()


def _render_metric(data):
    img,y=_frame(data)
    for section, metrics in data.metrics:
        next_y=_section(img,y,section,metrics)
        if next_y<0:
            raise ValueError('Card metric content overflow')
        y=next_y
    _footer(img)
    return [_to_png(img)]


def _render_ranking(data):
    img,y=_frame(data,show_identity=False)
    _glass(img,(68,154,WIDTH-68,225),radius=22)
    _text(img,95,173,'МЕСТО',24,(195,182,227))
    _text(img,265,173,'ИГРОК',24,(195,182,227))
    _text(img,730,173,'РЕЗУЛЬТАТ',21,(195,182,227))
    y=245
    if not data.rows:
        _glass(img,(76,y,WIDTH-76,y+120));_text(img,103,y+32,'Пока нет игроков в рейтинге',29)
    for i,(name,score) in enumerate(data.rows[:10]):
        height=87
        _glass(img,(75,y,WIDTH-75,y+height),radius=23,bright=(i<3))
        color=((244,204,117) if i==0 else (208,225,247) if i==1 else (236,176,133) if i==2 else (180,160,224))
        _text(img,102,y+22,str(i+1),33,color,bold=True)
        _text(img,226,y+24,name,26,bold=(i<3),maxw=460)
        _text(img,743,y+24,_fmt(score),28,color,bold=True,maxw=184)
        y+=97
    _footer(img,'Рейтинг формируется из реальных данных игроков')
    return [_to_png(img)]


def _render_achievements(data):
    results=[]
    # actual achievement count is likely 22+. 8 per image, no fake progress.
    pages=[data.items[i:i+6] for i in range(0,len(data.items),6)] or [()]
    owned=sum(1 for _,_,ok in data.items)
    for page_no, items in enumerate(pages,1):
        img,y=_frame(data)
        _glass(img,(78,y,WIDTH-78,y+82))
        _text(img,108,y+23,f'Открыто: {owned} из {len(data.items)}',28,bold=True)
        _text(img,740,y+25,f'{page_no}/{len(pages)}',25,(191,173,238))
        y+=99
        for title,desc,ok in items:
            _glass(img,(78,y,WIDTH-78,y+102),radius=22)
            color=(155,239,202) if ok else (204,182,241)
            _text(img,101,y+19,'✓' if ok else '◈',33,color,bold=True)
            _text(img,165,y+10,title,26,bold=True,maxw=655)
            _text(img,165,y+50,desc,22,(197,186,232),maxw=650)
            y+=113
        _footer(img,'✓ Получено     ◈ Ещё не открыто')
        results.append(_to_png(img))
    return results


def _wrap(draw,text,maxwidth,font):
    words=text.split()
    out=[];line=''
    for word in words:
        candidate=(line+' '+word).strip()
        if draw.textbbox((0,0),candidate,font=font)[2] > maxwidth and line:
            out.append(line);line=word
        else:line=candidate
    if line:out.append(line)
    return out or ['']


def _render_list(data):
    # Show all service lines (don't silently crop catalogs), in separate images.
    # The caller bounds the total cards; oversized response falls back to text.
    normal=[]
    for item in data.lines:
        stripped=item.strip()
        if not stripped:continue
        if 'КВЕРАТО' in stripped.upper() and len(stripped)<80:continue
        normal.append(stripped)
    formatted=[]
    tmp=_bg();measure=ImageDraw.Draw(tmp)
    for line in normal:
        formatted.extend(_wrap(measure,line,WIDTH-214,_font(26,s=line)))
    chunks=[formatted[i:i+13] for i in range(0,len(formatted),13)] or [[]]
    # A VK response has a strict four-card limit. Avoid allocating and encoding
    # dozens of 1000x1330 images before the adapter inevitably falls back to
    # its complete text response. No catalog entries are ever discarded.
    if len(chunks) > 4:
        return []
    results=[]
    for ix, chunk in enumerate(chunks,1):
        img,y=_frame(data,show_identity=False)
        if len(chunks)>1:_text(img,770,80,f'{ix}/{len(chunks)}',24,(194,174,235))
        y=157
        for line in chunk:
            if y+67 > HEIGHT-103:break
            _glass(img,(72,y,WIDTH-72,y+64),radius=19)
            _text(img,91,y+16,line,25,maxw=WIDTH-200)
            y+=74
        _footer(img,'Для действия отправь команду текстом в чат')
        results.append(_to_png(img))
    return results


# Designed display tiles. Actual button presses are intentionally NOT shown:
# the VK bot uses commands, not the old permanently visible keyboard.
_MENU_TILES = (
    ('Профиль','Данные и прогресс','◉'),
    ('Статистика','Игры и достижения','▥'),
    ('Игры','Мини-игры и дуэли','◆'),
    ('Экономика','Банк и заработок','◎'),
    ('Достижения','Награды игрока','★'),
    ('Топ','Богатейшие игроки','▲'),
    ('Топ репутации','команда: топреп','♥'),
    ('Топ побед','команда: топпобед','✦'),
    ('Магазин','Значки и предметы','◇'),
    ('Гардероб','Облик и одежда','▣'),
    ('Сезон','Задания и награды','◷'),
    ('Клан','Играй вместе','♛'),
)
_GAME_TILES = (
    ('Монетка','Ставка на орёл или решку','◎'),
    ('Кубик','Игра со ставкой','◆'),
    ('Угадай','Выбери число','?'),
    ('КНБ','Камень, ножницы, бумага','✂'),
    ('Слоты','Комбинации символов','▤'),
    ('Дуэль','Вызов соперника','⚔'),
    ('Гонка','Заезд на транспорте','↗'),
    ('Турнир','Соревнуйся с игроками','★'),
)


def _render_tiles(data):
    tiles = _MENU_TILES if data.command == 'help' else _GAME_TILES
    img,y=_frame(data,show_identity=False)
    _glass(img,(65,150,WIDTH-65,275),radius=26)
    _text(img,96,165,'Выбирай раздел и развивайся!',29,bold=True,maxw=660)
    _text(img,96,219,'Отправь название раздела в чат',23,(192,179,231),maxw=635)
    y=302
    for i,(name,desc,symbol) in enumerate(tiles):
        col=i%2;row=i//2
        gap=14;tile_width=(WIDTH-144-gap)//2
        x=71+col*(tile_width+gap)
        ty=y+row*140
        _glass(img,(x,ty,x+tile_width,ty+127),radius=25,bright=(row==0))
        if not _icon(img,symbol,x+18,ty+33,54):
            _text(img,x+18,ty+23,symbol,34,(209,168,255),bold=True)
        _text(img,x+82,ty+20,name,27,bold=True,maxw=tile_width-96)
        _text(img,x+82,ty+62,desc,20,(206,192,232),maxw=tile_width-95)
    _footer(img,'В беседах пиши ! перед командой')
    return [_to_png(img)]


def is_safe_image_request(command: str | None, input_text: str) -> bool:
    """Only non-mutating, permission-checked requests can become cards."""
    if not command or command not in CARD_COMMANDS:
        return False
    words=input_text.strip().split()
    if not words:
        return False
    if command=='help' and words[0].lstrip('!/.').lower() not in ('меню','menu'):
        return False  # Full /помощь stays readable as a complete text command list.
    if len(words)==1:return True
    # The universal catalog command is pure information for any category.
    if command=='catalog':return True
    # These exact subcommands are known to be informational in the game logic.
    return len(words)==2 and words[1].lower() in ('каталог','список','помощь') and command in (
        'pets','boxes','titles','home','vehicle','wardrobe','craft','equipment',
        'market','clan','station','season','race','tournament',
    )

def prepare_card_data(service,ctx,command,raw:str,avatar:bytes|None=None)->CardData|None:
    """Fetch game facts *after* service.handle; do not modify DB.

    A failed command or a blocked account must never be converted to a card.
    """
    if not raw or command not in CARD_COMMANDS:
        return None
    if raw.lstrip().startswith(('❌','⚠','🚫','🔒','⛔','⌛','⏳','❗','⏸')):
        return None
    title=HEADINGS.get(command,command.upper())
    with closing(service.db.connect()) as con:
        p=con.execute('SELECT * FROM players WHERE platform=? AND user_id=?',
                      (ctx.platform,ctx.user_id)).fetchone()
        if p is None:return None
        name=p['display_name'] or ctx.name
        kw=dict(command=command,title=title,name=name,user_id=ctx.user_id,avatar=avatar)
        if command == 'profile':
            from .v08 import career_rank,career_progress
            from .v14 import active_title
            enterprises=con.execute('SELECT COUNT(*) FROM player_businesses WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id)).fetchone()[0]
            vehicles=con.execute('SELECT COUNT(*) FROM player_vehicles WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id)).fetchone()[0]
            home=con.execute('SELECT 1 FROM player_homes WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id)).fetchone()
            c=career_progress(con,ctx,p['job_key'])
            racer=con.execute('SELECT races,wins,rating FROM race_progress WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id)).fetchone()
            s=con.execute('SELECT title FROM season_titles WHERE platform=? AND user_id=? AND equipped=1 ORDER BY season_id DESC LIMIT 1',(ctx.platform,ctx.user_id)).fetchone()
            t=active_title(con,ctx) or (s['title'] if s else 'не выбран')
            return CardData(**kw,metrics=(
                ('ОСНОВНОЕ', (Metric('Значок',str(p['badge']),'✦'),Metric('Титул',str(t),'★'))),
                ('ФИНАНСЫ',(Metric('Монеты',_fmt(p['coins']),'◉'),Metric('Банк',_fmt(p['bank_coins']),'▣'))),
                ('ПРОГРЕСС',(Metric('Уровень',str(level_from_xp(p['xp'])),'★'),Metric('Опыт',f"{_fmt(p['xp'])} XP",'✧'),Metric('Игры / победы',f"{p['games_played']} / {p['games_won']}",'◆'),Metric('Репутация',str(p['reputation']),'♥'))),
                ('ИМУЩЕСТВО',(Metric('Карьера',f"{career_rank(c['xp'] if c else 0)}/5",'▣'),Metric('Предприятия',str(enterprises),'▥'),Metric('Жильё','есть' if home else 'нет','⌂'),Metric('Транспорт',str(vehicles),'◇'))),
                ('ГОНКИ',(Metric('Гонки / победы',f"{racer['races'] if racer else 0} / {racer['wins'] if racer else 0}",'↗'),Metric('Рейтинг',str(racer['rating'] if racer else 0),'▲'))),
            ))
        if command == 'stats':
            created=datetime.fromtimestamp(p['created_at'],tz=timezone.utc)
            days=max(1,(datetime.fromtimestamp(service.clock(),tz=timezone.utc)-created).days+1)
            earned=len(con.execute('SELECT 1 FROM achievements WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id)).fetchall())
            return CardData(**kw,metrics=(
                ('АКТИВНОСТЬ',(Metric('Дней в игре',str(days),'◷'),Metric('Работ выполнено',str(p['work_count']),'▣'))),
                ('ИГРОВАЯ СТАТИСТИКА',(Metric('Игр сыграно',str(p['games_played']),'◆'),Metric('Побед',str(p['games_won']),'★'),Metric('Процент побед',f"{round(100*p['games_won']/p['games_played']) if p['games_played'] else 0}%",'◉'),Metric('Репутация',str(p['reputation']),'♥'))),
                ('ПРОГРЕСС',(Metric('Уровень',str(level_from_xp(p['xp'])),'✦'),Metric('Опыт',f"{_fmt(p['xp'])} XP",'✧'),Metric('Достижения',str(earned),'★'),Metric('Профессия',JOBS.get(p['job_key'],JOBS['courier'])[0],'▣'))),
            ))
        if command in ('top','topxp','topwins','toprep'):
            key={'top':'coins','topxp':'xp','topwins':'games_won','toprep':'reputation'}[command]
            rows=con.execute(f'SELECT display_name,{key} FROM players WHERE platform=? ORDER BY {key} DESC,created_at ASC LIMIT 10',(ctx.platform,)).fetchall()
            return CardData(**kw,rows=tuple((r['display_name'],_fmt(r[key])) for r in rows))
        if command=='achievements':
            earned={r[0] for r in con.execute('SELECT achievement_key FROM achievements WHERE platform=? AND user_id=?',(ctx.platform,ctx.user_id))}
            return CardData(**kw,items=tuple((name,desc,key in earned) for key,(name,desc) in ACHIEVEMENTS.items()))
    return CardData(**kw,lines=tuple(raw.splitlines()))


def render_card_pages(data: CardData)->list[bytes]:
    if data.command in ('profile','stats'):
        return _render_metric(data)
    if data.command in ('top','topxp','topwins','toprep'):
        return _render_ranking(data)
    if data.command == 'achievements':
        return _render_achievements(data)
    if data.command in ('help','games'):
        return _render_tiles(data)
    return _render_list(data)
