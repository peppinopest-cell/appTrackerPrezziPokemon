"""One ordinary browser navigation; no credentials, masking, proxies or retries."""
import json
from playwright.sync_api import sync_playwright
from pricing import parse_offers

url = 'https://www.cardmarket.com/en/Pokemon/Products/Singles/Darkness-Ablaze/Furret-DAA136?language=5&minCondition=2'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
        result = {'http_status': response.status if response else None, 'title': page.title()}
        if response and response.status == 200:
            parsed = parse_offers(page.content(), 'it', 'NM', 'normal')
            result.update({k: parsed.get(k) for k in ('status', 'price_cents', 'matched_offers', 'message')})
        else:
            result['status'] = 'access_refused'
        print('CARDMARKET_DIAGNOSTIC=' + json.dumps(result, ensure_ascii=True), flush=True)
    finally:
        browser.close()
