"""Bounded isolated tests of additional free readers. Never prints session cookies."""
import asyncio
import json
import shutil
import sys
import time
from bs4 import BeautifulSoup
from cardmarket_reader import parse_page

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'
METHOD = sys.argv[1]
SAFE_HEADERS = {'server', 'cf-ray', 'content-type', 'www-authenticate'}

def inspect(html, url, status, headers=None):
    result = parse_page(html, url, status if status is not None else 200)
    result['http_status'] = status
    result['headers'] = {k.lower(): v for k, v in (headers or {}).items() if k.lower() in SAFE_HEADERS}
    # Public anonymous response summary only; no cookies, tokens or full pages.
    body = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
    if result['status'] != 'ok':
        result['body_summary'] = body[:200]
    return result

def solver():
    import requests
    endpoint = 'http://127.0.0.1:8191'
    for _ in range(90):
        try:
            response = requests.get(endpoint, timeout=2)
            if response.status_code < 500:
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    else:
        return {'status': 'solver_startup_failed', 'price': None}
    response = requests.post(endpoint + '/v1', json={'cmd': 'request.get', 'url': URL, 'maxTimeout': 60000}, timeout=85)
    response.raise_for_status()
    data = response.json()
    if data.get('status') != 'ok':
        return {'status': 'solver_error', 'message': data.get('message', '')[:250], 'price': None}
    solution = data.get('solution', {})
    result = inspect(solution.get('response', ''), solution.get('url', URL), solution.get('status'), solution.get('headers'))
    result['solver_version'] = data.get('version')
    return result

async def zendriver():
    import zendriver as zd
    browser = await zd.start(headless=False, browser_executable_path=shutil.which('google-chrome') or shutil.which('chromium'))
    try:
        tab = await browser.get('about:blank')
        state = {'status': None, 'headers': {}}
        async def receive(event):
            if event.type_ == zd.cdp.network.ResourceType.DOCUMENT and event.response.url.startswith('https://www.cardmarket.com/'):
                state['status'] = int(event.response.status)
                state['headers'] = dict(event.response.headers)
        tab.add_handler(zd.cdp.network.ResponseReceived, receive)
        await tab.send(zd.cdp.network.enable())
        tab = await browser.get(URL)
        try:
            await tab.select('div.article-row', timeout=15)
        except Exception:
            pass
        return inspect(await tab.get_content(), URL, state['status'], state['headers'])
    finally:
        await browser.stop()

started = time.monotonic()
try:
    result = asyncio.run(asyncio.wait_for(zendriver(), timeout=75)) if METHOD == 'zendriver' else solver()
except Exception as exc:
    result = {'status': 'diagnostic_error', 'exception_type': type(exc).__name__, 'message': str(exc)[:250], 'price': None}
print('SOLVER_RESULT=' + json.dumps({'method': METHOD, 'elapsed_seconds': round(time.monotonic() - started, 2), 'result': result}, ensure_ascii=True), flush=True)
