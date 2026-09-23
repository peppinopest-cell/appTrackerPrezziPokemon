"""Conservative Cardmarket reader. No CAPTCHA solving, identity rotation or price-guide fallback."""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit, urljoin

from bs4 import BeautifulSoup
import requests

LANGUAGES = {"en": (1, "English"), "fr": (2, "French"), "de": (3, "German"),
             "es": (4, "Spanish"), "it": (5, "Italian"), "ja": (7, "Japanese"),
             "pt": (8, "Portuguese"), "ko": (10, "Korean")}
CONDITIONS = {"MT": 1, "NM": 2, "EX": 3, "GD": 4, "LP": 5, "PL": 6, "PO": 7}
LANGUAGE_NAMES = {"en": ("english", "inglese"), "it": ("italian", "italiano"),
                  "fr": ("french", "francese"), "de": ("german", "tedesco"),
                  "es": ("spanish", "spagnolo"), "ja": ("japanese", "giapponese"),
                  "pt": ("portuguese", "portoghese"), "ko": ("korean", "coreano")}


def product_url(value: str) -> str:
    """Allow only a Cardmarket Pokémon product; discard tracking and unmodelled filters."""
    u = urlsplit(value.strip())
    if (u.scheme != "https" or u.hostname not in {"www.cardmarket.com", "cardmarket.com"}
            or u.username or u.password or u.port not in (None, 443)):
        raise ValueError("Usa un link HTTPS diretto a una carta Pokémon su Cardmarket.")
    if not re.fullmatch(r"/[a-z]{2}/Pokemon/Products(?:/Singles/[^?#]+)?/?", u.path):
        raise ValueError("Il link deve aprire una carta singola Pokémon.")
    pid = parse_qs(u.query).get("idProduct", [None])[0]
    if "/Singles/" not in u.path and not (pid and pid.isdigit() and int(pid) > 0):
        raise ValueError("Nel link manca l'identificativo della carta.")
    # Force English UI for stable labels. Product identity is independent of UI language.
    path = re.sub(r"^/[a-z]{2}/", "/en/", u.path).rstrip("/")
    return urlunsplit(("https", "www.cardmarket.com", path, urlencode({"idProduct": pid}) if pid else "", ""))


def filtered_url(base: str, language: str, condition: str, variant: str) -> str:
    if language not in LANGUAGES or condition not in CONDITIONS or variant not in {"normal", "holo", "reverse"}:
        raise ValueError("Lingua, condizione o variante non supportata.")
    u = urlsplit(product_url(base))
    query = {k: v[0] for k, v in parse_qs(u.query).items()}
    query.update(language=str(LANGUAGES[language][0]), minCondition=str(CONDITIONS[condition]),
                 isReverseHolo="Y" if variant == "reverse" else "N", isSigned="N", isAltered="N",
                 isFirstEd="N", sortBy="price_asc")
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(sorted(query.items())), ""))


def money_cents(text: str) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,3}(?:[.\s\u00a0]\d{3})*|\d+),(\d{2})\s*€\s*", text)
    if not m:
        return None
    try:
        return int(Decimal(re.sub(r"[.\s\u00a0]", "", m[1]) + "." + m[2]) * 100)
    except InvalidOperation:
        return None


def labels(node):
    return " ".join(str(node.get(a, "")) for a in ("aria-label", "title", "data-original-title", "data-bs-original-title", "onmouseover")).lower()


def parse_offers(html: str, language: str, condition: str, variant: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if (soup.select_one('#challenge-form, #cf-challenge-running, .cf-turnstile')
            or "just a moment" in title or "attention required" in title):
        return {"status": "blocked", "message": "Verifica richiesta da Cardmarket."}
    rows = soup.select("div.article-row")
    if not rows:
        # A missing table is not evidence that the price is zero or that the product has no offers.
        return {"status": "unverified", "message": "Tabella offerte non disponibile o formato cambiato."}
    prices = []
    for row in rows:
        cond = row.select_one(".article-condition .badge, .article-condition")
        if not cond or cond.get_text(strip=True).upper() != condition:
            continue
        attrs = row.select(".product-attributes [aria-label], .product-attributes [title], .product-attributes [data-original-title], .product-attributes [onmouseover], span.icon")
        attr_text = " ".join(labels(n) for n in attrs)
        if not any(re.search(r"\b" + re.escape(name) + r"\b", attr_text) for name in LANGUAGE_NAMES[language]):
            continue
        # Reverse/first-edition/signed/altered markers are listing attributes, never seller comments.
        if any(marker in attr_text for marker in ("signed", "altered", "first edition", "first ed.", "1st edition", "firmat", "alterat", "prima edizione")):
            continue
        reverse = "reverse" in attr_text
        if (variant == "reverse") != reverse:
            continue
        # Holo/normal are different products when TCGdex provides a unique mapping. If an
        # explicit listing attribute contradicts that mapping, reject it.
        if variant == "normal" and re.search(r"\b(?:holo|foil)\b", attr_text):
            continue
        tag = row.select_one(".price-container .color-primary, .price-container .fw-bold, .price-container .font-weight-bold")
        cents = money_cents(tag.get_text(" ", strip=True)) if tag else None
        if cents is not None:
            prices.append(cents)
    if not prices:
        return {"status": "no_match", "message": "Nessuna offerta verificabile con questi filtri nella pagina letta."}
    return {"status": "ok", "price_cents": min(prices), "matched_offers": len(prices),
            "message": "Minimo delle offerte verificate nella pagina, spedizione esclusa."}


class CardmarketReader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PriceBotCardsPokemon/2.0", "Accept-Language": "en"})

    def fetch(self, url, language, condition, variant):
        # No retries here: the persistent queue owns every retry and the global cooldown.
        for _ in range(4):
            response = self.session.get(url, timeout=(5, 20), allow_redirects=False)
            if response.status_code in (403, 429, 503):
                return {"status": "blocked", "http_status": response.status_code,
                        "retry_after": response.headers.get("Retry-After"), "message": "Accesso temporaneamente limitato da Cardmarket."}
            if response.is_redirect:
                url = urljoin(url, response.headers.get("Location", ""))
                # Prevent redirects out of the trusted host and to login/challenge endpoints.
                url = filtered_url(url, language, condition, variant)
                continue
            response.raise_for_status()
            if len(response.content) > 8_000_000:
                return {"status": "unverified", "message": "Risposta troppo grande."}
            return parse_offers(response.text, language, condition, variant)
        return {"status": "unverified", "message": "Troppi reindirizzamenti."}
