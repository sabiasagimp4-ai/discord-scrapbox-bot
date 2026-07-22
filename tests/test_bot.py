import json
import unittest
from unittest.mock import patch

import bot


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=''):
        self._json_data = json_data or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json_data


class NormalizeTitleTests(unittest.TestCase):
    def test_collapses_whitespace_and_newlines(self):
        self.assertEqual(bot._normalize_title('動画\nタイトル  です'), '動画 タイトル です')

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(bot._normalize_title('  タイトル  '), 'タイトル')


class ExtractOgImageTests(unittest.TestCase):
    def test_property_before_content(self):
        html = '<meta property="og:image" content="https://example.com/a.png">'
        self.assertEqual(bot._extract_og_image(html), 'https://example.com/a.png')

    def test_content_before_property(self):
        html = '<meta content="https://example.com/b.png" property="og:image">'
        self.assertEqual(bot._extract_og_image(html), 'https://example.com/b.png')

    def test_no_match_returns_empty_string(self):
        self.assertEqual(bot._extract_og_image('<html></html>'), '')


class FormatStatusLineTests(unittest.TestCase):
    def test_ok_true_shows_check_mark(self):
        self.assertEqual(bot._format_status_line('Scrapbox', (True, '接続OK')), '✅ Scrapbox: 接続OK')

    def test_ok_false_shows_cross_mark(self):
        self.assertEqual(bot._format_status_line('Scrapbox', (False, 'エラー')), '❌ Scrapbox: エラー')

    def test_ok_none_shows_skip_mark(self):
        self.assertEqual(bot._format_status_line('Gyazo', (None, '未設定')), '⏭️ Gyazo: 未設定')


class FormatErrorReplyTests(unittest.TestCase):
    def test_403_returns_cookie_expired_message(self):
        message = bot._format_error_reply(403, 'forbidden')
        self.assertIn('Cookie', message)

    def test_other_status_includes_body(self):
        message = bot._format_error_reply(500, 'server error')
        self.assertIn('500', message)
        self.assertIn('server error', message)


class FetchMetadataTests(unittest.TestCase):
    def test_youtube_data_api_success(self):
        response = FakeResponse({'items': [{'snippet': {'title': '動画タイトル', 'description': '概要欄'}}]})
        with patch.object(bot, 'YOUTUBE_API_KEY', 'AIza-test'):
            with patch('bot.requests.get', return_value=response) as mock_get:
                result = bot.fetch_metadata('https://www.youtube.com/watch?v=xxxxxxxxxxx')
        mock_get.assert_called_once()
        self.assertEqual(result['title'], '動画タイトル')
        self.assertEqual(result['description'], '概要欄')
        self.assertEqual(result['source'], 'YouTube Data API')

    def test_youtube_data_api_error_falls_back_to_oembed(self):
        oembed_response = FakeResponse({'title': 'oEmbedタイトル'}, status_code=200)
        with patch.object(bot, 'YOUTUBE_API_KEY', 'AIza-test'):
            with patch('bot.requests.get', side_effect=[Exception('timeout'), oembed_response]) as mock_get:
                result = bot.fetch_metadata('https://youtu.be/xxxxxxxxxxx')
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(result['title'], 'oEmbedタイトル')
        self.assertEqual(result['description'], '')
        self.assertIn('oEmbed', result['source'])

    def test_youtube_no_api_key_uses_oembed_directly(self):
        oembed_response = FakeResponse({'title': 'oEmbedタイトル'}, status_code=200)
        with patch.object(bot, 'YOUTUBE_API_KEY', ''):
            with patch('bot.requests.get', return_value=oembed_response) as mock_get:
                result = bot.fetch_metadata('https://youtu.be/xxxxxxxxxxx')
        mock_get.assert_called_once()
        self.assertEqual(result['title'], 'oEmbedタイトル')

    def test_vimeo_success(self):
        response = FakeResponse({'title': 'Vimeoタイトル', 'description': 'Vimeo概要'}, status_code=200)
        with patch('bot.requests.get', return_value=response):
            result = bot.fetch_metadata('https://vimeo.com/12345')
        self.assertEqual(result['title'], 'Vimeoタイトル')
        self.assertEqual(result['description'], 'Vimeo概要')
        self.assertEqual(result['source'], 'Vimeo oEmbed')

    def test_generic_html_title_and_og_image(self):
        html = '<html><head><title>汎用サイト</title><meta property="og:image" content="https://example.com/img.png"></head></html>'
        response = FakeResponse(status_code=200, text=html)
        with patch('bot.requests.get', return_value=response):
            result = bot.fetch_metadata('https://example.com/article')
        self.assertEqual(result['title'], '汎用サイト')
        self.assertEqual(result['thumbnail'], 'https://example.com/img.png')
        self.assertIn('HTML', result['source'])

    def test_all_requests_fail_returns_netloc_title(self):
        with patch('bot.requests.get', side_effect=Exception('network error')):
            result = bot.fetch_metadata('https://example.com/article')
        self.assertEqual(result['title'], 'example.com')
        self.assertEqual(result['source'], '取得失敗')

    def test_instagram_url_uses_ytdlp(self):
        info = {'title': 'リール タイトル', 'description': 'キャプション', 'thumbnail': 'https://t.example/1.jpg', 'uploader': 'user'}
        with patch('bot.ytdlp_extractor.fetch', return_value=info) as mock_fetch:
            with patch('bot.requests.get') as mock_get:
                result = bot.fetch_metadata('https://www.instagram.com/reel/ABC123/')
        mock_fetch.assert_called_once()
        mock_get.assert_not_called()
        self.assertEqual(result['title'], 'リール タイトル')
        self.assertEqual(result['description'], 'キャプション')
        self.assertEqual(result['thumbnail'], 'https://t.example/1.jpg')
        self.assertEqual(result['source'], 'yt-dlp')

    def test_instagram_ytdlp_failure_falls_back_to_html(self):
        html = '<html><head><title>Instagramのページ</title></head></html>'
        with patch('bot.ytdlp_extractor.fetch', return_value=None):
            with patch('bot.requests.get', return_value=FakeResponse(status_code=200, text=html)):
                result = bot.fetch_metadata('https://www.instagram.com/reel/ABC123/')
        self.assertEqual(result['title'], 'Instagramのページ')
        self.assertIn('HTML', result['source'])


