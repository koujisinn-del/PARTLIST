from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import natural_sort_key, normalized_text
from .runtime_paths import app_data_root
from .template import SiteTemplate, default_template_path


class SiteTemplateStoreError(ValueError):
    pass


class SiteTemplateStore:
    """User-facing named site templates stored in the application's data folder."""

    SCHEMA_VERSION = 1

    def __init__(self, language: str = "zh", path: Path | None = None):
        self.language = language if language in {"zh", "ja"} else "zh"
        self.path = path or (app_data_root() / f"site_templates_{self.language}.json")
        self._records = self._load_records()

    @property
    def default_name(self) -> str:
        return "標準現場" if self.language == "ja" else "默认现场"

    def _default_record(self) -> dict[str, Any]:
        template = SiteTemplate.load(default_template_path(self.language))
        layout = copy.deepcopy(template.data)
        # The DWG itself stays outside the template layout; only its local path
        # is saved as a UI preference.
        layout["frame_dwg"] = ""
        return {
            "layout": layout,
            "ui": {
                "output_path": "",
                "frame_dwg_path": "",
                "sort_descending": False,
                "output_pdf": True,
                "output_dxf": True,
                "output_dwg": True,
                "material_symbols": False,
            },
            "updated_at": "built-in",
        }

    def _load_records(self) -> dict[str, dict[str, Any]]:
        records = {self.default_name: self._default_record()}
        if not self.path.exists():
            return records
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return records
        stored = payload.get("templates", {})
        if isinstance(stored, dict):
            for name, record in stored.items():
                if isinstance(name, str) and isinstance(record, dict):
                    records[name] = record
        return records

    def names(self) -> list[str]:
        return sorted(self._records, key=natural_sort_key)

    def exists(self, name: str) -> bool:
        return normalized_text(name) in self._records

    def get(self, name: str) -> dict[str, Any] | None:
        record = self._records.get(normalized_text(name))
        return copy.deepcopy(record) if record is not None else None

    def save(
        self,
        name: str,
        layout: dict[str, Any],
        ui_settings: dict[str, Any],
    ) -> Path:
        clean_name = normalized_text(name)
        if not clean_name:
            raise SiteTemplateStoreError("请输入现场模板名称。")
        if len(clean_name) > 80:
            raise SiteTemplateStoreError("现场模板名称不能超过 80 个字符。")
        saved_layout = copy.deepcopy(layout)
        saved_layout["name"] = clean_name
        saved_layout["frame_dwg"] = ""
        SiteTemplate(default_template_path(self.language), saved_layout)
        self._records[clean_name] = {
            "layout": saved_layout,
            "ui": copy.deepcopy(ui_settings),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._write()
        return self.path

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "templates": self._records,
        }
        fd, temporary_name = tempfile.mkstemp(
            prefix=".site-templates-",
            suffix=".json",
            dir=self.path.parent,
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
