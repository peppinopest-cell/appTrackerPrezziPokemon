# Price Bot · tracciamento e diagnostica Telegram

L'app mostra solo carte tracciate e catalogo. Aggiungi una carta tramite link
Cardmarket o scegli dal catalogo; quantità e gestione della collezione sono
state rimosse dall'interfaccia. Le tabelle esistenti restano compatibili e non
vengono cancellate, ma gli aggiornamenti automatici riguardano solo carte tracciate.

## Lettore e profili

Il lettore usa curl_cffi 0.17.0 con una sessione coerente per profilo:
chrome120, safari15_5, firefox133. Sono profili di compatibilità TLS/HTTP,
non browser con JavaScript, e non garantiscono l'accesso a Cardmarket.
Non vengono aggiunti cache-buster o risolutori CAPTCHA.

Dopo un blocco di accesso, il profilo successivo viene memorizzato in SQLite
ed è utilizzato soltanto al primo tentativo consentito dopo il cooldown.
HTTP 429 mantiene il profilo e rispetta Retry-After. HTTP 401 è segnalato come
richiesta di autenticazione: non viene trattato come un problema risolvibile
cambiando browser. La pausa iniziale per blocchi rimane 30 minuti di default.
Cache prezzi: 5 minuti di default; coda, errori e blocchi possono prolungarla.

## Messaggi Telegram

La configurazione può essere verificata da Impostazioni. Alla prima attivazione
del controllo viene accodato un messaggio con carta e filtri. Il primo prezzo
verificato e i successivi cambi generano notifiche separate.

Diagnostica Telegram, attiva per default e disattivabile da Impostazioni,
aggiunge un messaggio per ogni controllo realmente eseguito: profilo, HTTP,
esito, conteggio controlli, blocchi e rifiuti 401 degli ultimi 30 giorni,
prossima attesa e profilo successivo. Non invia test fittizi durante le pause.
I messaggi usano l'outbox persistente e i retry Telegram esistenti.

I prezzi sono il minimo delle offerte verificabili nella pagina letta, con
lingua e condizione esatte e spedizione esclusa. Nessun errore diventa un
prezzo zero; nessun prezzo aggregato sostituisce le offerte filtrate.

## Validazione e hosting

Eseguire `.venv\Scripts\python.exe -m unittest discover -s tests -v`,
`npm.cmd run typecheck` e `npm.cmd run build:web`.
I test simulano Cardmarket e Telegram: non attestano l'accesso live.

Il backend richiede app.py, catalog.py, pricing.py e requirements.txt.
Start: `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`.
ALLOWED_ORIGINS deve includere l'origine del frontend. RUN_WORKER=1 abilita
il processo prezzi e Telegram. RUN_WORKER=0 è solo per test isolati.

DB_PATH deve puntare a un database su disco persistente per conservare i dati.
Il piano Render Free attuale ha disco temporaneo: i deploy possono perdere
account, configurazione, carte, contatori e cooldown. UptimeRobot non rende
il disco persistente. Non distribuire file HAR, token, .env o database.

## Limite noto

Le prove precedenti da server hanno restituito HTTP 401 da Cardmarket.
I profili e i messaggi diagnostici non costituiscono una soluzione verificata
al rifiuto di accesso. Non sono stati attivati servizi o piani a pagamento.
