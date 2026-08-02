import json
import unittest
from unittest.mock import patch

import name_linker


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=''):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


class NormalizeTests(unittest.TestCase):
    def test_strips_and_lowercases(self):
        self.assertEqual(name_linker._normalize('  Yamada Taro  '), 'yamada taro')


class DiceCoefficientTests(unittest.TestCase):
    def test_identical_strings_score_one(self):
        self.assertEqual(name_linker._dice_coefficient('Yamada Taro', 'Yamada Taro'), 1.0)

    def test_completely_different_strings_score_zero(self):
        self.assertEqual(name_linker._dice_coefficient('abc', 'xyz'), 0.0)

    def test_near_match_scores_high(self):
        score = name_linker._dice_coefficient('Yamada Tarou', 'Yamada Taro')
        self.assertGreaterEqual(score, 0.9)


class ResolveNameTests(unittest.TestCase):
    def test_exact_match_returns_link(self):
        pages = ['山田太郎', '鈴木花子']
        self.assertEqual(name_linker.resolve_name('山田太郎', pages, {}), '[山田太郎]')

    def test_exact_match_is_case_insensitive(self):
        pages = ['Yamada Taro']
        self.assertEqual(name_linker.resolve_name('yamada taro', pages, {}), '[Yamada Taro]')

    def test_alias_resolves_to_existing_page(self):
        pages = ['山田太郎']
        alias_map = {'taro': '山田太郎'}
        self.assertEqual(name_linker.resolve_name('Taro', pages, alias_map), '[山田太郎]')

    def test_alias_canonical_not_in_pages_still_links_canonical(self):
        pages = []
        alias_map = {'taro': '山田太郎'}
        self.assertEqual(name_linker.resolve_name('Taro', pages, alias_map), '[山田太郎]')

    def test_fuzzy_match_above_threshold(self):
        pages = ['Christopher Anderson']
        self.assertEqual(name_linker.resolve_name('Christoper Anderson', pages, {}), '[Christopher Anderson]')

    def test_no_match_returns_plain_name(self):
        pages = ['鈴木花子']
        self.assertEqual(name_linker.resolve_name('全然違う名前', pages, {}), '全然違う名前')


class LoadAliasMapTests(unittest.TestCase):
    def test_empty_title_returns_empty_dict_without_network(self):
        with patch('name_linker.requests.get') as mock_get:
            result = name_linker.load_alias_map('proj', 'sid', '')
            mock_get.assert_not_called()
            self.assertEqual(result, {})

    def test_parses_canonical_alias_lines(self):
        lines = [
            {'text': '山田太郎 == タロー, Taro'},
            {'text': 'this line has no separator'},
        ]
        with patch('name_linker.requests.get', return_value=FakeResponse(200, {'lines': lines})):
            result = name_linker.load_alias_map('proj', 'sid', '表記ゆれ')
        self.assertEqual(result, {'タロー': '山田太郎', 'taro': '山田太郎'})

    def test_non_200_returns_empty_dict(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(404)):
            result = name_linker.load_alias_map('proj', 'sid', '表記ゆれ')
        self.assertEqual(result, {})


class CheckPageExistsTests(unittest.TestCase):
    def test_persistent_true_returns_true(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(200, {'persistent': True})):
            self.assertTrue(name_linker.check_page_exists('proj', 'sid', 'タイトル'))

    def test_link_only_page_persistent_false_returns_false(self):
        # Scrapboxはリンクのみのページでもステータス200を返すため、persistentで判定する必要がある
        with patch('name_linker.requests.get', return_value=FakeResponse(200, {'persistent': False})):
            self.assertFalse(name_linker.check_page_exists('proj', 'sid', 'タイトル'))

    def test_non_200_returns_false(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(403)):
            self.assertFalse(name_linker.check_page_exists('proj', 'sid', 'タイトル'))

    def test_request_exception_returns_false(self):
        with patch('name_linker.requests.get', side_effect=Exception('network error')):
            self.assertFalse(name_linker.check_page_exists('proj', 'sid', 'タイトル'))


