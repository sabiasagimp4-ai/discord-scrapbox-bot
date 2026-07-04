import json
import unittest
from datetime import datetime
from unittest.mock import patch

import audit_log


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=''):
        self._json_data = json_data or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json_data


NOW = datetime(2026, 7, 4, 21, 3, tzinfo=audit_log.JST)


class PageTitleForTests(unittest.TestCase):
    def test_monthly_rotation(self):
        self.assertEqual(audit_log.page_title_for(NOW), 'bot設定/監査ログ/2026-07')


class BuildEntryTests(unittest.TestCase):
    def test_basic_format(self):
        entry = audit_log.build_entry(NOW, 'note', 'MV案件X', 'sabiasagi')
        self.assertEqual(entry, ' 2026-07-04 21:03 | note | MV案件X | 実行者:sabiasagi')

    def test_detail_is_appended(self):
        entry = audit_log.build_entry(NOW, 'save', 'ページ', 'user', 'https://example.com')
        self.assertTrue(entry.endswith(' | https://example.com'))

    def test_newlines_and_pipes_are_sanitized(self):
        entry = audit_log.build_entry(NOW, 'note', 'ペー|ジ\n改行', 'us|er')
        self.assertNotIn('\n', entry)
        # 区切り文字は4つのまま（detail無し）であること
        self.assertEqual(entry.count('|'), 3)

    def test_empty_actor_becomes_unknown(self):
        entry = audit_log.build_entry(NOW, 'save', 'ページ', '')
        self.assertIn('実行者:不明', entry)


class AppendEntryTests(unittest.TestCase):
    def test_appends_to_existing_month_page(self):
        existing = {'persistent': True, 'lines': [
            {'text': 'bot設定/監査ログ/2026-07'}, {'text': ' 2026-07-01 09:00 | save | 既存 | 実行者:a'},
        ]}
        with patch('audit_log.requests.get', return_value=FakeResponse(existing)):
            with patch('audit_log.requests.post', return_value=FakeResponse(status_code=200)) as mock_post:
                status = audit_log.append_entry('proj', 'sid', 'note', 'MV案件X', 'user', now=NOW)
        self.assertEqual(status, 200)
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        self.assertEqual(lines[1], ' 2026-07-01 09:00 | save | 既存 | 実行者:a')
        self.assertIn('MV案件X', lines[2])

    def test_creates_month_page_when_missing(self):
        with patch('audit_log.requests.get', return_value=FakeResponse(status_code=404)):
            with patch('audit_log.requests.post', return_value=FakeResponse(status_code=200)) as mock_post:
                status = audit_log.append_entry('proj', 'sid', 'save', 'ページ', 'user', now=NOW)
        self.assertEqual(status, 200)
        lines = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])['pages'][0]['lines']
        self.assertEqual(lines[0], 'bot設定/監査ログ/2026-07')
        self.assertEqual(len(lines), 2)

    def test_get_failure_returns_none_without_post(self):
        with patch('audit_log.requests.get', side_effect=Exception('timeout')):
            with patch('audit_log.requests.post') as mock_post:
                status = audit_log.append_entry('proj', 'sid', 'save', 'ページ', 'user', now=NOW)
        mock_post.assert_not_called()
        self.assertIsNone(status)

    def test_post_failure_returns_none(self):
        with patch('audit_log.requests.get', return_value=FakeResponse(status_code=404)):
            with patch('audit_log.requests.post', side_effect=Exception('boom')):
                status = audit_log.append_entry('proj', 'sid', 'save', 'ページ', 'user', now=NOW)
        self.assertIsNone(status)


if __name__ == '__main__':
    unittest.main()
