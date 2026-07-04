import unittest
from unittest.mock import patch

import rag_qa


class CleanQueryTests(unittest.TestCase):
    def test_removes_question_marks_and_punctuation(self):
        self.assertEqual(rag_qa._clean_query('監督は誰？'), '監督は誰')

    def test_collapses_whitespace(self):
        self.assertEqual(rag_qa._clean_query('山田  太郎'), '山田 太郎')


class ExtractKeywordsTests(unittest.TestCase):
    def test_parses_one_keyword_per_line(self):
        with patch('rag_qa._chat', return_value=('山田太郎\n3DCG\nMV', None)):
            keywords = rag_qa.extract_keywords('山田太郎が3DCGを担当したMV')
        self.assertEqual(keywords, ['山田太郎', '3DCG', 'MV'])

    def test_leading_digit_keyword_is_preserved(self):
        # "3DCG" のような先頭数字のキーワードを箇条書き番号と誤認して壊さないこと
        with patch('rag_qa._chat', return_value=('3DCG', None)):
            self.assertEqual(rag_qa.extract_keywords('q'), ['3DCG'])

    def test_strips_list_markers(self):
        with patch('rag_qa._chat', return_value=('1. 山田太郎\n- 3DCG\n・監督', None)):
            self.assertEqual(rag_qa.extract_keywords('q'), ['山田太郎', '3DCG', '監督'])

    def test_caps_at_five_keywords(self):
        with patch('rag_qa._chat', return_value=('a\nb\nc\nd\ne\nf\ng', None)):
            self.assertEqual(rag_qa.extract_keywords('q'), ['a', 'b', 'c', 'd', 'e'])

    def test_llm_error_returns_empty(self):
        with patch('rag_qa._chat', return_value=(None, 'timeout')):
            self.assertEqual(rag_qa.extract_keywords('q'), [])


class FallbackKeywordsTests(unittest.TestCase):
    def test_strips_toha_dare_suffix(self):
        self.assertEqual(rag_qa._fallback_keywords('山口駿とは誰？'), ['山口駿'])

    def test_preserves_english_name_internal_space(self):
        self.assertEqual(rag_qa._fallback_keywords('Shun Yamaguchiとは誰？'), ['Shun Yamaguchi'])

    def test_strips_ha_dare_suffix(self):
        self.assertEqual(rag_qa._fallback_keywords('監督は誰'), ['監督'])

    def test_strips_nitsuite_oshiete(self):
        self.assertEqual(rag_qa._fallback_keywords('山田太郎について教えて'), ['山田太郎'])

    def test_noise_only_returns_empty(self):
        self.assertEqual(rag_qa._fallback_keywords('？？？'), [])


