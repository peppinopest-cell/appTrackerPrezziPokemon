"""Anonymous login-page reachability only. Never submits a form or credentials."""
import json
import time
import requests
from bs4 import BeautifulSoup

endpoint = 'http://127.0.0.1:8191'
for _ in range(90):
    try:
        if requests.get(endpoint, timeout=2).status_code < 500:
            break
    except requests.RequestException:
        pass
    time.sleep(2)
else:
    raise RuntimeError('Solver startup failed')

response = requests.post(endpoint + '/v1', json={'cmd': 'request.get', 'url': 'https://www.cardmarket.com/it/Pokemon/Login', 'maxTimeout': 60000}, timeout=85)
response.raise_for_status()
data = response.json()
solution = data.get('solution', {})
soup = BeautifulSoup(solution.get('response', ''), 'html.parser')
print('LOGIN_ACCESS_RESULT=' + json.dumps({
    'solver_status': data.get('status'), 'http_status': solution.get('status'),
    'title': soup.title.get_text(' ', strip=True) if soup.title else '',
    'has_password_field': bool(soup.select_one('input[type="password"]')),
    'has_form': bool(soup.find('form')),
    'body_summary': soup.get_text(' ', strip=True)[:200] if not soup.find('form') else '(form present; content omitted)',
    'headers': {k.lower(): v for k, v in solution.get('headers', {}).items() if k.lower() in ('server', 'cf-ray', 'content-type')}
}, ensure_ascii=True), flush=True)
