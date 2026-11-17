"""Bounded crash restart; never retry invalid configuration forever."""
from pathlib import Path
import subprocess
import sys
import time
root = Path(__file__).resolve().parents[1]
failures = 0
while True:
    started = time.monotonic()
    code = subprocess.call([sys.executable, str(root / 'main.py')], cwd=root)
    if code in (0, 2):
        sys.exit(code)
    failures = failures + 1 if time.monotonic() - started < 120 else 1
    if failures >= 5:
        print('[КВЕРАТО Discord] 5 быстрых сбоев, повторный запуск остановлен.')
        sys.exit(code)
    delay = min(60, 5 * 2 ** (failures - 1))
    print(f'[КВЕРАТО Discord] Ошибка запуска, повтор через {delay} сек.')
    time.sleep(delay)