class CheckYoutubeConnectionTests(unittest.TestCase):
    def test_no_api_key_returns_none(self):
        with patch.object(bot, 'YOUTUBE_API_KEY', ''):
            with patch('bot.requests.get') as mock_get:
                ok, message = bot.check_youtube_connection()
        mock_get.assert_not_called()
        self.assertIsNone(ok)
        self.assertEqual(message, '未設定')

    def test_status_200_returns_ok(self):
        with patch.object(bot, 'YOUTUBE_API_KEY', 'AIza-test'):
            with patch('bot.requests.get', return_value=FakeResponse(status_code=200)):
                ok, message = bot.check_youtube_connection()
        self.assertTrue(ok)

    def test_other_status_returns_false(self):
        with patch.object(bot, 'YOUTUBE_API_KEY', 'AIza-test'):
            with patch('bot.requests.get', return_value=FakeResponse(status_code=403)):
                ok, message = bot.check_youtube_connection()
        self.assertFalse(ok)

    def test_request_exception_returns_false(self):
        with patch.object(bot, 'YOUTUBE_API_KEY', 'AIza-test'):
            with patch('bot.requests.get', side_effect=Exception('boom')):
                ok, message = bot.check_youtube_connection()
        self.assertFalse(ok)
        self.assertEqual(message, 'boom')


class ExpandUrlsTests(unittest.TestCase):
    def test_non_playlist_urls_pass_through_deduped(self):
        with patch('bot.playlist_loader.extract_playlist_id', return_value=None):
            result = bot.expand_urls(['https://a.com', 'https://a.com', 'https://b.com'])
        self.assertEqual(result, ['https://a.com', 'https://b.com'])

    def test_playlist_url_expands_to_video_urls(self):
        with patch('bot.playlist_loader.extract_playlist_id', return_value='PLabc'):
            with patch('bot.playlist_loader.fetch_playlist_video_urls', return_value=['https://v1', 'https://v2']):
                result = bot.expand_urls(['https://youtube.com/playlist?list=PLabc'])
        self.assertEqual(result, ['https://v1', 'https://v2'])

    def test_empty_expansion_falls_back_to_original_url(self):
        with patch('bot.playlist_loader.extract_playlist_id', return_value='PLabc'):
            with patch('bot.playlist_loader.fetch_playlist_video_urls', return_value=[]):
                result = bot.expand_urls(['https://youtube.com/playlist?list=PLabc'])
        self.assertEqual(result, ['https://youtube.com/playlist?list=PLabc'])


