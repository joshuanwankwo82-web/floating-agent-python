"""Store Image Generation panel state in Friday's encrypted kv memory.

This records project/prompt/render metadata and the user's output folder. It
deliberately does not feed the prompt into Friday's semantic vector memory
and does not invoke an embedding or image provider.
"""
from __future__ import annotations

import os
from pathlib import Path

from .model import AppSettings, ImageProject, decode_state, encode_state


class EncryptedImageStore:
    KEY = "image_panel_ui:v1"

    def default_output_dir(self) -> str:
        import memory

        return str(Path(memory.get_memory_dir()) / "generated" / "images")

    def load(self):
        import memory

        path = memory._kv_path(self.KEY)
        record = memory._read_encrypted(path)
        if record is None:
            if os.path.exists(path):
                raise ValueError("Image panel history could not be decrypted or read")
            settings = AppSettings(output_dir=self.default_output_dir(),
                                   state_path=str(Path(path)))
            return [ImageProject()], None, settings
        if not isinstance(record, dict) or "value" not in record:
            raise ValueError("Invalid saved image panel history record")
        projects, active, settings = decode_state(record["value"], state_path=str(Path(path)))
        if not settings.output_dir:
            settings.output_dir = self.default_output_dir()
        return projects, active, settings

    def save(self, projects, active_id, settings):
        import memory

        memory.kv_set(self.KEY, encode_state(projects, active_id, settings))