class LoadExistingPagesTests(unittest.TestCase):
    def test_single_page_under_limit_stops_pagination(self):
        batch = [{'title': 'A'}, {'title': 'B'}]
        with patch('name_linker.requests.get', return_value=FakeResponse(200, {'pages': batch})) as mock_get:
            result = name_linker.load_existing_pages('proj', 'sid')
        self.assertEqual(result, ['A', 'B'])
        mock_get.assert_called_once()

    def test_request_exception_returns_empty_list(self):
        with patch('name_linker.requests.get', side_effect=Exception('network error')):
            result = name_linker.load_existing_pages('proj', 'sid')
        self.assertEqual(result, [])


class FetchAllPageTitlesTests(unittest.TestCase):
    def test_complete_fetch_reports_ok(self):
        batch = [{'title': 'A'}, {'title': 'B'}]
        with patch('name_linker.requests.get', return_value=FakeResponse(200, {'pages': batch})):
            ok, titles = name_linker.fetch_all_page_titles('proj', 'sid')
        self.assertTrue(ok)
        self.assertEqual(titles, ['A', 'B'])

    def test_request_exception_reports_incomplete(self):
        # 「取れなかった」を「ページが減った」と取り違えると、新規ページ通知が暴発する
        with patch('name_linker.requests.get', side_effect=Exception('network error')):
            ok, titles = name_linker.fetch_all_page_titles('proj', 'sid')
        self.assertFalse(ok)
        self.assertEqual(titles, [])

    def test_error_status_reports_incomplete(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(403)):
            ok, titles = name_linker.fetch_all_page_titles('proj', 'sid')
        self.assertFalse(ok)

    def test_failure_midway_returns_the_partial_list_as_incomplete(self):
        full_batch = [{'title': f'記事{i}'} for i in range(1000)]
        responses = [FakeResponse(200, {'pages': full_batch}), FakeResponse(500)]
        with patch('name_linker.requests.get', side_effect=responses):
            ok, titles = name_linker.fetch_all_page_titles('proj', 'sid')
        self.assertFalse(ok)
        self.assertEqual(len(titles), 1000)

    def test_invalid_json_reports_incomplete(self):
        response = FakeResponse(200)
        response.json = lambda: (_ for _ in ()).throw(ValueError('not json'))
        with patch('name_linker.requests.get', return_value=response):
            ok, titles = name_linker.fetch_all_page_titles('proj', 'sid')
        self.assertFalse(ok)


class AddAliasTests(unittest.TestCase):
    @staticmethod
    def _sent_lines(mock_post):
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        return payload['pages'][0]['lines']

    def test_creates_new_line_when_page_does_not_exist(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(200, {'persistent': False})):
            with patch('name_linker.requests.post', return_value=FakeResponse(200, text='ok')) as mock_post:
                status, body = name_linker.add_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'タロー')
        self.assertEqual(status, 200)
        self.assertEqual(self._sent_lines(mock_post), ['表記ゆれ', '山田太郎 == タロー'])

    def test_appends_to_existing_canonical_line_without_dropping_other_lines(self):
        page = {
            'persistent': True,
            'lines': [
                {'text': '表記ゆれ'},
                {'text': '山田太郎 == タロー'},
                {'text': '鈴木花子 == ハナコ'},
            ],
        }
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            with patch('name_linker.requests.post', return_value=FakeResponse(200, text='ok')) as mock_post:
                status, body = name_linker.add_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'Taro')
        self.assertEqual(status, 200)
        self.assertEqual(
            self._sent_lines(mock_post),
            ['表記ゆれ', '山田太郎 == タロー, Taro', '鈴木花子 == ハナコ'],
        )

    def test_creates_new_line_when_canonical_not_found_in_existing_page(self):
        page = {
            'persistent': True,
            'lines': [{'text': '表記ゆれ'}, {'text': '鈴木花子 == ハナコ'}],
        }
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            with patch('name_linker.requests.post', return_value=FakeResponse(200, text='ok')) as mock_post:
                status, body = name_linker.add_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'タロー')
        self.assertEqual(status, 200)
        self.assertEqual(
            self._sent_lines(mock_post),
            ['表記ゆれ', '鈴木花子 == ハナコ', '山田太郎 == タロー'],
        )

    def test_already_registered_alias_skips_post(self):
        page = {
            'persistent': True,
            'lines': [{'text': '表記ゆれ'}, {'text': '山田太郎 == タロー, Taro'}],
        }
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            with patch('name_linker.requests.post') as mock_post:
                status, body = name_linker.add_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'taro')
        mock_post.assert_not_called()
        self.assertEqual(status, 200)
        self.assertEqual(body, '既に登録済みです')

    def test_get_exception_returns_none_status(self):
        with patch('name_linker.requests.get', side_effect=Exception('network error')):
            with patch('name_linker.requests.post') as mock_post:
                status, body = name_linker.add_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'タロー')
        mock_post.assert_not_called()
        self.assertIsNone(status)


