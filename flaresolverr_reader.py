"""Serialized anonymous browser session used by the existing bot API."""
import atexit
import threading
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from cardmarket_reader import LANGUAGES, parse_page, validate_url


class ReaderError(RuntimeError):
    pass


class FlareSolverrReader:
    def __init__(self, endpoint, timeout_ms=60000):
        self.endpoint = endpoint.rstrip('/')
        if not self.endpoint.endswith('/v1'):
            self.endpoint += '/v1'
        self.timeout_ms = timeout_ms
        self.session = None
        self.lock = threading.RLock()
        self.blocked_until = 0
        self.last_error = ''
        atexit.register(self.close)

    def command(self, command, **fields):
        try:
            response = requests.post(self.endpoint, json={'cmd': command, **fields},
                                     timeout=(5, self.timeout_ms / 1000 + 15))
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ReaderError('FlareSolverr non raggiungibile o risposta non valida.') from exc
        if data.get('status') != 'ok':
            raise ReaderError('FlareSolverr non ha completato la lettura della pagina.')
        return data

    def close(self):
        with self.lock:
            session, self.session = self.session, None
            if session:
                try:
                    self.command('sessions.destroy', session=session)
                except ReaderError:
                    pass

    def read(self, url):
        validate_url(url)
        with self.lock:
            if time.monotonic() < self.blocked_until:
                raise ReaderError(self.last_error + ' Nuovo controllo dopo il periodo di attesa.')
            try:
                if self.session is None:
                    self.session = self.command('sessions.create')['session']
                    # This is a public anonymous entry page; no login is submitted.
                    locale = urlsplit(url).path.split('/')[1]
                    self.command('request.get', session=self.session,
                                 url=f'https://www.cardmarket.com/{locale}/Pokemon/Login',
                                 maxTimeout=self.timeout_ms)
                data = self.command('request.get', session=self.session, url=url,
                                    maxTimeout=self.timeout_ms)
                solution = data.get('solution', {})
                html = solution.get('response', '')
                final_url = solution.get('url', url)
                try:
                    result = parse_page(html, final_url, solution.get('status', 200))
                except ValueError as exc:
                    raise ReaderError('Cardmarket ha reindirizzato a una pagina diversa dalla carta.') from exc
                if result['status'] != 'ok':
                    messages = {
                        'access_denied': 'Cardmarket ha negato la lettura delle offerte. Nessun prezzo verificato.',
                        'challenge_required': 'Cardmarket richiede una verifica di sicurezza. Nessun prezzo verificato.',
                        'no_verified_offers': 'Nessuna offerta verificabile con i filtri del link.',
                    }
                    raise ReaderError(messages.get(result['status'], 'Risposta Cardmarket non utilizzabile.'))
                soup = BeautifulSoup(html, 'html.parser')
                image = soup.select_one('meta[property="og:image"]')
                language = LANGUAGES.get(result['language'], ('', '🌐'))[1]
                return {'price': result['price'], 'image': image.get('content', '') if image else '',
                        'condition': result['condition'], 'language': language}
            except (ReaderError, KeyError) as exc:
                self.last_error = str(exc) if isinstance(exc, ReaderError) else 'Sessione FlareSolverr non valida.'
                self.blocked_until = time.monotonic() + 300
                self.close()
                raise ReaderError(self.last_error) from exc
