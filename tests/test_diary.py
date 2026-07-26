import json
import unittest
from datetime import datetime
from unittest.mock import patch

import diary


class FakeResponse:
    def __init__(self, status_code=200, text='', json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


NOW = datetime(2026, 7, 6, 0, 5, tzinfo=diary.JST)


class DiaryTitleForTests(unittest.TestCase):
    def test_formats_as_iso_date(self):
        self.assertEqual(diary.diary_title_for(NOW), '2026-07-06')


class BuildTemplateTests(unittest.TestCase):
    def test_tag_line_is_first(self):
        # タイトル（日付）の直下に #日記 タグを置く。「日記」ページの逆リンクが
        # 全日記ページの一覧として機能する（Karureの #Karure制作 と同じ仕組み）
        lines = diary.build_template(NOW)
        self.assertEqual(lines[0], '#日記')

    def test_nav_line_links_prev_today_next(self):
        lines = diary.build_template(NOW)
        self.assertEqual(lines[1], '<- [2026-07-05] / [2026-07-06] / [2026-07-07] ->')

    def test_headings_and_blank_lines(self):
        lines = diary.build_template(NOW)
        self.assertEqual(lines[2:], ['', '', '【新しく知った単語】', '', '【日記】'])

    def test_month_boundary(self):
        dt = datetime(2026, 8, 1, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[1], '<- [2026-07-31] / [2026-08-01] / [2026-08-02] ->')

    def test_year_boundary(self):
        dt = datetime(2027, 1, 1, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[1], '<- [2026-12-31] / [2027-01-01] / [2027-01-02] ->')

    def test_leap_year_february_29(self):
        # 2028年はうるう年 → 2/29が存在する
        dt = datetime(2028, 2, 29, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[1], '<- [2028-02-28] / [2028-02-29] / [2028-03-01] ->')

    def test_non_leap_year_february_28_rolls_to_march(self):
        # 2026年は平年 → 2/28の翌日は2/29ではなく3/1
        dt = datetime(2026, 2, 28, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[1], '<- [2026-02-27] / [2026-02-28] / [2026-03-01] ->')


class CreateDiaryPageTests(unittest.TestCase):
    def test_creates_page_with_template_when_missing(self):
        with patch('diary.name_linker.check_page_exists', return_value=False):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                status, title = diary.create_diary_page('proj', 'sid', dt=NOW)
        self.assertEqual(status, 'created')
        self.assertEqual(title, '2026-07-06')
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        lines = payload['pages'][0]['lines']
        self.assertEqual(lines[0], '2026-07-06')
        self.assertEqual(lines[1], '#日記')
        self.assertIn('<- [2026-07-05] / [2026-07-06] / [2026-07-07] ->', lines)
        self.assertIn('【新しく知った単語】', lines)
        self.assertIn('【日記】', lines)

    def test_skips_when_page_already_exists(self):
        with patch('diary.name_linker.check_page_exists', return_value=True):
            with patch('diary.requests.post') as mock_post:
                status, title = diary.create_diary_page('proj', 'sid', dt=NOW)
        mock_post.assert_not_called()
        self.assertEqual(status, 'exists')

    def test_defaults_to_now_when_dt_omitted(self):
        with patch('diary.name_linker.check_page_exists', return_value=True) as mock_check:
            diary.create_diary_page('proj', 'sid')
        called_title = mock_check.call_args.args[2]
        self.assertRegex(called_title, r'^\d{4}-\d{2}-\d{2}$')

    def test_post_failure_returns_status_code(self):
        with patch('diary.name_linker.check_page_exists', return_value=False):
            with patch('diary.requests.post', return_value=FakeResponse(403, 'forbidden')):
                status, title = diary.create_diary_page('proj', 'sid', dt=NOW)
        self.assertEqual(status, 403)

    def test_network_exception_returns_none(self):
        with patch('diary.name_linker.check_page_exists', return_value=False):
            with patch('diary.requests.post', side_effect=Exception('timeout')):
                status, title = diary.create_diary_page('proj', 'sid', dt=NOW)
        self.assertIsNone(status)


class BuildEntryLinesTests(unittest.TestCase):
    def test_single_line_entry(self):
        self.assertEqual(diary.build_entry_lines('今日は楽しかった'), [' 今日は楽しかった'])

    def test_multiline_entry_indents_continuation(self):
        lines = diary.build_entry_lines('1行目\n2行目')
        self.assertEqual(lines, [' 1行目', '  2行目'])

    def test_trailing_whitespace_is_stripped(self):
        self.assertEqual(diary.build_entry_lines('本文   '), [' 本文'])


class ClassifyEntryTests(unittest.TestCase):
    def test_vocab_prefix_half_width_colon(self):
        self.assertEqual(diary.classify_entry('単語:serendipity'), ('vocab', 'serendipity'))

    def test_vocab_prefix_full_width_colon(self):
        self.assertEqual(diary.classify_entry('単語：serendipity'), ('vocab', 'serendipity'))

    def test_no_prefix_is_diary(self):
        self.assertEqual(diary.classify_entry('今日は楽しかった'), ('diary', '今日は楽しかった'))

    def test_prefix_with_extra_spaces_is_trimmed(self):
        self.assertEqual(diary.classify_entry('単語:  serendipity  '), ('vocab', 'serendipity'))


class AppendDiaryEntryTests(unittest.TestCase):
    def test_appends_to_existing_page_preserving_body(self):
        existing = {'persistent': True, 'lines': [
            {'text': '2026-07-06'},
            {'text': '<- [2026-07-05] / [2026-07-06] / [2026-07-07] ->'},
            {'text': ''}, {'text': ''},
            {'text': '【新しく知った単語】'}, {'text': ''}, {'text': '【日記】'},
        ]}
        with patch('diary.requests.get', return_value=FakeResponse(200, json_data=existing)):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                status, title = diary.append_diary_entry('proj', 'sid', '楽しい一日だった', dt=NOW)
        self.assertEqual(status, 'appended')
        self.assertEqual(title, '2026-07-06')
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        lines = payload['pages'][0]['lines']
        self.assertEqual(lines[0], '2026-07-06')
        self.assertIn('【日記】', lines)  # 既存の雛形部分が保持されている
        self.assertEqual(lines[-1], ' 楽しい一日だった')

    def test_trailing_blank_lines_from_fetch_are_trimmed_before_append(self):
        # Scrapbox取得結果の末尾に空行が残っていても、追記のたびに空行が増えない
        existing = {'persistent': True, 'lines': [
            {'text': '2026-07-06'}, {'text': '【日記】'}, {'text': ' 既存エントリ'}, {'text': ''}, {'text': ''},
        ]}
        with patch('diary.requests.get', return_value=FakeResponse(200, json_data=existing)):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                diary.append_diary_entry('proj', 'sid', '新しいエントリ', dt=NOW)
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        self.assertEqual(lines[-2:], [' 既存エントリ', ' 新しいエントリ'])

    def test_creates_page_with_template_when_missing_then_appends(self):
        with patch('diary.requests.get', return_value=FakeResponse(404)):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                status, title = diary.append_diary_entry('proj', 'sid', '初めての投稿', dt=NOW)
        self.assertEqual(status, 'appended')
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        lines = payload['pages'][0]['lines']
        self.assertEqual(lines[0], '2026-07-06')
        self.assertIn('【日記】', lines)
        self.assertEqual(lines[-1], ' 初めての投稿')

    def test_multiline_message_is_appended_correctly(self):
        with patch('diary.requests.get', return_value=FakeResponse(404)):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                diary.append_diary_entry('proj', 'sid', '1行目\n2行目', dt=NOW)
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        self.assertEqual(lines[-2:], [' 1行目', '  2行目'])

    def test_vocab_section_inserts_before_diary_heading(self):
        existing = {'persistent': True, 'lines': [
            {'text': '2026-07-06'},
            {'text': '【新しく知った単語】'}, {'text': ' 既存単語'},
            {'text': '【日記】'}, {'text': ' 既存日記'},
        ]}
        with patch('diary.requests.get', return_value=FakeResponse(200, json_data=existing)):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                status, title = diary.append_diary_entry('proj', 'sid', 'serendipity', section='vocab', dt=NOW)
        self.assertEqual(status, 'appended')
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        # 単語はScrapboxのリンク記法 [ ] で囲まれ、クリックで単語ごとのページを開ける
        self.assertEqual(lines, [
            '2026-07-06', '【新しく知った単語】', ' 既存単語', ' [serendipity]', '【日記】', ' 既存日記',
        ])

    def test_vocab_section_falls_back_to_append_when_heading_missing(self):
        existing = {'persistent': True, 'lines': [{'text': '2026-07-06'}, {'text': '本文のみ'}]}
        with patch('diary.requests.get', return_value=FakeResponse(200, json_data=existing)):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                diary.append_diary_entry('proj', 'sid', 'serendipity', section='vocab', dt=NOW)
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        self.assertEqual(lines[-1], ' [serendipity]')

    def test_diary_section_is_not_wrapped_in_brackets(self):
        # 【日記】欄はこれまで通りプレーンテキストのまま（単語欄のみ[]で囲む）
        with patch('diary.requests.get', return_value=FakeResponse(404)):
            with patch('diary.requests.post', return_value=FakeResponse(200)) as mock_post:
                diary.append_diary_entry('proj', 'sid', '今日は楽しかった', section='diary', dt=NOW)
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        self.assertEqual(lines[-1], ' 今日は楽しかった')

    def test_get_failure_returns_none_without_post(self):
        with patch('diary.requests.get', side_effect=Exception('timeout')):
            with patch('diary.requests.post') as mock_post:
                status, title = diary.append_diary_entry('proj', 'sid', 'テキスト', dt=NOW)
        mock_post.assert_not_called()
        self.assertIsNone(status)

    def test_post_failure_returns_status_code(self):
        with patch('diary.requests.get', return_value=FakeResponse(404)):
            with patch('diary.requests.post', return_value=FakeResponse(500)):
                status, title = diary.append_diary_entry('proj', 'sid', 'テキスト', dt=NOW)
        self.assertEqual(status, 500)

    def test_defaults_to_now_when_dt_omitted(self):
        with patch('diary.requests.get', return_value=FakeResponse(404)) as mock_get:
            with patch('diary.requests.post', return_value=FakeResponse(200)):
                diary.append_diary_entry('proj', 'sid', 'テキスト')
        called_url = mock_get.call_args.args[0]
        self.assertRegex(called_url, r'\d{4}-\d{2}-\d{2}')


class HasEntriesTests(unittest.TestCase):
    def test_fresh_template_has_no_entries(self):
        self.assertFalse(diary.has_entries(diary.build_template(NOW)))

    def test_empty_body_has_no_entries(self):
        self.assertFalse(diary.has_entries([]))

    def test_diary_entry_counts_as_written(self):
        body = diary.build_template(NOW) + [' 今日は楽しかった']
        self.assertTrue(diary.has_entries(body))

    def test_vocab_entry_counts_as_written(self):
        body = list(diary.build_template(NOW))
        diary._insert_before_heading(body, diary.DIARY_HEADING, [' [serendipity]'])
        self.assertTrue(diary.has_entries(body))

    def test_whitespace_only_lines_are_not_entries(self):
        self.assertFalse(diary.has_entries(['   ', '\t', '']))

    def test_nav_line_of_any_date_is_not_an_entry(self):
        self.assertFalse(diary.has_entries(['<- [2028-02-28] / [2028-02-29] / [2028-03-01] ->']))

    def test_indented_headings_are_still_template(self):
        # Scrapbox上でインデントされていても見出しは雛形の一部
        self.assertFalse(diary.has_entries([' 【日記】', ' #日記']))


class CheckDiaryWrittenTests(unittest.TestCase):
    def test_template_only_page_is_empty(self):
        existing = {'persistent': True, 'lines': [{'text': '2026-07-06'}] + [
            {'text': line} for line in diary.build_template(NOW)
        ]}
        with patch('diary.requests.get', return_value=FakeResponse(200, json_data=existing)):
            state, title = diary.check_diary_written('proj', 'sid', dt=NOW)
        self.assertEqual((state, title), ('empty', '2026-07-06'))

    def test_page_with_entry_is_written(self):
        existing = {'persistent': True, 'lines': [
            {'text': '2026-07-06'}, {'text': '【日記】'}, {'text': ' 今日は楽しかった'},
        ]}
        with patch('diary.requests.get', return_value=FakeResponse(200, json_data=existing)):
            state, _ = diary.check_diary_written('proj', 'sid', dt=NOW)
        self.assertEqual(state, 'written')

    def test_missing_page_is_empty(self):
        with patch('diary.requests.get', return_value=FakeResponse(404)):
            state, title = diary.check_diary_written('proj', 'sid', dt=NOW)
        self.assertEqual((state, title), ('empty', '2026-07-06'))

    def test_non_persistent_page_is_empty(self):
        with patch('diary.requests.get', return_value=FakeResponse(200, json_data={'persistent': False})):
            state, _ = diary.check_diary_written('proj', 'sid', dt=NOW)
        self.assertEqual(state, 'empty')

    def test_network_failure_returns_none_not_empty(self):
        # 通信失敗を「空」と扱うと、書いてあるのに催促DMが飛んでしまう
        with patch('diary.requests.get', side_effect=Exception('timeout')):
            state, title = diary.check_diary_written('proj', 'sid', dt=NOW)
        self.assertIsNone(state)
        self.assertEqual(title, '2026-07-06')

    def test_defaults_to_now_when_dt_omitted(self):
        with patch('diary.requests.get', return_value=FakeResponse(404)) as mock_get:
            diary.check_diary_written('proj', 'sid')
        self.assertRegex(mock_get.call_args.args[0], r'\d{4}-\d{2}-\d{2}')


if __name__ == '__main__':
    unittest.main()
