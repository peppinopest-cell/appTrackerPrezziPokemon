"""One browser per read, isolated from the API process. No credential logging."""
import json
import sys
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from cardmarket_reader import LANGUAGES, parse_page, validate_url


def diagnostics(html, final_url, status, headers, navigation_statuses):
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True).lower().rstrip('.')
    headers = {key.lower(): value for key, value in headers.items()}
    result = {'http_status': status, 'navigation_statuses': navigation_statuses,
        'final_host': urlsplit(final_url).hostname, 'final_path': urlsplit(final_url).path,
        'html_bytes': len(html.encode()), 'html_sha256': hashlib.sha256(html.encode()).hexdigest(),
        'offer_rows': len(soup.select('div.article-row')),
        'login_message': text in ('please login to continue', 'please login and retry', 'please log in to continue'),
        'challenge_marker': bool(soup.select_one('#challenge-form, #cf-challenge-running, .cf-turnstile')),
        'cloudflare_header': 'cf-ray' in headers,
        'challenge_header': headers.get('cf-mitigated') == 'challenge',
        'www_authenticate_present': 'www-authenticate' in headers,
        'server_cloudflare': 'cloudflare' in headers.get('server', '').lower()}
    peak = Path('/sys/fs/cgroup/memory.peak')
    if peak.exists():
        result['container_peak_mib'] = round(int(peak.read_text()) / 1048576, 1)
    return result


def read(url):
    validate_url(url)
    from cloakbrowser import launch
    browser = launch(headless=True)
    try:
        page = browser.new_page()
        navigation = []
        latest = {'status': 0, 'headers': {}}
        def record(response):
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                navigation.append(response.status)
                latest.update(status=response.status, headers=response.headers)
        page.on('response', record)
        response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        status = response.status if response else 0
        page.wait_for_timeout(3000)
        if status == 200:
            try:
                page.locator('div.article-row').first.wait_for(timeout=10000)
            except Exception:
                pass
        html = page.content()
        status = latest['status'] or status
        print('CLOAK_DIAGNOSTIC=' + json.dumps(diagnostics(html, page.url, status, latest['headers'], navigation)), flush=True)
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
