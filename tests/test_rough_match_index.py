import unittest
from unittest.mock import patch

from codex_history_interface import C_RoughMatchIndex


class RoughMatchIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = C_RoughMatchIndex()

    def test_empty_index_returns_no_results(self):
        self.assertEqual(self.index.roughMatch("截骨规划", 5), [])

    def test_title_and_body_are_searchable(self):
        self.index.updateDocument("title", "截骨规划")
        self.index.updateDocument("body", "", ["截骨规划"])
        self.assertEqual(self.index.roughMatch("截骨规划", 5), ["title", "body"])

    def test_visible_title_has_more_weight_than_equal_body_text(self):
        self.index.updateDocument("a-body", "", ["rendering"])
        self.index.updateDocument("z-title", "rendering")
        self.assertEqual(self.index.roughMatch("rendering", 1), ["z-title"])

    def test_exact_title_phrase_receives_a_boost(self):
        self.index.updateDocument("a", "骨规划截")
        self.index.updateDocument("z", "截骨规划")
        self.assertEqual(self.index.roughMatch("截骨规划", 1), ["z"])

    def test_multiple_query_terms_improve_relevance(self):
        self.index.updateDocument("a", "", ["Qt"])
        self.index.updateDocument("z", "", ["Qt 截骨规划"])
        self.assertEqual(self.index.roughMatch("Qt 截骨规划", 1), ["z"])

    def test_repeated_body_terms_cannot_overwhelm_an_exact_title(self):
        self.index.updateDocument("a", "", ["markdown " * 10000])
        self.index.updateDocument("z", "Markdown")
        self.assertEqual(self.index.roughMatch("markdown", 1), ["z"])

    def test_casefold_and_unicode_compatibility_normalization(self):
        self.index.updateDocument("a", "Ｐｙｔｈｏｎ Straße")
        self.assertEqual(self.index.roughMatch("PYTHON", 1), ["a"])
        self.assertEqual(self.index.roughMatch("STRASSE", 1), ["a"])

    def test_english_misspelling_can_match_shared_trigrams(self):
        self.index.updateDocument("a", "Markdown 渲染")
        self.assertEqual(self.index.roughMatch("markdwon", 1), ["a"])

    def test_english_substring_can_match_without_a_whole_word_hit(self):
        self.index.updateDocument("a", "mathematics")
        self.assertEqual(self.index.roughMatch("math", 1), ["a"])

    def test_chinese_partial_phrase_and_single_character(self):
        self.index.updateDocument("a", "实现截骨规划界面")
        self.assertEqual(self.index.roughMatch("骨规划", 1), ["a"])
        self.assertEqual(self.index.roughMatch("骨", 1), ["a"])

    def test_mixed_chinese_english_and_code_identifiers(self):
        self.index.updateDocument("a", "实现Qt截骨GUI", ["使用 getChatMessage 和 export_to_tex"])
        for query in ("Qt", "截骨", "GUI", "getChatMessage", "export to tex"):
            with self.subTest(query=query):
                self.assertEqual(self.index.roughMatch(query, 1), ["a"])

    def test_japanese_korean_and_supplementary_han_characters(self):
        self.index.updateDocument("a", "カタカナ 한글 𠀀骨")
        for query in ("カタ", "한글", "𠀀骨"):
            with self.subTest(query=query):
                self.assertEqual(self.index.roughMatch(query, 1), ["a"])

    def test_unrelated_query_returns_no_padding_results(self):
        self.index.updateDocument("a", "截骨规划")
        self.assertEqual(self.index.roughMatch("astronomy", 10), [])

    def test_result_count_is_limited_without_duplicates(self):
        for identity in ("c", "a", "b"):
            self.index.updateDocument(identity, "截骨规划", ["截骨规划"] * 10)
        self.assertEqual(self.index.roughMatch("截骨规划", 2), ["a", "b"])
        self.assertEqual(self.index.roughMatch("截骨规划", 100), ["a", "b", "c"])

    def test_zero_and_empty_queries_return_no_results(self):
        self.index.updateDocument("a", "截骨规划")
        for query, maximum in (("截骨", 0), ("", 5), (" \n\t", 5), ("!? [] {}", 5)):
            with self.subTest(query=query, maximum=maximum):
                self.assertEqual(self.index.roughMatch(query, maximum), [])

    def test_invalid_content_types_are_rejected(self):
        for content in (None, 42, b"query", [], {}):
            with self.subTest(content=content), self.assertRaises(TypeError):
                self.index.roughMatch(content, 1)

    def test_invalid_maximum_types_and_negative_values_are_rejected(self):
        for maximum in (None, True, False, 1.0, "1"):
            with self.subTest(maximum=maximum), self.assertRaises(TypeError):
                self.index.roughMatch("query", maximum)
        with self.assertRaises(ValueError):
            self.index.roughMatch("query", -1)

    def test_update_removes_obsolete_terms(self):
        self.index.updateDocument("a", "截骨规划", ["数学公式"])
        self.index.updateDocument("a", "晚餐食谱", ["今天做饭"])
        self.assertEqual(self.index.roughMatch("截骨 数学", 5), [])
        self.assertEqual(self.index.roughMatch("食谱", 5), ["a"])

    def test_title_only_update_retains_body_without_retokenizing_it(self):
        self.index.updateDocument("a", "旧标题", ["数学公式"])
        with patch.object(self.index, "_features", wraps=self.index._features) as tokenize:
            self.index.updateDocument("a", "新标题")
            tokenize.assert_called_once_with("新标题")
        self.assertEqual(self.index.roughMatch("数学公式", 5), ["a"])
        self.assertEqual(self.index.roughMatch("新标题", 5), ["a"])

    def test_explicit_empty_contents_clear_the_old_body(self):
        self.index.updateDocument("a", "标题", ["mathematics"])
        self.index.updateDocument("a", "标题", [])
        self.assertEqual(self.index.roughMatch("mathematics", 5), [])

    def test_body_does_not_create_ngrams_across_message_boundaries(self):
        self.index.updateDocument("a", "", ["ab", "cdef"])
        self.assertEqual(self.index.roughMatch("abc", 5), [])

    def test_remove_clears_postings_lengths_and_document_ids(self):
        self.index.updateDocument("a", "截骨规划", ["数学公式"])
        self.assertTrue(self.index.removeDocument("a"))
        self.assertFalse(self.index.removeDocument("a"))
        self.assertEqual(self.index.getChatList(), [])
        self.assertEqual(self.index._postings, {})
        self.assertEqual((self.index._title_length, self.index._body_length), (0, 0))

    def test_failed_update_keeps_the_previously_indexed_document(self):
        self.index.updateDocument("a", "截骨规划")
        with self.assertRaises(TypeError):
            self.index.updateDocument("a", "晚餐食谱", ["valid", None])
        self.assertEqual(self.index.roughMatch("截骨规划", 5), ["a"])

    def test_invalid_document_inputs_are_rejected(self):
        for identity in (None, "", " "):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.index.updateDocument(identity, "title")
        with self.assertRaises(TypeError):
            self.index.updateDocument("a", None)

    def test_string_and_generator_contents_are_supported(self):
        self.index.updateDocument("a", "", "数学公式")
        self.index.updateDocument("b", "", (text for text in ("数学公式", "表格")))
        self.assertEqual(set(self.index.roughMatch("数学公式", 5)), {"a", "b"})

    def test_query_does_not_scan_the_whole_vocabulary_or_document_collection(self):
        class NonIterableDict(dict):
            def __iter__(self):
                raise AssertionError("Full collection scan")

        for number in range(1000):
            self.index.updateDocument(str(number), "", ["ordinary background configuration"])
        self.index.updateDocument("needle", "截骨规划")
        self.index._postings = NonIterableDict(self.index._postings)
        self.index._documents = NonIterableDict(self.index._documents)
        with patch.object(self.index, "_features", wraps=self.index._features) as tokenize:
            self.assertEqual(self.index.roughMatch("截骨", 5), ["needle"])
            tokenize.assert_called_once_with("截骨")

    def test_repeated_queries_are_deterministic_and_do_not_mutate_the_index(self):
        self.index.updateDocument("b", "数学公式")
        self.index.updateDocument("a", "数学公式")
        expected = self.index.roughMatch("数学公式", 5)
        before = {term: dict(postings) for term, postings in self.index._postings.items()}
        for _ in range(5):
            self.assertEqual(self.index.roughMatch("数学公式", 5), expected)
        self.assertEqual(self.index._postings, before)


if __name__ == "__main__":
    unittest.main()
