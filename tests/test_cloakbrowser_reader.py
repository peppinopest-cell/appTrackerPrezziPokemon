import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch
from cloakbrowser_reader import CloakBrowserReader, ReaderError

URL = 'https://www.cardmarket.com/it/Pokemon/Products/Singles/Set/Card?language=5&minCondition=2'

class ReaderTests(unittest.TestCase):
    def test_missing_key_does_not_launch(self):
        with patch.dict(os.environ, {'CLOAKBROWSER_LICENSE_KEY': ''}), patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ReaderError, 'non configurato'):
                CloakBrowserReader().read(URL)
            launch.assert_not_called()

    def test_verified_price_and_no_key_in_command(self):
        data = {'price': 22.0, 'image': '', 'condition': 'NM', 'language': 'Italiano'}
        process = Mock()
        process.communicate.return_value = ('CLOAK_RESULT=' + json.dumps({'status': 'ok', 'data': data}), None)
        with patch.dict(os.environ, {'CLOAKBROWSER_LICENSE_KEY': 'test-key'}), patch('subprocess.Popen', return_value=process) as launch:
            self.assertEqual(CloakBrowserReader().read(URL), data)
            self.assertNotIn('test-key', str(launch.call_args))

    def test_access_denial_returns_http_cause(self):
        process = Mock()
        process.communicate.return_value = ('CLOAK_RESULT={"status":"access_denied","http_status":401}', None)
        with patch.dict(os.environ, {'CLOAKBROWSER_LICENSE_KEY': 'test-key'}), patch('subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(ReaderError, 'HTTP 401'):
                CloakBrowserReader().read(URL)

    def test_timeout_terminates_process_and_releases_lock(self):
        process = Mock(pid=987654)
        process.communicate.side_effect = [subprocess.TimeoutExpired('test', 1), ('', None)]
        reader = CloakBrowserReader(timeout=1)
        with patch.dict(os.environ, {'CLOAKBROWSER_LICENSE_KEY': 'test-key'}), patch('subprocess.Popen', return_value=process), patch('os.killpg', create=True) as kill:
            with self.assertRaisesRegex(ReaderError, 'non ha risposto'):
                reader.read(URL)
            if os.name != 'nt':
                kill.assert_called_once()
            else:
                process.kill.assert_called_once()
            self.assertFalse(reader.lock.locked())

if __name__ == '__main__':
    unittest.main()
