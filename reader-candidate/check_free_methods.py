"""Bounded cloud checks of free tools. No CAPTCHA clicks or paid services."""
import json
import sys
import tempfile
import time
from importlib.metadata import version
from cardmarket_reader import parse_page

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'
METHOD = sys.argv[1]

def inspect_page(page):
    status = {'http': 0}
    def on_response(response):
        if response.request.is_navigation_request() and response.frame == page.main_frame:
            status['http'] = response.status
    page.on('response', on_response)
    page.goto(URL, wait_until='domcontentloaded', timeout=45000)
    try:
        page.locator('div.article-row').first.wait_for(state='attached', timeout=15000)
    except Exception:
        pass
    return parse_page(page.content(), page.url, status['http'])

def run():
    if METHOD == 'camoufox':
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=False) as browser:
            return inspect_page(browser.new_page())
    if METHOD == 'persistent_chromium':
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as p:
            context = p.chromium.launch_persistent_context(profile, headless=False, locale='it-IT')
            try:
                first = inspect_page(context.pages[0])
                if first['status'] != 'ok':
                    return first
            finally:
                context.close()
            # A second access is meaningful only after a successful first read.
            context = p.chromium.launch_persistent_context(profile, headless=False, locale='it-IT')
            try:
                return {'first_read': first, 'reopened_profile': inspect_page(context.pages[0])}
            finally:
                context.close()
    if METHOD == 'seleniumbase':
        from seleniumbase import SB
        from selenium.webdriver.support.ui import WebDriverWait
        with SB(uc=True, xvfb=True, locale='it') as sb:
            sb.driver.uc_open_with_reconnect(URL, 4)
            try:
                WebDriverWait(sb.driver, 15).until(lambda d: d.find_elements('css selector', 'div.article-row'))
            except Exception:
                pass
            # WebDriver does not expose an HTTP status here: keep it unknown.
            result = parse_page(sb.driver.page_source, sb.driver.current_url)
            result['http_status'] = None
            body = sb.driver.find_element('tag name', 'body').text.lower()
            if result['status'] != 'ok' and any(s in body for s in ('please login to continue', 'please log in to continue', '401 unauthorized')):
                result['status'] = 'auth_required'
            return result
    raise ValueError('Unknown method')

started = time.monotonic()
try:
    result = run()
except Exception as exc:
    result = {'status': 'diagnostic_error', 'exception_type': type(exc).__name__, 'message': str(exc)[:250]}
package = 'camoufox' if METHOD == 'camoufox' else 'seleniumbase' if METHOD == 'seleniumbase' else 'playwright'
print('FREE_METHOD_RESULT=' + json.dumps({'method': METHOD, 'package_version': version(package),
      'elapsed_seconds': round(time.monotonic() - started, 2), 'result': result}, ensure_ascii=True), flush=True)
