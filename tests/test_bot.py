import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import bot
from eagle_import import EagleImportStore


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=''):
        self._json_data = json_data or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json_data


def _http_exception(status):
    # Cloudflareにブロックされた場合、discord.pyは実際にこの形（error code: 0）の
    # HTTPExceptionを送出する。本番の /status に出ていたエラー文言と同じ形。
    response = MagicMock()
    response.status = status
    response.reason = 'Too Many Requests' if status == 429 else 'Error'
    return discord.HTTPException(response, 'You are being blocked from accessing our API temporarily due')


class FetchWithRetryTests(unittest.TestCase):
    def test_cached_value_is_returned_without_calling_fetch(self):
        get_cached = MagicMock(return_value='cached-channel')
        fetch = AsyncMock()
        result = asyncio.run(bot._fetch_with_retry(get_cached, fetch, 123))
        self.assertEqual(result, 'cached-channel')
        fetch.assert_not_awaited()

    def test_cache_miss_falls_back_to_fetch(self):
        get_cached = MagicMock(return_value=None)
        fetch = AsyncMock(return_value='fetched-channel')
        result = asyncio.run(bot._fetch_with_retry(get_cached, fetch, 123))
        self.assertEqual(result, 'fetched-channel')
        fetch.assert_awaited_once_with(123)

    def test_cloudflare_429_is_retried_after_a_delay(self):
        # discord.pyはこの429を自動リトライしない仕様のため、ここで待って再試行する
        get_cached = MagicMock(return_value=None)
        fetch = AsyncMock(side_effect=[_http_exception(429), 'fetched-channel'])
        with patch.object(bot.asyncio, 'sleep', new_callable=AsyncMock) as mock_sleep:
            result = asyncio.run(bot._fetch_with_retry(get_cached, fetch, 123, attempts=3, base_delay=5))
        self.assertEqual(result, 'fetched-channel')
        mock_sleep.assert_awaited_once_with(5)

    def test_non_429_error_is_not_retried(self):
        get_cached = MagicMock(return_value=None)
        fetch = AsyncMock(side_effect=_http_exception(404))
        with patch.object(bot.asyncio, 'sleep', new_callable=AsyncMock) as mock_sleep:
            with self.assertRaises(discord.HTTPException):
                asyncio.run(bot._fetch_with_retry(get_cached, fetch, 123))
        mock_sleep.assert_not_awaited()

    def test_exhausting_all_attempts_raises(self):
        get_cached = MagicMock(return_value=None)
        fetch = AsyncMock(side_effect=_http_exception(429))
        with patch.object(bot.asyncio, 'sleep', new_callable=AsyncMock):
            with self.assertRaises(discord.HTTPException):
                asyncio.run(bot._fetch_with_retry(get_cached, fetch, 123, attempts=2))
        self.assertEqual(fetch.await_count, 2)


class NormalizeTitleTests(unittest.TestCase):
    def test_collapses_whitespace_and_newlines(self):
        self.assertEqual(bot._normalize_title('動画\nタイトル  です'), '動画 タイトル です')

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(bot._normalize_title('  タイトル  '), 'タイトル')

    def test_square_brackets_become_parentheses(self):
        # Scrapboxはタイトルに [ ] を含められず、そのまま送ると400で弾かれる
        self.assertEqual(
            bot._normalize_title('ChouCho - Defy the Silence [Lyric MV]'),
            'ChouCho - Defy the Silence (Lyric MV)',
        )

    def test_multiple_and_unbalanced_brackets_are_converted(self):
        self.assertEqual(
            bot._normalize_title('【歌ってみた】朔雀 - シンデレラ [DECO*27 Cover] ['),
            '【歌ってみた】朔雀 - シンデレラ (DECO*27 Cover) (',
        )

    def test_full_width_brackets_are_kept(self):
        # 全角の 【 】 はScrapboxでも使えるので触らない
        self.assertEqual(bot._normalize_title('【MV】タイトル'), '【MV】タイトル')


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
        fresh_cache = {'pages': ['既存ページ'], 'ts': bot.time.time(), 'complete': True}
        with patch.object(bot, '_pages_cache', fresh_cache):
            with patch('bot.name_linker.fetch_all_page_titles') as mock_load:
                result = bot.get_existing_pages()
        mock_load.assert_not_called()
        self.assertEqual(result, ['既存ページ'])

    def test_cache_refreshes_after_ttl_expires(self):
        stale_cache = {'pages': ['古いページ'], 'ts': 0.0, 'complete': True}
        with patch.object(bot, '_pages_cache', stale_cache):
            with patch('bot.name_linker.fetch_all_page_titles', return_value=(True, ['新しいページ'])) as mock_load:
                result = bot.get_existing_pages()
        mock_load.assert_called_once()
        self.assertEqual(result, ['新しいページ'])

    def test_incomplete_fetch_keeps_the_previous_list(self):
        # 欠けた一覧で上書きすると、次に取得できたときに既存ページが全部「新規」になる
        stale_cache = {'pages': ['ページA', 'ページB'], 'ts': 0.0, 'complete': True}
        with patch.object(bot, '_pages_cache', stale_cache):
            with patch('bot.name_linker.fetch_all_page_titles', return_value=(False, ['ページA'])):
                complete, pages = bot.get_existing_pages_status()
        self.assertFalse(complete)
        self.assertEqual(pages, ['ページA', 'ページB'])

    def test_incomplete_fetch_still_advances_the_cache_timestamp(self):
        # 失敗のたびに全ページ取得を叩き直さない
        stale_cache = {'pages': [], 'ts': 0.0, 'complete': True}
        with patch.object(bot, '_pages_cache', stale_cache):
            with patch('bot.name_linker.fetch_all_page_titles', return_value=(False, [])) as mock_load:
                bot.get_existing_pages_status()
                bot.get_existing_pages_status()
        mock_load.assert_called_once()

    def test_successful_fetch_is_reported_as_complete(self):
        stale_cache = {'pages': [], 'ts': 0.0, 'complete': False}
        with patch.object(bot, '_pages_cache', stale_cache):
            with patch('bot.name_linker.fetch_all_page_titles', return_value=(True, ['ページA'])):
                complete, pages = bot.get_existing_pages_status()
        self.assertTrue(complete)
        self.assertEqual(pages, ['ページA'])


