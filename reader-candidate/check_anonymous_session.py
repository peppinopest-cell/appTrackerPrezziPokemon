"""Login page followed by product in one anonymous browser. No login submission."""
import json
import time
import requests
from bs4 import BeautifulSoup
from cardmarket_reader import parse_page

BASE = 'http://127.0.0.1:8191'
PRODUCT = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'
for _ in range(60):
    try:
        if requests.get(BASE, timeout=2).status_code < 500:
            break
    except requests.RequestException:
        pass
    time.sleep(2)
else:
    raise RuntimeError('Solver startup failed')

def command(payload):
    response = requests.post(BASE + '/v1', json=payload, timeout=85)
    response.raise_for_status()
    data = response.json()
    if data.get('status') != 'ok':
        raise RuntimeError(data.get('message', 'Solver error')[:200])
    return data

session = command({'cmd': 'sessions.create'})['session']
try:
    data = command({'cmd': 'request.get', 'url': 'https://www.cardmarket.com/it/Pokemon/Login', 'session': session, 'maxTimeout': 60000})
    soup = BeautifulSoup(data['solution'].get('response', ''), 'html.parser')
    entry = {'has_password_field': bool(soup.select_one('input[type="password"]')),
             'title': soup.title.get_text(' ', strip=True) if soup.title else ''}
    data = command({'cmd': 'request.get', 'url': PRODUCT, 'session': session, 'maxTimeout': 60000})
    solution = data['solution']
    result = parse_page(solution.get('response', ''), PRODUCT, solution.get('status', 200))
    result['solver_reported_status'] = result.pop('http_status')
    print('ANONYMOUS_SESSION_RESULT=' + json.dumps({'entry_page': entry, 'product': result}, ensure_ascii=True), flush=True)
finally:
    command({'cmd': 'sessions.destroy', 'session': session})
