"""Quiet Chat's adapted data model and Friday-encrypted UI history.

Run: python -m unittest discover -s dev_tests -p test_friday_chat_model.py -v
No GUI, provider keys, network calls or semantic memory writes required.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from friday_chat.model import (Conversation, Message, decode_state, demo_conversations,
                               demo_reply, encode_state, export_text)
from friday_chat.storage import EncryptedChatStore


class ModelTests(unittest.TestCase):
    def test_ids_and_attachment_arrays_are_independent(self):
        a, b = Conversation(), Conversation()
        self.assertNotEqual(a.id, b.id)
        m, n = Message("user", "hello"), Message("user", "hello")
        m.attachments.append("notes.txt")
        self.assertEqual(n.attachments, [])

    def test_title_created_from_first_user_message_only(self):
        c = Conversation()
        c.add(Message("assistant", "hello"))
        self.assertEqual(c.title, "New conversation")
        c.add(Message("user", "  A small\n  clear thought  "))
        self.assertEqual(c.title, "A small clear thought")
        c.add(Message("user", "A second thought"))
        self.assertEqual(c.title, "A small clear thought")

    def test_long_title_and_file_only_title(self):
        c = Conversation()
        c.add(Message("user", "An unusually long conversation title which should fit"))
        self.assertLessEqual(len(c.title), 30)
        self.assertTrue(c.title.endswith("…"))
        c = Conversation()
        c.add(Message("user", "", attachments=["file.txt"]))
        self.assertEqual(c.title, "A file reference")

    def test_roundtrip_keeps_messages_timestamps_and_active_id(self):
        chats = demo_conversations()
        chats[0].add(Message("user", "Hello\n世界", attachments=["notes.txt"]))
        encoded = encode_state(chats, chats[1].id)
        restored, active = decode_state(encoded)
        self.assertEqual(encoded, encode_state(restored, active))
        self.assertEqual(active, chats[1].id)

    def test_invalid_active_id_has_stable_fallback(self):
        chats = demo_conversations()
        restored, active = decode_state(encode_state(chats, "missing"))
        self.assertEqual(active, restored[0].id)
        self.assertEqual(decode_state(encode_state([], "missing")), ([], None))

    def test_unknown_version_rejected(self):
        for value in [None, [], {}, {"version": 2, "conversations": []}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_state(value)

    def test_bad_conversation_fields_rejected(self):
        good = encode_state(demo_conversations(), "missing")
        for key, value in [("id", ""), ("title", None), ("messages", None),
                           ("created_at", 42), ("updated_at", [])]:
            bad = deepcopy(good)
            bad["conversations"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                decode_state(bad)
        bad = deepcopy(good)
        bad["conversations"].append(bad["conversations"][0])
        with self.assertRaises(ValueError):
            decode_state(bad)

    def test_bad_message_fields_rejected(self):
        good = encode_state(demo_conversations(), "missing")
        for key, value in [("role", "system"), ("content", None),
                           ("attachments", [32]), ("created_at", {})]:
            bad = deepcopy(good)
            bad["conversations"][0]["messages"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                decode_state(bad)

    def test_wire_does_not_expose_mutable_attachment_array(self):
        msg = Message("user", "hello", attachments=["local.txt"])
        wire = msg.wire()
        wire["attachments"].append("other.txt")
        self.assertEqual(msg.attachments, ["local.txt"])
        self.assertEqual(set(wire), {"role", "content", "attachments"})

    def test_sample_reply_is_honest_about_file_names(self):
        reply = demo_reply("Read this", [{"attachments": ["secret.txt"]}])
        self.assertIn("doesn’t read or upload files", reply)
        for prompt in ("plan my day", "write something", "explore an idea", "hi"):
            self.assertTrue(demo_reply(prompt, []))

    def test_export_identifies_friday_and_preserves_text_and_references(self):
        c = Conversation(title="An idea", messages=[Message("user", "A\nB", attachments=["file.txt"]),
                                                    Message("assistant", "C")])
        text = export_text(c)
        for expected in ("An idea", "You", "Friday", "A\nB", "file.txt", "C"):
            self.assertIn(expected, text)


class EncryptedStoreTests(unittest.TestCase):
    def setUp(self):
        import memory
        self.memory = memory
        self.previous_dir = memory._memory_dir
        self.tmp = tempfile.TemporaryDirectory()
        memory.set_memory_dir(self.tmp.name)
        self.store = EncryptedChatStore()

    def tearDown(self):
        self.memory.set_memory_dir(self.previous_dir)
        self.tmp.cleanup()

    def test_missing_state_is_empty(self):
        self.assertEqual(self.store.load(), ([], None))

    def test_roundtrip_is_encrypted_and_separate_from_semantic_memory(self):
        c = Conversation(title="private-title", messages=[Message("user", "private-test-utterance")])
        with patch.object(self.memory, "embed_text", side_effect=AssertionError("No embeddings")), \
             patch.object(self.memory, "log_turn_async", side_effect=AssertionError("No live chat log")):
            self.store.save([c], c.id)
            restored, active = self.store.load()
        self.assertEqual(encode_state(restored, active), encode_state([c], c.id))
        path = Path(self.memory._kv_path(self.store.KEY))
        self.assertTrue(path.name.endswith(".json.enc"))
        self.assertNotIn(b"private-test-utterance", path.read_bytes())
        self.assertNotIn(b"private-title", path.read_bytes())
        self.assertFalse(list(Path(self.tmp.name).rglob("conversations.json")))
        self.assertFalse(list((Path(self.tmp.name)/"conversations").glob("*.json.enc")))

    def test_corrupt_record_is_reported_and_not_replaced(self):
        path = Path(self.memory._kv_path(self.store.KEY))
        path.write_bytes(b"unreadable-encrypted-history")
        with self.assertRaises(ValueError):
            self.store.load()
        self.assertEqual(path.read_bytes(), b"unreadable-encrypted-history")

    def test_invalid_schema_is_rejected_not_loaded_as_empty_history(self):
        self.memory.kv_set(self.store.KEY, {"version": 99, "conversations": []})
        with self.assertRaises(ValueError):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
