"""Compare anonymous entry-before-product transports; no credentials."""
import json
import sys
import time
from cardmarket_reader import parse_page

PRODUCT = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'
ENTRY = 'https://www.cardmarket.com/it/Pokemon/Login'
METHOD = sys.argv[1]

def http_reader():
    from curl_cffi import requests
    with requests.Session(impersonate='safari15_5') as session:
        entry = session.get(ENTRY, timeout=25)
        response = session.get(PRODUCT, timeout=25)
        result = parse_page(response.text, response.url, response.status_code)
        return {'entry_status': entry.status_code, 'product': result}

def chromium_reader():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page(locale='it-IT')
            entry = page.goto(ENTRY, wait_until='domcontentloaded', timeout=45000)
            response = page.goto(PRODUCT, wait_until='domcontentloaded', timeout=45000)
            try:
                page.locator('div.article-row').first.wait_for(state='attached', timeout=15000)
            except Exception:
                pass
            return {'entry_status': entry.status if entry else None,
                    'product': parse_page(page.content(), page.url, response.status if response else 0)}
        finally:
            browser.close()

def repeated_solver():
    import requests
    endpoint = 'http://127.0.0.1:8191'
    for _ in range(60):
        try:
            if requests.get(endpoint, timeout=2).status_code < 500:
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    def call(data):
        r = requests.post(endpoint + '/v1', json=data, timeout=85)
        r.raise_for_status()
        payload = r.json()
        if payload.get('status') != 'ok':
            raise RuntimeError(payload.get('message', 'Solver error')[:200])
        return payload
    session = call({'cmd': 'sessions.create'})['session']
    try:
        call({'cmd': 'request.get', 'url': ENTRY, 'session': session, 'maxTimeout': 60000})
        results = []
        for index in range(2):
            if index:
                time.sleep(300)
            solution = call({'cmd': 'request.get', 'url': PRODUCT, 'session': session, 'maxTimeout': 60000})['solution']
            result = parse_page(solution.get('response', ''), PRODUCT, solution.get('status', 200))
            result['solver_reported_status'] = result.pop('http_status')
            results.append(result)
            print('REPEATED_SESSION_READ=' + json.dumps({'read': index + 1, 'result': result}), flush=True)
            if result['status'] != 'ok':
                break
        return {'reads': results}
    finally:
        call({'cmd': 'sessions.destroy', 'session': session})

try:
    result = http_reader() if METHOD == 'curl_cffi' else chromium_reader() if METHOD == 'chromium' else repeated_solver()
except Exception as exc:
    result = {'status': 'diagnostic_error', 'exception_type': type(exc).__name__, 'message': str(exc)[:250]}
print('WARM_READER_RESULT=' + json.dumps({'method': METHOD, 'result': result}, ensure_ascii=True), flush=True)
