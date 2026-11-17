"""Opt-in Windows watchdog for the VK process; never restarts on a clean exit.

Stops after repeated crashes to avoid hammering VK when the token is invalid.
Does not edit the live database. main.py owns the single-instance DB lock.
"""
from __future__ import annotations

import subprocess
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_CRASHES = 5
CRASH_WINDOW_SECONDS = 5 * 60


def crash_action(crashes: deque[float], at: float) -> tuple[bool, int]:
    """Return (restart, wait_seconds) for a failed child process."""
    while crashes and at - crashes[0] >= CRASH_WINDOW_SECONDS:
        crashes.popleft()
    crashes.append(at)
    if len(crashes) >= MAX_CRASHES:
        return False, 0
    return True, min(60, 5 * (2 ** (len(crashes) - 1)))


def main() -> int:
    python = ROOT / '.venv312' / 'Scripts' / 'python.exe'
    if not python.is_file():
        print('[ERROR] Python 3.12 env not installed. Run install_vk_windows.bat.', flush=True)
        return 2
    if not (ROOT / '.env').is_file():
        print('[ERROR] Missing .env. Configure VK_TOKEN first.', flush=True)
        return 2
    crashes: deque[float] = deque()
    while True:
        print('[KVERATO] Starting VK process...', flush=True)
        process = subprocess.Popen([str(python), str(ROOT / 'main.py'), 'vk'], cwd=ROOT)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            print('[KVERATO] Stopping on operator request...', flush=True)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            return 0
        if code == 0:
            print('[KVERATO] Clean exit; watchdog stopped.', flush=True)
            return 0
        restart, delay = crash_action(crashes, time.monotonic())
        if not restart:
            print('[ERROR] Five crashes in five minutes; automatic restarts stopped.', flush=True)
            print('[NEXT] Inspect safe logs, check your configuration and safe log output; fix before retrying.', flush=True)
            return code if code > 0 else 1
        print(f'[WARN] Bot exited (code={code}). Restarting in {delay}s. Ctrl+C to stop.', flush=True)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            print('[KVERATO] Operator stopped watchdog.', flush=True)
            return 0


if __name__ == '__main__':
    raise SystemExit(main())