class ListAliasesTests(unittest.TestCase):
    def test_empty_title_returns_empty_list_without_network(self):
        with patch('name_linker.requests.get') as mock_get:
            result = name_linker.list_aliases('proj', 'sid', '')
            mock_get.assert_not_called()
            self.assertEqual(result, [])

    def test_returns_body_lines_excluding_title(self):
        page = {
            'persistent': True,
            'lines': [
                {'text': '表記ゆれ'},
                {'text': '山田太郎 == タロー, Taro'},
                {'text': ''},
                {'text': '鈴木花子 == ハナコ'},
            ],
        }
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            result = name_linker.list_aliases('proj', 'sid', '表記ゆれ')
        self.assertEqual(result, ['山田太郎 == タロー, Taro', '鈴木花子 == ハナコ'])

    def test_non_persistent_page_returns_empty_list(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(200, {'persistent': False})):
            result = name_linker.list_aliases('proj', 'sid', '表記ゆれ')
        self.assertEqual(result, [])

    def test_request_exception_returns_empty_list(self):
        with patch('name_linker.requests.get', side_effect=Exception('network error')):
            result = name_linker.list_aliases('proj', 'sid', '表記ゆれ')
        self.assertEqual(result, [])


class RemoveAliasTests(unittest.TestCase):
    @staticmethod
    def _sent_lines(mock_post):
        payload = json.loads(mock_post.call_args.kwargs['files']['import-file'][1])
        return payload['pages'][0]['lines']

    def test_removes_alias_keeping_other_aliases_on_line(self):
        page = {
            'persistent': True,
            'lines': [
                {'text': '表記ゆれ'},
                {'text': '山田太郎 == タロー, Taro'},
                {'text': '鈴木花子 == ハナコ'},
            ],
        }
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            with patch('name_linker.requests.post', return_value=FakeResponse(200, text='ok')) as mock_post:
                status, body = name_linker.remove_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'Taro')
        self.assertEqual(status, 200)
        self.assertEqual(
            self._sent_lines(mock_post),
            ['表記ゆれ', '山田太郎 == タロー', '鈴木花子 == ハナコ'],
        )

    def test_removes_line_entirely_when_last_alias_removed(self):
        page = {
            'persistent': True,
            'lines': [
                {'text': '表記ゆれ'},
                {'text': '山田太郎 == タロー'},
                {'text': '鈴木花子 == ハナコ'},
            ],
        }
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            with patch('name_linker.requests.post', return_value=FakeResponse(200, text='ok')) as mock_post:
                status, body = name_linker.remove_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'タロー')
        self.assertEqual(status, 200)
        self.assertEqual(self._sent_lines(mock_post), ['表記ゆれ', '鈴木花子 == ハナコ'])

    def test_unregistered_canonical_returns_404_without_post(self):
        page = {'persistent': True, 'lines': [{'text': '表記ゆれ'}, {'text': '鈴木花子 == ハナコ'}]}
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            with patch('name_linker.requests.post') as mock_post:
                status, body = name_linker.remove_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'タロー')
        mock_post.assert_not_called()
        self.assertEqual(status, 404)
        self.assertEqual(body, '登録されていない本名です')

    def test_unregistered_alias_returns_404_without_post(self):
        page = {'persistent': True, 'lines': [{'text': '表記ゆれ'}, {'text': '山田太郎 == タロー'}]}
        with patch('name_linker.requests.get', return_value=FakeResponse(200, page)):
            with patch('name_linker.requests.post') as mock_post:
                status, body = name_linker.remove_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'Taro')
        mock_post.assert_not_called()
        self.assertEqual(status, 404)
        self.assertEqual(body, '登録されていない別名です')

    def test_get_exception_returns_none_status(self):
        with patch('name_linker.requests.get', side_effect=Exception('network error')):
            with patch('name_linker.requests.post') as mock_post:
                status, body = name_linker.remove_alias('proj', 'sid', '表記ゆれ', '山田太郎', 'タロー')
        mock_post.assert_not_called()
        self.assertIsNone(status)


