"""Read verified listing prices through an explicitly configured Thermoptic proxy."""
import os
import threading
import requests
from bs4 import BeautifulSoup
from cardmarket_reader import LANGUAGES, parse_page, validate_url
from cloakbrowser_reader import ReaderError


class ThermopticReader:
    def __init__(self):
        self.last_error = ''
        self.lock = threading.Lock()

    def read(self, url):
        validate_url(url)
        proxy = os.getenv('THERMOPTIC_PROXY_URL', '').strip()
        if not proxy:
            self.last_error = 'Manca THERMOPTIC_PROXY_URL sul server.'
            raise ReaderError(self.last_error)
        if not self.lock.acquire(blocking=False):
            self.last_error = 'Un controllo Thermoptic è già in corso.'
            raise ReaderError(self.last_error)
        try:
            # Do not follow redirects to hosts outside the validated product URL.
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(url, proxies={'http': proxy, 'https': proxy},
                    verify=os.getenv('THERMOPTIC_CA_FILE') or True,
                    timeout=(10, 60), allow_redirects=False)
            result = parse_page(response.text, url, response.status_code)
            if result['status'] != 'ok':
                raise ReaderError(f"Thermoptic: nessuna offerta verificata (HTTP {response.status_code}, {result['status']}). Nessun prezzo salvato.")
            image = BeautifulSoup(response.text, 'html.parser').select_one('meta[property="og:image"]')
            self.last_error = ''
            return {'price': result['price'], 'condition': result['condition'],
                    'language': LANGUAGES.get(result['language'], ('', ''))[1],
                    'image': image.get('content', '') if image else ''}
        except requests.RequestException as exc:
            self.last_error = 'Thermoptic non raggiungibile o certificato non valido. Nessun prezzo salvato.'
            raise ReaderError(self.last_error) from exc
        except ReaderError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.lock.release()
