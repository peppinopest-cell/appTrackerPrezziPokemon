"""Bounded anonymous reader probe; never exports cookies or account data."""
import asyncio
import importlib.metadata
import json
import shutil
from pydoll import Chrome, ChromiumOptions
from cardmarket_reader import parse_page

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'

async def main():
    options = ChromiumOptions()
    options.binary_location = shutil.which('google-chrome')
    options.block_notifications = True
    print('PYDOLL_VERSION=' + importlib.metadata.version('pydoll-python'), flush=True)
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await asyncio.wait_for(tab.go_to(URL), timeout=60)
        for number in range(2):
            response = await asyncio.wait_for(tab.request.get(URL), timeout=45)
            result = parse_page(response.text, response.url, response.status_code)
            print('PYDOLL_RESULT=' + json.dumps({'read': number + 1, 'http_status': response.status_code,
                  'status': result.get('status'), 'price': result.get('price'),
                  'language': result.get('language'), 'condition': result.get('condition')}), flush=True)
            if result.get('status') != 'ok':
                return
            await asyncio.sleep(3)

try:
    asyncio.run(main())
except Exception as exc:
    print('PYDOLL_ERROR=' + type(exc).__name__, flush=True)
    raise SystemExit(2)
