"""Run API with the bundled FlareSolverr engine in the same Python process."""
import os

if __name__ == '__main__':
    os.environ.setdefault('DISABLE_MEDIA', 'true')
    os.environ.setdefault('LOG_LEVEL', 'warning')
    os.execv('/opt/bot-venv/bin/python', [
        '/opt/bot-venv/bin/python', '-m', 'uvicorn', 'app:app',
        '--host', '0.0.0.0', '--port', os.getenv('PORT', '10000')])
