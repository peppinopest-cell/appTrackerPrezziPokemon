"""Isolated legacy transport check; no Telegram, stored credentials or retry loop."""
import json
import cloudscraper
from bs4 import BeautifulSoup
from pricing import parse_offers

url = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/Phantasmal-Flames/Mega-Charizard-X-ex-V3-PFL125?language=5&minCondition=2'
scraper = cloudscraper.create_scraper()
try:
    response = scraper.get(url, timeout=(5, 25), allow_redirects=False)
    result = {'http_status': response.status_code}
    soup = BeautifulSoup(response.text, 'html.parser')
    result['title'] = soup.title.get_text(' ', strip=True) if soup.title else ''
    if response.status_code == 200:
        price = soup.select_one('span.color-primary.small.text-end.text-nowrap.fw-bold')
        result['legacy_price'] = price.get_text(strip=True) if price else None
        parsed = parse_offers(response.text, 'it', 'NM', 'holo')
        result['verified'] = {k: parsed.get(k) for k in ('status', 'price_cents', 'matched_offers')}
    print('LEGACY_CARDMARKET_DIAGNOSTIC=' + json.dumps(result, ensure_ascii=True), flush=True)
except Exception as exc:
    print('LEGACY_CARDMARKET_DIAGNOSTIC=' + json.dumps({'exception_type': type(exc).__name__}), flush=True)
finally:
    scraper.close()
