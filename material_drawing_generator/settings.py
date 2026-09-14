from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .runtime_paths import app_data_root, dwg_tools_available


@dataclass(slots=True)
class UserSettings:
    excel_path: str = ""
    output_path: str = ""
    template_path: str = ""
    active_template_name: str = ""
    frame_dwg_path: str = ""
    sort_descending: bool = False
    output_pdf: bool = True
    output_dxf: bool = True
    output_dwg: bool = field(default_factory=dwg_tools_available)
    preview_sheet: str = ""
    material_symbols: bool = False

    @classmethod
    def path(cls) -> Path:
        return app_data_root() / "settings.json"

    @classmethod
    def load(cls) -> "UserSettings":
        path = cls.path()
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: payload[key] for key in allowed if key in payload})

    def save(self) -> Path:
        target = self.path()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".settings-",
            suffix=".json",
            dir=target.parent,
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target
