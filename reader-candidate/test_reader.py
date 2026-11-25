import unittest
from cardmarket_reader import parse_page

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'
def row(price, condition='NM', language='Italiano'):
    return f'<div class="article-row"><a class="article-condition"><span class="badge">{condition}</span></a><span class="icon" aria-label="{language}"></span><div class="price-container"><span class="color-primary">{price} €</span></div></div>'

class ReaderTests(unittest.TestCase):
    def test_only_matching_listing_prices(self):
        html = '<dd>0,01 €</dd>' + row('22,00') + row('1,00', 'EX') + row('2,00', language='English') + row('23,00', 'MT')
        result = parse_page(html, URL)
        self.assertEqual(result['price_cents'], 2200)
        self.assertEqual(result['matched_offers'], 2)

    def test_guide_price_does_not_become_offer(self):
        self.assertEqual(parse_page('<dd>24,59 €</dd>', URL)['status'], 'no_verified_offers')

    def test_challenge_has_no_price(self):
        result = parse_page('<title>Just a moment...</title>' + row('22,00'), URL, 403)
        self.assertEqual(result['status'], 'challenge_required')
        self.assertIsNone(result['price'])

    def test_unauthorized_has_no_price(self):
        self.assertEqual(parse_page(row('22,00'), URL, 401)['status'], 'auth_required')

    def test_invalid_host(self):
        with self.assertRaises(ValueError):
            parse_page('', 'https://cardmarket.com.example.com/it/Pokemon/Products/Singles/X/Y')

if __name__ == '__main__':
    unittest.main()
