import importlib.util
import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from flaresolverr_reader import ReaderError

class BotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_price_changes_and_closed_app(self):
        spec = importlib.util.spec_from_file_location('bot_integration_test', 'app.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'DB_PATH': ':memory:', 'FLARESOLVERR_URL': 'http://127.0.0.1:8191'}), patch('threading.Thread.start'):
            spec.loader.exec_module(module)
        price = {'price': 22.0, 'image': '', 'condition': 'NM', 'language': 'Italiano'}
        with patch.object(module.solver_reader, 'read', return_value=price) as reader, patch.object(module, 'send_telegram_message') as telegram, patch.object(module.time, 'sleep'):
            await module.save_settings(module.UserSettings(user_id='test', bot_token='test', chat_id='test', check_interval=1))
            telegram.reset_mock()
            url = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/30th-Celebration/Rayquaza-EX-30CDRX-85?language=5&minCondition=2'
            added = await module.add_watch(module.WatchItem(user_id='test', card_url=url))
            self.assertEqual(added['prezzo'], 22.0)
            self.assertEqual(telegram.call_count, 1)
            module.active_users.clear()
            reader.return_value = {**price, 'price': 23.0}
            module.job_check_prices()
            self.assertEqual(telegram.call_count, 2)
            self.assertEqual((await module.get_watchlist('test'))[0]['last_price'], 23.0)
            module.job_check_prices()
            self.assertEqual(telegram.call_count, 2)
            reader.side_effect = ReaderError('Accesso negato')
            module.job_check_prices()
            self.assertEqual((await module.get_watchlist('test'))[0]['last_price'], 23.0)
            self.assertEqual(telegram.call_count, 2)
            with self.assertRaises(HTTPException) as error:
                await module.add_watch(module.WatchItem(user_id='test', card_url=url))
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(len(await module.get_watchlist('test')), 1)
        module.conn.close()

if __name__ == '__main__':
    unittest.main()
