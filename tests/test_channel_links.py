import json
import unittest
from unittest.mock import patch

import channel_links


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=''):
        self._json_data = json_data or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json_data


class ParseLinksTests(unittest.TestCase):
    def test_parses_valid_lines(self):
        lines = [' 123 | MV案件X', ' 456 | MV案件Y']
        self.assertEqual(channel_links.parse_links(lines), {123: 'MV案件X', 456: 'MV案件Y'})

    def test_skips_invalid_lines(self):
        lines = ['', 'ただのメモ', ' abc | ページ', ' 123 |', ' 123 | 有効ページ']
        self.assertEqual(channel_links.parse_links(lines), {123: '有効ページ'})

    def test_title_containing_pipe_is_preserved(self):
        # partition なので最初の | 以降がすべてタイトルになる
        lines = [' 123 | 案件 | 特別版']
        self.assertEqual(channel_links.parse_links(lines), {123: '案件 | 特別版'})

    def test_empty_input_returns_empty(self):
        self.assertEqual(channel_links.parse_links([]), {})


class SerializeLinksTests(unittest.TestCase):
    def test_round_trip(self):
        links = {123: 'MV案件X', 456: 'MV案件Y'}
        lines = channel_links.serialize_links(links)
        self.assertEqual(lines[0], channel_links.LINKS_PAGE_TITLE)
        self.assertEqual(channel_links.parse_links(lines[1:]), links)


class LoadLinksTests(unittest.TestCase):
    def test_loads_and_parses_page(self):
        data = {'persistent': True, 'lines': [
            {'text': channel_links.LINKS_PAGE_TITLE}, {'text': ' 123 | MV案件X'},
        ]}
        with patch('channel_links.requests.get', return_value=FakeResponse(data)):
            self.assertEqual(channel_links.load_links('proj', 'sid'), {123: 'MV案件X'})

    def test_missing_page_returns_empty_dict(self):
        with patch('channel_links.requests.get', return_value=FakeResponse(status_code=404)):
            self.assertEqual(channel_links.load_links('proj', 'sid'), {})

    def test_non_persistent_page_returns_empty_dict(self):
        data = {'persistent': False, 'lines': []}
        with patch('channel_links.requests.get', return_value=FakeResponse(data)):
            self.assertEqual(channel_links.load_links('proj', 'sid'), {})

    def test_network_failure_returns_none(self):
        with patch('channel_links.requests.get', side_effect=Exception('timeout')):
            self.assertIsNone(channel_links.load_links('proj', 'sid'))


class SaveLinksTests(unittest.TestCase):
    def test_posts_serialized_page(self):
        with patch('channel_links.requests.post', return_value=FakeResponse(status_code=200, text='ok')) as mock_post:
            status, _ = channel_links.save_links('proj', 'sid', {123: 'MV案件X'})
        self.assertEqual(status, 200)
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        self.assertEqual(payload['pages'][0]['title'], channel_links.LINKS_PAGE_TITLE)
        self.assertIn(' 123 | MV案件X', payload['pages'][0]['lines'])

    def test_network_failure_returns_none_status(self):
        with patch('channel_links.requests.post', side_effect=Exception('boom')):
            status, body = channel_links.save_links('proj', 'sid', {})
        self.assertIsNone(status)
        self.assertEqual(body, 'boom')


if __name__ == '__main__':
    unittest.main()