class SelectNotifiableTitlesTests(unittest.TestCase):
    def test_bot_saved_titles_are_skipped_once(self):
        with patch.object(bot, '_recently_saved_titles', {'自分で保存した記事'}):
            self.assertEqual(bot.select_notifiable_titles(['自分で保存した記事', '他人の記事']), ['他人の記事'])

    def test_config_pages_are_skipped(self):
        with patch.object(bot, 'CREDIT_MAPPING_PAGE', '表記ゆれ'), \
                patch.object(bot, '_recently_saved_titles', set()):
            titles = bot.select_notifiable_titles(['表記ゆれ', 'bot設定/監査ログ/2026-07', '記事'])
        self.assertEqual(titles, ['記事'])


class BuildBulkNewPagesMessageTests(unittest.TestCase):
    def test_message_states_the_total_and_lists_titles(self):
        message = bot.build_bulk_new_pages_message(['A', 'B', 'C'])
        self.assertIn('3件', message)
        self.assertIn('・A', message)
        self.assertIn('・C', message)

    def test_long_lists_are_truncated(self):
        titles = [f'記事{i}' for i in range(30)]
        message = bot.build_bulk_new_pages_message(titles)
        self.assertIn('30件', message)
        self.assertIn(f'…ほか{30 - bot.NOTIFY_NEW_PAGES_LIST_MAX}件', message)
        self.assertNotIn('記事29', message)


class NotifyNewPagesTests(unittest.TestCase):
    def setUp(self):
        patch.object(bot, '_known_page_titles', None).start()
        patch.object(bot, '_recently_saved_titles', set()).start()
        self.channel = MagicMock()
        self.channel.send = AsyncMock()
        fake_client = MagicMock()
        fake_client.get_channel.return_value = self.channel
        patch.object(bot, 'client', fake_client).start()
        self.addCleanup(patch.stopall)

    def _run(self):
        asyncio.run(bot.notify_new_pages.coro())

    def test_incomplete_page_list_is_not_used_as_a_baseline(self):
        # ここでベースラインを作ると、次に全件取れたときに既存ページを全部通知してしまう
        with patch.object(bot, 'get_existing_pages_status', return_value=(False, ['記事A'])):
            self._run()
        self.assertIsNone(bot._known_page_titles)
        self.channel.send.assert_not_awaited()
        self.assertFalse(bot._task_last_runs['新規ページ通知']['ok'])

    def test_first_complete_run_only_records_the_baseline(self):
        with patch.object(bot, 'get_existing_pages_status', return_value=(True, ['記事A', '記事B'])):
            self._run()
        self.assertEqual(bot._known_page_titles, {'記事A', '記事B'})
        self.channel.send.assert_not_awaited()

    def test_new_page_is_notified_individually(self):
        with patch.object(bot, '_known_page_titles', {'記事A'}), \
                patch.object(bot, 'get_existing_pages_status', return_value=(True, ['記事A', '記事B'])):
            self._run()
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.channel.send.await_args.kwargs['embed'].title, '記事B')

    def test_many_new_pages_are_summarised_in_one_message(self):
        titles = [f'記事{i}' for i in range(bot.NOTIFY_NEW_PAGES_MAX + 3)]
        with patch.object(bot, '_known_page_titles', set()), \
                patch.object(bot, 'get_existing_pages_status', return_value=(True, titles)):
            self._run()
        self.channel.send.assert_awaited_once()
        self.assertIn(f'{len(titles)}件', self.channel.send.await_args.args[0])

    def test_failure_to_fetch_does_not_overwrite_the_baseline(self):
        with patch.object(bot, '_known_page_titles', {'記事A'}), \
                patch.object(bot, 'get_existing_pages_status', return_value=(False, [])):
            self._run()
            self.assertEqual(bot._known_page_titles, {'記事A'})


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
            # 自動リンクのページ一覧取得でScrapboxに出ていかないようにする
            patch.object(bot, 'get_diary_pages', return_value=[]),
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

    def test_known_page_names_are_linked(self):
        body = json.dumps({'text': 'Blenderを触った'}).encode('utf-8')
        with patch.object(bot, 'get_diary_pages', return_value=['Blender']), \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-07')) as mock_append:
            bot.handle_diary_webhook_request('secret-token', body)
        mock_append.assert_called_once_with('diary-proj', 'sid', '[Blender]を触った', 'diary')


class EnvHourTests(unittest.TestCase):
    def test_reads_valid_hour(self):
        with patch.dict(bot.os.environ, {'X_HOUR': '7'}):
            self.assertEqual(bot._env_hour('X_HOUR', 22), 7)

    def test_unset_falls_back_to_default(self):
        with patch.dict(bot.os.environ, {}, clear=True):
            self.assertEqual(bot._env_hour('X_HOUR', 22), 22)

    def test_out_of_range_falls_back_to_default(self):
        with patch.dict(bot.os.environ, {'X_HOUR': '25'}):
            self.assertEqual(bot._env_hour('X_HOUR', 22), 22)

    def test_non_numeric_falls_back_to_default(self):
        with patch.dict(bot.os.environ, {'X_HOUR': 'とんでもない値'}):
            self.assertEqual(bot._env_hour('X_HOUR', 22), 22)


