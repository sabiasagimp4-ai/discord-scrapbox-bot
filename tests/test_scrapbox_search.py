import unittest
from unittest.mock import patch

import scrapbox_search


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, raise_on_json=False):
        self._json_data = json_data or {}
        self.status_code = status_code
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError('invalid json')
        return self._json_data


class SearchPagesTests(unittest.TestCase):
    def test_success_returns_pages_and_none_error(self):
        data = {'pages': [{'title': '記事A', 'lines': ['行1', '行2']}, {'title': '記事B', 'lines': ['行3']}]}
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(data)):
            pages, error = scrapbox_search.search_pages('proj', 'sid', 'クエリ')
        self.assertIsNone(error)
        self.assertEqual(pages[0], {'title': '記事A', 'snippet': '行1 行2'})
        self.assertEqual(pages[1], {'title': '記事B', 'snippet': '行3'})

    def test_zero_hits_is_success(self):
        with patch('scrapbox_search.requests.get', return_value=FakeResponse({'pages': []})):
            pages, error = scrapbox_search.search_pages('proj', 'sid', 'クエリ')
        self.assertEqual(pages, [])
        self.assertIsNone(error)

    def test_limit_truncates_results(self):
        data = {'pages': [{'title': f'記事{i}', 'lines': []} for i in range(15)]}
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(data)):
            pages, error = scrapbox_search.search_pages('proj', 'sid', 'クエリ', limit=3)
        self.assertEqual(len(pages), 3)

    def test_403_returns_auth_error(self):
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(status_code=403)):
            pages, error = scrapbox_search.search_pages('proj', 'sid', 'クエリ')
        self.assertEqual(pages, [])
        self.assertEqual(error, 'auth')

    def test_500_returns_status_error(self):
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(status_code=500)):
            pages, error = scrapbox_search.search_pages('proj', 'sid', 'クエリ')
        self.assertEqual(pages, [])
        self.assertIn('500', error)

    def test_exception_returns_error(self):
        with patch('scrapbox_search.requests.get', side_effect=Exception('timeout')):
            pages, error = scrapbox_search.search_pages('proj', 'sid', 'クエリ')
        self.assertEqual(pages, [])
        self.assertEqual(error, 'timeout')

    def test_invalid_json_returns_error(self):
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(raise_on_json=True)):
            pages, error = scrapbox_search.search_pages('proj', 'sid', 'クエリ')
        self.assertEqual(pages, [])
        self.assertEqual(error, 'JSON不正')


class FetchPageTextTests(unittest.TestCase):
    def test_success_joins_line_texts(self):
        data = {'lines': [{'text': 'タイトル'}, {'text': '本文1'}, {'text': '本文2'}]}
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(data)):
            text = scrapbox_search.fetch_page_text('proj', 'sid', '記事A')
        self.assertEqual(text, 'タイトル\n本文1\n本文2')

    def test_truncates_to_max_chars(self):
        data = {'lines': [{'text': 'あ' * 2000}]}
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(data)):
            text = scrapbox_search.fetch_page_text('proj', 'sid', '記事A', max_chars=100)
        self.assertTrue(text.endswith('…(省略)'))
        self.assertEqual(len(text), 100 + len('…(省略)'))

    def test_non_200_returns_empty(self):
        with patch('scrapbox_search.requests.get', return_value=FakeResponse(status_code=404)):
            text = scrapbox_search.fetch_page_text('proj', 'sid', '記事A')
        self.assertEqual(text, '')

    def test_exception_returns_empty(self):
        with patch('scrapbox_search.requests.get', side_effect=Exception('boom')):
            text = scrapbox_search.fetch_page_text('proj', 'sid', '記事A')
        self.assertEqual(text, '')


class MergeSearchResultsTests(unittest.TestCase):
    def test_pages_hit_by_more_queries_rank_higher(self):
        q1 = [{'title': 'A', 'snippet': 's'}, {'title': 'B', 'snippet': 's'}]
        q2 = [{'title': 'B', 'snippet': 's'}, {'title': 'C', 'snippet': 's'}]
        merged = scrapbox_search.merge_search_results([q1, q2])
        # B は2クエリでヒット → 先頭
        self.assertEqual(merged[0]['title'], 'B')
        self.assertEqual(merged[0]['score'], 2)

    def test_deduplicates_titles(self):
        q1 = [{'title': 'A', 'snippet': 's'}]
        q2 = [{'title': 'A', 'snippet': 's'}]
        merged = scrapbox_search.merge_search_results([q1, q2])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['score'], 2)

    def test_tie_keeps_insertion_order(self):
        q1 = [{'title': 'A', 'snippet': 's'}]
        q2 = [{'title': 'B', 'snippet': 's'}]
        merged = scrapbox_search.merge_search_results([q1, q2])
        self.assertEqual([m['title'] for m in merged], ['A', 'B'])

    def test_keeps_longest_snippet(self):
        q1 = [{'title': 'A', 'snippet': '短い'}]
        q2 = [{'title': 'A', 'snippet': 'こちらのほうが長いスニペット'}]
        merged = scrapbox_search.merge_search_results([q1, q2])
        self.assertEqual(merged[0]['snippet'], 'こちらのほうが長いスニペット')

    def test_empty_input_returns_empty(self):
        self.assertEqual(scrapbox_search.merge_search_results([]), [])


if __name__ == '__main__':
    unittest.main()
