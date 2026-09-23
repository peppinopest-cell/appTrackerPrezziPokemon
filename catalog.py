"""Public catalog only; aggregate catalog prices are deliberately not used for valuation."""
import re
import threading
import requests
from fastapi import HTTPException

CATALOG_LANGUAGES = {"en", "it", "fr", "de", "es", "pt", "ja", "ko"}
catalog_lock = threading.Lock()


def fetch_catalog(path):
    try:
        r = requests.get("https://api.tcgdex.net/v2/" + path, timeout=(5, 25))
        if r.status_code == 404:
            raise HTTPException(404, "Carta o espansione non disponibile in questo catalogo.")
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(503, "Catalogo temporaneamente non disponibile. Riprova più tardi.")


def validate_path(lang, kind, item_id=None):
    if lang not in CATALOG_LANGUAGES or kind not in {"sets", "cards"}:
        raise HTTPException(422, "Catalogo non supportato.")
    if item_id and not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", item_id):
        raise HTTPException(422, "Identificativo non valido.")
    return f"{lang}/{kind}" + (f"/{item_id}" if item_id else "")


def card_options(card):
    options = []
    details = card.get("variants_detailed") or []
    for v in details:
        kind = v.get("type")
        if kind not in {"normal", "holo", "reverse"} or v.get("size", "standard") != "standard":
            continue
        if v.get("stamp") or v.get("firstEdition"):
            continue
        product_id = (v.get("thirdParty") or {}).get("cardmarket")
        if not isinstance(product_id, int) or product_id <= 0:
            continue
        options.append({"id": v.get("variantId") or f"{kind}-{product_id}", "variant": kind,
                        "product_id": product_id, "label": {"normal": "Normale", "holo": "Holo", "reverse": "Reverse holo"}[kind],
                        "url": f"https://www.cardmarket.com/en/Pokemon/Products?idProduct={product_id}"})
    # A shared normal/holo ID does not prove that the two finishes can be distinguished.
    ambiguous = {o["product_id"] for o in options if o["variant"] == "normal"} & {o["product_id"] for o in options if o["variant"] == "holo"}
    return [o for o in options if o["product_id"] not in ambiguous]
