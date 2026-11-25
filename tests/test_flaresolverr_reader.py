import unittest
from unittest.mock import patch
from flaresolverr_reader import FlareSolverrReader, ReaderError

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'
HTML = '''<meta property="og:image" content="https://static.cardmarket.com/card.jpg"><div class="article-row"><div class="product-attributes"><a class="article-condition"><span class="badge">NM</span></a><span aria-label="Italiano"></span></div><div class="price-container"><span class="color-primary">22,00 €</span></div></div>'''

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = FlareSolverrReader('http://127.0.0.1:8191')
        self.addCleanup(lambda: setattr(self.reader, 'session', None))

    def test_session_reused_and_listing_metadata_preserved(self):
        with patch.object(self.reader, 'command', side_effect=[{'session': 'anonymous'}, {},
                {'solution': {'url': URL, 'status': 200, 'response': HTML}},
                {'solution': {'url': URL, 'status': 200, 'response': HTML}}]) as command:
            result = self.reader.read(URL)
            self.assertEqual(result['price'], 22.0)
            self.assertEqual(result['language'], 'Italiano')
            self.assertEqual(result['condition'], 'NM')
            self.assertTrue(result['image'].endswith('card.jpg'))
            self.reader.read(URL)
            self.assertEqual([c.args[0] for c in command.call_args_list].count('sessions.create'), 1)
            self.assertEqual(command.call_args_list[-1].kwargs['session'], 'anonymous')

    def test_solver_200_without_offers_is_failure_with_cooldown(self):
        with patch.object(self.reader, 'command', side_effect=[{'session': 'anonymous'}, {},
                {'solution': {'url': URL, 'status': 200, 'response': 'Please login to continue'}}, {}]) as command:
            with self.assertRaises(ReaderError):
                self.reader.read(URL)
            count = command.call_count
            with self.assertRaises(ReaderError):
                self.reader.read(URL)
            self.assertEqual(command.call_count, count)
            self.assertIsNone(self.reader.session)

    def test_redirect_to_login_not_treated_as_product(self):
        with patch.object(self.reader, 'command', side_effect=[{'session': 'anonymous'}, {},
                {'solution': {'url': 'https://www.cardmarket.com/it/Pokemon/Login', 'status': 200, 'response': '<form></form>'}}, {}]):
            with self.assertRaisesRegex(ReaderError, 'reindirizzato'):
                self.reader.read(URL)

    def test_unrelated_url_never_sent_to_solver(self):
        with patch.object(self.reader, 'command') as command:
            with self.assertRaises(ValueError):
                self.reader.read('https://example.com/')
            command.assert_not_called()

    def test_service_unavailable_is_explicit_error(self):
        import requests
        with patch('flaresolverr_reader.requests.post', side_effect=requests.ConnectionError):
            with self.assertRaisesRegex(ReaderError, 'non raggiungibile'):
                self.reader.read(URL)

    def test_embedded_engine_does_not_use_network_api(self):
        import sys
        from types import SimpleNamespace
        reader = FlareSolverrReader('embedded')
        from unittest.mock import Mock
        driver = Mock()
        driver.execute_script.return_value = 'Chrome'
        service = SimpleNamespace(controller_v1_endpoint=lambda request: {'status': 'ok', 'session': 'test'},
                                  SESSIONS_STORAGE=SimpleNamespace(get=lambda session: (SimpleNamespace(driver=driver), False)))
        utils = SimpleNamespace(get_current_platform=Mock(), get_user_agent=Mock(), object_to_dict=lambda value: value)
        with patch.dict(sys.modules, {'flaresolverr_service': service, 'utils': utils, 'dtos': SimpleNamespace(V1RequestBase=lambda value: value)}), patch('flaresolverr_reader.requests.post') as post:
            self.assertEqual(reader.command('sessions.create')['session'], 'test')
            reader.command('sessions.list')
            utils.get_user_agent.assert_not_called()
            self.assertEqual(utils.USER_AGENT, 'Chrome')
            post.assert_not_called()

    def test_lightweight_entry_does_not_render_login_form(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        reader = FlareSolverrReader('embedded')
        reader.session = 'test'
        self.addCleanup(lambda: setattr(reader, 'session', None))
        driver = Mock()
        driver.execute_async_script.return_value = {'status': 200}
        reader.embedded_api = (SimpleNamespace(SESSIONS_STORAGE=SimpleNamespace(get=lambda session: (SimpleNamespace(driver=driver), False))), None, None)
        reader.prime_anonymous_session('it')
        driver.get.assert_called_once_with('https://www.cardmarket.com/robots.txt')
        self.assertIn('fetch(', driver.execute_async_script.call_args.args[0])

if __name__ == '__main__':
    unittest.main()
