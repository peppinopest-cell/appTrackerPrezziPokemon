"""Single isolated read; no credentials, proxies, CAPTCHA interaction or retries."""
import json
import time
from camoufox.sync_api import Camoufox
from pricing import parse_offers

url = 'https://www.cardmarket.com/en/Pokemon/Products/Singles/Darkness-Ablaze/Furret-DAA136?language=5&minCondition=2'
started = time.monotonic()
try:
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
        result = {'http_status': response.status if response else None, 'title': page.title()}
        if response and response.status == 200:
            parsed = parse_offers(page.content(), 'it', 'NM', 'normal')
            result.update({k: parsed.get(k) for k in ('status', 'price_cents', 'matched_offers', 'message')})
        else:
            result['status'] = 'access_refused'
except Exception as exc:
    result = {'status': 'diagnostic_error', 'exception_type': type(exc).__name__}
result['elapsed_seconds'] = round(time.monotonic() - started, 2)
print('CAMOUFOX_CARDMARKET_DIAGNOSTIC=' + json.dumps(result, ensure_ascii=True), flush=True)