class SaveToScrapboxTests(unittest.TestCase):
    def setUp(self):
        self._recently_saved_titles_patch = patch.object(bot, '_recently_saved_titles', set())
        self._recently_saved_titles_patch.start()
        self.addCleanup(self._recently_saved_titles_patch.stop)
        audit_patch = patch.object(bot, '_audit')
        self.audit_mock = audit_patch.start()
        self.addCleanup(audit_patch.stop)

    def test_duplicate_page_skips_post(self):
        with patch.object(bot, 'fetch_metadata', return_value={'title': '既存記事', 'description': '', 'thumbnail': ''}):
            with patch('bot.name_linker.check_page_exists', return_value=True):
                with patch('bot.requests.post') as mock_post:
                    status, body, title, thumbnail = bot.save_to_scrapbox('https://example.com')
        mock_post.assert_not_called()
        self.assertEqual(status, 'duplicate')
        self.assertEqual(title, '既存記事')

    def test_successful_save_records_title_and_returns_200(self):
        metadata = {'title': '新規記事', 'description': '', 'thumbnail': ''}
        with patch.object(bot, 'fetch_metadata', return_value=metadata):
            with patch('bot.name_linker.check_page_exists', return_value=False):
                with patch.object(bot, 'credit_extractor') as mock_credit_extractor:
                    mock_credit_extractor.extract_credits.return_value = []
                    with patch('bot.requests.post', return_value=FakeResponse(status_code=200, text='ok')):
                        status, body, title, thumbnail = bot.save_to_scrapbox('https://example.com')
        self.assertEqual(status, 200)
        self.assertEqual(title, '新規記事')
        self.assertIn('新規記事', bot._recently_saved_titles)

    def test_failed_save_does_not_record_title(self):
        metadata = {'title': '失敗記事', 'description': '', 'thumbnail': ''}
        with patch.object(bot, 'fetch_metadata', return_value=metadata):
            with patch('bot.name_linker.check_page_exists', return_value=False):
                with patch.object(bot, 'credit_extractor') as mock_credit_extractor:
                    mock_credit_extractor.extract_credits.return_value = []
                    with patch('bot.requests.post', return_value=FakeResponse(status_code=500, text='error')):
                        status, body, title, thumbnail = bot.save_to_scrapbox('https://example.com')
        self.assertEqual(status, 500)
        self.assertNotIn('失敗記事', bot._recently_saved_titles)

    def test_credits_are_resolved_and_appended_as_lines(self):
        metadata = {'title': 'クレジット記事', 'description': '概要', 'thumbnail': ''}
        with patch.object(bot, 'fetch_metadata', return_value=metadata):
            with patch('bot.name_linker.check_page_exists', return_value=False):
                with patch.object(bot, 'credit_extractor') as mock_credit_extractor:
                    mock_credit_extractor.extract_credits.return_value = [{'role': 'Direction', 'name': '山田太郎'}]
                    with patch.object(bot, 'get_existing_pages', return_value=['山田太郎']):
                        with patch.object(bot, 'get_alias_map', return_value={}):
                            with patch('bot.requests.post', return_value=FakeResponse(status_code=200)) as mock_post:
                                bot.save_to_scrapbox('https://example.com')
        sent_payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        self.assertIn(' Direction: [山田太郎]', sent_payload['pages'][0]['lines'])


class WritePageToScrapboxTests(unittest.TestCase):
    def setUp(self):
        bot._recently_saved_titles.clear()
        audit_patch = patch.object(bot, '_audit')
        self.audit_mock = audit_patch.start()
        self.addCleanup(audit_patch.stop)

    def tearDown(self):
        bot._recently_saved_titles.clear()

    @patch('bot.requests.post')
    def test_success_returns_200_and_records_title(self, mock_post):
        mock_post.return_value = FakeResponse(status_code=200, text='ok')
        status, _ = bot.write_page_to_scrapbox('新しいページ', '本文1行目\n2行目')
        self.assertEqual(status, 200)
        self.assertIn('新しいページ', bot._recently_saved_titles)
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        self.assertEqual(payload['pages'][0]['lines'], ['新しいページ', '本文1行目', '2行目'])

    @patch('bot.requests.post')
    def test_empty_body_writes_title_only(self, mock_post):
        mock_post.return_value = FakeResponse(status_code=200, text='ok')
        bot.write_page_to_scrapbox('タイトルのみ', '')
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        self.assertEqual(payload['pages'][0]['lines'], ['タイトルのみ'])

    @patch('bot.requests.post')
    def test_error_response_does_not_record_title(self, mock_post):
        mock_post.return_value = FakeResponse(status_code=403, text='Forbidden')
        status, _ = bot.write_page_to_scrapbox('失敗ページ', '本文')
        self.assertEqual(status, 403)
        self.assertNotIn('失敗ページ', bot._recently_saved_titles)


