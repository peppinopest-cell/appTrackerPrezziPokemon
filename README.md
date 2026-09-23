# Price Bot Pokémon — backend v2

Backend FastAPI per catalogo visuale, collezione e tracciamento dei prezzi con notifiche Telegram.

## Funzionalità

- Catalogo TCGdex, immagini e associazioni Cardmarket per variante; cache del catalogo di 24 ore.
- Filtri persistenti per lingua, condizione esatta e variante normale/holo/reverse.
- Collezione con quantità e totali, separata dalle carte tracciate su Telegram.
- Coda persistente unica, prezzi condivisi tra utenti e pausa crescente dopo blocchi Cardmarket.
- Ultimo prezzo valido e data conservati quando il recupero fallisce; nessun prezzo generico di ripiego.
- Sessioni Bearer e controllo della proprietà delle carte.

## Aggiornamento su Render

**Questo backend richiede il frontend v2. Il frontend precedente non è compatibile con le nuove API `/api/...`.** Coordinare i due deploy; il push su un ramo separato consente la revisione prima di aggiornare il ramo collegato a Render.

Prima del deploy, eseguire un backup del database reale. Lo script usa l'API backup SQLite per includere le modifiche WAL già confermate:

```sh
python scripts/backup_db.py /var/data/watchlist_v4.db /var/data/backups/watchlist-before-v2.db
```

Sostituire i percorsi con quelli effettivi. Il backup non sovrascrive un file esistente.

- Python: 3.11 o successivo (vedi `.python-version`).
- Build: `pip install -r requirements.txt`.
- Start: `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`.
- `DB_PATH`: percorso del database esistente su disco persistente. Non puntare a un nuovo database vuoto.
- `ALLOWED_ORIGINS`: origine HTTPS del frontend Vercel, senza slash finale. Più origini separate da virgola.
- Il processo deve rimanere attivo per aggiornamenti e notifiche a frontend chiuso.

La migrazione conserva le tabelle originali e importa le carte nelle nuove liste. Richiede conferma dei filtri delle carte vecchie prima del recupero: i vecchi prezzi non sono considerati verificati. Se una carta era presente in entrambe le liste possono esserci due righe da riconciliare, salvando quantità e tracciamento in una sola riga e rimuovendo il duplicato.

## Frequenza e blocchi

| Variabile | Default |
| --- | --- |
| `PRICE_MIN_GAP_SECONDS` | 30 secondi tra recuperi |
| `PRICE_CACHE_SECONDS` | 900 secondi (15 minuti) |
| `COLLECTION_INTERVAL_SECONDS` | 86400 secondi (24 ore) |
| `BLOCK_COOLDOWN_SECONDS` | 1800 secondi (30 minuti) |
| `RUN_WORKER` | 1; usare 0 nei test/anteprime senza Cardmarket e Telegram |

L'intervallo utente è soggetto a cache e coda. Con il default, scegliere 5 minuti non forza un recupero prima di 15 minuti. Per un minimo di 5 minuti si può impostare `PRICE_CACHE_SECONDS=300`, aumentando il traffico.

Su 403, 429, 503 o challenge il sistema si ferma globalmente. Pause crescenti fino a 24 ore, rispettando anche `Retry-After` più lunghi. Nessuna rotazione IP/browser, cache-buster o risoluzione CAPTCHA. Il pulsante di aggiornamento non aggira cache e pause. Lo stato persiste ai riavvii.

**Non viene garantita l'eliminazione di Cloudflare.** La richiesta live effettuata durante lo sviluppo ha restituito 403. Il parser è verificato con fixture, ma l'estrazione dall'HTML live corrente resta da confermare su una risposta accessibile.

Il prezzo è il minimo tra le offerte verificabili nella pagina letta, con lingua e condizione esatte, spedizione esclusa. Non si scandiscono tutte le pagine e non si garantisce il minimo assoluto del mercato. Il link nel browser usa la condizione minima Cardmarket, mentre il parser filtra il grado esatto. Non sono supportate prime edizioni, timbri speciali, carte firmate/alterate o certificate. Associazioni mancanti o ambigue nel catalogo richiedono un collegamento manuale.

## Verifica locale

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

I test usano database temporanei e simulano Cardmarket e Telegram, senza inviare messaggi reali. Non inserire nel repository database, token o file `.env`.

Catalogo: [TCGdex](https://tcgdex.dev/reference/card).
