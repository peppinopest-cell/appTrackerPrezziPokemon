"""Smoke test combined container against an anonymous public card. No Telegram."""
import json
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:10000'
def call(path, body=None, timeout=90):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)

for _ in range(60):
    try:
        with urllib.request.urlopen(BASE + '/docs', timeout=2) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(2)
else:
    raise RuntimeError('API did not start')

# Allow solver to complete its own browser startup check.
time.sleep(15)
status, data = call('/users/settings', {'user_id': 'container-smoke', 'bot_token': '', 'chat_id': '', 'check_interval': 5})
assert status == 200, (status, data)
status, data = call('/watch', {'user_id': 'container-smoke', 'card_url':
    'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'}, timeout=180)
result = {'api_started': True, 'http_status': status, 'price': data.get('prezzo'),
          'status': 'verified_price' if status == 200 and data.get('prezzo') is not None else 'price_unavailable',
          'message': data.get('detail')}
if result['status'] == 'verified_price':
    _, rows = call('/watchlist/container-smoke')
    assert len(rows) == 1 and rows[0]['last_price'] == result['price']
print('BOT_INTEGRATION_RESULT=' + json.dumps(result), flush=True)
