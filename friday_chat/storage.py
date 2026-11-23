"""Store the Chat panel's UI history in Friday's existing encrypted kv store.

Local demo history is deliberately separate from the assistant's embedded
conversation log: loading/saving this state must not invoke an embedding
provider or imply that the standalone Chat panel has a live AI backend.
"""
from __future__ import annotations

import os

from .model import decode_state, encode_state


class EncryptedChatStore:
    KEY = "chat_panel_ui:v1"

    def load(self):
        import memory

        # A corrupt existing record is not an empty first-run store. Refuse
        # to overwrite it; ChatView will keep a clearly labelled temporary
        # session instead, just like the original Quiet Chat UI did.
        path = memory._kv_path(self.KEY)
        record = memory._read_encrypted(path)
        if record is None:
            if os.path.exists(path):
                raise ValueError("Chat history could not be decrypted or read")
            return [], None
        if not isinstance(record, dict) or "value" not in record:
            raise ValueError("Invalid saved Chat history record")
        return decode_state(record["value"])

    def save(self, conversations, active_id):
        import memory

        memory.kv_set(self.KEY, encode_state(conversations, active_id))
