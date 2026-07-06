import unittest
from unittest.mock import patch

import ytdlp_extractor


class MatchesTests(unittest.TestCase):
    def test_instagram_reel_matches(self):
        self.assertTrue(ytdlp_extractor.matches('https://www.instagram.com/reel/ABC123/'))

    def test_tiktok_matches(self):
        self.assertTrue(ytdlp_extractor.matches('https://www.tiktok.com/@user/video/123'))

    def test_x_matches(self):
        self.assertTrue(ytdlp_extractor.matches('https://x.com/user/status/123'))

    def test_youtube_does_not_match(self):
        # YouTubeはData API/oEmbedの専用経路を使うため yt-dlp 対象外
        self.assertFalse(ytdlp_extractor.matches('https://www.youtube.com/watch?v=xxx'))

    def test_lookalike_domain_does_not_match(self):
        self.assertFalse(ytdlp_extractor.matches('https://fakeinstagram.com/reel/ABC/'))

    def test_generic_site_does_not_match(self):
        self.assertFalse(ytdlp_extractor.matches('https://example.com/page'))


class FetchTests(unittest.TestCase):
    def test_success_maps_fields(self):
        info = {
            'title': 'リールのキャプション冒頭',
            'description': 'キャプション全文 Direction: 山田太郎',
            'thumbnail': 'https://cdn.example.com/thumb.jpg',
            'uploader': 'karure_official',
        }
        with patch.object(ytdlp_extractor, '_extract_info', return_value=info):
            result = ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC/')
        self.assertEqual(result['title'], 'リールのキャプション冒頭')
        self.assertEqual(result['description'], 'キャプション全文 Direction: 山田太郎')
        self.assertEqual(result['thumbnail'], 'https://cdn.example.com/thumb.jpg')
        self.assertEqual(result['uploader'], 'karure_official')

    def test_missing_title_falls_back_to_uploader(self):
        info = {'title': '', 'uploader': 'karure_official'}
        with patch.object(ytdlp_extractor, '_extract_info', return_value=info):
            result = ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC/')
        self.assertEqual(result['title'], 'karure_official')

    def test_no_title_or_uploader_returns_none(self):
        with patch.object(ytdlp_extractor, '_extract_info', return_value={'title': '', 'uploader': ''}):
            self.assertIsNone(ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC/'))

    def test_extraction_error_returns_none(self):
        with patch.object(ytdlp_extractor, '_extract_info', side_effect=Exception('login required')):
            self.assertIsNone(ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC/'))

    def test_empty_info_returns_none(self):
        with patch.object(ytdlp_extractor, '_extract_info', return_value=None):
            self.assertIsNone(ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC/'))

    def test_missing_optional_fields_default_to_empty(self):
        with patch.object(ytdlp_extractor, '_extract_info', return_value={'title': 'タイトル'}):
            result = ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC/')
        self.assertEqual(result['description'], '')
        self.assertEqual(result['thumbnail'], '')


if __name__ == '__main__':
    unittest.main()
