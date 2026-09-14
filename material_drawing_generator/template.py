from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import normalized_text
from .runtime_paths import resource_root


class TemplateError(ValueError):
    pass


class SiteTemplate:
    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path
        self.data = data
        self._validate()

    @classmethod
    def load(cls, path: str | Path) -> "SiteTemplate":
        template_path = Path(path).resolve()
        try:
            data = json.loads(template_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TemplateError(f"找不到现场模板：{template_path}") from exc
        except json.JSONDecodeError as exc:
            raise TemplateError(f"现场模板格式错误：{exc}") from exc
        return cls(template_path, data)

    def _validate(self):
        required = ["name", "paper", "headers", "category_order", "tables"]
        missing = [key for key in required if key not in self.data]
        if missing:
            raise TemplateError(f"现场模板缺少字段：{', '.join(missing)}")
        if not self.data["tables"]:
            raise TemplateError("现场模板至少需要一个表格区域。")
        for table in self.data["tables"]:
            widths = [float(column["width"]) for column in table.get("columns", [])]
            if not widths or any(width <= 0 for width in widths):
                raise TemplateError(f"表格 {table.get('id', '?')} 的列宽设置无效。")
            if float(table.get("max_height", 0)) <= float(table.get("header_height", 0)):
                raise TemplateError(f"表格 {table.get('id', '?')} 的高度设置无效。")

    @property
    def name(self) -> str:
        return str(self.data["name"])

    @property
    def material_symbols_enabled(self) -> bool:
        return self.data.get("material_symbols", {}).get("enabled") is True

    @property
    def headers(self) -> dict[str, list[str]]:
        universal_aliases = {
            "member_name": ["梁マーク"],
            "category": ["部材種"],
            "joint": ["継手マーク"],
        }
        if self.material_symbols_enabled:
            universal_aliases.update({
                "web_material": ["Web材质", "Web材質", "腹板材质", "ウェブ材質"],
                "top_flange_material": ["上Flange材质", "上Flange材質", "上翼缘材质", "上フランジ材質"],
                "bottom_flange_material": ["下Flange材质", "下Flange材質", "下翼缘材质", "下フランジ材質"],
            })
        configured = self.data["headers"]
        result: dict[str, list[str]] = {}
        for field in dict.fromkeys([*configured, *universal_aliases]):
            values = [*configured.get(field, []), *universal_aliases.get(field, [])]
            result[field] = list(dict.fromkeys(values))
        return result

    @property
    def category_order(self) -> list[str]:
        return list(self.data["category_order"])

    @property
    def tables(self) -> list[dict[str, Any]]:
        return list(self.data["tables"])

    @property
    def paper(self) -> dict[str, Any]:
        return self.data["paper"]

    @property
    def drawing_list(self) -> dict[str, Any]:
        defaults = {
            "sheet_names": ["图纸列表", "図面リスト", "Drawing List"],
            "name_headers": ["图纸名称", "図面名称", "Drawing Name"],
            "number_headers": ["图纸编号", "図面番号", "図番", "Drawing Number"],
            "submission_date_cell": "",
        }
        configured = dict(self.data.get("drawing_list", {}))
        return {**defaults, **configured}

    @property
    def compound_headers(self) -> list[dict[str, Any]]:
        """Merged/compound Excel headers that map to consecutive columns."""
        defaults = [
            {
                "aliases": ["梁サイズ", "梁断面サイズ"],
                "fields": ["position", "section"],
            }
        ]
        configured = self.data.get("compound_headers")
        return list(configured) if isinstance(configured, list) else defaults

    @property
    def frame_placeholders(self) -> dict[str, Any]:
        """Text markers embedded in a site DWG for automatic metadata filling."""
        defaults: dict[str, Any] = {
            "drawing_name": "図面名称",
            # Exact labels in the user's original (unannotated) frame.
            "drawing_name_aliases": ["M5FL 部材リスト"],
            "drawing_number": "図面番号",
            "version": "R-X",
            "version_aliases": ["R-"],
            "creation_date": "20xx/xx/xx",
            "revision_date": "20XX/XX/XX",
            "revision_spacing": 5.0,
        }
        configured = self.data.get("frame_placeholders", {})
        return {**defaults, **configured} if isinstance(configured, dict) else defaults

    @property
    def frame_dwg(self) -> str:
        return str(self.data.get("frame_dwg", "")).strip()

    @property
    def frame_dwg_path(self) -> Path | None:
        value = self.frame_dwg
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.path.parent / path
        return path.resolve()

    def label(self, key: str, default: str = "") -> str:
        return str(self.data.get("labels", {}).get(key, default))

    def with_frame_dwg(self, path: str | Path | None) -> "SiteTemplate":
        copied = json.loads(json.dumps(self.data, ensure_ascii=False))
        copied["frame_dwg"] = str(path or "")
        return SiteTemplate(self.path, copied)

    def canonical_category(self, value: str) -> str:
        normalized = normalized_text(value).casefold()
        aliases: dict[str, list[str]] = self.data.get("category_aliases", {})
        for canonical in self.category_order:
            candidates = [canonical, *aliases.get(canonical, [])]
            if normalized in {
                normalized_text(candidate).casefold() for candidate in candidates
            }:
                return canonical
        return normalized_text(value)

    def table_for_category(self, category: str) -> dict[str, Any] | None:
        canonical = self.canonical_category(category)
        for table in self.tables:
            if self.canonical_category(str(table["category"])) == canonical:
                return table
        return None

    def tables_for_categories(self, categories: list[str]) -> list[dict[str, Any]]:
        """Create drawing table areas from the categories present in one sheet."""
        canonical_categories: list[str] = []
        seen: set[str] = set()
        for category in categories:
            canonical = self.canonical_category(category)
            key = normalized_text(canonical).casefold()
            if key and key not in seen:
                seen.add(key)
                canonical_categories.append(canonical)
        if not canonical_categories:
            return []

        presets = self.tables
        default_preset = presets[0]
        layout = dict(self.data.get("dynamic_table_layout", {}))
        left = float(layout.get("left", min(float(table["x"]) for table in presets)))
        right = float(
            layout.get(
                "right",
                float(self.paper.get("revision_left", self.paper["width"])) - 6.0,
            )
        )
        top = float(layout.get("top", max(float(table["top"]) for table in presets)))
        bottom = float(
            layout.get("bottom", float(self.paper.get("bottom_info_top", 55.0)) + 8.0)
        )
        horizontal_gap = float(layout.get("horizontal_gap", 8.0))
        cell_height = float(layout.get("cell_height", 7.0))
        available_width = right - left - horizontal_gap * (len(canonical_categories) - 1)
        if available_width <= 0 or top <= bottom:
            raise TemplateError("现场模板的动态表格范围无效。")

        generated: list[dict[str, Any]] = []
        base_widths: list[float] = []
        for index, category in enumerate(canonical_categories):
            matched = self.table_for_category(category)
            preset = matched or default_preset
            table = json.loads(json.dumps(preset, ensure_ascii=False))
            table["id"] = f"dynamic_{index + 1}"
            table["category"] = category
            table["title"] = category + "部材リスト"
            table["top"] = top
            table["max_height"] = top - bottom
            # Header and ordinary cells start from the same 7 mm CAD grid.
            # Dense tables may still compress uniformly to stay on one A3 page.
            table["header_height"] = cell_height
            table["row_height"] = cell_height
            table["min_row_height"] = float(layout.get("min_row_height", 2.2))
            generated.append(table)
            base_widths.append(sum(float(column["width"]) for column in table["columns"]))

        width_scale = min(1.0, available_width / sum(base_widths))
        current_x = left
        for table, base_width in zip(generated, base_widths):
            table["x"] = current_x
            for column in table["columns"]:
                column["width"] = float(column["width"]) * width_scale
            table_width = base_width * width_scale
            table_bottom = bottom
            note = self.data.get("note_area")
            if isinstance(note, dict):
                note_left = float(note.get("x", 0.0))
                note_right = note_left + float(note.get("width", 0.0))
                overlaps_note = current_x < note_right and current_x + table_width > note_left
                if overlaps_note:
                    note_top = float(note.get("y", 0.0)) + float(note.get("height", 0.0))
                    table_bottom = max(table_bottom, note_top + 6.0)
            table["max_height"] = top - table_bottom
            current_x += table_width + horizontal_gap
        return generated


def default_template_path(language: str = "zh") -> Path:
    filename = "concept_a3_ja.json" if language == "ja" else "concept_a3.json"
    return resource_root() / "site_templates" / filename
