import json
from cardmarket_reader import read_browser
url = "https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2"
print("CLOUD_READER_RESULT=" + json.dumps(read_browser(url), ensure_ascii=True), flush=True)