class BuildRagContextTests(unittest.TestCase):
    def test_includes_all_when_within_limit(self):
        pages = [{'title': 'A', 'text': 'foo'}, {'title': 'B', 'text': 'bar'}]
        context, sources = rag_qa.build_rag_context(pages, total_max_chars=1000)
        self.assertIn('# ページ: A', context)
        self.assertIn('# ページ: B', context)
        self.assertEqual(sources, ['A', 'B'])

    def test_excludes_low_rank_pages_over_limit(self):
        pages = [{'title': 'A', 'text': 'x' * 100}, {'title': 'B', 'text': 'y' * 100}]
        context, sources = rag_qa.build_rag_context(pages, total_max_chars=120)
        # 1件目で上限に達するので2件目は丸ごと除外
        self.assertEqual(sources, ['A'])
        self.assertNotIn('# ページ: B', context)

    def test_always_includes_at_least_first_page(self):
        pages = [{'title': 'A', 'text': 'x' * 5000}]
        context, sources = rag_qa.build_rag_context(pages, total_max_chars=100)
        self.assertEqual(sources, ['A'])


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self):
        self.api_key_patch = patch.object(rag_qa, 'OPENROUTER_API_KEY', 'sk-test')
        self.api_key_patch.start()
        self.addCleanup(self.api_key_patch.stop)

    def test_no_api_key_returns_error(self):
        with patch.object(rag_qa, 'OPENROUTER_API_KEY', ''):
            answer, sources, error = rag_qa.answer_question('q', 'proj', 'sid')
        self.assertIsNone(answer)
        self.assertEqual(error, 'OPENROUTER_API_KEYが未設定です')

    def test_zero_hits_does_not_call_llm(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([], None)):
                with patch.object(rag_qa, 'generate_answer') as mock_gen:
                    answer, sources, error = rag_qa.answer_question('q', 'proj', 'sid')
        mock_gen.assert_not_called()
        self.assertTrue(error.startswith('no_hits'))

    def test_all_queries_auth_error_returns_auth(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田', '太郎']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([], 'auth')):
                answer, sources, error = rag_qa.answer_question('q', 'proj', 'sid')
        self.assertEqual(error, 'auth')

    def test_all_queries_fail_returns_search_error(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([], 'timeout')):
                answer, sources, error = rag_qa.answer_question('q', 'proj', 'sid')
        self.assertEqual(error, 'search')

    def test_falls_back_to_question_when_no_keywords(self):
        captured = []

        def fake_search(project, sid, kw):
            captured.append(kw)
            return [], None

        with patch.object(rag_qa, 'extract_keywords', return_value=[]):
            with patch('rag_qa.scrapbox_search.search_pages', side_effect=fake_search):
                rag_qa.answer_question('監督は誰？', 'proj', 'sid')
        # キーワード抽出が空でも、決定論的フォールバックで疑問文語尾を削った語を検索する
        self.assertEqual(captured, ['監督'])

    def test_combines_llm_keywords_with_fallback(self):
        captured = []

        def fake_search(project, sid, kw):
            captured.append(kw)
            return [], None

        with patch.object(rag_qa, 'extract_keywords', return_value=['X']):
            with patch('rag_qa.scrapbox_search.search_pages', side_effect=fake_search):
                rag_qa.answer_question('Yとは誰？', 'proj', 'sid')
        # LLM由来の 'X' と、フォールバックで抽出した 'Y' の両方を検索する
        self.assertIn('X', captured)
        self.assertIn('Y', captured)

    def test_no_hits_error_carries_searched_terms(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山口駿']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([], None)):
                with patch.object(rag_qa, 'generate_answer') as mock_gen:
                    answer, sources, error = rag_qa.answer_question('山口駿とは誰？', 'proj', 'sid')
        mock_gen.assert_not_called()
        self.assertTrue(error.startswith('no_hits:'))
        self.assertIn('山口駿', error)

    def test_success_path_returns_answer_and_sources(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([{'title': 'A', 'snippet': 's'}], None)):
                with patch('rag_qa.scrapbox_search.fetch_page_text', return_value='本文'):
                    with patch.object(rag_qa, 'generate_answer', return_value=('回答です', None)):
                        answer, sources, error = rag_qa.answer_question('q', 'proj', 'sid')
        self.assertIsNone(error)
        self.assertEqual(answer, '回答です')
        self.assertEqual(sources, ['A'])

    def test_llm_failure_returns_llm_error_with_sources(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([{'title': 'A', 'snippet': 's'}], None)):
                with patch('rag_qa.scrapbox_search.fetch_page_text', return_value='本文'):
                    with patch.object(rag_qa, 'generate_answer', return_value=(None, 'ステータス(429)')):
                        answer, sources, error = rag_qa.answer_question('q', 'proj', 'sid')
        self.assertIsNone(answer)
        self.assertEqual(sources, ['A'])
        self.assertEqual(error, 'llm:ステータス(429)')

    def test_snippet_used_when_body_fetch_fails(self):
        captured = {}

        def fake_generate(question, context, model, history=None):
            captured['context'] = context
            return '回答', None

        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([{'title': 'A', 'snippet': 'スニペット代替'}], None)):
                with patch('rag_qa.scrapbox_search.fetch_page_text', return_value=''):
                    with patch.object(rag_qa, 'generate_answer', side_effect=fake_generate):
                        rag_qa.answer_question('q', 'proj', 'sid')
        self.assertIn('スニペット代替', captured['context'])

    def test_followup_mixes_previous_question_into_search(self):
        captured = []

        def fake_search(project, sid, kw):
            captured.append(kw)
            return [], None

        history = [{'q': '山口駿とは誰？', 'a': '映像作家です'}]
        with patch.object(rag_qa, 'extract_keywords', return_value=[]):
            with patch('rag_qa.scrapbox_search.search_pages', side_effect=fake_search):
                rag_qa.answer_question('その人の作品は？', 'proj', 'sid', history=history)
        # 追い質問は代名詞なので、直前の質問（山口駿）を検索語に混ぜて文脈を補う
        joined = ' '.join(captured)
        self.assertIn('山口駿', joined)

    def test_followup_passes_history_to_generation(self):
        captured = {}

        def fake_generate(question, context, model, history=None):
            captured['history'] = history
            return '回答', None

        history = [{'q': '前の質問', 'a': '前の回答'}]
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([{'title': 'A', 'snippet': 's'}], None)):
                with patch('rag_qa.scrapbox_search.fetch_page_text', return_value='本文'):
                    with patch.object(rag_qa, 'generate_answer', side_effect=fake_generate):
                        rag_qa.answer_question('追い質問', 'proj', 'sid', history=history)
        self.assertEqual(captured['history'], history)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.api_key_patch = patch.object(rag_qa, 'OPENROUTER_API_KEY', 'sk-test')
        self.api_key_patch.start()
        self.addCleanup(self.api_key_patch.stop)

    def test_trace_recorded_on_success(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([{'title': 'A', 'snippet': 's'}], None)):
                with patch('rag_qa.scrapbox_search.fetch_page_text', return_value='本文'):
                    with patch.object(rag_qa, 'generate_answer', return_value=('回答', None)):
                        rag_qa.answer_question('質問', 'proj', 'sid')
        trace = rag_qa.last_trace
        self.assertEqual(trace['question'], '質問')
        self.assertIn('山田', trace['keywords'])
        self.assertEqual(trace['hits'][0][0], '山田')
        self.assertEqual(trace['hits'][0][1], 1)
        # キーワードはLLM抽出('山田')とフォールバック('質問')の2本 → 両方でヒットしたAのスコアは2
        self.assertEqual(trace['selected'], [('A', 2)])
        self.assertGreater(trace['context_chars'], 0)
        self.assertIsNone(trace['error'])

    def test_trace_records_no_hits_error(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([], None)):
                rag_qa.answer_question('質問', 'proj', 'sid')
        trace = rag_qa.last_trace
        self.assertTrue(trace['error'].startswith('no_hits'))
        self.assertEqual(trace['hits'][0][1], 0)

    def test_trace_records_search_errors_per_keyword(self):
        with patch.object(rag_qa, 'extract_keywords', return_value=['山田']):
            with patch('rag_qa.scrapbox_search.search_pages', return_value=([], 'timeout')):
                rag_qa.answer_question('質問', 'proj', 'sid')
        trace = rag_qa.last_trace
        self.assertEqual(trace['hits'][0][1], 'error:timeout')
        self.assertEqual(trace['error'], 'search')


