"""Verify API startup and missing-license response without claiming a real price."""
import json
import time
import urllib.error
import urllib.request

base = 'http://127.0.0.1:10000'
for _ in range(30):
    try:
        with urllib.request.urlopen(base + '/docs', timeout=2) as response:
            assert response.status == 200
            break
    except OSError:
        time.sleep(1)
else:
    raise RuntimeError('API did not start')
request = urllib.request.Request(base + '/watch',
    data=json.dumps({'user_id': 'cloak-smoke', 'card_url': 'https://www.cardmarket.com/it/Pokemon/Products/Singles/Set/Card'}).encode(),
    headers={'Content-Type': 'application/json'})
try:
    urllib.request.urlopen(request, timeout=5)
    raise AssertionError('An unconfigured reader must not insert a card')
except urllib.error.HTTPError as error:
    data = json.load(error)
    assert error.code == 503 and 'CLOAKBROWSER_LICENSE_KEY' in data['detail'], (error.code, data)
with urllib.request.urlopen(base + '/watchlist/cloak-smoke') as response:
    assert json.load(response) == []
print('API startup and missing-license path verified; real Cardmarket access unverified.')
