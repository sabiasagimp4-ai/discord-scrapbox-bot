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
        self.assertIn('[* 今日のできごと]', lines)
        self.assertIn('#日記', lines)

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
