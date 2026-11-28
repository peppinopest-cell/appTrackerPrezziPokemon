"""Exercise actual bot reader on server; no local PC, secrets, or Telegram."""
import json
import socket
import time
import subprocess
from datetime import datetime, timezone
from thermoptic_reader import ThermopticReader
from cloakbrowser_reader import ReaderError

URL='https://www.cardmarket.com/en/Pokemon/Products/Singles/30th-Celebration/Mew-V3-30CBRGB?language=5'
deadline=time.monotonic()+150
while True:
    try:
        logs=subprocess.check_output(['docker','compose','-f','docker-compose.yml','-f','compose.probe.yml','logs','thermoptic'],cwd='/tmp/thermoptic',text=True)
        if 'HTTP Proxy server is now running' in logs:
            break
        if time.monotonic()>deadline:
            raise SystemExit('THERMOPTIC_SERVER_RESULT=proxy_not_ready')
        time.sleep(2)
    except OSError:
        if time.monotonic()>deadline:
            raise SystemExit('THERMOPTIC_SERVER_RESULT=proxy_not_ready')
        time.sleep(2)
reader=ThermopticReader()
for index in range(2):
    if index:
        print('THERMOPTIC_SERVER_STAGE=waiting_300_seconds',flush=True)
        time.sleep(300)
    try:
        data=reader.read(URL)
        print('THERMOPTIC_SERVER_RESULT='+json.dumps({'read':index+1,'utc':datetime.now(timezone.utc).isoformat(),'status':'ok','data':data}),flush=True)
    except ReaderError as exc:
        print('THERMOPTIC_SERVER_RESULT='+json.dumps({'read':index+1,'utc':datetime.now(timezone.utc).isoformat(),'status':'failed','error':str(exc)}),flush=True)
        raise SystemExit(2)
