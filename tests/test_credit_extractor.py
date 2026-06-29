import unittest
from unittest.mock import patch

import credit_extractor


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=''):
        self._json_data = json_data or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json_data


def _openrouter_response(content):
    return FakeResponse({'choices': [{'message': {'content': content}}]})


class ExtractCreditsTests(unittest.TestCase):
    def test_no_api_key_returns_empty_list_without_network(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', ''):
            with patch('credit_extractor.requests.post') as mock_post:
                result = credit_extractor.extract_credits('監督: 山田太郎')
        mock_post.assert_not_called()
        self.assertEqual(result, [])

    def test_no_description_returns_empty_list_without_network(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post') as mock_post:
                result = credit_extractor.extract_credits('')
        mock_post.assert_not_called()
        self.assertEqual(result, [])

    def test_successful_extraction_parses_credits(self):
        content = 'Direction: 山田太郎\nIllustration: 鈴木花子'
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response(content)):
                result = credit_extractor.extract_credits('監督: 山田太郎')
        self.assertEqual(
            result,
            [{'role': 'Direction', 'name': '山田太郎'}, {'role': 'Illustration', 'name': '鈴木花子'}],
        )

    def test_full_width_colon_is_supported(self):
        content = 'Direction：山田太郎'
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response(content)):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [{'role': 'Direction', 'name': '山田太郎'}])

    def test_nashi_response_returns_empty_list(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response('なし')):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [])

    def test_lines_without_colon_are_ignored(self):
        content = 'よろしくお願いします\nDirection: 山田太郎'
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response(content)):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [{'role': 'Direction', 'name': '山田太郎'}])

    def test_request_exception_returns_empty_list(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', side_effect=Exception('timeout')):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [])


class ExtractCreditsDebugTests(unittest.TestCase):
    def test_no_api_key_returns_error_without_network(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', ''):
            with patch('credit_extractor.requests.post') as mock_post:
                credits, raw_response, error = credit_extractor.extract_credits_debug('監督: 山田太郎')
        mock_post.assert_not_called()
        self.assertEqual(credits, [])
        self.assertIsNone(raw_response)
        self.assertEqual(error, 'OPENROUTER_API_KEYが未設定です')

    def test_no_description_returns_error_without_network(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post') as mock_post:
                credits, raw_response, error = credit_extractor.extract_credits_debug('')
        mock_post.assert_not_called()
        self.assertEqual(credits, [])
        self.assertIsNone(raw_response)
        self.assertEqual(error, '概要欄が空です')

    def test_successful_extraction_returns_credits_and_raw_response(self):
        content = 'Direction: 山田太郎'
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response(content)):
                credits, raw_response, error = credit_extractor.extract_credits_debug('監督: 山田太郎')
        self.assertEqual(credits, [{'role': 'Direction', 'name': '山田太郎'}])
        self.assertEqual(raw_response, content)
        self.assertIsNone(error)

    def test_nashi_response_returns_empty_credits_without_error(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response('なし')):
                credits, raw_response, error = credit_extractor.extract_credits_debug('説明')
        self.assertEqual(credits, [])
        self.assertEqual(raw_response, 'なし')
        self.assertIsNone(error)

    def test_system_prompt_and_user_content_are_sent(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response('なし')) as mock_post:
                credit_extractor.extract_credits_debug('概要欄の本文')
        sent_messages = mock_post.call_args.kwargs['json']['messages']
        self.assertEqual(sent_messages[0]['role'], 'system')
        self.assertEqual(sent_messages[1], {'role': 'user', 'content': '概要欄の本文'})

    def test_non_200_status_returns_error_with_body(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=FakeResponse(status_code=401, text='unauthorized')):
                credits, raw_response, error = credit_extractor.extract_credits_debug('説明')
        self.assertEqual(credits, [])
        self.assertIsNone(raw_response)
        self.assertIn('401', error)
        self.assertIn('unauthorized', error)

    def test_malformed_response_returns_error_with_raw_text(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=FakeResponse({'unexpected': 'shape'}, text='raw body')):
                credits, raw_response, error = credit_extractor.extract_credits_debug('説明')
        self.assertEqual(credits, [])
        self.assertEqual(raw_response, 'raw body')
        self.assertEqual(error, 'レスポンスの形式が想定と異なります')

    def test_request_exception_returns_error_without_raw_response(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', side_effect=Exception('timeout')):
                credits, raw_response, error = credit_extractor.extract_credits_debug('説明')
        self.assertEqual(credits, [])
        self.assertIsNone(raw_response)
        self.assertEqual(error, 'リクエストエラー: timeout')


class CheckConnectionTests(unittest.TestCase):
    def test_no_api_key_returns_none_without_network(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', ''):
            with patch('credit_extractor.requests.get') as mock_get:
                ok, message = credit_extractor.check_connection()
        mock_get.assert_not_called()
        self.assertIsNone(ok)
        self.assertEqual(message, '未設定')

    def test_status_200_returns_ok(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.get', return_value=FakeResponse(status_code=200)):
                ok, message = credit_extractor.check_connection()
        self.assertTrue(ok)
        self.assertEqual(message, '接続OK')

    def test_other_status_returns_false(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.get', return_value=FakeResponse(status_code=401)):
                ok, message = credit_extractor.check_connection()
        self.assertFalse(ok)

    def test_request_exception_returns_false(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.get', side_effect=Exception('network error')):
                ok, message = credit_extractor.check_connection()
        self.assertFalse(ok)
        self.assertEqual(message, 'network error')


if __name__ == '__main__':
    unittest.main()
