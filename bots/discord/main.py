"""KVERATO Discord development build. Configure .env, install dependencies and run main.py."""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from kverato.db import Database
from kverato.discord_lock import AlreadyRunning, discord_instance_lock
from kverato.operations import daily_prestart_backup, verify_sqlite
from kverato.service import BotService
from kverato.version import VERSION

ROOT = Path(__file__).resolve().parent


def main() -> int:
    load_dotenv(ROOT / '.env')
    token = os.getenv('DISCORD_TOKEN', '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{40,200}', token):
        print('[ERROR] Создай Discord-бота в Developer Portal и укажи DISCORD_TOKEN в .env.', file=sys.stderr)
        return 2
    owner_ids = {id_.strip() for id_ in os.getenv('DISCORD_OWNER_IDS', '').split(',')
                 if re.fullmatch(r'[1-9][0-9]{0,18}', id_.strip())}
    guild_raw = os.getenv('DISCORD_GUILD_ID', '').strip()
    if guild_raw and not re.fullmatch(r'[1-9][0-9]{0,18}', guild_raw):
        print('[ERROR] DISCORD_GUILD_ID должен содержать только цифры.', file=sys.stderr)
        return 2
    db_path = Path(os.getenv('KVERATO_DB_PATH', 'data/kverato_discord.sqlite3'))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    try:
        from kverato.discord_app import KveratoDiscord
        with discord_instance_lock(db_path):
            if os.getenv('KVERATO_AUTO_BACKUP', '1') != '0':
                path = daily_prestart_backup(db_path, ROOT / 'backups')
                if path:
                    print(f'[КВЕРАТО Discord] Резервная копия: {path.name}', flush=True)
            if db_path.is_file():
                okay, reason = verify_sqlite(db_path)
                if not okay:
                    raise RuntimeError('Player database integrity check failed: ' + reason)
            service = BotService(Database(db_path), owner_ids=owner_ids)
            print(f'[КВЕРАТО Discord v{VERSION}] База: {db_path}', flush=True)
            if not owner_ids:
                print('[WARN] DISCORD_OWNER_IDS не заполнен: команда /admin недоступна.', flush=True)
            bot = KveratoDiscord(service,
                                 guild_id=int(guild_raw) if guild_raw else None,
                                 image_cards=os.getenv('KVERATO_IMAGE_CARDS', '1') != '0')
            bot.run(token, log_handler=None)
    except KeyboardInterrupt:
        print('[КВЕРАТО Discord] Остановлен.', flush=True)
        return 0
    except AlreadyRunning as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 1
    except ImportError:
        print('[ERROR] Не хватает библиотек. Запусти install_discord_windows.bat.', file=sys.stderr)
        return 2
    except Exception as exc:
        # Never print exception details containing credentials or URLs.
        print(f'[ERROR] Ошибка Discord: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
