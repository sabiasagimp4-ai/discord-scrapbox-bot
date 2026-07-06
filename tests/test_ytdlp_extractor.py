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


class ExtractPostIdTests(unittest.TestCase):
    def test_instagram_reel(self):
        self.assertEqual(ytdlp_extractor._extract_post_id('https://www.instagram.com/reel/ABC123xyz/'), 'ABC123xyz')

    def test_instagram_p(self):
        self.assertEqual(ytdlp_extractor._extract_post_id('https://www.instagram.com/p/XYZ789/'), 'XYZ789')

    def test_tiktok_video(self):
        self.assertEqual(ytdlp_extractor._extract_post_id('https://www.tiktok.com/@user/video/1234567890'), '1234567890')

    def test_twitter_status(self):
        self.assertEqual(ytdlp_extractor._extract_post_id('https://x.com/user/status/9876543210'), '9876543210')

    def test_same_url_yields_same_id(self):
        # 同一URLの再投稿は同じIDになる＝既存の重複判定を壊さない
        url = 'https://www.instagram.com/reel/ABC123xyz/'
        self.assertEqual(ytdlp_extractor._extract_post_id(url), ytdlp_extractor._extract_post_id(url))

    def test_query_params_do_not_affect_id(self):
        # パスベースなのでクエリパラメータ違いでも同一投稿と判定できる
        a = ytdlp_extractor._extract_post_id('https://www.instagram.com/reel/ABC123xyz/')
        b = ytdlp_extractor._extract_post_id('https://www.instagram.com/reel/ABC123xyz/?utm_source=ig_web')
        self.assertEqual(a, b)

    def test_unknown_format_falls_back_to_last_path_segment(self):
        self.assertEqual(ytdlp_extractor._extract_post_id('https://www.instagram.com/stories/highlights/999/'), '999')

    def test_no_path_falls_back_to_url_hash(self):
        result = ytdlp_extractor._extract_post_id('https://instagram.com')
        self.assertEqual(len(result), 8)

    def test_different_urls_yield_different_hash_fallback(self):
        a = ytdlp_extractor._extract_post_id('https://instagram.com')
        b = ytdlp_extractor._extract_post_id('https://x.com')
        self.assertNotEqual(a, b)


class FetchTests(unittest.TestCase):
    def test_success_maps_fields_and_appends_post_id(self):
        info = {
            'title': 'リールのキャプション冒頭',
            'description': 'キャプション全文 Direction: 山田太郎',
            'thumbnail': 'https://cdn.example.com/thumb.jpg',
            'uploader': 'karure_official',
        }
        with patch.object(ytdlp_extractor, '_extract_info', return_value=info):
            result = ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC123/')
        self.assertEqual(result['title'], 'リールのキャプション冒頭 (ABC123)')
        self.assertEqual(result['description'], 'キャプション全文 Direction: 山田太郎')
        self.assertEqual(result['thumbnail'], 'https://cdn.example.com/thumb.jpg')
        self.assertEqual(result['uploader'], 'karure_official')

    def test_generic_title_from_same_uploader_no_longer_collides(self):
        # 実際のバグ: キャプション未取得で全動画が "Video by uploader" になり、
        # 同一投稿者の別動画がタイトル完全一致で「重複」扱いされ保存されなかった。
        info1 = {'title': 'Video by karure_official', 'uploader': 'karure_official'}
        info2 = {'title': 'Video by karure_official', 'uploader': 'karure_official'}
        with patch.object(ytdlp_extractor, '_extract_info', return_value=info1):
            result1 = ytdlp_extractor.fetch('https://www.instagram.com/reel/AAA111/')
        with patch.object(ytdlp_extractor, '_extract_info', return_value=info2):
            result2 = ytdlp_extractor.fetch('https://www.instagram.com/reel/BBB222/')
        self.assertNotEqual(result1['title'], result2['title'])

    def test_missing_title_falls_back_to_uploader_then_appends_id(self):
        info = {'title': '', 'uploader': 'karure_official'}
        with patch.object(ytdlp_extractor, '_extract_info', return_value=info):
            result = ytdlp_extractor.fetch('https://www.instagram.com/reel/ABC123/')
        self.assertEqual(result['title'], 'karure_official (ABC123)')

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
