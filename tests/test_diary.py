import json
import unittest
from datetime import datetime
from unittest.mock import patch

import diary


class FakeResponse:
    def __init__(self, status_code=200, text=''):
        self.status_code = status_code
        self.text = text


NOW = datetime(2026, 7, 6, 0, 5, tzinfo=diary.JST)


class DiaryTitleForTests(unittest.TestCase):
    def test_formats_as_iso_date(self):
        self.assertEqual(diary.diary_title_for(NOW), '2026-07-06')


class BuildTemplateTests(unittest.TestCase):
    def test_nav_line_links_prev_today_next(self):
        lines = diary.build_template(NOW)
        self.assertEqual(lines[0], '<- [2026-07-05] / [2026-07-06] / [2026-07-07] ->')

    def test_headings_and_blank_lines(self):
        lines = diary.build_template(NOW)
        self.assertEqual(lines[1:], ['', '', '【新しく知った単語】', '', '【日記】'])

    def test_month_boundary(self):
        dt = datetime(2026, 8, 1, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[0], '<- [2026-07-31] / [2026-08-01] / [2026-08-02] ->')

    def test_year_boundary(self):
        dt = datetime(2027, 1, 1, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[0], '<- [2026-12-31] / [2027-01-01] / [2027-01-02] ->')

    def test_leap_year_february_29(self):
        # 2028年はうるう年 → 2/29が存在する
        dt = datetime(2028, 2, 29, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[0], '<- [2028-02-28] / [2028-02-29] / [2028-03-01] ->')

    def test_non_leap_year_february_28_rolls_to_march(self):
        # 2026年は平年 → 2/28の翌日は2/29ではなく3/1
        dt = datetime(2026, 2, 28, tzinfo=diary.JST)
        self.assertEqual(diary.build_template(dt)[0], '<- [2026-02-27] / [2026-02-28] / [2026-03-01] ->')


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


if __name__ == '__main__':
    unittest.main()
