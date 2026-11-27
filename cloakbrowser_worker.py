"""One browser per read, isolated from the API process. No credential logging."""
import json
import sys
from bs4 import BeautifulSoup
from cardmarket_reader import LANGUAGES, parse_page, validate_url


def read(url):
    validate_url(url)
    from cloakbrowser import launch
    browser = launch(headless=True)
    try:
        page = browser.new_page()
        response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        status = response.status if response else 0
        if status == 200:
            try:
                page.locator('div.article-row').first.wait_for(timeout=10000)
            except Exception:
                pass
        html = page.content()
        result = parse_page(html, page.url, status)
        if result['status'] == 'ok':
            image = BeautifulSoup(html, 'html.parser').select_one('meta[property="og:image"]')
            result['data'] = {'price': result['price'], 'condition': result['condition'],
                'language': LANGUAGES.get(result['language'], ('', '🌐'))[1],
                'image': image.get('content', '') if image else ''}
        return {key: value for key, value in result.items() if key != 'title'}
    finally:
        browser.close()


if __name__ == '__main__':
    try:
        result = read(sys.argv[1])
    except Exception as exc:
        result = {'status': 'license_error' if 'license' in type(exc).__name__.lower() else 'browser_error'}
    print('CLOAK_RESULT=' + json.dumps(result), flush=True)
