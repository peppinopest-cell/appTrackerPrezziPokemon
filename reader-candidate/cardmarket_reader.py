"""Isolated reader candidate. Reads listing rows, never guide prices."""
import re
from urllib.parse import parse_qs, urlsplit
from bs4 import BeautifulSoup

LANGUAGES = {1: ('English', 'Inglese'), 2: ('French', 'Francese'),
             3: ('German', 'Tedesco'), 4: ('Spanish', 'Spagnolo'),
             5: ('Italian', 'Italiano'), 7: ('Japanese', 'Giapponese'),
             8: ('Portuguese', 'Portoghese'), 10: ('Korean', 'Coreano')}
CONDITIONS = {'MT': 1, 'NM': 2, 'EX': 3, 'GD': 4, 'LP': 5, 'PL': 6, 'PO': 7}


def validate_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in ('www.cardmarket.com', 'cardmarket.com')
            or p.username or p.password or p.port not in (None, 443)
            or not re.fullmatch(r'/[a-z]{2}/Pokemon/Products/Singles/[^/]+/[^/]+/?', p.path)):
        raise ValueError('Serve un URL HTTPS di una carta Cardmarket.')
    return url


def parse_page(html, url, http_status=200):
    query = parse_qs(urlsplit(validate_url(url)).query)
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    result = {'http_status': http_status, 'title': title, 'price': None}
    body = soup.get_text(' ', strip=True).lower().rstrip('.')
    if body in ('please login to continue', 'please login and retry', 'please log in to continue'):
        return {**result, 'status': 'auth_required'}
    if ('just a moment' in title.lower() or 'attention required' in title.lower()
            or soup.select_one('#challenge-form, #cf-challenge-running, .cf-turnstile')):
        return {**result, 'status': 'challenge_required'}
    if http_status != 200:
        return {**result, 'status': 'auth_required' if http_status == 401 else 'http_error'}
    language = int(query['language'][0]) if 'language' in query else None
    minimum = int(query['minCondition'][0]) if 'minCondition' in query else 7
    if language is not None and language not in LANGUAGES:
        return {**result, 'status': 'unsupported_filter'}
    prices = []
    rows = soup.select('div.article-row')
    for row in rows:
        condition_node = row.select_one('.article-condition .badge, .article-condition')
        condition = condition_node.get_text(strip=True).upper() if condition_node else ''
        if CONDITIONS.get(condition, 100) > minimum:
            continue
        attributes = ' '.join(str(n.get(a, '')) for n in row.select('.product-attributes [aria-label], .product-attributes [title], .product-attributes [data-original-title], .product-attributes [data-bs-original-title], .product-attributes [onmouseover]')
                              for a in ('aria-label', 'title', 'data-original-title', 'data-bs-original-title', 'onmouseover'))
        if language is not None and not any(re.search(r'\b' + re.escape(v) + r'\b', attributes, re.I) for v in LANGUAGES[language]):
            continue
        price_node = row.select_one('.price-container .color-primary, .price-container .fw-bold, .price-container .font-weight-bold')
        if price_node is None:
            continue
        match = re.fullmatch(r'\s*([\d.\s\u00a0]+),(\d{2})\s*€\s*', price_node.get_text(' ', strip=True))
        if match:
            cents = int(re.sub(r'[.\s\u00a0]', '', match[1])) * 100 + int(match[2])
            prices.append((cents, condition))
    if not prices:
        return {**result, 'status': 'no_verified_offers', 'offer_rows': len(rows)}
    cents, condition = min(prices)
    return {**result, 'status': 'ok', 'price': cents / 100, 'price_cents': cents,
            'condition': condition, 'language': language, 'matched_offers': len(prices)}


def read_browser(url):
    """One standard browser visit. No challenge interaction or identity rotation."""
    from playwright.sync_api import sync_playwright
    validate_url(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(locale='it-IT')
            status = {'http': 0}
            def record_response(response):
                if response.request.is_navigation_request() and response.frame == page.main_frame:
                    status['http'] = response.status
            page.on('response', record_response)
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            try:
                page.locator('div.article-row').first.wait_for(state='attached', timeout=15000)
            except Exception:
                pass
            return parse_page(page.content(), page.url, status['http'])
        finally:
            browser.close()
