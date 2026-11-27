"""Isolated anonymous CloakBrowser probe. Never logs keys or cookies."""
import json
import os
from pathlib import Path
from cloakbrowser import launch
from cardmarket_reader import parse_page

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'

def memory():
    result = {}
    for name in ('memory.current', 'memory.peak'):
        p = Path('/sys/fs/cgroup') / name
        if p.exists():
            result[name] = round(int(p.read_text()) / 1024**2, 1)
    return result

def main():
    if not os.getenv('CLOAKBROWSER_LICENSE_KEY'):
        print('CLOAK_RESULT=' + json.dumps({'status': 'license_required', 'price': None}))
        return 2
    browser = launch(headless=False, humanize=True, human_preset='careful')
    try:
        page = browser.new_page()
        response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(10000)
        result = parse_page(page.content(), page.url, response.status if response else 0)
        print('CLOAK_RESULT=' + json.dumps({
            'status': result.get('status'), 'price': result.get('price'),
            'http_status': result.get('http_status'),
            'language': result.get('language'), 'condition': result.get('condition'),
            'memory_mib': memory()}), flush=True)
        return 0 if result.get('status') == 'ok' else 2
    finally:
        browser.close()

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('CLOAK_ERROR=' + type(exc).__name__, flush=True)
        raise SystemExit(2)