class CheckConnectionTests(unittest.TestCase):
    def test_status_200_returns_ok(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(200)):
            ok, message = name_linker.check_connection('proj', 'sid')
        self.assertTrue(ok)
        self.assertEqual(message, '接続OK')

    def test_status_403_returns_cookie_expired_message(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(403)):
            ok, message = name_linker.check_connection('proj', 'sid')
        self.assertFalse(ok)
        self.assertIn('Cookie', message)

    def test_other_status_returns_false(self):
        with patch('name_linker.requests.get', return_value=FakeResponse(500)):
            ok, message = name_linker.check_connection('proj', 'sid')
        self.assertFalse(ok)

    def test_request_exception_returns_false(self):
        with patch('name_linker.requests.get', side_effect=Exception('network error')):
            ok, message = name_linker.check_connection('proj', 'sid')
        self.assertFalse(ok)
        self.assertEqual(message, 'network error')


class LinkKnownPagesTests(unittest.TestCase):
    def test_wraps_known_page_name_in_brackets(self):
        result = name_linker.link_known_pages('今日はBlenderを触った', ['Blender'])
        self.assertEqual(result, '今日は[Blender]を触った')

    def test_leaves_unknown_words_untouched(self):
        result = name_linker.link_known_pages('今日はMayaを触った', ['Blender'])
        self.assertEqual(result, '今日はMayaを触った')

    def test_matches_case_insensitively_and_uses_page_spelling(self):
        # 「scrapbox」と書いても [Scrapbox] ページに繋がってほしい
        result = name_linker.link_known_pages('scrapboxに書いた', ['Scrapbox'])
        self.assertEqual(result, '[Scrapbox]に書いた')

    def test_prefers_the_longest_match(self):
        result = name_linker.link_known_pages('Blender Guruを見た', ['Blender', 'Blender Guru'])
        self.assertEqual(result, '[Blender Guru]を見た')

    def test_does_not_double_wrap_existing_links(self):
        result = name_linker.link_known_pages('[Blender]を触った', ['Blender'])
        self.assertEqual(result, '[Blender]を触った')

    def test_does_not_touch_urls(self):
        # URLの一部がページ名と一致しても、途中に[]が入るとリンクが壊れる
        result = name_linker.link_known_pages('https://example.com/Blender を見た', ['Blender'])
        self.assertEqual(result, 'https://example.com/Blender を見た')

    def test_does_not_touch_tags(self):
        result = name_linker.link_known_pages('#Blender の話', ['Blender'])
        self.assertEqual(result, '#Blender の話')

    def test_links_text_around_protected_parts(self):
        result = name_linker.link_known_pages('[Maya] と Blender', ['Blender', 'Maya'])
        self.assertEqual(result, '[Maya] と [Blender]')

    def test_links_every_occurrence(self):
        result = name_linker.link_known_pages('Blenderの話。Blenderは良い', ['Blender'])
        self.assertEqual(result, '[Blender]の話。[Blender]は良い')

    def test_ignores_titles_shorter_than_min_length(self):
        # 1文字のページ名は無関係な文字に当たって誤リンクを量産する
        result = name_linker.link_known_pages('今日は良い日', ['日'])
        self.assertEqual(result, '今日は良い日')

    def test_no_pages_returns_text_unchanged(self):
        self.assertEqual(name_linker.link_known_pages('今日は良い日', []), '今日は良い日')

    def test_length_changing_lowercase_does_not_shift_the_brackets(self):
        # 「İ」は小文字化すると2文字になり、位置がずれると無関係な場所に[]が入る
        result = name_linker.link_known_pages('İstanbulでBlenderを触った', ['Blender'])
        self.assertEqual(result, 'İstanbulで[Blender]を触った')

    def test_multiline_text_is_linked_on_every_line(self):
        result = name_linker.link_known_pages('Blenderを触った\nMayaも触った', ['Blender', 'Maya'])
        self.assertEqual(result, '[Blender]を触った\n[Maya]も触った')


if __name__ == '__main__':
    unittest.main()