class DiaryReminderMessageTests(unittest.TestCase):
    def test_message_includes_date_and_page_url(self):
        message = bot.build_diary_reminder_message('2026-07-06', 'my-diary')
        self.assertIn('2026-07-06', message)
        self.assertIn('https://scrapbox.io/my-diary/2026-07-06', message)

    def test_message_explains_dm_reply_shortcut(self):
        # 「開いて書く」より軽い手段（DM返信・単語:）を毎回添える
        message = bot.build_diary_reminder_message('2026-07-06', 'my-diary')
        self.assertIn('返信', message)
        self.assertIn('単語:', message)

    def test_message_includes_the_prompt_of_the_day(self):
        # 「何を書けばいいか分からない」で止まらないよう、具体的なお題を1つ出す
        dt = datetime(2026, 7, 6)
        message = bot.build_diary_reminder_message('2026-07-06', 'my-diary', dt=dt)
        self.assertIn(bot.diary.prompt_for(dt), message)

    def test_prompt_changes_with_the_date(self):
        first = bot.build_diary_reminder_message('2026-07-06', 'my-diary', dt=datetime(2026, 7, 6))
        second = bot.build_diary_reminder_message('2026-07-07', 'my-diary', dt=datetime(2026, 7, 7))
        self.assertNotEqual(first, second)


class GetDiaryPagesTests(unittest.TestCase):
    def setUp(self):
        bot._diary_pages_cache['pages'] = []
        bot._diary_pages_cache['ts'] = 0.0
        self.addCleanup(lambda: bot._diary_pages_cache.update({'pages': [], 'ts': 0.0}))

    def test_second_call_uses_cache(self):
        # DMのたびに全ページを取りに行くと追記が目に見えて遅くなる
        with patch.object(bot, 'DIARY_SCRAPBOX_PROJECT', 'diary-proj'), \
                patch.object(bot, 'DIARY_SCRAPBOX_SID', 'sid'), \
                patch.object(bot.name_linker, 'fetch_all_page_titles', return_value=(True, ['Blender'])) as mock_load:
            first = bot.get_diary_pages()
            second = bot.get_diary_pages()
        self.assertEqual(first, ['Blender'])
        self.assertEqual(second, ['Blender'])
        mock_load.assert_called_once_with('diary-proj', 'sid')

    def test_expired_cache_is_reloaded(self):
        with patch.object(bot.name_linker, 'fetch_all_page_titles', return_value=(True, ['Blender'])) as mock_load:
            bot.get_diary_pages()
            bot._diary_pages_cache['ts'] -= bot.PAGES_CACHE_TTL + 1
            bot.get_diary_pages()
        self.assertEqual(mock_load.call_count, 2)

    def test_incomplete_fetch_keeps_the_previous_list(self):
        # 欠けた一覧で上書きすると、リンクできるはずの単語が5分間リンクされなくなる
        with patch.object(bot.name_linker, 'fetch_all_page_titles', return_value=(True, ['Blender'])):
            bot.get_diary_pages()
        bot._diary_pages_cache['ts'] -= bot.PAGES_CACHE_TTL + 1
        with patch.object(bot.name_linker, 'fetch_all_page_titles', return_value=(False, [])):
            self.assertEqual(bot.get_diary_pages(), ['Blender'])


class AutolinkDiaryTextTests(unittest.TestCase):
    def test_links_known_page_names(self):
        with patch.object(bot, 'DIARY_AUTOLINK', True), \
                patch.object(bot, 'get_diary_pages', return_value=['Blender']):
            self.assertEqual(bot.autolink_diary_text('Blenderを触った'), '[Blender]を触った')

    def test_date_pages_are_not_linked(self):
        with patch.object(bot, 'DIARY_AUTOLINK', True), \
                patch.object(bot, 'get_diary_pages', return_value=['2026-07-06']):
            self.assertEqual(bot.autolink_diary_text('2026-07-06は暑かった'), '2026-07-06は暑かった')

    def test_disabled_returns_text_unchanged_without_fetching(self):
        with patch.object(bot, 'DIARY_AUTOLINK', False), \
                patch.object(bot, 'get_diary_pages') as mock_pages:
            self.assertEqual(bot.autolink_diary_text('Blenderを触った'), 'Blenderを触った')
        mock_pages.assert_not_called()

    def test_page_list_failure_keeps_the_original_text(self):
        # リンク化は付加価値なので、失敗しても追記そのものは通す
        with patch.object(bot, 'DIARY_AUTOLINK', True), \
                patch.object(bot, 'get_diary_pages', side_effect=Exception('接続できません')), \
                patch.object(bot, 'record_error') as mock_record:
            self.assertEqual(bot.autolink_diary_text('Blenderを触った'), 'Blenderを触った')
        mock_record.assert_called_once()

    def test_empty_text_is_returned_as_is(self):
        with patch.object(bot, 'get_diary_pages') as mock_pages:
            self.assertEqual(bot.autolink_diary_text(''), '')
        mock_pages.assert_not_called()


def _fake_attachment(filename='photo.png', content_type='image/png', data=b'bytes'):
    attachment = MagicMock()
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.read = AsyncMock(return_value=data)
    return attachment


