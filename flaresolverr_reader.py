"""Serialized anonymous browser session used by the existing bot API."""
import atexit
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from cardmarket_reader import LANGUAGES, parse_page, validate_url


class ReaderError(RuntimeError):
    pass


def memory_diagnostic(stage):
    if os.getenv('FS_MEMORY_DIAGNOSTIC') != '1':
        return
    try:
        base = Path('/sys/fs/cgroup')
        stat = dict(line.split() for line in (base / 'memory.stat').read_text().splitlines())
        values = {key: round(int(stat.get(key, 0)) / 1048576, 1) for key in ('anon', 'file', 'slab')}
        values.update(current=round(int((base / 'memory.current').read_text()) / 1048576, 1),
                      peak=round(int((base / 'memory.peak').read_text()) / 1048576, 1))
        print('FS_MEMORY=' + json.dumps({'stage': stage, 'MiB': values}), flush=True)
    except OSError:
        pass


class FlareSolverrReader:
    def __init__(self, endpoint, timeout_ms=60000):
        self.embedded = endpoint == 'embedded'
        self.embedded_api = None
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
        if self.embedded:
            try:
                if self.embedded_api is None:
                    memory_diagnostic('before_engine_initialize')
                    import flaresolverr_service
                    import utils
                    from dtos import V1RequestBase
                    utils.get_current_platform()
                    utils.get_user_agent()
                    memory_diagnostic('after_engine_initialize')
                    self.embedded_api = (flaresolverr_service, utils, V1RequestBase)
                service, utils, request_type = self.embedded_api
                memory_diagnostic('before_' + command)
                data = utils.object_to_dict(service.controller_v1_endpoint(request_type({'cmd': command, **fields})))
                memory_diagnostic('after_' + command)
            except Exception as exc:
                raise ReaderError('Il motore FlareSolverr non ha completato la richiesta.') from exc
            if data.get('status') != 'ok':
                raise ReaderError('FlareSolverr non ha completato la lettura della pagina.')
            return data
        try:
            response = requests.post(self.endpoint, json={'cmd': command, **fields},
                                     timeout=(5, 5 if command == 'sessions.destroy' else self.timeout_ms / 1000 + 15))
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
                                 maxTimeout=self.timeout_ms, disableMedia=True)
                data = self.command('request.get', session=self.session, url=url,
                                    maxTimeout=self.timeout_ms, disableMedia=True)
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
                self.last_error = ''
                return {'price': result['price'], 'image': image.get('content', '') if image else '',
                        'condition': result['condition'], 'language': language}
            except (ReaderError, KeyError) as exc:
                self.last_error = str(exc) if isinstance(exc, ReaderError) else 'Sessione FlareSolverr non valida.'
                self.blocked_until = time.monotonic() + 300
                self.close()
                raise ReaderError(self.last_error) from exc
