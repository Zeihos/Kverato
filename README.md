<div align="center">
  <img src="branding/cover.jpg" alt="КВЕРАТО — игровая вселенная" width="100%">

  <h1>👑 КВЕРАТО</h1>
  <p><strong>Многопользовательский игровой бот для ВКонтакте, Telegram и Discord</strong></p>
  <p>Экономика · Персонажи · Бизнесы · Кланы · Дуэли · Достижения · Рейтинги</p>
  <p><strong>VK 1.0</strong> · <strong>Telegram 1.0</strong> · <strong>Discord 1.0</strong></p>
</div>

[![Проверка проекта](https://github.com/Zeihos/Kverato/actions/workflows/ci.yml/badge.svg)](https://github.com/Zeihos/Kverato/actions/workflows/ci.yml)

## Что такое КВЕРАТО?

**КВЕРАТО** — игровая вселенная внутри привычных чатов. Игроки развивают персонажа, зарабатывают виртуальные монеты, покупают транспорт и имущество, открывают бизнесы, вступают в кланы и участвуют в соревнованиях.

Три версии используют одинаковые игровые принципы, но работают **независимо**: балансы, аккаунты и прогресс между платформами автоматически не объединяются.

## 📦 Скачать v1.0

| Платформа | Исходники | Архив |
|---|---|---|
| **ВКонтакте** | [bots/vk](bots/vk) | [VK 1.0](releases/v1.0/KVERATO-VK-v1.0-FINAL.zip) |
| **Telegram** | [bots/telegram](bots/telegram) | [Telegram 1.0](releases/v1.0/KVERATO-TG-v1.0-FINAL.zip) |
| **Discord** | [bots/discord](bots/discord) | [Discord 1.0](releases/v1.0/KVERATO-DISCORD-v1.0-FINAL.zip) |
| **Все три платформы** | Отдельные боты | [Полный комплект 1.0](releases/v1.0/KVERATO-v1.0-FULL-RELEASE.zip) |

Архивы 1.0 сохранены без изменений. Разработка продолжается в папке **bots/**: это редактируемые исходники, а не очередные копии ZIP.

## 🚀 Быстрый запуск на Windows

1. Установите **Python 3.12**.
2. Откройте папку нужного бота: **bots/vk**, **bots/telegram** или **bots/discord**.
3. Создайте **.env** из **.env.example**, заполните токен платформы и ID владельца.
4. Запустите установщик и стартовый BAT-файл своей платформы:

| Платформа | Установка | Запуск |
|---|---|---|
| VK | install_vk_windows.bat | start_vk.bat |
| Telegram | install_tg_windows.bat | start_tg.bat |
| Discord | install_discord_windows.bat | start_discord.bat |

Для постоянной работы есть отдельные запускатели **start_*_24x7.bat**. Не запускайте несколько экземпляров одного бота одновременно.

**Подробная установка и обновление:** [docs/INSTALLATION.md](docs/INSTALLATION.md).

## 🗂 Структура проекта

~~~text
Kverato/
├── bots/
│   ├── vk/             # VK API, команды и карточки
│   ├── telegram/       # Telegram Bot API, меню и кнопки
│   └── discord/        # Slash-команды и интерфейс Discord
├── branding/           # Фирменные изображения и материалы
├── docs/               # Установка, устройство проекта
├── releases/v1.0/      # Неизменённые архивы первого релиза
├── tools/               # Локальные проверки без токенов
└── .github/workflows/   # Автоматические проверки
~~~

Игровые модули внутри трёх ботов пока хранятся отдельно. Их объединение требует отдельной проверки миграции и сохранения балансов — в релизе 1.0 такой рискованный рефакторинг не проводился.

## 🧪 Разработка и проверка

~~~powershell
python tools/release_audit.py
python tools/core_smoke.py
python -m compileall -q bots
~~~

GitHub Actions автоматически проверяет целостность релизных архивов, Python-синтаксис, основные операции с временной SQLite-базой и отсутствие приватных файлов среди отслеживаемых Git-файлов.

[Устройство проекта](docs/ARCHITECTURE.md) · [Как предложить изменение](CONTRIBUTING.md) · [Сообщить о проблеме](https://github.com/Zeihos/Kverato/issues)

## 🔐 Безопасность

**Никогда не публикуйте** токены, файлы .env, базы SQLite и резервные копии. При обновлении сначала сохраните конфигурацию и данные игроков. Игровые монеты — виртуальная валюта, не реальные деньги.

[Политика сообщения об уязвимостях](SECURITY.md).

---

<div align="center">
  <sub>© КВЕРАТО · Первый официальный релиз 1.0 · VK / Telegram / Discord</sub>
</div>
