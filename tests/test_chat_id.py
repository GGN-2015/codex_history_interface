import json
from typing import get_type_hints
import unittest

from codex_history_interface import (
    Abstract,
    C_CodexInterface,
    C_HistoryRepository,
    C_RoughMatchIndex,
    D_ChatId,
    I_CodexInterface,
    I_HistoryRepository,
)
from codex_history_interface.interface import D_ChatId as LegacyChatId
from test_export_tex import CHAT_ID, ExportFixture


class ChatIdTests(unittest.TestCase):
    def test_value_is_a_distinct_runtime_type_compatible_with_strings(self):
        chat_id = D_ChatId(CHAT_ID)
        self.assertIs(type(chat_id), D_ChatId)
        self.assertIsInstance(chat_id, str)
        self.assertEqual(chat_id, CHAT_ID)
        self.assertEqual(hash(chat_id), hash(CHAT_ID))
        self.assertEqual({CHAT_ID: "record"}[chat_id], "record")
        self.assertIs(type(chat_id.value), str)
        self.assertEqual(chat_id.value, CHAT_ID)
        self.assertEqual(str(chat_id), CHAT_ID)
        self.assertEqual(repr(chat_id), f"D_ChatId({CHAT_ID!r})")

    def test_serialization_preserves_the_original_id(self):
        self.assertEqual(json.loads(json.dumps([D_ChatId(CHAT_ID)])), [CHAT_ID])

    def test_id_is_immutable(self):
        chat_id = D_ChatId(CHAT_ID)
        for attribute in ("value", "other"):
            with self.subTest(attribute=attribute), self.assertRaises(AttributeError):
                setattr(chat_id, attribute, "changed")

    def test_empty_or_non_string_values_are_rejected(self):
        for value in (None, 1, True, [], b"id"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                D_ChatId(value)
        for value in ("", " \n\t"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                D_ChatId(value)

    def test_non_uuid_ids_are_preserved_without_normalization(self):
        self.assertEqual(D_ChatId(" local-id ").value, " local-id ")

    def test_all_public_imports_have_identical_types(self):
        self.assertIs(Abstract.D_ChatId, D_ChatId)
        self.assertIs(Abstract.History.D_ChatId, D_ChatId)
        self.assertIs(LegacyChatId, D_ChatId)

    def test_search_contract_uses_chat_id_at_every_layer(self):
        for component in (I_CodexInterface, I_HistoryRepository, C_CodexInterface, C_HistoryRepository, C_RoughMatchIndex):
            with self.subTest(component=component.__name__):
                self.assertEqual(get_type_hints(component.roughMatch)["return"], list[D_ChatId])

    def test_search_index_returns_real_chat_ids(self):
        index = C_RoughMatchIndex()
        index.updateDocument(CHAT_ID, "截骨规划", ["数学公式"])
        result = index.roughMatch("截骨", 1)
        self.assertEqual(result, [D_ChatId(CHAT_ID)])
        self.assertTrue(all(type(chat_id) is D_ChatId for chat_id in result))
        self.assertEqual(index.roughMatch("missing", 1), [])


class ChatIdIntegrationTests(ExportFixture, unittest.TestCase):
    def test_search_result_can_be_passed_to_every_id_consumer(self):
        result = self.reader.roughMatch("表格", 1)
        self.assertEqual(len(result), 1)
        chat_id = result[0]
        self.assertIs(type(chat_id), D_ChatId)
        self.assertEqual(self.reader.getChatTitle(chat_id), self.reader.getChatTitle(CHAT_ID))
        self.assertEqual(self.reader.getChatMessage(chat_id), self.reader.getChatMessage(CHAT_ID))
        self.assertEqual(self.reader.getCreateTimeStamp(chat_id), self.reader.getCreateTimeStamp(CHAT_ID))
        self.assertEqual(self.reader.getLastMessageTimeStamp(chat_id), self.reader.getLastMessageTimeStamp(CHAT_ID))
        output = self.reader.exportToTex(chat_id, self.output)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