class ProcessUrlsTests(unittest.TestCase):
    def test_mixed_results_build_correct_embeds_and_errors(self):
        def fake_save(url, overwrite=False, actor=''):
            return {
                'https://dup.com': ('duplicate', None, '重複記事', ''),
                'https://ok.com': (200, 'ok', '新規記事', ''),
                'https://err.com': (500, 'サーバーエラー', 'エラー記事', ''),
            }[url]

        with patch.object(bot, 'save_to_scrapbox', side_effect=fake_save):
            results, embeds = bot.process_urls(['https://dup.com', 'https://ok.com', 'https://err.com'])

        self.assertEqual(len(embeds), 2)
        self.assertEqual(embeds[0].title, '重複記事')
        self.assertEqual(embeds[0].description, '既に保存済みです')
        self.assertEqual(embeds[1].title, '新規記事')
        self.assertEqual(embeds[1].description, '保存しました')
        self.assertEqual(len(results), 1)
        self.assertIn('500', results[0])

    def test_overwrite_changes_success_message(self):
        with patch.object(bot, 'save_to_scrapbox', return_value=(200, 'ok', 'タイトル', '')):
            results, embeds = bot.process_urls(['https://example.com'], overwrite=True)
        self.assertEqual(embeds[0].description, '上書き保存しました')


class GetExistingPagesTests(unittest.TestCase):
    def test_cache_is_used_within_ttl(self):
        fresh_cache = {'pages': ['既存ページ'], 'ts': bot.time.time()}
        with patch.object(bot, '_pages_cache', fresh_cache):
            with patch('bot.name_linker.load_existing_pages') as mock_load:
                result = bot.get_existing_pages()
        mock_load.assert_not_called()
        self.assertEqual(result, ['既存ページ'])

    def test_cache_refreshes_after_ttl_expires(self):
        stale_cache = {'pages': ['古いページ'], 'ts': 0.0}
        with patch.object(bot, '_pages_cache', stale_cache):
            with patch('bot.name_linker.load_existing_pages', return_value=['新しいページ']) as mock_load:
                result = bot.get_existing_pages()
        mock_load.assert_called_once()
        self.assertEqual(result, ['新しいページ'])


class FindNewTitlesTests(unittest.TestCase):
    def test_returns_titles_not_in_known_set(self):
        result = bot.find_new_titles({'A', 'B'}, ['A', 'B', 'C'])
        self.assertEqual(result, ['C'])

    def test_no_new_titles_returns_empty_list(self):
        result = bot.find_new_titles({'A', 'B'}, ['A', 'B'])
        self.assertEqual(result, [])

    def test_empty_known_set_returns_all_current_titles(self):
        result = bot.find_new_titles(set(), ['A', 'B'])
        self.assertEqual(result, ['A', 'B'])


