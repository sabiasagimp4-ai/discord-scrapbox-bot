import unittest
from unittest.mock import patch

import name_linker


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

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


if __name__ == '__main__':
    unittest.main()