class UploadDiaryImagesTests(unittest.TestCase):
    def test_image_is_uploaded_to_gyazo_and_returned_as_a_link_line(self):
        # Discordの添付URLは失効するため、必ずGyazoの恒久URLに変換して貼る
        attachment = _fake_attachment()
        with patch.object(bot.gyazo_uploader, 'upload_image', return_value='https://i.gyazo.com/a.png') as mock_upload:
            lines = asyncio.run(bot.upload_diary_images([attachment]))
        self.assertEqual(lines, ['[https://i.gyazo.com/a.png]'])
        mock_upload.assert_called_once_with(b'bytes', 'photo.png')

    def test_non_image_attachment_is_skipped(self):
        attachment = _fake_attachment('memo.pdf', 'application/pdf')
        with patch.object(bot.gyazo_uploader, 'upload_image') as mock_upload:
            lines = asyncio.run(bot.upload_diary_images([attachment]))
        self.assertEqual(lines, [])
        mock_upload.assert_not_called()

    def test_attachment_without_content_type_is_skipped(self):
        attachment = _fake_attachment(content_type=None)
        with patch.object(bot.gyazo_uploader, 'upload_image') as mock_upload:
            lines = asyncio.run(bot.upload_diary_images([attachment]))
        self.assertEqual(lines, [])
        mock_upload.assert_not_called()

    def test_upload_failure_is_recorded_and_skipped(self):
        with patch.object(bot.gyazo_uploader, 'upload_image', return_value=''), \
                patch.object(bot, 'record_error') as mock_record:
            lines = asyncio.run(bot.upload_diary_images([_fake_attachment()]))
        self.assertEqual(lines, [])
        mock_record.assert_called_once()

    def test_download_exception_does_not_stop_other_images(self):
        broken = _fake_attachment('broken.png')
        broken.read = AsyncMock(side_effect=Exception('読めません'))
        with patch.object(bot.gyazo_uploader, 'upload_image', return_value='https://i.gyazo.com/b.png'), \
                patch.object(bot, 'record_error'):
            lines = asyncio.run(bot.upload_diary_images([broken, _fake_attachment()]))
        self.assertEqual(lines, ['[https://i.gyazo.com/b.png]'])


class BuildDiaryEntriesTests(unittest.TestCase):
    def test_text_only(self):
        self.assertEqual(bot.build_diary_entries('今日は暑い', []), [('diary', '今日は暑い')])

    def test_image_only(self):
        # 本文なしで写真だけ送っても日記に残る
        self.assertEqual(bot.build_diary_entries('', ['[url]']), [('diary', '[url]')])

    def test_text_and_image_become_one_entry(self):
        # キャプション付きの写真は1件のまとまりとして扱う
        self.assertEqual(bot.build_diary_entries('展示に行った', ['[url]']), [('diary', '展示に行った\n[url]')])

    def test_vocab_prefix_routes_text_to_vocab(self):
        self.assertEqual(bot.build_diary_entries('単語:serendipity', []), [('vocab', 'serendipity')])

    def test_vocab_with_image_splits_into_two_entries(self):
        # 写真は単語ではなくその日の記録なので【日記】欄に入れる
        self.assertEqual(
            bot.build_diary_entries('単語:serendipity', ['[url]']),
            [('vocab', 'serendipity'), ('diary', '[url]')],
        )

    def test_nothing_to_write_returns_no_entries(self):
        self.assertEqual(bot.build_diary_entries('', []), [])


class BuildPageBodyLinesTests(unittest.TestCase):
    def test_body_lines_are_not_indented(self):
        # 日記への追記と違い、ここはページ本文そのものになる
        self.assertEqual(bot.build_page_body_lines('一行目\n二行目', []), ['一行目', '二行目'])

    def test_images_follow_the_body(self):
        self.assertEqual(bot.build_page_body_lines('本文', ['[url]']), ['本文', '[url]'])

    def test_empty_body_with_image_only(self):
        self.assertEqual(bot.build_page_body_lines('', ['[url]']), ['[url]'])

    def test_trailing_blank_lines_are_dropped(self):
        self.assertEqual(bot.build_page_body_lines('本文\n\n\n', []), ['本文'])


class CreateDiarySidePageTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(bot, 'DIARY_SCRAPBOX_PROJECT', 'diary-proj'),
            patch.object(bot, 'DIARY_SCRAPBOX_SID', 'sid'),
            patch.object(bot, 'autolink_diary_text', side_effect=lambda text: text),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_page_is_created_and_linked_from_the_diary(self):
        # リンクを張らないとどこからも辿れないページになる
        with patch.object(bot.diary, 'create_page', return_value=('created', 'Blender Guru')) as mock_create, \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')) as mock_append:
            status, title = asyncio.run(bot.create_diary_side_page('Blender Guru', '本文', []))
        self.assertEqual(status, 'created')
        mock_create.assert_called_once_with('diary-proj', 'sid', 'Blender Guru', ['本文'])
        mock_append.assert_called_once_with('diary-proj', 'sid', '[Blender Guru]', 'diary')

    def test_brackets_in_the_title_are_normalized(self):
        # Scrapboxはタイトルに [ ] を含められない
        with patch.object(bot.diary, 'create_page', return_value=('created', 'x')) as mock_create, \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')):
            asyncio.run(bot.create_diary_side_page('MV [Official]', '', []))
        self.assertEqual(mock_create.call_args.args[2], 'MV (Official)')

    def test_empty_title_is_rejected_without_writing(self):
        with patch.object(bot.diary, 'create_page') as mock_create, \
                patch.object(bot, 'record_error') as mock_record:
            status, title = asyncio.run(bot.create_diary_side_page('   ', '本文', []))
        self.assertEqual(status, 'no-title')
        mock_create.assert_not_called()
        mock_record.assert_called_once()

    def test_creation_failure_skips_the_diary_link(self):
        with patch.object(bot.diary, 'create_page', return_value=(500, 'Blender Guru')), \
                patch.object(bot.diary, 'append_diary_entry') as mock_append:
            status, title = asyncio.run(bot.create_diary_side_page('Blender Guru', '', []))
        self.assertEqual(status, 500)
        mock_append.assert_not_called()

    def test_link_failure_is_reported_as_failure(self):
        with patch.object(bot.diary, 'create_page', return_value=('created', 'Blender Guru')), \
                patch.object(bot.diary, 'append_diary_entry', return_value=(500, '2026-07-06')), \
                patch.object(bot, 'record_error') as mock_record:
            status, title = asyncio.run(bot.create_diary_side_page('Blender Guru', '', []))
        self.assertEqual(status, 500)
        mock_record.assert_called_once()

    def test_page_cache_is_invalidated_so_the_new_page_links(self):
        bot._diary_pages_cache['ts'] = bot.time.time()
        self.addCleanup(lambda: bot._diary_pages_cache.update({'pages': [], 'ts': 0.0}))
        with patch.object(bot.diary, 'create_page', return_value=('created', 'Blender Guru')), \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')):
            asyncio.run(bot.create_diary_side_page('Blender Guru', '', []))
        self.assertEqual(bot._diary_pages_cache['ts'], 0.0)

    def test_body_is_autolinked(self):
        with patch.object(bot, 'autolink_diary_text', return_value='[Blender]の話') as mock_autolink, \
                patch.object(bot.diary, 'create_page', return_value=('created', 'メモ')) as mock_create, \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')):
            asyncio.run(bot.create_diary_side_page('メモ', 'Blenderの話', []))
        mock_autolink.assert_called_once_with('Blenderの話')
        self.assertEqual(mock_create.call_args.args[3], ['[Blender]の話'])


class HandleDiaryDmTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(bot, 'DIARY_SCRAPBOX_PROJECT', 'diary-proj'),
            patch.object(bot, 'DIARY_SCRAPBOX_SID', 'sid'),
            patch.object(bot, 'DIARY_OWNER_USER_ID', 123),
            patch.object(bot, 'autolink_diary_text', side_effect=lambda text: text),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _message(self, content='今日は暑い', author_id=123, attachments=()):
        message = MagicMock()
        message.content = content
        message.author.id = author_id
        message.attachments = list(attachments)
        message.add_reaction = AsyncMock()
        return message

    def test_text_dm_is_appended_and_acknowledged(self):
        message = self._message()
        with patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')) as mock_append:
            asyncio.run(bot.handle_diary_dm(message))
        mock_append.assert_called_once_with('diary-proj', 'sid', '今日は暑い', 'diary')
        message.add_reaction.assert_awaited_once_with('✅')

    def test_image_only_dm_is_appended(self):
        message = self._message(content='', attachments=[_fake_attachment()])
        with patch.object(bot.gyazo_uploader, 'upload_image', return_value='https://i.gyazo.com/a.png'), \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')) as mock_append:
            asyncio.run(bot.handle_diary_dm(message))
        mock_append.assert_called_once_with('diary-proj', 'sid', '[https://i.gyazo.com/a.png]', 'diary')
        message.add_reaction.assert_awaited_once_with('✅')

    def test_vocab_dm_with_image_appends_twice(self):
        message = self._message(content='単語:serendipity', attachments=[_fake_attachment()])
        with patch.object(bot.gyazo_uploader, 'upload_image', return_value='https://i.gyazo.com/a.png'), \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')) as mock_append:
            asyncio.run(bot.handle_diary_dm(message))
        self.assertEqual(mock_append.call_count, 2)
        self.assertEqual(mock_append.call_args_list[0].args[3], 'vocab')
        self.assertEqual(mock_append.call_args_list[1].args[3], 'diary')

    def test_diary_text_is_autolinked(self):
        message = self._message(content='Blenderを触った')
        with patch.object(bot, 'autolink_diary_text', return_value='[Blender]を触った'), \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')) as mock_append:
            asyncio.run(bot.handle_diary_dm(message))
        mock_append.assert_called_once_with('diary-proj', 'sid', '[Blender]を触った', 'diary')

    def test_vocab_text_is_not_autolinked(self):
        # 単語欄は追記時に [ ] で囲まれるため、事前のリンク化は二重になる
        message = self._message(content='単語:Blender')
        with patch.object(bot, 'autolink_diary_text') as mock_autolink, \
                patch.object(bot.diary, 'append_diary_entry', return_value=('appended', '2026-07-06')):
            asyncio.run(bot.handle_diary_dm(message))
        mock_autolink.assert_not_called()

    def test_dm_from_someone_else_is_ignored(self):
        message = self._message(author_id=999)
        with patch.object(bot.diary, 'append_diary_entry') as mock_append:
            asyncio.run(bot.handle_diary_dm(message))
        mock_append.assert_not_called()

    def test_empty_dm_without_attachments_is_ignored(self):
        message = self._message(content='   ')
        with patch.object(bot.diary, 'append_diary_entry') as mock_append:
            asyncio.run(bot.handle_diary_dm(message))
        mock_append.assert_not_called()

    def test_page_prefix_creates_a_page_instead_of_appending(self):
        message = self._message(content='ページ:Blender Guru\nチュートリアルが分かりやすい')
        with patch.object(bot, 'create_diary_side_page', new_callable=AsyncMock) as mock_create, \
                patch.object(bot.diary, 'append_diary_entry') as mock_append:
            mock_create.return_value = ('created', 'Blender Guru')
            asyncio.run(bot.handle_diary_dm(message))
        mock_create.assert_awaited_once_with('Blender Guru', 'チュートリアルが分かりやすい', [])
        mock_append.assert_not_called()
        message.add_reaction.assert_awaited_once_with('✅')

    def test_page_prefix_carries_attached_images(self):
        message = self._message(content='ページ:展示メモ', attachments=[_fake_attachment()])
        with patch.object(bot.gyazo_uploader, 'upload_image', return_value='https://i.gyazo.com/a.png'), \
                patch.object(bot, 'create_diary_side_page', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = ('created', '展示メモ')
            asyncio.run(bot.handle_diary_dm(message))
        self.assertEqual(mock_create.await_args.args[2], ['[https://i.gyazo.com/a.png]'])

    def test_page_creation_failure_is_marked_with_a_cross(self):
        message = self._message(content='ページ:Blender Guru')
        with patch.object(bot, 'create_diary_side_page', new_callable=AsyncMock) as mock_create, \
                patch.object(bot, 'record_error') as mock_record:
            mock_create.return_value = (500, 'Blender Guru')
            asyncio.run(bot.handle_diary_dm(message))
        message.add_reaction.assert_awaited_once_with('❌')
        mock_record.assert_called_once()

    def test_append_failure_is_marked_with_a_cross(self):
        message = self._message()
        with patch.object(bot.diary, 'append_diary_entry', return_value=(500, '2026-07-06')), \
                patch.object(bot, 'record_error') as mock_record:
            asyncio.run(bot.handle_diary_dm(message))
        message.add_reaction.assert_awaited_once_with('❌')
        mock_record.assert_called_once()

    def test_first_append_failure_stops_the_rest(self):
        # 単語欄への追記が失敗した状態で写真だけ入ると、成否がリアクションと食い違う
        message = self._message(content='単語:serendipity', attachments=[_fake_attachment()])
        with patch.object(bot.gyazo_uploader, 'upload_image', return_value='https://i.gyazo.com/a.png'), \
                patch.object(bot.diary, 'append_diary_entry', return_value=(500, '2026-07-06')) as mock_append, \
                patch.object(bot, 'record_error'):
            asyncio.run(bot.handle_diary_dm(message))
        self.assertEqual(mock_append.call_count, 1)
        message.add_reaction.assert_awaited_once_with('❌')


class DiaryReminderTaskTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(bot, 'DIARY_SCRAPBOX_PROJECT', 'diary-proj'),
            patch.object(bot, 'DIARY_SCRAPBOX_SID', 'sid'),
            patch.object(bot, 'DIARY_OWNER_USER_ID', 123),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.dm = patch.object(bot, 'send_diary_reminder_dm', new_callable=AsyncMock).start()
        self.addCleanup(patch.stopall)

    def _run(self):
        asyncio.run(bot.diary_reminder_task.coro())

    def test_empty_diary_sends_dm(self):
        with patch.object(bot.diary, 'check_diary_written', return_value=('empty', '2026-07-06')) as mock_check:
            self._run()
        mock_check.assert_called_once_with('diary-proj', 'sid')
        self.dm.assert_awaited_once_with('2026-07-06')

    def test_written_diary_sends_nothing(self):
        with patch.object(bot.diary, 'check_diary_written', return_value=('written', '2026-07-06')):
            self._run()
        self.dm.assert_not_awaited()

    def test_check_failure_does_not_send_dm(self):
        # Scrapboxに繋がらなかっただけで催促すると、書いてある日にもDMが飛ぶ
        with patch.object(bot.diary, 'check_diary_written', return_value=(None, '2026-07-06')):
            self._run()
        self.dm.assert_not_awaited()
        self.assertEqual(bot._task_last_runs['日記リマインド']['ok'], False)

    def test_dm_failure_is_recorded_not_raised(self):
        self.dm.side_effect = Exception('DMを送れません')
        with patch.object(bot.diary, 'check_diary_written', return_value=('empty', '2026-07-06')):
            self._run()
        self.assertEqual(bot._task_last_runs['日記リマインド']['ok'], False)


class SendDiaryReminderDmTests(unittest.TestCase):
    def test_dm_is_sent_to_owner(self):
        user = AsyncMock()
        fake_client = MagicMock()
        fake_client.get_user.return_value = user
        with patch.object(bot, 'client', fake_client), \
                patch.object(bot, 'DIARY_OWNER_USER_ID', 123), \
                patch.object(bot, 'DIARY_SCRAPBOX_PROJECT', 'my-diary'):
            asyncio.run(bot.send_diary_reminder_dm('2026-07-06'))
        fake_client.get_user.assert_called_once_with(123)
        user.send.assert_awaited_once()
        self.assertIn('2026-07-06', user.send.await_args.args[0])

    def test_uncached_user_is_fetched(self):
        user = AsyncMock()
        fake_client = MagicMock()
        fake_client.get_user.return_value = None
        fake_client.fetch_user = AsyncMock(return_value=user)
        with patch.object(bot, 'client', fake_client), \
                patch.object(bot, 'DIARY_OWNER_USER_ID', 123), \
                patch.object(bot, 'DIARY_SCRAPBOX_PROJECT', 'my-diary'):
            asyncio.run(bot.send_diary_reminder_dm('2026-07-06'))
        fake_client.fetch_user.assert_awaited_once_with(123)
        user.send.assert_awaited_once()


class YouTubeRssNotificationTests(unittest.TestCase):
    def test_youtube_rss_task_sends_one_dm_for_new_items(self):
        user = AsyncMock()
        fake_client = MagicMock()
        fake_client.get_user.return_value = user
        item = {
            'video_id': 'abc',
            'channel_title': 'テストチャンネル',
            'title': '新着',
            'url': 'https://youtu.be/abc',
        }
        newer_item = {
            **item,
            'video_id': 'def',
            'title': '次の新着',
            'url': 'https://youtu.be/def',
        }
        with patch.object(bot, 'client', fake_client), \
                patch.object(bot, 'DIARY_OWNER_USER_ID', 123), \
                patch.object(bot, '_youtube_tracker', bot.NotificationTracker()), \
                patch.object(
                    bot,
                    'fetch_youtube_feed',
                    side_effect=[[item]] * 5 + [[item, newer_item]] * 5,
                ):
            asyncio.run(bot.run_youtube_rss_check())
            user.send.assert_not_called()
            asyncio.run(bot.run_youtube_rss_check())
        user.send.assert_awaited_once()
        self.assertIn('次の新着', user.send.await_args.args[0])

    def test_youtube_rss_task_skips_when_owner_is_unset(self):
        fake_client = MagicMock()
        with patch.object(bot, 'client', fake_client), \
                patch.object(bot, 'DIARY_OWNER_USER_ID', 0), \
                patch.object(bot, 'fetch_youtube_feed') as fetch_feed:
            asyncio.run(bot.run_youtube_rss_check())
        fetch_feed.assert_not_called()
        fake_client.get_user.assert_not_called()

    def test_youtube_rss_task_keeps_tracker_when_feed_fetch_fails(self):
        user = AsyncMock()
        fake_client = MagicMock()
        fake_client.get_user.return_value = user
        item = {
            'video_id': 'abc',
            'channel_title': 'テストチャンネル',
            'title': '既存',
            'url': 'https://youtu.be/abc',
        }
        newer_item = {**item, 'video_id': 'def', 'title': '復旧後の新着'}
        fetch_results = [[item]] * 5
        fetch_results += [RuntimeError('RSS failure'), [item], [item], [item], [item]]
        fetch_results += [[item, newer_item]] * 5
        with patch.object(bot, 'client', fake_client), \
                patch.object(bot, 'DIARY_OWNER_USER_ID', 123), \
                patch.object(bot, '_youtube_tracker', bot.NotificationTracker()), \
                patch.object(bot, 'fetch_youtube_feed', side_effect=fetch_results), \
                patch.object(bot, 'record_error') as record_error:
            asyncio.run(bot.run_youtube_rss_check())
            asyncio.run(bot.run_youtube_rss_check())
            record_error.assert_called_once()
            asyncio.run(bot.run_youtube_rss_check())
        user.send.assert_awaited_once()
        self.assertIn('復旧後の新着', user.send.await_args.args[0])

    def test_youtube_rss_task_retries_after_dm_failure(self):
        user = AsyncMock()
        user.send.side_effect = [RuntimeError('DM failure'), None]
        fake_client = MagicMock()
        fake_client.get_user.return_value = user
        item = {
            'video_id': 'abc',
            'channel_title': 'テストチャンネル',
            'title': '既存',
            'url': 'https://youtu.be/abc',
        }
        newer_item = {**item, 'video_id': 'def', 'title': '再送された新着'}
        fetch_results = [[item]] * 5 + [[item, newer_item]] * 10
        with patch.object(bot, 'client', fake_client), \
                patch.object(bot, 'DIARY_OWNER_USER_ID', 123), \
                patch.object(bot, '_youtube_tracker', bot.NotificationTracker()), \
                patch.object(bot, 'fetch_youtube_feed', side_effect=fetch_results), \
                patch.object(bot, 'record_error') as record_error:
            asyncio.run(bot.run_youtube_rss_check())
            asyncio.run(bot.run_youtube_rss_check())
            record_error.assert_called_once()
            asyncio.run(bot.run_youtube_rss_check())
        self.assertEqual(user.send.await_count, 2)
        self.assertIn('再送された新着', user.send.await_args.args[0])


class GenericRssNotificationTests(unittest.TestCase):
    def setUp(self):
        self.user = AsyncMock()
        self.fake_client = MagicMock()
        self.fake_client.get_user.return_value = self.user
        self.old_feed = bot.FeedConfig('feed-a', 'https://example.com/a.xml')
        self.other_feed = bot.FeedConfig('feed-b', 'https://example.com/b.xml')
        self.old_item = {
            'item_id': 'old', 'feed_name': 'feed-a', 'channel_title': 'Feed A',
            'title': 'old', 'url': 'https://example.com/old',
        }
        self.new_item = {**self.old_item, 'item_id': 'new', 'title': 'new'}

    def test_rss_check_notifies_successful_feed_when_another_feed_fails(self):
        with patch.object(bot, 'client', self.fake_client), \
                patch.object(bot, 'DIARY_OWNER_USER_ID', 123), \
                patch.object(bot, 'RSS_FEEDS', [self.old_feed, self.other_feed]), \
                patch.object(bot, '_rss_tracker', bot.NotificationTracker()), \
                patch.object(bot, '_rss_health', {feed.name: bot.FeedHealth() for feed in [self.old_feed, self.other_feed]}), \
                patch.object(bot, 'fetch_rss_feed', side_effect=[
                    [self.old_item], RuntimeError('bad'),
                    [self.old_item, self.new_item], RuntimeError('bad'),
                ]):
            asyncio.run(bot.run_rss_check())
            self.user.send.assert_not_awaited()
            asyncio.run(bot.run_rss_check())
        self.user.send.assert_awaited_once()
        self.assertIn('new', self.user.send.await_args.args[0])

    def test_rss_check_skips_when_owner_is_unset(self):
        with patch.object(bot, 'DIARY_OWNER_USER_ID', 0), \
                patch.object(bot, 'fetch_rss_feed') as fetch_feed:
            asyncio.run(bot.run_rss_check())
        fetch_feed.assert_not_called()

    def test_rss_status_lines_include_feed_failure_count(self):
        health = bot.FeedHealth(consecutive_failures=2, last_error='timeout')
        with patch.object(bot, 'RSS_FEEDS', [self.old_feed]), \
                patch.object(bot, '_rss_health', {'feed-a': health}):
            lines = bot.build_rss_status_lines(now=1000)
        self.assertIn('feed-a', '\n'.join(lines))
        self.assertIn('失敗2回', '\n'.join(lines))
        self.assertIn('timeout', '\n'.join(lines))


class RssCommandTests(unittest.TestCase):
    def test_owner_guard_accepts_only_configured_user(self):
        owner = MagicMock()
        owner.id = 123
        other = MagicMock()
        other.id = 456
        with patch.object(bot, 'DIARY_OWNER_USER_ID', 123):
            self.assertTrue(bot._rss_owner_allowed(owner))
            self.assertFalse(bot._rss_owner_allowed(other))

    def test_unconfigured_owner_is_denied(self):
        owner = MagicMock(id=123)
        with patch.object(bot, 'DIARY_OWNER_USER_ID', 0):
            self.assertFalse(bot._rss_owner_allowed(owner))

    def test_find_feed_by_name(self):
        feed = bot.FeedConfig('qiita', 'https://example.com/feed.xml')
        with patch.object(bot, 'RSS_FEEDS', [feed]):
            self.assertIs(bot._find_rss_feed('qiita'), feed)
            self.assertIsNone(bot._find_rss_feed('missing'))

    def test_pause_and_resume_helpers_change_runtime_state(self):
        feed = bot.FeedConfig('qiita', 'https://example.com/feed.xml')
        health = bot.FeedHealth()
        with patch.object(bot, 'RSS_FEEDS', [feed]), \
                patch.object(bot, '_rss_health', {'qiita': health}):
            self.assertTrue(bot.set_rss_paused('qiita', True))
            self.assertTrue(health.paused)
            self.assertTrue(bot.set_rss_paused('qiita', False))
            self.assertFalse(health.paused)
            self.assertFalse(bot.set_rss_paused('missing', True))


class EagleImportApiTests(unittest.TestCase):
    def setUp(self):
        self.store_patch = patch.object(bot, '_eagle_store', EagleImportStore())
        self.store = self.store_patch.start()
        self.token_patch = patch.object(bot, 'EAGLE_BRIDGE_TOKEN', 'test-token')
        self.token_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.token_patch.stop)

    def _seed_running_job(self):
        preview = self.store.create_preview(
            ['A'], lambda title: [title, 'https://youtu.be/x'], 'https://scrapbox.io/proj'
        )
        job = self.store.confirm(preview.preview_id)[0]
        return self.store.claim(1)[0]

    def test_eagle_jobs_rejects_missing_bridge_token(self):
        status, payload = bot.handle_eagle_jobs_request('', 1)

        self.assertEqual(status, 401)
        self.assertIn('error', payload)

    def test_eagle_jobs_claims_pending_job_with_valid_token(self):
        preview = self.store.create_preview(
            ['A'], lambda title: [title, 'https://youtu.be/x'], 'https://scrapbox.io/proj'
        )
        job = self.store.confirm(preview.preview_id)[0]

        status, payload = bot.handle_eagle_jobs_request('test-token', 1)

        self.assertEqual(status, 200)
        self.assertEqual(payload['jobs'][0]['job_id'], job.job_id)
        self.assertEqual(self.store.status()['running'], 1)

    def test_eagle_result_marks_job_succeeded(self):
        job = self._seed_running_job()

        status, payload = bot.handle_eagle_result_request(
            'test-token', job.job_id, json.dumps({'status': 'succeeded', 'title': 'Video'}).encode()
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload['status'], 'succeeded')
        self.assertEqual(self.store.status()['succeeded'], 1)

    def test_eagle_preview_rejects_missing_bridge_token(self):
        status, payload = bot.handle_eagle_preview_request('')

        self.assertEqual(status, 401)
        self.assertIn('error', payload)

    def test_eagle_preview_returns_preview_for_plugin(self):
        preview = self.store.create_preview(
            ['A'], lambda title: [title, 'https://youtu.be/x'], 'https://scrapbox.io/proj'
        )
        with patch.object(bot, 'create_eagle_preview', return_value=(preview, None)):
            status, payload = bot.handle_eagle_preview_request('test-token')

        self.assertEqual(status, 200)
        self.assertEqual(payload['preview_id'], preview.preview_id)
        self.assertEqual(payload['video_count'], 1)

    def test_eagle_confirm_creates_jobs_for_plugin(self):
        preview = self.store.create_preview(
            ['A'], lambda title: [title, 'https://youtu.be/x'], 'https://scrapbox.io/proj'
        )

        status, payload = bot.handle_eagle_confirm_request('test-token', preview.preview_id)

        self.assertEqual(status, 200)
        self.assertEqual(payload['jobs_created'], 1)
        self.assertEqual(self.store.status()['pending'], 1)

    def test_eagle_status_returns_counts_for_plugin(self):
        status, payload = bot.handle_eagle_status_request('test-token')

        self.assertEqual(status, 200)
        self.assertEqual(payload['counts']['pending'], 0)
        self.assertIn('preview_id', payload)


class EagleImportCommandTests(unittest.TestCase):
    def test_owner_guard_accepts_only_configured_user(self):
        owner = MagicMock(id=123)
        other = MagicMock(id=456)
        with patch.object(bot, 'DIARY_OWNER_USER_ID', 123):
            self.assertTrue(bot._eagle_owner_allowed(owner))
            self.assertFalse(bot._eagle_owner_allowed(other))

    def test_import_all_without_confirm_only_sends_preview(self):
        interaction = MagicMock()
        interaction.user.id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        store = EagleImportStore()
        preview = store.create_preview(
            ['A'], lambda title: [title, 'https://youtu.be/x'], 'https://scrapbox.io/proj'
        )
        with patch.object(bot, 'DIARY_OWNER_USER_ID', 123), \
                patch.object(bot, '_eagle_store', store), \
                patch.object(bot, 'create_eagle_preview', return_value=(preview, None)):
            asyncio.run(bot.eagle_import_all.callback(interaction, confirm=False))

        self.assertEqual(store.status()['pending'], 0)
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()


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