class RunDailyHealthChecksTests(unittest.TestCase):
    def test_all_ok_returns_empty_list(self):
        with patch.object(bot, 'name_linker') as mock_name_linker:
            mock_name_linker.check_connection.return_value = (True, '接続OK')
            with patch.object(bot, 'check_youtube_connection', return_value=(None, '未設定')):
                with patch.object(bot, 'credit_extractor') as mock_credit_extractor:
                    mock_credit_extractor.check_connection.return_value = (True, '接続OK')
                    problems = bot.run_daily_health_checks()
        self.assertEqual(problems, [])

    def test_failing_check_is_reported(self):
        with patch.object(bot, 'name_linker') as mock_name_linker:
            mock_name_linker.check_connection.return_value = (False, 'Cookie期限切れ')
            with patch.object(bot, 'check_youtube_connection', return_value=(None, '未設定')):
                with patch.object(bot, 'credit_extractor') as mock_credit_extractor:
                    mock_credit_extractor.check_connection.return_value = (None, '未設定')
                    problems = bot.run_daily_health_checks()
        self.assertEqual(len(problems), 1)
        self.assertIn('Scrapbox', problems[0])
        self.assertIn('Cookie期限切れ', problems[0])

    def test_exception_during_check_is_treated_as_failure(self):
        with patch.object(bot, 'name_linker') as mock_name_linker:
            mock_name_linker.check_connection.side_effect = Exception('timeout')
            with patch.object(bot, 'check_youtube_connection', return_value=(None, '未設定')):
                with patch.object(bot, 'credit_extractor') as mock_credit_extractor:
                    mock_credit_extractor.check_connection.return_value = (None, '未設定')
                    problems = bot.run_daily_health_checks()
        self.assertEqual(len(problems), 1)
        self.assertIn('timeout', problems[0])

    def test_gyazo_failure_is_not_checked_daily(self):
        # Gyazoは/statusでは確認するが、毎日の自動ヘルスチェック対象からは除外している
        with patch.object(bot, 'name_linker') as mock_name_linker:
            mock_name_linker.check_connection.return_value = (True, '接続OK')
            with patch.object(bot, 'check_youtube_connection', return_value=(None, '未設定')):
                with patch.object(bot, 'credit_extractor') as mock_credit_extractor:
                    mock_credit_extractor.check_connection.return_value = (True, '接続OK')
                    with patch.object(bot, 'gyazo_uploader') as mock_gyazo:
                        mock_gyazo.check_connection.return_value = (False, 'トークン失効')
                        problems = bot.run_daily_health_checks()
        self.assertEqual(problems, [])
        mock_gyazo.check_connection.assert_not_called()


class FetchRandomArticleTests(unittest.TestCase):
    def test_no_pages_returns_none(self):
        with patch.object(bot, 'get_existing_pages', return_value=[]):
            result = bot.fetch_random_article()
        self.assertIsNone(result)

    def test_picks_a_page_and_fetches_details(self):
        response = FakeResponse({'image': 'https://example.com/thumb.png', 'descriptions': ['本文冒頭']}, status_code=200)
        with patch.object(bot, 'get_existing_pages', return_value=['記事A']):
            with patch('bot.requests.get', return_value=response):
                result = bot.fetch_random_article()
        self.assertEqual(result['title'], '記事A')
        self.assertEqual(result['thumbnail'], 'https://example.com/thumb.png')
        self.assertEqual(result['description'], '本文冒頭')


class CleanQuestionTests(unittest.TestCase):
    def test_strips_control_characters(self):
        cleaned, truncated = bot._clean_question('質問\x00\x07です')
        self.assertEqual(cleaned, '質問です')
        self.assertFalse(truncated)

    def test_keeps_newlines(self):
        cleaned, _ = bot._clean_question('1行目\n2行目')
        self.assertEqual(cleaned, '1行目\n2行目')

    def test_truncates_over_500_chars(self):
        cleaned, truncated = bot._clean_question('あ' * 600)
        self.assertEqual(len(cleaned), 500)
        self.assertTrue(truncated)

    def test_whitespace_only_becomes_empty(self):
        cleaned, _ = bot._clean_question('   ')
        self.assertEqual(cleaned, '')


class FormatAskErrorTests(unittest.TestCase):
    def test_auth_reuses_403_message(self):
        self.assertIn('Cookie', bot._format_ask_error('auth', 'q'))

    def test_no_hits_includes_query(self):
        message = bot._format_ask_error('no_hits', '山田太郎')
        self.assertIn('見つかりませんでした', message)
        self.assertIn('山田太郎', message)

    def test_no_hits_shows_searched_terms_over_raw_query(self):
        # 実際に検索したキーワードがある場合はそちらを表示する
        message = bot._format_ask_error('no_hits:山口駿,Shun Yamaguchi', '山口駿とは誰？')
        self.assertIn('山口駿,Shun Yamaguchi', message)
        self.assertNotIn('とは誰', message)

    def test_search_error(self):
        self.assertIn('検索', bot._format_ask_error('search', 'q'))

    def test_llm_error_shows_detail(self):
        self.assertIn('429', bot._format_ask_error('llm:ステータス(429)', 'q'))


