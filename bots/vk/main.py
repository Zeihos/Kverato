"""КВЕРАТО VK v1.0: official bot entrypoint."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from kverato.version import VERSION
from kverato.db import Database
from kverato.service import BotService

def main() -> None:
    project = Path(__file__).resolve().parent
    load_dotenv(project / '.env')
    parser = argparse.ArgumentParser(description='КВЕРАТО VK v1.0 — официальный релиз')
    parser.add_argument('platform', nargs='?', choices=('vk',), default='vk')
    args = parser.parse_args()
    if not os.getenv('VK_TOKEN'):
        parser.error('Не задан VK_TOKEN в .env')

    db_path = Path(os.getenv('KVERATO_DB_PATH', 'data/kverato.sqlite3'))
    if not db_path.is_absolute():
        db_path = project / db_path
    # VK: use the Windows trust store (SSL verification remains enabled).
    # Suppress third-party diagnostic locals: VKBottle/Loguru may otherwise print
    # raw access_token values from HTTP request dictionaries in error traces.
    if args.platform == 'vk':
        import truststore
        truststore.inject_into_ssl()
        from loguru import logger as loguru_logger
        loguru_logger.remove()
        token = os.environ['VK_TOKEN']

        from kverato.safe_logging import redact

        def safe_sink(message):
            sys.stderr.write(redact(str(message), (token,)))

        loguru_logger.add(safe_sink, level='INFO', backtrace=False, diagnose=False,
                          colorize=False, format='{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}')
        # Some libraries use stdlib logging, not Loguru. Redact the same secret
        # before it reaches stderr, including any exception traceback text.
        class SafeStderrHandler(logging.StreamHandler):
            def emit(self, record):
                try:
                    output = self.format(record)
                    self.stream.write(redact(output, (token,)) + self.terminator)
                    self.flush()
                except Exception:
                    self.handleError(record)

        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(SafeStderrHandler(sys.stderr))
        root_logger.setLevel(logging.INFO)

    from kverato.runtime_lock import vk_instance_lock, InstanceAlreadyRunningError
    single = vk_instance_lock(db_path)
    try:
        with single:
            # Back up the existing live SQLite state BEFORE any schema migrations.
            # Fail closed rather than risk starting after an unsuccessful safety copy.
            if os.getenv('KVERATO_AUTO_BACKUP', '1') != '0':
                from kverato.operations import daily_prestart_backup
                copy = daily_prestart_backup(db_path, project / 'backups')
                if copy:
                    print(f'[КВЕРАТО] Проверенная копия до запуска: {copy}', flush=True)
            service = BotService(Database(db_path))
            print(f'[КВЕРАТО VK v{VERSION}] Запуск. Хранилище: {db_path}', flush=True)
            from kverato.adapters.vk import run
            run(os.environ['VK_TOKEN'], service)
    except InstanceAlreadyRunningError as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        raise SystemExit(1) from None



if __name__ == '__main__':
    main()