class GenerateAnswerTests(unittest.TestCase):
    def test_history_is_included_as_prior_turns(self):
        captured = {}

        def fake_chat_messages(model, messages, max_tokens, timeout):
            captured['messages'] = messages
            return '回答', None

        history = [{'q': '山口駿とは？', 'a': '映像作家'}]
        with patch.object(rag_qa, '_chat_messages', side_effect=fake_chat_messages):
            rag_qa.generate_answer('その作品は？', 'ctx', 'model', history=history)
        roles = [m['role'] for m in captured['messages']]
        # system → 過去(user, assistant) → 今回(user)
        self.assertEqual(roles, ['system', 'user', 'assistant', 'user'])
        self.assertEqual(captured['messages'][1]['content'], '山口駿とは？')
        self.assertEqual(captured['messages'][2]['content'], '映像作家')

    def test_no_history_is_single_turn(self):
        captured = {}

        def fake_chat_messages(model, messages, max_tokens, timeout):
            captured['messages'] = messages
            return '回答', None

        with patch.object(rag_qa, '_chat_messages', side_effect=fake_chat_messages):
            rag_qa.generate_answer('質問', 'ctx', 'model')
        roles = [m['role'] for m in captured['messages']]
        self.assertEqual(roles, ['system', 'user'])


if __name__ == '__main__':
    unittest.main()
