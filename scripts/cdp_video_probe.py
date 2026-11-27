"""One bounded server experiment of the video's pure CDP configuration."""
import importlib.metadata
import json
import time
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from seleniumbase import sb_cdp
from cardmarket_reader import parse_page

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Mew-V3-30CBRGB?language=5'

def inspect(sb, number):
    sb.goto(URL)
    sb.sleep(8)
    html = sb.get_page_source()
    initial = parse_page(html, URL)
    if initial.get('status') == 'challenge_required':
        print('CDP_STAGE=challenge_helper', flush=True)
        sb.solve_captcha()
        sb.sleep(10)
        html = sb.get_page_source()
    nav = sb.evaluate('performance.getEntriesByType("navigation").map(x => ({status:x.responseStatus || 0}))[0] || {}')
    http = nav.get('status', 0) if isinstance(nav, dict) else 0
    result = parse_page(html, URL, http or 200)
    soup = BeautifulSoup(html, 'html.parser')
    safe = {k: result.get(k) for k in ('status','price','price_cents','language','condition','matched_offers','reason')}
    safe.update(read=number, utc=datetime.now(timezone.utc).isoformat(),
                http_status=http or None, offer_rows=len(soup.select('div.article-row')),
                html_bytes=len(html.encode()), mode='pure_cdp_headed_xvfb')
    print('CDP_RESULT=' + json.dumps(safe), flush=True)
    return result.get('status') == 'ok' and safe['offer_rows'] > 0

print('SELENIUMBASE_VERSION=' + importlib.metadata.version('seleniumbase'), flush=True)
sb = None
try:
    sb = sb_cdp.Chrome(headless=False, locale='it')
    if inspect(sb, 1):
        print('CDP_STAGE=waiting_300_seconds', flush=True)
        time.sleep(300)
        inspect(sb, 2)
except Exception as exc:
    print('CDP_ERROR=' + type(exc).__name__, flush=True)
    raise
finally:
    if sb is not None:
        sb.quit()
