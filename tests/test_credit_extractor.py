import unittest
from unittest.mock import patch

import credit_extractor


class FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

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
        content = '{"credits": [{"role": "Direction", "name": "山田太郎"}]}'
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response(content)):
                result = credit_extractor.extract_credits('監督: 山田太郎')
        self.assertEqual(result, [{'role': 'Direction', 'name': '山田太郎'}])

    def test_credits_missing_name_or_role_are_filtered_out(self):
        content = '{"credits": [{"role": "Direction"}, {"name": "鈴木花子"}, {"role": "X", "name": "Y"}]}'
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response(content)):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [{'role': 'X', 'name': 'Y'}])

    def test_response_without_json_object_returns_empty_list(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response('no json here')):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [])

    def test_invalid_json_returns_empty_list(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', return_value=_openrouter_response('{not valid json}')):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [])

    def test_request_exception_returns_empty_list(self):
        with patch.object(credit_extractor, 'OPENROUTER_API_KEY', 'sk-or-test'):
            with patch('credit_extractor.requests.post', side_effect=Exception('timeout')):
                result = credit_extractor.extract_credits('説明')
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
