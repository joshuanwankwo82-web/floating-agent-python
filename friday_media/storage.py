"""Store a media panel's state in Friday's encrypted kv memory.

Records project/prompt/render metadata and the output folder only. Nothing
here feeds Friday's semantic memory or calls an embedding/AI provider.
"""
from __future__ import annotations

import os
from pathlib import Path


class EncryptedPanelStore:
    """Subclasses set KEY, LABEL, OUTPUT_SUBDIR and the model hooks."""

    KEY = ""
    LABEL = ""            # "image" / "video" -- used in error messages
    OUTPUT_SUBDIR = ""    # under <memory>/generated/
    settings_cls = None
    project_cls = None
    decode_state = None
    encode_state = None

    def default_output_dir(self) -> str:
        import memory

        return str(Path(memory.get_memory_dir()) / "generated" / self.OUTPUT_SUBDIR)

    def load(self):
        import memory

        path = memory._kv_path(self.KEY)
        record = memory._read_encrypted(path)
        if record is None:
            if os.path.exists(path):
                raise ValueError(f"{self.LABEL.capitalize()} panel history could "
                                 "not be decrypted or read")
            settings = self.settings_cls(output_dir=self.default_output_dir(),
                                         state_path=str(Path(path)))
            return [self.project_cls()], None, settings
        if not isinstance(record, dict) or "value" not in record:
            raise ValueError(f"Invalid saved {self.LABEL} panel history record")
        projects, active, settings = type(self).decode_state(
            record["value"], state_path=str(Path(path)))
        if not settings.output_dir:
            settings.output_dir = self.default_output_dir()
        return projects, active, settings

    def save(self, projects, active_id, settings):
        import memory

        memory.kv_set(self.KEY, type(self).encode_state(projects, active_id, settings))
