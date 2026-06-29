import unittest
from unittest.mock import patch

import playlist_loader


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class ExtractPlaylistIdTests(unittest.TestCase):
    def test_playlist_url_returns_id(self):
        self.assertEqual(
            playlist_loader.extract_playlist_id('https://www.youtube.com/playlist?list=PLabc123'),
            'PLabc123',
        )

    def test_watch_url_with_list_param_returns_id(self):
        self.assertEqual(
            playlist_loader.extract_playlist_id('https://www.youtube.com/watch?v=xxxxxxxxxxx&list=PLabc123'),
            'PLabc123',
        )

    def test_url_without_list_param_returns_none(self):
        self.assertIsNone(playlist_loader.extract_playlist_id('https://www.youtube.com/watch?v=xxxxxxxxxxx'))


class FetchPlaylistVideoUrlsTests(unittest.TestCase):
    def test_no_api_key_returns_empty_without_network(self):
        with patch('playlist_loader.requests.get') as mock_get:
            result = playlist_loader.fetch_playlist_video_urls('PLabc123', '')
        mock_get.assert_not_called()
        self.assertEqual(result, [])

    def test_single_page_returns_video_urls(self):
        page = {'items': [{'contentDetails': {'videoId': 'aaaaaaaaaaa'}}, {'contentDetails': {'videoId': 'bbbbbbbbbbb'}}]}
        with patch('playlist_loader.requests.get', return_value=FakeResponse(200, page)) as mock_get:
            result = playlist_loader.fetch_playlist_video_urls('PLabc123', 'key')
        mock_get.assert_called_once()
        self.assertEqual(
            result,
            ['https://www.youtube.com/watch?v=aaaaaaaaaaa', 'https://www.youtube.com/watch?v=bbbbbbbbbbb'],
        )

    def test_pagination_follows_next_page_token(self):
        page1 = {'items': [{'contentDetails': {'videoId': 'aaaaaaaaaaa'}}], 'nextPageToken': 'token2'}
        page2 = {'items': [{'contentDetails': {'videoId': 'bbbbbbbbbbb'}}]}
        with patch('playlist_loader.requests.get', side_effect=[FakeResponse(200, page1), FakeResponse(200, page2)]) as mock_get:
            result = playlist_loader.fetch_playlist_video_urls('PLabc123', 'key')
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(
            result,
            ['https://www.youtube.com/watch?v=aaaaaaaaaaa', 'https://www.youtube.com/watch?v=bbbbbbbbbbb'],
        )

    def test_duplicate_video_ids_are_deduplicated(self):
        page = {'items': [{'contentDetails': {'videoId': 'aaaaaaaaaaa'}}, {'contentDetails': {'videoId': 'aaaaaaaaaaa'}}]}
        with patch('playlist_loader.requests.get', return_value=FakeResponse(200, page)):
            result = playlist_loader.fetch_playlist_video_urls('PLabc123', 'key')
        self.assertEqual(result, ['https://www.youtube.com/watch?v=aaaaaaaaaaa'])

    def test_stops_once_max_videos_reached(self):
        page = {
            'items': [{'contentDetails': {'videoId': f'video{i:06d}'}} for i in range(5)],
            'nextPageToken': 'token2',
        }
        with patch('playlist_loader.requests.get', return_value=page and FakeResponse(200, page)) as mock_get:
            result = playlist_loader.fetch_playlist_video_urls('PLabc123', 'key', max_videos=3)
        mock_get.assert_called_once()
        self.assertEqual(len(result), 3)

    def test_non_200_status_returns_empty(self):
        with patch('playlist_loader.requests.get', return_value=FakeResponse(403)):
            result = playlist_loader.fetch_playlist_video_urls('PLabc123', 'key')
        self.assertEqual(result, [])

    def test_request_exception_returns_collected_so_far(self):
        with patch('playlist_loader.requests.get', side_effect=Exception('network error')):
            result = playlist_loader.fetch_playlist_video_urls('PLabc123', 'key')
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
