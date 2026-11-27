"""Bounded, serialized CloakBrowser reads for the API and scheduled checks."""
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from cardmarket_reader import validate_url


class ReaderError(RuntimeError):
    pass


class CloakBrowserReader:
    def __init__(self, timeout=70):
        self.timeout = timeout
        self.last_error = ''
        self.lock = threading.Lock()

    def read(self, url):
        validate_url(url)
        if not os.getenv('CLOAKBROWSER_LICENSE_KEY', '').strip():
            self.last_error = 'CloakBrowser non configurato: manca CLOAKBROWSER_LICENSE_KEY sul server.'
            raise ReaderError(self.last_error)
        if not self.lock.acquire(blocking=False):
            self.last_error = 'Un controllo CloakBrowser è già in corso. Riprova quando termina.'
            raise ReaderError(self.last_error)
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('cloakbrowser_worker.py')), url],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                start_new_session=os.name != 'nt')
            try:
                output, _ = process.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.communicate()
                raise ReaderError('CloakBrowser non ha risposto entro 70 secondi. Nessun prezzo salvato.')
            result = None
            for line in output.splitlines():
                if line.startswith('CLOAK_DIAGNOSTIC='):
                    logging.warning('CLOAK_DIAGNOSTIC=%s', line.split('=', 1)[1])
                if line.startswith('CLOAK_RESULT='):
                    result = json.loads(line.split('=', 1)[1])
            if not result:
                raise ReaderError('CloakBrowser non ha completato l’avvio o la lettura. Nessun prezzo salvato.')
            if result.get('status') != 'ok':
                messages = {
                    'access_denied': 'Cardmarket ha negato l’accesso a CloakBrowser',
                    'challenge_required': 'Cardmarket richiede ancora una verifica di sicurezza',
                    'no_verified_offers': 'Nessuna offerta verificabile con lingua e condizione del link',
                    'license_error': 'CloakBrowser non ha accettato la licenza o la sessione è già occupata',
                    'browser_error': 'CloakBrowser non ha completato la lettura della pagina',
                    'http_error': 'Cardmarket ha restituito un errore HTTP',
                }
                message = messages.get(result.get('status'), 'Risposta Cardmarket non utilizzabile')
                if result.get('http_status'):
                    message += f" (HTTP {result['http_status']})"
                raise ReaderError(message + '. Nessun prezzo salvato.')
            data = result.get('data', {})
            if not isinstance(data.get('price'), (int, float)) or data['price'] < 0:
                raise ReaderError('CloakBrowser non ha restituito un prezzo verificato.')
            self.last_error = ''
            return data
        except (OSError, ValueError) as exc:
            self.last_error = 'Errore del lettore CloakBrowser. Nessun prezzo salvato.'
            raise ReaderError(self.last_error) from exc
        except ReaderError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.lock.release()