class BuildNoteLinesTests(unittest.TestCase):
    def test_single_line_note(self):
        lines = bot._build_note_lines('良い感じだった', '2026-07-03', 'sabiasagi')
        self.assertEqual(lines, [' 2026-07-03 sabiasagi', '  良い感じだった'])

    def test_multiline_note_is_indented(self):
        lines = bot._build_note_lines('1行目\n2行目', '2026-07-03', 'user')
        self.assertEqual(lines, [' 2026-07-03 user', '  1行目', '  2行目'])

    def test_trailing_whitespace_stripped(self):
        lines = bot._build_note_lines('本文   ', '2026-07-03', 'user')
        self.assertEqual(lines[1], '  本文')


class AppendNoteToScrapboxTests(unittest.TestCase):
    def setUp(self):
        bot._recently_saved_titles.clear()
        audit_patch = patch.object(bot, '_audit')
        self.audit_mock = audit_patch.start()
        self.addCleanup(audit_patch.stop)

    def tearDown(self):
        bot._recently_saved_titles.clear()

    def test_appends_to_existing_page_preserving_body(self):
        existing = {'persistent': True, 'lines': [{'text': '案件X'}, {'text': '[* 概要]'}, {'text': 'クライアント情報'}]}
        with patch('bot.requests.get', return_value=FakeResponse(existing)):
            with patch('bot.requests.post', return_value=FakeResponse(status_code=200, text='ok')) as mock_post:
                status, _ = bot.append_note_to_scrapbox('案件X', '進捗メモ', 'user')
        self.assertEqual(status, 200)
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        lines = payload['pages'][0]['lines']
        # 既存本文が保持され、末尾に日付行+本文が付く
        self.assertEqual(lines[:3], ['案件X', '[* 概要]', 'クライアント情報'])
        self.assertTrue(lines[3].startswith(' ') and 'user' in lines[3])
        self.assertEqual(lines[4], '  進捗メモ')
        # 既存ページへの追記は新規ページ通知の抑制対象にしない
        self.assertNotIn('案件X', bot._recently_saved_titles)

    def test_missing_page_without_allow_create_returns_not_found(self):
        # タイポで迷子ページが量産されるのを防ぐため、明示しない限り新規作成しない
        missing = {'persistent': False, 'lines': []}
        with patch('bot.requests.get', return_value=FakeResponse(missing)):
            with patch('bot.requests.post') as mock_post:
                status, _ = bot.append_note_to_scrapbox('存在しない案件', 'メモ', 'user')
        mock_post.assert_not_called()
        self.assertEqual(status, 'not_found')

    def test_creates_page_when_missing_and_allow_create(self):
        missing = {'persistent': False, 'lines': []}
        with patch('bot.requests.get', return_value=FakeResponse(missing)):
            with patch('bot.requests.post', return_value=FakeResponse(status_code=200, text='ok')) as mock_post:
                status, _ = bot.append_note_to_scrapbox('新規案件', 'メモ', 'user', allow_create=True)
        self.assertEqual(status, 200)
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        self.assertEqual(payload['pages'][0]['lines'][0], '新規案件')
        # 新規作成なので新規ページ通知の二重通知を抑制する
        self.assertIn('新規案件', bot._recently_saved_titles)

    def test_note_inserted_before_trailing_tag_block(self):
        # 雛形ページでは末尾タグの下ではなく「メモ・感想」セクション配下に入る
        existing = {'persistent': True, 'lines': [
            {'text': '案件X'}, {'text': '[* メモ・感想]'}, {'text': ''}, {'text': '#Karure制作'},
        ]}
        with patch('bot.requests.get', return_value=FakeResponse(existing)):
            with patch('bot.requests.post', return_value=FakeResponse(status_code=200, text='ok')) as mock_post:
                bot.append_note_to_scrapbox('案件X', '進捗メモ', 'user')
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        # タグはページ最下部のまま、メモは見出しの直下に入る
        self.assertEqual(lines[-1], '#Karure制作')
        self.assertEqual(lines[1], '[* メモ・感想]')
        self.assertEqual(lines[3], '  進捗メモ')

    def test_get_failure_returns_error_without_post(self):
        with patch('bot.requests.get', side_effect=Exception('timeout')):
            with patch('bot.requests.post') as mock_post:
                status, body = bot.append_note_to_scrapbox('案件X', 'メモ', 'user')
        mock_post.assert_not_called()
        self.assertIsNone(status)
        self.assertEqual(body, 'timeout')

    def test_post_failure_does_not_record_title(self):
        missing = {'persistent': False, 'lines': []}
        with patch('bot.requests.get', return_value=FakeResponse(missing)):
            with patch('bot.requests.post', return_value=FakeResponse(status_code=500, text='error')):
                status, _ = bot.append_note_to_scrapbox('失敗案件', 'メモ', 'user', allow_create=True)
        self.assertEqual(status, 500)
        self.assertNotIn('失敗案件', bot._recently_saved_titles)


