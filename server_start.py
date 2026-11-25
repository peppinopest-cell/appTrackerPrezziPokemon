"""Run API and private FlareSolverr in the same container."""
import os
import signal
import subprocess
import time

children = []
stopping = False

def stop(*_):
    global stopping
    stopping = True

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

def main():
    solver_env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': '8191', 'LOG_LEVEL': 'warning'}
    children.append(subprocess.Popen(['/usr/local/bin/python', '-u', '/app/flaresolverr.py'], env=solver_env, cwd='/app', start_new_session=True))
    children.append(subprocess.Popen(['/opt/bot-venv/bin/python', '-m', 'uvicorn', 'app:app', '--host', '0.0.0.0', '--port', os.getenv('PORT', '10000')], start_new_session=True))
    try:
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.5)
        return 0 if stopping else 1
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)

if __name__ == '__main__':
    raise SystemExit(main())
