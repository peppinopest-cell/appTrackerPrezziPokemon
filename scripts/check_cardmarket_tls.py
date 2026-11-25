import json
from curl_cffi import requests
from bs4 import BeautifulSoup
from pricing import parse_offers
url = "https://www.cardmarket.com/en/Pokemon/Products/Singles/Darkness-Ablaze/Furret-DAA136?language=5&minCondition=2"
try:
    with requests.Session(impersonate="chrome120") as session:
        response = session.get(url, timeout=25, allow_redirects=False)
        soup = BeautifulSoup(response.text, "html.parser")
        result = {"profile":"chrome120", "http_status":response.status_code, "title":soup.title.get_text(strip=True) if soup.title else ""}
        if response.status_code == 200:
            parsed = parse_offers(response.text, "it", "NM", "normal")
            result.update({k:parsed.get(k) for k in ("status","price_cents","matched_offers")})
        else:
            result["status"] = "access_refused"
except Exception as exc:
    result = {"status":"diagnostic_error", "exception_type":type(exc).__name__}
print("TLS_CARDMARKET_DIAGNOSTIC=" + json.dumps(result, ensure_ascii=True), flush=True)