class NoteInsertIndexTests(unittest.TestCase):
    def test_template_body_inserts_under_memo_section(self):
        body = ['[* 概要]', '', '[* データ]', '', '[* メモ・感想]', '', '#Karure制作']
        self.assertEqual(bot._note_insert_index(body), 5)

    def test_no_tags_appends_at_end(self):
        body = ['本文1', '本文2']
        self.assertEqual(bot._note_insert_index(body), 2)

    def test_multiple_trailing_tags_and_blanks_are_kept_at_bottom(self):
        body = ['本文', '', '#Karure制作 #MV', '#完了']
        self.assertEqual(bot._note_insert_index(body), 1)

    def test_empty_body_returns_zero(self):
        self.assertEqual(bot._note_insert_index([]), 0)

    def test_hashtag_in_sentence_is_not_treated_as_tag_line(self):
        body = ['文中に #タグ がある行']
        self.assertEqual(bot._note_insert_index(body), 1)


class ProjectPageTemplateTests(unittest.TestCase):
    def test_template_has_expected_sections(self):
        joined = '\n'.join(bot.PROJECT_PAGE_TEMPLATE)
        self.assertIn('[* 概要]', joined)
        self.assertIn('[* データ]', joined)
        self.assertIn('[* メモ・感想]', joined)

    def test_template_has_common_karure_link(self):
        # 全案件ページ共通のリンク。「Karure制作」ページの逆リンク一覧が案件一覧として機能する
        self.assertEqual(bot.PROJECT_PAGE_TEMPLATE[-1], '#Karure制作')


class ObservabilityTests(unittest.TestCase):
    def test_format_uptime_days(self):
        self.assertEqual(bot._format_uptime(90000), '1日1時間')

    def test_format_uptime_hours(self):
        self.assertEqual(bot._format_uptime(3900), '1時間5分')

    def test_format_uptime_minutes(self):
        self.assertEqual(bot._format_uptime(120), '2分')

    def test_record_error_ring_buffer_caps_at_maxlen(self):
        with patch.object(bot, '_recent_errors', bot.deque(maxlen=3)):
            for i in range(5):
                bot.record_error('test', f'err{i}')
            messages = [m for _, _, m in bot._recent_errors]
        self.assertEqual(messages, ['err2', 'err3', 'err4'])

    def test_observability_lines_include_task_and_errors(self):
        with patch.object(bot, '_task_last_runs', {'新規ページ通知': {'ts': 1751500000.0, 'ok': False, 'detail': 'timeout'}}):
            with patch.object(bot, '_recent_errors', bot.deque([(1751500000.0, 'notify', 'boom')], maxlen=20)):
                lines = bot._build_observability_lines(now=1751500060.0)
        joined = '\n'.join(lines)
        self.assertIn('稼働時間', joined)
        self.assertIn('❌ 新規ページ通知', joined)
        self.assertIn('timeout', joined)
        self.assertIn('[notify] boom', joined)

    def test_audit_failure_is_recorded_not_raised(self):
        with patch.object(bot, '_recent_errors', bot.deque(maxlen=20)):
            with patch('bot.audit_log.append_entry', side_effect=Exception('network down')):
                bot._audit('save', 'ページ', 'user')  # 例外が漏れないこと
            self.assertEqual(len(bot._recent_errors), 1)
            self.assertEqual(bot._recent_errors[0][1], 'audit')


