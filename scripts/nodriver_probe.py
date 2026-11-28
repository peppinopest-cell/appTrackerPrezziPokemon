"""Bounded remote reproduction of the video's wait-and-read method."""
import asyncio
import importlib.metadata
import json
import shutil
from datetime import datetime, timezone
import nodriver as uc
from bs4 import BeautifulSoup
from cardmarket_reader import parse_page

URL='https://www.cardmarket.com/en/Pokemon/Products/Singles/30th-Celebration/Mew-V3-30CBRGB?language=5'

async def inspect(browser, number):
    page=await browser.get(URL)
    await asyncio.sleep(5)
    html=await page.get_content()
    status=await page.evaluate('performance.getEntriesByType("navigation")[0]?.responseStatus || 0')
    result=parse_page(html,URL,int(status) or 200)
    rows=len(BeautifulSoup(html,'html.parser').select('div.article-row'))
    print('NODRIVER_RESULT='+json.dumps({'read':number,'utc':datetime.now(timezone.utc).isoformat(),
        'http_status':int(status) or None,'status':result.get('status'),'reason':result.get('reason'),
        'price':result.get('price'),'condition':result.get('condition'),'language':result.get('language'),
        'matched_offers':result.get('matched_offers'),'offer_rows':rows,'html_bytes':len(html.encode())}),flush=True)
    return result.get('status')=='ok' and rows>0

async def main():
    print('NODRIVER_VERSION='+importlib.metadata.version('nodriver'),flush=True)
    browser=await uc.start(headless=False,browser_executable_path=shutil.which('google-chrome'),sandbox=False)
    try:
        if await inspect(browser,1):
            print('NODRIVER_STAGE=waiting_300_seconds',flush=True)
            await asyncio.sleep(300)
            await inspect(browser,2)
    finally:
        browser.stop()

uc.loop().run_until_complete(asyncio.wait_for(main(),timeout=450))
