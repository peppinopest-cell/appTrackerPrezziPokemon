"""Start the API; browser reads run as bounded child processes."""
import os
import sys

if __name__ == '__main__':
    os.execv(sys.executable, [
        sys.executable, '-m', 'uvicorn', 'app:app', '--loop', 'asyncio',
        '--host', '0.0.0.0', '--port', os.getenv('PORT', '10000')])