class AuditWiringTests(unittest.TestCase):
    def setUp(self):
        bot._recently_saved_titles.clear()
        audit_patch = patch.object(bot, '_audit')
        self.audit_mock = audit_patch.start()
        self.addCleanup(audit_patch.stop)

    def tearDown(self):
        bot._recently_saved_titles.clear()

    def test_successful_note_is_audited_with_actor(self):
        existing = {'persistent': True, 'updated': 1751500000, 'lines': [{'text': '案件X'}, {'text': '本文'}]}
        with patch('bot.requests.get', return_value=FakeResponse(existing)):
            with patch('bot.requests.post', return_value=FakeResponse(status_code=200)):
                bot.append_note_to_scrapbox('案件X', 'メモ', 'sabiasagi')
        self.audit_mock.assert_called_once()
        args = self.audit_mock.call_args.args
        self.assertEqual(args[0], 'note')
        self.assertEqual(args[1], '案件X')
        self.assertEqual(args[2], 'sabiasagi')
        self.assertIn('直前updated:1751500000', args[3])

    def test_failed_note_is_not_audited(self):
        existing = {'persistent': True, 'lines': [{'text': '案件X'}]}
        with patch('bot.requests.get', return_value=FakeResponse(existing)):
            with patch('bot.requests.post', return_value=FakeResponse(status_code=500)):
                bot.append_note_to_scrapbox('案件X', 'メモ', 'user')
        self.audit_mock.assert_not_called()

    def test_successful_write_is_audited_with_action(self):
        with patch('bot.requests.post', return_value=FakeResponse(status_code=200)):
            bot.write_page_to_scrapbox('新ページ', '本文', 'user', 'project-create')
        self.audit_mock.assert_called_once()
        self.assertEqual(self.audit_mock.call_args.args[0], 'project-create')


class DiaryWebhookRequestTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(bot, 'DIARY_SCRAPBOX_PROJECT', 'diary-proj'),
            patch.object(bot, 'DIARY_SCRAPBOX_SID', 'sid'),
            patch.object(bot, 'DIARY_WEBHOOK_TOKEN', 'secret-token'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_disabled_when_not_configured(self):
        with patch.object(bot, 'DIARY_WEBHOOK_TOKEN', ''):
            status, payload = bot.handle_diary_webhook_request('secret-token', b'{"text": "hi"}')
        self.assertEqual(status, 503)
        self.assertIn('error', payload)

    def test_wrong_token_is_rejected(self):
        status, payload = bot.handle_diary_webhook_request('wrong-token', b'{"text": "hi"}')
        self.assertEqual(status, 401)

    def test_missing_token_is_rejected(self):
        status, payload = bot.handle_diary_webhook_request('', b'{"text": "hi"}')
        self.assertEqual(status, 401)

    def test_invalid_json_returns_400(self):
        status, payload = bot.handle_diary_webhook_request('secret-token', b'not json')
        self.assertEqual(status, 400)

    def test_empty_text_returns_400(self):
        status, payload = bot.handle_diary_webhook_request('secret-token', b'{"text": "  "}')
        self.assertEqual(status, 400)

    def test_valid_request_appends_diary_entry(self):
        with patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-07')) as mock_append:
            status, payload = bot.handle_diary_webhook_request('secret-token', b'{"text": "\xe4\xbb\x8a\xe6\x97\xa5"}')
        self.assertEqual(status, 200)
        self.assertEqual(payload, {'status': 'appended', 'title': '2026-07-07', 'section': 'diary'})
        mock_append.assert_called_once_with('diary-proj', 'sid', '今日', 'diary')

    def test_vocab_prefix_routes_to_vocab_section(self):
        body = json.dumps({'text': '単語:serendipity'}).encode('utf-8')
        with patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-07')) as mock_append:
            status, payload = bot.handle_diary_webhook_request('secret-token', body)
        self.assertEqual(payload['section'], 'vocab')
        mock_append.assert_called_once_with('diary-proj', 'sid', 'serendipity', 'vocab')

    def test_append_failure_returns_502(self):
        with patch.object(bot.diary, 'append_diary_entry', return_value=(500, '2026-07-07')):
            status, payload = bot.handle_diary_webhook_request('secret-token', b'{"text": "hi"}')
        self.assertEqual(status, 502)
        self.assertIn('error', payload)


class ReactionActionTests(unittest.TestCase):
    def test_save_emojis_map_to_save(self):
        for emoji in bot.SAVE_REACTION_EMOJIS:
            self.assertEqual(bot._reaction_action(emoji), 'save')

    def test_ask_emojis_map_to_ask(self):
        for emoji in bot.ASK_REACTION_EMOJIS:
            self.assertEqual(bot._reaction_action(emoji), 'ask')

    def test_unrelated_emoji_maps_to_none(self):
        self.assertIsNone(bot._reaction_action('😀'))


if __name__ == '__main__':
    unittest.main()
