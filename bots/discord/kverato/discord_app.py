"""Native Discord slash commands and interactive menus for КВЕРАТО.

Only the Discord adapter knows about discord.py. All economic actions run
through the already transactional game service and the 'discord' namespace.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from contextlib import closing, suppress
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

from .discord_maintenance import maintenance, MaintenanceHealth
from .discord_health import inspect_release
from .discord_duel_stats import summarize_duels, format_duel_summary
from .discord_comparison import compare_players, display_comparison
from .discord_transfers import TransferHistory
from .discord_friends import DiscordFriends
from .discord_social import page_data, leaderboard_text, RESULT_TEXT, safe_name
from .discord_runtime import InteractionStore, RateLimiter, chunks, make_context, normalize
from .image_cards import is_safe_image_request, prepare_card_data, render_card_pages
from .service import level_from_xp
from .version import VERSION

log = logging.getLogger(__name__)

# Commands available directly via Discord's / picker. All other core commands
# work via /command text:"..." without enabling Message Content Intent.
DIRECT = {
    'start': ('start', 'Начать игру и зарегистрироваться'),
    'profile': ('profile', 'Игровой профиль и карточка'),
    'balance': ('balance', 'Монеты и банк'),
    'daily': ('daily', 'Получить ежедневный бонус'),
    'work': ('work', 'Выполнить работу'),
    'level': ('level', 'Уровень персонажа'),
    'stats': ('stats', 'Статистика игрока'),
    'games': ('games', 'Каталог развлечений'),
    'economy': ('economy', 'Экономика и способы заработка'),
    'shop': ('shop', 'Игровой магазин'),
    'inventory': ('inventory', 'Инвентарь'),
    'quests': ('quests', 'Ежедневные задания'),
    'claim': ('claim', 'Забрать награду за задания'),
    'achievements': ('achievements', 'Достижения игрока'),
    'bank': ('bank', 'Банковский счёт'),
    'deposit': ('deposit', 'Положить монеты в банк'),
    'withdraw': ('withdraw', 'Снять монеты из банка'),
    'buy': ('buy', 'Купить предмет в магазине'),
    'top': ('top', 'Рейтинг по монетам'),
    'topxp': ('topxp', 'Рейтинг по опыту'),
    'topwins': ('topwins', 'Рейтинг по победам'),
    'clan': ('clan', 'Кланы и управление кланом'),
    'clantop': ('clantop', 'Рейтинг кланов'),
    'season': ('season', 'Текущий игровой сезон'),
    'city': ('city', 'Игровой город'),
    'businesses': ('businesses', 'Доступные бизнесы'),
    'business': ('business', 'Управление бизнесом'),
    'garage': ('garage', 'Транспорт и гараж'),
    'pets': ('pets', 'Питомцы и бонусы'),
    'race': ('race', 'Гонки'),
    'tournament': ('tournament', 'Турниры'),
    'catalog': ('catalog', 'Каталоги и цены'),
    'coin': ('coin', 'Монетка со ставкой'),
    'dice': ('dice', 'Кубик со ставкой'),
    'slots': ('slots', 'Игровые слоты'),
    'rps': ('rps', 'Камень, ножницы, бумага'),
    'rep': ('rep', 'Репутация игрока'),
    'report': ('report', 'Отправить обращение администрации'),
    'tickets': ('tickets', 'Мои обращения'),
    'wardrobe': ('wardrobe', 'Гардероб игрока'),
    'market': ('market', 'Рынок предметов'),
    'craft': ('craft', 'Создание предметов'),
    'materials': ('materials', 'Материалы и склад'),
    'event': ('event', 'События сезона'),
}

NAV = [
    ('👤 Профиль', 'profile'), ('💰 Баланс', 'balance'),
    ('🎁 Бонус', 'daily'), ('💼 Работа', 'work'),
    ('🎮 Игры', 'games'), ('🏆 Рейтинг', 'top'),
    ('🛒 Магазин', 'shop'), ('👑 Кланы', 'clan'),
    ('📚 Каталог', 'catalog'), ('🌠 Сезон', 'season'),
    ('👥 Друзья', 'friends'), ('🎖 Топ друзей', 'friendtop'),
    ('⚔️ Дуэли', 'duels'), ('🧾 Переводы', 'transfers'), ('⚙️ Настройки', 'settings'),
    ('📖 Справка', 'guide'),
]

HELP = '''👑 КВЕРАТО — игровой Discord-бот

👤 /start · /profile · /balance · /stats · /achievements
💰 /daily · /work · /bank · /deposit · /withdraw
🎮 /games · /coin · /dice · /slots · /rps
🛒 /shop · /buy · /inventory · /market · /craft
👑 /clan · /clantop · /season · /quests · /claim
🌆 /city · /businesses · /garage · /pets
🏆 /top · /topxp · /topwins · /catalog
🤝 /transfer — перевод виртуальных монет другому игроку
🧾 /transfers — личная история входящих и исходящих переводов
⚔️ /duel — вызов; /duels — активная дуэль, /duelhistory — история
   /duelstats — личная статистика побед и поражений
   /accept /decline /cancelduel — управление ставкой
👥 /friends, /friend, /friendaction, /friendtop — друзья и личный рейтинг
🔐 /admin — панель владельца в личных сообщениях
📜 /rules · /privacy — правила и игровые данные
📡 /ping — соединение; /selftest — проверка владельца
📖 /guide — интерактивный справочник команд
⚖️ /compare — сравнить статистику двух игроков
⚙️ /settings — личные настройки карточек и аватаров
⚙️ /cards — быстро включить/выключить карточки
📖 /command — любые игровые команды КВЕРАТО с аргументами

Примеры /command:
«профессия курьер», «клан создать Название», «питомец каталог».
Новые игроки сначала используют /start. Все деньги — игровые.
''' 


RULES = '''📜 **КВЕРАТО — правила игры**

1. Не использовать баги, накрутки, мультиаккаунты и автоматизацию ради преимущества.
2. Не обманывать участников при переводах, сделках и игровых взаимодействиях.
3. Не мешать другим игрокам угрозами, спамом или оскорблениями.
4. Внутриигровые монеты и предметы не являются реальными деньгами.
5. При нарушениях администрация может ограничить доступ к игровым функциям.

Об ошибках можно сообщить через /report; обращения доступны в /tickets.
'''

PRIVACY = '''🔒 **КВЕРАТО Discord — игровые данные**

Бот сохраняет Discord ID, игровое имя, игровой прогресс, монеты, покупки,
достижения, результаты игр, заявки друзей и журнал игровых операций в SQLite.
Аватар может временно загружаться для PNG-карточки (настройка /settings).

VK и Telegram используют отдельные базы. Бот не запрашивает пароль Discord.
Чтобы обсудить свои данные или запросить удаление игрового профиля,
используй /report в личном чате с ботом.
'''


GUIDE_SECTIONS = (
    ('start', '🏠 Начало', '👑 **Начало игры**\n/start — регистрация\n/menu — главное меню\n/profile — профиль\n/player — публичная статистика участника\n/compare — сравнение двоих\n/help — общий список команд'),
    ('money', '💰 Экономика', '💰 **Заработок и банк**\n/balance · /daily · /work\n/bank · /deposit · /withdraw\n/transfer — перевод игроку\n/transfers — история переводов\n/economy — все способы заработка'),
    ('games', '🎮 Игры', '🎮 **Развлечения**\n/games · /coin · /dice · /slots · /rps\n/duel · /duels · /duelstats · /duelhistory\n/accept · /decline · /cancelduel'),
    ('city', '🌆 Город', '🌆 **Покупки и развитие**\n/city · /businesses · /business\n/shop · /buy · /inventory · /market\n/garage · /pets · /craft · /materials'),
    ('social', '👥 Общение', '👥 **Друзья и рейтинги**\n/friend · /friends · /friendaction\n/friendtop — рейтинг друзей\n/top · /topxp · /topwins\n/clan · /clantop'),
    ('progress', '⭐ Прогресс', '⭐ **Достижения**\n/level · /stats · /quests · /claim\n/achievements · /season · /event'),
    ('other', '⚙️ Помощь', '⚙️ **Настройки и помощь**\n/settings · /cards\n/rules · /privacy · /report · /tickets\n/id · /ping · /version\n/command — прочие команды с аргументами'),
)


class KveratoCommandTree(app_commands.CommandTree):
    """Discord.py-supported override; no instance monkeypatching of the tree."""
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await self.client._command_error(interaction, error)


class SafeView(discord.ui.View):
    """Catch failed component callbacks without replaying game operations.

    A failed network send can have an unknown delivery outcome; only try a
    fresh error message when Discord has not acknowledged this interaction.
    """
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.warning('Discord component error type=%s view=%s',
                    type(error).__name__, type(self).__name__)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    '⚠️ Не удалось обработать нажатие. Проверь состояние в меню, прежде чем повторять действие.',
                    ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            # Never retry a callback or reveal provider errors/tokens to players.
            log.warning('Discord component error feedback unavailable view=%s', type(self).__name__)


class GuideView(SafeView):
    """Read-only, owner-locked help menu with no database or paid actions."""
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.current = 'start'
        for index, (key, title, content) in enumerate(GUIDE_SECTIONS):
            button = discord.ui.Button(label=title, style=discord.ButtonStyle.primary if index == 0 else discord.ButtonStyle.secondary,
                                       row=index // 5)
            async def select(interaction: discord.Interaction, chosen=key):
                await interaction.response.defer()
                self.current = chosen
                for i, item in enumerate(self.children):
                    item.style = (discord.ButtonStyle.primary if GUIDE_SECTIONS[i][0] == chosen
                                  else discord.ButtonStyle.secondary)
                await interaction.edit_original_response(content=self.content(), view=self,
                                                         allowed_mentions=discord.AllowedMentions.none())
            button.callback = select
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message('🔒 Это меню открыто другим игроком.', ephemeral=True)
        return False

    def content(self) -> str:
        return next(section for key, _, section in GUIDE_SECTIONS if key == self.current)


class NavigationView(SafeView):
    """Persistent reusable view. Clicking never repeats paid operations."""
    def __init__(self, bot: 'KveratoDiscord'):
        super().__init__(timeout=None)
        for index, (label, core) in enumerate(NAV):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary,
                                       custom_id=f'kvdiscord:nav:{core}', row=index // 5)
            async def handler(interaction: discord.Interaction, selected: str = core):
                if selected == 'duels':
                    await bot.show_duels(interaction)
                elif selected == 'friends':
                    await bot.show_friends(interaction)
                elif selected == 'friendtop':
                    await bot.show_friendtop(interaction)
                elif selected == 'settings':
                    await bot.show_settings(interaction)
                elif selected == 'transfers':
                    await bot.show_transfers(interaction)
                elif selected == 'guide':
                    await bot.show_guide(interaction)
                else:
                    await bot.execute(interaction, '/' + selected, override_ephemeral=True)
            button.callback = handler
            self.add_item(button)


# Only the exact text of a bot-authored duel invitation supplies the duel ID.
# No user-supplied custom IDs or client-provided numbers are trusted.
DUEL_HEADER = re.compile(r'\A⚔️ Дуэль #([1-9][0-9]{0,13}):')


def duel_message_id(message, bot_user_id: int | None) -> int | None:
    if bot_user_id is None or message is None:
        return None
    if getattr(getattr(message, 'author', None), 'id', None) != bot_user_id:
        return None
    text = getattr(message, 'content', '')
    if not isinstance(text, str):
        return None
    matched = DUEL_HEADER.match(text)
    return int(matched.group(1)) if matched else None


class DuelView(SafeView):
    """Single persistent callback set for every duel and every bot restart.

    The duel ID is read from the bot's original challenge message, then
    revalidated by the transactional game core using channel and participant.
    """
    def __init__(self, bot: 'KveratoDiscord'):
        super().__init__(timeout=None)
        self.bot = bot
        for action, label, style in (
            ('accept', 'Принять', discord.ButtonStyle.success),
            ('decline', 'Отказаться', discord.ButtonStyle.danger),
            ('cancel', 'Отменить вызов', discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(
                label=label, style=style, custom_id=f'kvdiscord:duel:v3:{action}')
            async def press(interaction: discord.Interaction, selected=action):
                await self.bot.duel_button(interaction, selected)
            button.callback = press
            self.add_item(button)


class FriendsPanel(SafeView):
    """Private paginated social panel. Every mutation revalidates owner in SQLite."""
    def __init__(self, bot: 'KveratoDiscord', owner: int, tab: str = 'friends', page: int = 0):
        super().__init__(timeout=600)
        self.bot, self.owner, self.tab, self.page = bot, owner, tab, page
        self.message = ''

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message('🔒 Этот список принадлежит другому игроку.', ephemeral=True)
        return False

    async def refresh(self):
        overview = await asyncio.to_thread(self.bot.friends.overview, self.owner)
        page = page_data(overview, self.tab, self.page)
        self.tab, self.page, self.message = page['tab'], page['page'], page['text']
        self.clear_items()
        for key, label in (('friends', '👥 Друзья'), ('incoming', '📩 Входящие'), ('outgoing', '📤 Исходящие')):
            button = discord.ui.Button(label=label, row=0,
                    style=discord.ButtonStyle.primary if self.tab == key else discord.ButtonStyle.secondary)
            async def select(interaction: discord.Interaction, selected=key):
                await interaction.response.defer()
                self.tab, self.page = selected, 0
                await self.refresh()
                await interaction.edit_original_response(content=self.message, view=self)
            button.callback = select
            self.add_item(button)
        for text, delta in (('◀ Назад', -1), ('Вперёд ▶', 1)):
            button = discord.ui.Button(label=text, row=1, style=discord.ButtonStyle.secondary,
                                      disabled=(self.page + delta < 0 or self.page + delta >= page['pages']))
            async def navigate(interaction: discord.Interaction, step=delta):
                await interaction.response.defer()
                self.page += step
                await self.refresh()
                await interaction.edit_original_response(content=self.message, view=self)
            button.callback = navigate
            self.add_item(button)
        for offset, (uid, _name) in enumerate(page['items']):
            if self.tab == 'incoming':
                actions = (('Принять', 'accept', discord.ButtonStyle.success),
                           ('Отклонить', 'decline', discord.ButtonStyle.danger))
            elif self.tab == 'outgoing':
                actions = (('Отменить', 'cancel', discord.ButtonStyle.danger),)
            else:
                actions = (('Удалить', 'remove', discord.ButtonStyle.danger),)
            for label, action, style in actions:
                button = discord.ui.Button(label=f'{label} #{self.page * 3 + offset + 1}',
                                           row=2 + offset, style=style)
                async def change(interaction: discord.Interaction, other=uid, operation=action):
                    await interaction.response.defer()
                    if not self.bot.limiter.allow(interaction.user.id):
                        await interaction.followup.send('⏳ Слишком частые действия.', ephemeral=True)
                        return
                    if not await asyncio.to_thread(self.bot.store.reserve, interaction.id):
                        await interaction.followup.send('Действие уже обработано.', ephemeral=True)
                        return
                    result = await asyncio.to_thread(self.bot.friends.transition, self.owner, other, operation)
                    await self.refresh()
                    await interaction.edit_original_response(
                        content=RESULT_TEXT.get(result.code, 'Операция завершена.') + '\n\n' + self.message,
                        view=self)
                button.callback = change
                self.add_item(button)


class FriendTopPanel(SafeView):
    """Owner-only, read-only comparison of confirmed friends."""
    def __init__(self, bot: 'KveratoDiscord', owner: int, metric: str = 'xp'):
        super().__init__(timeout=600)
        self.bot, self.owner, self.metric = bot, owner, metric
        for choice, label in (('xp', '⭐ Опыт'), ('wins', '🏆 Победы')):
            button = discord.ui.Button(label=label, row=0, style=discord.ButtonStyle.primary if choice == metric else discord.ButtonStyle.secondary)
            async def pick(interaction: discord.Interaction, selected=choice):
                await interaction.response.defer()
                self.metric = selected
                for item in self.children:
                    if isinstance(item, discord.ui.Button):
                        item.style = discord.ButtonStyle.primary if ('Опыт' in item.label) == (selected == 'xp') else discord.ButtonStyle.secondary
                await interaction.edit_original_response(content=await self.content(), view=self)
            button.callback = pick
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message('🔒 Этот рейтинг принадлежит другому игроку.', ephemeral=True)
        return False

    async def content(self) -> str:
        records = await asyncio.to_thread(self.bot.friends.leaderboard, self.owner, metric=self.metric)
        return leaderboard_text(records, self.owner, self.metric)


class SettingsPanel(SafeView):
    """Owner-locked short-lived settings UI; stores only visual preferences."""
    def __init__(self, bot: 'KveratoDiscord', owner: int):
        super().__init__(timeout=600)
        self.bot, self.owner = bot, owner
        self.values = {'cards': True, 'avatars': True}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message('🔒 Эти настройки принадлежат другому игроку.', ephemeral=True)
        return False

    async def refresh(self):
        self.values = await asyncio.to_thread(self.bot.store.preferences, self.owner)
        self.clear_items()
        for key, label in (('cards', '🖼 Карточки'), ('avatars', '👤 Аватар')):
            enabled = self.values[key]
            button = discord.ui.Button(
                label=f'{label}: {"ВКЛ" if enabled else "ВЫКЛ"}',
                style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
                row=0)
            async def toggle(interaction: discord.Interaction, selected=key):
                # Confirm within Discord's interaction deadline, before SQLite I/O.
                await interaction.response.defer()
                if not self.bot.limiter.allow(interaction.user.id):
                    await interaction.followup.send('⏳ Слишком частые действия.', ephemeral=True)
                    return
                if not await asyncio.to_thread(self.bot.store.reserve, interaction.id):
                    await interaction.followup.send('Действие уже обработано.', ephemeral=True)
                    return
                current = await asyncio.to_thread(self.bot.store.preferences, self.owner)
                await asyncio.to_thread(self.bot.store.set_preference, self.owner,
                                        selected, not current[selected])
                await self.refresh()
                await interaction.edit_original_response(content=self.content(), view=self)
            button.callback = toggle
            self.add_item(button)

    def content(self) -> str:
        c = 'включены' if self.values['cards'] else 'отключены'
        a = 'включены' if self.values['avatars'] else 'отключены'
        return (f'⚙️ **КВЕРАТО — настройки**\n'
                f'🖼 Графические карточки: **{c}**\n'
                f'👤 Аватары в карточках: **{a}**\n\n'
                'Изменения применяются к твоим следующим ответам и сохраняются после перезапуска. '
                'Игровые монеты и предметы не меняются.')


class TransfersPanel(SafeView):
    """Private, read-only ledger navigation; never accepts a target user ID."""
    def __init__(self, bot: 'KveratoDiscord', owner: int):
        super().__init__(timeout=600)
        self.bot, self.owner = bot, owner
        self.tab, self.page, self.message = 'all', 0, ''

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message('🔒 Историю переводов видит только её владелец.', ephemeral=True)
        return False

    async def refresh(self):
        data = await asyncio.to_thread(self.bot.transfers.page, self.owner, self.tab, self.page)
        self.tab, self.page, self.message = data['tab'], data['page'], data['text']
        self.clear_items()
        for key, label in (('all', '🧾 Все'), ('incoming', '📥 Входящие'), ('outgoing', '📤 Исходящие')):
            button = discord.ui.Button(label=label, row=0,
                      style=discord.ButtonStyle.primary if key == self.tab else discord.ButtonStyle.secondary)
            async def select(interaction: discord.Interaction, selected=key):
                await interaction.response.defer()
                if not self.bot.limiter.allow(interaction.user.id):
                    await interaction.followup.send('⏳ Слишком частые запросы.', ephemeral=True)
                    return
                self.tab, self.page = selected, 0
                await self.refresh()
                await interaction.edit_original_response(content=self.message, view=self,
                                                         allowed_mentions=discord.AllowedMentions.none())
            button.callback = select
            self.add_item(button)
        for label, step in (('◀ Назад', -1), ('Далее ▶', 1)):
            button = discord.ui.Button(label=label, row=1, style=discord.ButtonStyle.secondary,
                    disabled=(self.page + step < 0 or self.page + step >= data['pages']))
            async def move(interaction: discord.Interaction, delta=step):
                await interaction.response.defer()
                if not self.bot.limiter.allow(interaction.user.id):
                    await interaction.followup.send('⏳ Слишком частые запросы.', ephemeral=True)
                    return
                self.page += delta
                await self.refresh()
                await interaction.edit_original_response(content=self.message, view=self,
                                                         allowed_mentions=discord.AllowedMentions.none())
            button.callback = move
            self.add_item(button)


class KveratoDiscord(discord.Client):
    def __init__(self, service, guild_id: int | None = None, image_cards: bool = True):
        intents = discord.Intents.default()
        intents.message_content = False  # No privileged Message Content Intent required.
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = KveratoCommandTree(self)
        self.started_at = time.monotonic()
        self.service = service
        self.store = InteractionStore(service.db)
        self.friends = DiscordFriends(service.db)
        self.transfers = TransferHistory(service.db)
        self.limiter = RateLimiter()
        self.guild_id = guild_id
        self.image_cards = image_cards
        self.render_slots = asyncio.Semaphore(3)
        self.execute_slots = asyncio.Semaphore(8)
        self._maintenance_task = None
        self.maintenance_health = MaintenanceHealth()
        self._register_commands()

    async def _command_error(self, interaction: discord.Interaction, error: Exception):
        """Fail closed for unexpected slash errors without exposing sensitive details."""
        log.warning('Discord slash error type=%s', type(error).__name__)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    '⚠️ Запрос не выполнен. Если это была денежная команда, сначала проверь баланс.',
                    ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            # The server might already have accepted a reply; do not send again.
            log.warning('Discord slash error feedback unavailable')

    def ping_text(self) -> str:
        """Report actual gateway latency and process uptime without DB access."""
        elapsed = max(0, int(time.monotonic() - self.started_at))
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        latency = getattr(self, 'latency', None)
        ping = (f'{round(latency * 1000)} мс' if isinstance(latency, (int, float))
                and 0 <= latency < 1000 else 'нет данных')
        return (f'🏓 КВЕРАТО Discord v{VERSION} · онлайн\n'
                f'📶 Задержка Discord: {ping}\n'
                f'⏱ Время работы: {hours} ч {minutes} мин {seconds} сек')

    async def setup_hook(self):
        self.add_view(NavigationView(self))
        # Reconnect all previously sent duel buttons after restart.
        self.add_view(DuelView(self))
        if self.guild_id:
            # Guild-only sync hides commands in DMs, including the owner's /admin.
            # Keep a test-guild copy for quick rollout *and* global registration
            # for DM interactions. Discord may delay worldwide command propagation.
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            guild_commands = await self.tree.sync(guild=guild)
            log.info('Discord guild commands registered count=%s', len(guild_commands))
        commands = await self.tree.sync()
        log.info('Discord global/DM commands registered count=%s', len(commands))
        self._maintenance_task = asyncio.create_task(
            maintenance(self.service, self.store, self.friends, health=self.maintenance_health), name='discord-maintenance')

    async def close(self):
        if self._maintenance_task:
            self._maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance_task
        await super().close()

    async def on_ready(self):
        log.info('КВЕРАТО Discord v%s connected', VERSION)
        try:
            # Bot activity, not per-user Rich Presence. No privileged intents.
            await self.change_presence(status=discord.Status.online,
                                       activity=discord.Game(name='КВЕРАТО | /start'))
        except (discord.HTTPException, OSError) as exc:
            log.warning('Discord presence unavailable type=%s', type(exc).__name__)

    def _register_commands(self):
        tree = self.tree

        @tree.command(name='menu', description='Открыть кнопочное меню КВЕРАТО')
        async def menu(interaction: discord.Interaction):
            await interaction.response.send_message('👑 **КВЕРАТО — главное меню**\nВыбери раздел:',
                                                    view=NavigationView(self),
                                                    ephemeral=interaction.guild is not None)

        @tree.command(name='help', description='Все основные команды бота')
        async def help_command(interaction: discord.Interaction):
            await interaction.response.send_message(HELP, ephemeral=True)

        @tree.command(name='version', description='Версия Discord-бота')
        async def version(interaction: discord.Interaction):
            await interaction.response.send_message(f'👑 КВЕРАТО Discord v{VERSION}', ephemeral=True)

        @tree.command(name='rules', description='Правила игры КВЕРАТО')
        async def rules(interaction: discord.Interaction):
            await interaction.response.send_message(RULES, ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())

        @tree.command(name='privacy', description='Какие игровые данные хранит КВЕРАТО')
        async def privacy(interaction: discord.Interaction):
            await interaction.response.send_message(PRIVACY, ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())

        @tree.command(name='ping', description='Проверка работы Discord-бота')
        async def ping(interaction: discord.Interaction):
            # No SQLite query or paid game operation; respond immediately.
            await interaction.response.send_message(self.ping_text(), ephemeral=True)

        @tree.command(name='guide', description='Интерактивный справочник по КВЕРАТО')
        async def guide(interaction: discord.Interaction):
            await self.show_guide(interaction)

        @tree.command(name='compare', description='Сравнить открытые показатели двух игроков')
        @app_commands.describe(first='Первый игрок', second='Второй игрок')
        async def compare(interaction: discord.Interaction, first: discord.User, second: discord.User):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if first.id == second.id:
                await interaction.followup.send('⚖️ Для сравнения выбери двух разных игроков.', ephemeral=True)
                return
            if first.bot or second.bot:
                await interaction.followup.send('🤖 Ботов нельзя сравнивать с игроками.', ephemeral=True)
                return
            if not self.limiter.allow(interaction.user.id):
                await interaction.followup.send('⏳ Слишком частые запросы.', ephemeral=True)
                return
            left, right = await asyncio.to_thread(compare_players, self.service.db, first.id, second.id)
            if left is None or right is None:
                await interaction.followup.send('ℹ️ Оба игрока должны зарегистрироваться через /start.', ephemeral=True)
                return
            result = display_comparison(left, right, discord.utils.escape_markdown)
            await interaction.followup.send(result, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())

        @tree.command(name='id', description='Узнать свой Discord User ID')
        async def my_id(interaction: discord.Interaction):
            await interaction.response.send_message(f'🆔 Твой Discord ID: {interaction.user.id}', ephemeral=True)

        @tree.command(name='transfers', description='Личная история входящих и исходящих переводов')
        async def transfers(interaction: discord.Interaction):
            await self.show_transfers(interaction)

        @tree.command(name='friends', description='Личный список друзей и заявок с кнопками')
        async def friends(interaction: discord.Interaction):
            await self.show_friends(interaction)

        @tree.command(name='friend', description='Пригласить зарегистрированного игрока в друзья')
        @app_commands.describe(user='Другой игрок, использовавший /start')
        async def friend(interaction: discord.Interaction, user: discord.User):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if user.bot:
                await interaction.followup.send('Нельзя пригласить бота.', ephemeral=True)
                return
            if not self.limiter.allow(interaction.user.id):
                await interaction.followup.send('⏳ Слишком частые действия.', ephemeral=True)
                return
            if not await asyncio.to_thread(self.store.reserve, interaction.id):
                await interaction.followup.send('Действие уже обработано.', ephemeral=True)
                return
            result = await asyncio.to_thread(self.friends.request, interaction.user.id, user.id)
            await interaction.followup.send(RESULT_TEXT.get(result.code, 'Операция завершена.'), ephemeral=True)

        @tree.command(name='friendaction', description='Принять, отклонить, удалить или заблокировать игрока')
        @app_commands.describe(action='Действие', user='Участник')
        async def friendaction(interaction: discord.Interaction,
                               action: Literal['accept', 'decline', 'cancel', 'remove', 'block', 'unblock'],
                               user: discord.User):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if not self.limiter.allow(interaction.user.id):
                await interaction.followup.send('⏳ Слишком частые действия.', ephemeral=True)
                return
            if not await asyncio.to_thread(self.store.reserve, interaction.id):
                await interaction.followup.send('Действие уже обработано.', ephemeral=True)
                return
            if action in ('block', 'unblock'):
                result = await asyncio.to_thread(self.friends.block, interaction.user.id,
                                                  user.id, enabled=action == 'block')
            else:
                result = await asyncio.to_thread(self.friends.transition, interaction.user.id,
                                                 user.id, action)
            await interaction.followup.send(RESULT_TEXT.get(result.code, 'Операция завершена.'), ephemeral=True)

        @tree.command(name='duels', description='Показать свою активную дуэль на сервере')
        async def duels(interaction: discord.Interaction):
            await self.show_duels(interaction)

        @tree.command(name='duelstats', description='Личная статистика Discord-дуэлей')
        async def duelstats(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if not self.limiter.allow(interaction.user.id):
                await interaction.followup.send('⏳ Слишком много запросов.', ephemeral=True)
                return
            result = await asyncio.to_thread(summarize_duels, self.service.db, interaction.user.id)
            await interaction.followup.send(format_duel_summary(result), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())

        @tree.command(name='duelhistory', description='История последних дуэлей в этом канале')
        async def duelhistory(interaction: discord.Interaction):
            await self.execute(interaction, '/историядуэлей', override_ephemeral=True)

        @tree.command(name='friendtop', description='Личный рейтинг опыта или побед среди друзей')
        @app_commands.describe(metric='Что сравнивать')
        async def friendtop(interaction: discord.Interaction, metric: Literal['xp', 'wins'] = 'xp'):
            await self.show_friendtop(interaction, metric)

        @tree.command(name='settings', description='Личные настройки карточек и аватаров')
        async def settings(interaction: discord.Interaction):
            await self.show_settings(interaction)

        @tree.command(name='cards', description='Включить или выключить графические карточки')
        @app_commands.describe(enabled='Показывать картинки вместо текста')
        async def cards(interaction: discord.Interaction, enabled: bool):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if not await asyncio.to_thread(self.store.reserve, interaction.id):
                await interaction.followup.send('Уже обработано.', ephemeral=True)
                return
            await asyncio.to_thread(self.store.set_cards, interaction.user.id, enabled)
            await interaction.followup.send('🖼 Карточки ' + ('включены.' if enabled else 'отключены.'), ephemeral=True)

        @tree.command(name='command', description='Любая команда игрового ядра КВЕРАТО')
        @app_commands.describe(text='Например: профессия курьер или питомец каталог')
        async def command(interaction: discord.Interaction, text: str):
            await self.execute(interaction, text)

        @tree.command(name='transfer', description='Перевод виртуальных монет зарегистрированному игроку')
        async def transfer(interaction: discord.Interaction, recipient: discord.User, amount: int):
            if recipient.bot or not 1 <= amount <= 100_000:
                await interaction.response.send_message('Нужен игрок и сумма от 1 до 100 000.', ephemeral=True)
                return
            await self.execute(interaction, f'/перевести {recipient.id} {amount}', target_id=recipient.id,
                               override_ephemeral=True)

        @tree.command(name='duel', description='Вызвать игрока на дуэль со ставкой до 5000')
        async def duel(interaction: discord.Interaction, opponent: discord.User, amount: int):
            if not interaction.guild:
                await interaction.response.send_message('⚔️ Дуэли доступны в каналах сервера.', ephemeral=True)
                return
            if opponent.bot or not 1 <= amount <= 5000:
                await interaction.response.send_message('Укажи игрока и ставку от 1 до 5000.', ephemeral=True)
                return
            await self.execute(interaction, f'/дуэль {opponent.id} {amount}', target_id=opponent.id,
                               override_ephemeral=False)

        for name, core, description in (
            ('accept', 'принять', 'Принять вызов на дуэль'),
            ('decline', 'отклонить', 'Отклонить вызов на дуэль'),
            ('cancelduel', 'отменитьдуэль', 'Отменить свой вызов на дуэль'),
        ):
            def register_duel_action(n=name, c=core, d=description):
                @tree.command(name=n, description=d)
                async def duel_action(interaction: discord.Interaction, number: int):
                    await self.execute(interaction, f'/{c} {number}', override_ephemeral=False)
            register_duel_action()

        @tree.command(name='admin', description='Внутренняя панель управления ботом (личные сообщения)')
        @app_commands.describe(action='Например: статус ID, журнал, блок ID 24 причина')
        async def admin(interaction: discord.Interaction, action: str = ''):
            if interaction.guild:
                await interaction.response.send_message('🔐 Панель доступна только в личном чате с ботом.', ephemeral=True)
                return
            await self.execute(interaction, '/админ ' + action, override_ephemeral=True)

        @tree.command(name='player', description='Публичная статистика игрока, без банковских данных')
        async def player(interaction: discord.Interaction, user: discord.User):
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                def query():
                    with closing(self.service.db.connect()) as con:
                        return con.execute('SELECT display_name,xp,games_played,games_won,reputation FROM players WHERE platform=? AND user_id=?',
                                           ('discord', str(user.id))).fetchone()
                record = await asyncio.to_thread(query)
                if record:
                    msg = (f'👤 {discord.utils.escape_markdown(record["display_name"])}\n'
                           f'⭐ Уровень: {level_from_xp(record["xp"])} | XP: {record["xp"]}\n'
                           f'🎮 Игр: {record["games_played"]} | побед: {record["games_won"]}\n'
                           f'🤝 Репутация: {record["reputation"]}')
                else:
                    msg = 'Игрок пока не зарегистрирован. Используй /start.'
                await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            except Exception as exc:
                log.warning('Discord public player failed type=%s', type(exc).__name__)
                await interaction.followup.send('Ошибка получения профиля.', ephemeral=True)

        @tree.command(name='selftest', description='Безопасная проверка базы и графики для владельца')
        async def selftest(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild is not None or str(interaction.user.id) not in self.service.owner_ids:
                await interaction.followup.send('🔒 Только владельцу в личных сообщениях.', ephemeral=True)
                return
            # No in-game service.handle(), awards, purchases or other mutations.
            report = await asyncio.to_thread(inspect_release, self.service.db.path,
                                              Path(__file__).resolve().parent.parent / 'assets')
            text = (report.summary() + '\n'
                    f'📋 Slash-команд: {len(self.tree.get_commands())}\n'
                    f'⚙️ Фоновое обслуживание: {self.maintenance_health.describe()}\n'
                    'ℹ️ Доставка изображений и доступность Discord API проверяются отдельно.')
            await interaction.followup.send(text, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())

        @tree.command(name='status', description='Приватная диагностика владельца')
        async def status(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild or str(interaction.user.id) not in self.service.owner_ids:
                await interaction.followup.send('🔒 Доступно только владельцу в личных сообщениях.', ephemeral=True)
                return
            def query():
                with closing(self.service.db.connect()) as con:
                    counts = con.execute('SELECT COUNT(*) FROM players WHERE platform=?', ('discord',)).fetchone()[0]
                    duels = con.execute("SELECT COUNT(*) FROM duels WHERE platform=? AND status='pending'", ('discord',)).fetchone()[0]
                    state = con.execute('PRAGMA quick_check').fetchone()[0]
                    requests = con.execute("SELECT COUNT(*) FROM discord_friend_links WHERE status='pending' AND expires_at>?", (int(time.time()),)).fetchone()[0]
                    return counts, duels, requests, state
            players, duels, requests, state = await asyncio.to_thread(query)
            await interaction.followup.send(
                f'👑 КВЕРАТО Discord {VERSION}\nИгроков: {players}\nАктивных дуэлей: {duels}\nАктивных заявок: {requests}\nSQLite: {state}\nОбслуживание: {self.maintenance_health.describe()}',
                ephemeral=True)

        for name, (core, description) in DIRECT.items():
            def register(n=name, c=core, d=description):
                @tree.command(name=n, description=d)
                @app_commands.describe(args='Необязательные параметры, например 50 или каталог')
                async def core_command(interaction: discord.Interaction, args: str = ''):
                    await self.execute(interaction, '/' + c + (' ' + args if args.strip() else ''))
            register()

    async def show_guide(self, interaction: discord.Interaction):
        panel = GuideView(interaction.user.id)
        await interaction.response.send_message(panel.content(), view=panel, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    async def show_duels(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                '⚔️ Дуэли доступны в каналах Discord-сервера.', ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        def query():
            with closing(self.service.db.connect()) as con:
                return con.execute('''SELECT id,challenger_id,opponent_id,stake,expires_at
                    FROM duels WHERE platform='discord' AND chat_id=? AND status='pending'
                    AND (challenger_id=? OR opponent_id=?) ORDER BY id DESC LIMIT 1''',
                    (str(interaction.channel_id), str(interaction.user.id),
                     str(interaction.user.id))).fetchone()
        row = await asyncio.to_thread(query)
        if not row:
            await interaction.followup.send('⚔️ В этом канале у тебя нет активной дуэли.', ephemeral=True)
            return
        left = max(0, int(row['expires_at']) - int(self.service.clock()))
        if not left:
            await interaction.followup.send(
                '⌛ Вызов просрочен. Возврат ставки выполнится обслуживанием бота.', ephemeral=True)
            return
        # This message starts with the same stable, verifiable prefix as new challenges.
        msg = (f'⚔️ Дуэль #{row["id"]}: {row["challenger_id"]} → {row["opponent_id"]}\n'
               f'💰 Ставка: {row["stake"]} монет с каждого. Осталось: {left} сек.\n'
               'Принять/отклонить может только соперник; отменить — создатель.')
        await interaction.followup.send(msg, ephemeral=True, view=DuelView(self),
                                        allowed_mentions=discord.AllowedMentions.none())

    async def duel_button(self, interaction: discord.Interaction, action: str):
        if action not in ('accept', 'decline', 'cancel'):
            await interaction.response.send_message('Неизвестное действие.', ephemeral=True)
            return
        identity = getattr(getattr(self, 'user', None), 'id', None)
        number = duel_message_id(getattr(interaction, 'message', None), identity)
        if interaction.guild is None or not number:
            await interaction.response.send_message('⚠️ Недействительная кнопка дуэли.', ephemeral=True)
            return
        # The core rechecks channel, actor, stake, expiry, and status under BEGIN IMMEDIATE.
        core = {'accept': 'принять', 'decline': 'отклонить',
                'cancel': 'отменитьдуэль'}[action]
        await self.execute(interaction, f'/{core} {number}', override_ephemeral=False)
        # Remove stale buttons only if the DB confirms a finalized duel.
        def status():
            with closing(self.service.db.connect()) as con:
                row = con.execute('''SELECT status FROM duels WHERE id=? AND platform='discord' AND chat_id=?''',
                                  (number, str(interaction.channel_id))).fetchone()
                return row['status'] if row else None
        try:
            state = await asyncio.to_thread(status)
            if state and state != 'pending':
                await interaction.message.edit(view=None)
        except Exception as exc:
            log.warning('Discord duel message cleanup failed type=%s', type(exc).__name__)

    async def show_transfers(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not self.limiter.allow(interaction.user.id):
            await interaction.followup.send('⏳ Слишком частые запросы.', ephemeral=True)
            return
        panel = TransfersPanel(self, interaction.user.id)
        await panel.refresh()
        await interaction.followup.send(panel.message, ephemeral=True, view=panel,
                                        allowed_mentions=discord.AllowedMentions.none())

    async def show_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        panel = SettingsPanel(self, interaction.user.id)
        await panel.refresh()
        await interaction.followup.send(panel.content(), ephemeral=True, view=panel,
                                        allowed_mentions=discord.AllowedMentions.none())

    async def show_friends(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = FriendsPanel(self, interaction.user.id)
        await view.refresh()
        await interaction.followup.send(view.message, ephemeral=True, view=view,
                                        allowed_mentions=discord.AllowedMentions.none())

    async def show_friendtop(self, interaction: discord.Interaction, metric: str = 'xp'):
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = FriendTopPanel(self, interaction.user.id, metric)
        await interaction.followup.send(await view.content(), ephemeral=True, view=view,
                                        allowed_mentions=discord.AllowedMentions.none())

    async def execute(self, interaction: discord.Interaction, raw: str,
                      target_id: int | None = None, override_ephemeral: bool | None = None):
        parsed = normalize(raw)
        if not parsed:
            if not interaction.response.is_done():
                await interaction.response.send_message('Неизвестная команда. Используй /help.', ephemeral=True)
            return
        command, content = parsed
        public = {'duel','acceptduel','declineduel','cancelduel','top','topxp','topwins','toprep',
                  'clantop','games','economy','catalog','seasontop'}
        ephemeral = override_ephemeral if override_ephemeral is not None else (
            interaction.guild is not None and command not in public)
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        if not self.limiter.allow(interaction.user.id):
            await interaction.followup.send('⏳ Слишком много команд. Повтори позже.', ephemeral=True)
            return
        if not await asyncio.to_thread(self.store.reserve, interaction.id):
            await interaction.followup.send('⏳ Эта команда уже обрабатывалась.', ephemeral=True)
            return
        ctx = make_context(interaction.user.id, interaction.channel_id,
                           interaction.user.display_name, interaction.guild is not None,
                           target_id)
        delivery_started = False
        try:
            async with self.execute_slots:
                answer = await asyncio.to_thread(self.service.handle, ctx, content)
            if not answer:
                delivery_started = True
                await interaction.followup.send('Команда не дала ответа. Используй /help.', ephemeral=True)
                return
            # Only render allowlisted, read-only cards. Rendering is a local
            # operation, but upload is a network operation with uncertain delivery.
            # Keeping these stages separate prevents duplicate text responses if
            # the Discord server accepted a photo but the connection was lost.
            files = []
            if self.image_cards and is_safe_image_request(command, content) and (
                await asyncio.to_thread(self.store.cards_enabled, interaction.user.id)):
                try:
                    async with self.render_slots:
                        avatar = None
                        if await asyncio.to_thread(self.store.avatars_enabled, interaction.user.id):
                            try:
                                avatar = await asyncio.wait_for(interaction.user.display_avatar.read(), timeout=4)
                                if len(avatar) > 512_000:
                                    avatar = None
                            except Exception:
                                pass
                        data = await asyncio.to_thread(prepare_card_data,
                                                       self.service, ctx, command, answer, avatar)
                        pages = await asyncio.to_thread(render_card_pages, data) if data else []
                    if 1 <= len(pages) <= 4 and all(len(page) < 7_000_000 for page in pages):
                        files = [discord.File(io.BytesIO(p), filename=f'kverato_{command}_{i}.png')
                                 for i, p in enumerate(pages, 1)]
                except Exception as exc:
                    # No upload was attempted; a normal text response is safe.
                    log.warning('Discord card render failed type=%s command=%s', type(exc).__name__, command)
            if files:
                try:
                    delivery_started = True
                    await interaction.followup.send(files=files, ephemeral=ephemeral)
                    return
                except discord.HTTPException as exc:
                    log.warning('Discord image delivery failed status=%s command=%s', exc.status, command)
                    # Definitive content/permission rejection. These are not
                    # ambiguous network/5xx errors, so text fallback is safe.
                    if exc.status not in (400, 403, 413):
                        return
                except Exception as exc:
                    # Timeout, connection reset or unknown API result. Discord
                    # may already have accepted the image: never send it again.
                    log.warning('Discord image delivery uncertain type=%s command=%s',
                                type(exc).__name__, command)
                    return
                finally:
                    for attachment in files:
                        with suppress(Exception):
                            attachment.close()
            output = chunks(answer)
            duel_match = re.search(r'⚔️ Дуэль #([1-9][0-9]{0,12}):', answer) if command == 'duel' else None
            for index, page in enumerate(output[:10]):
                view = DuelView(self) if duel_match and index == 0 else None
                delivery_started = True
                await interaction.followup.send(page, ephemeral=ephemeral, view=view,
                                                allowed_mentions=discord.AllowedMentions.none())
            if len(output) > 10:
                delivery_started = True
                await interaction.followup.send('Ответ слишком большой. Используй параметры каталога.', ephemeral=True)
        except discord.HTTPException as exc:
            # A failed network request may have succeeded server-side; never
            # repeat a monetary operation just because delivery failed.
            log.warning('Discord response failure status=%s command=%s', exc.status, command)
        except Exception as exc:
            log.exception('Discord command error type=%s command=%s', type(exc).__name__, command)
            if delivery_started:
                # A response may already be present in the channel. A second
                # message would be misleading; monetary operations must not run twice.
                return
            try:
                await interaction.followup.send('⚠ Не удалось завершить запрос. Не повторяй денежную операцию, пока не проверишь баланс.', ephemeral=True)
            except discord.HTTPException:
                pass
