from __future__ import annotations

import json
import hashlib
import math
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .models import MemberGroup, SheetData, normalized_text, source_text
from .template import SiteTemplate
from .runtime_paths import resource_root
from .runtime_paths import app_data_root


def _converter_root() -> Path:
    updated = resource_root() / "libredwg-0.14.8594-win64"
    return updated if updated.exists() else resource_root() / "libredwg-0.14-win64"


class DrawingError(ValueError):
    pass


class DrawingAdapter(Protocol):
    def line(self, x1: float, y1: float, x2: float, y2: float, layer: str, width: float = 0.25): ...
    def rect(self, x1: float, y1: float, x2: float, y2: float, layer: str, width: float = 0.25): ...
    def circle(self, x: float, y: float, radius: float, layer: str, width: float = 0.25): ...
    def text_box(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        value: str,
        layer: str,
        size: float = 2.5,
        min_size: float = 1.1,
        align: str = "center",
    ): ...


@dataclass(slots=True)
class DrawingMetadata:
    drawing_number: str = ""
    version: str = ""
    drawing_date: str = ""
    revision_dates: list[str] | None = None

    def revisions(self) -> list[str]:
        return list(self.revision_dates or [])


def _display_units(value: str) -> float:
    total = 0.0
    for char in value:
        total += 1.0 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 0.55
    return total


def _safe_drawing_text(value: Any) -> str:
    """Preserve visible characters; a missing glyph must not change a symbol."""
    return source_text(value).strip().replace("\r\n", " / ").replace("\n", " / ")


def _format_drawing_date(value: Any) -> str:
    """Normalize Excel dates to the YY.MM.DD notation used by the CAD frame."""
    text = _safe_drawing_text(value)
    match = re.fullmatch(r"(\d{2}|\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if not match:
        return text
    year, month, day = match.groups()
    return f"{year[-2:]}.{int(month):02d}.{int(day):02d}"


def _cjk_font_filename() -> str:
    candidates = (
        # Noto Sans SC covers both enclosed Latin marks (for example 🄱) and
        # the simplified/traditional Han characters in both language modes.
        # Yu Gothic is the next choice on Japanese Windows; YaHei lacks 🄱.
        Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
        Path("C:/Windows/Fonts/YuGothM.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/meiryo.ttc"),
    )
    return next((path.name for path in candidates if path.exists()), "meiryo.ttc")


def _text_size_cap(
    value: Any,
    width: float,
    height: float,
    preferred: float,
    horizontal_padding: float = 0.6,
    vertical_padding: float = 0.3,
) -> float:
    text = _safe_drawing_text(value)
    if not text:
        return preferred
    available_width = max(0.1, width - horizontal_padding * 2)
    available_height = max(0.1, height - vertical_padding * 2)
    width_limit = available_width / max(_display_units(text), 0.1)
    height_limit = available_height * 0.55
    return max(0.1, min(float(preferred), width_limit, height_limit))


def _second_smallest(values: list[float], default: float) -> float:
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return default
    return ordered[1] if len(ordered) > 1 else ordered[0]


class PdfAdapter:
    def __init__(self, output_path: Path, width_mm: float, height_mm: float):
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm

        self.mm = mm
        self.pdfmetrics = pdfmetrics
        self.output_path = output_path
        self.font_name = "HeiseiKakuGo-W5"
        font_candidates = [
            ("MicrosoftYaHei", Path("C:/Windows/Fonts/msyh.ttc")),
            ("Meiryo", Path("C:/Windows/Fonts/meiryo.ttc")),
            ("YuGothic", Path("C:/Windows/Fonts/YuGothM.ttc")),
        ]
        for font_name, font_path in font_candidates:
            if not font_path.exists():
                continue
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(font_path), subfontIndex=0))
                self.font_name = font_name
                break
            except Exception:
                continue
        else:
            pdfmetrics.registerFont(UnicodeCIDFont(self.font_name))
        self.canvas = canvas.Canvas(
            str(output_path),
            pagesize=(width_mm * mm, height_mm * mm),
            pageCompression=1,
        )
        self.canvas.setTitle(output_path.stem)

    def line(self, x1, y1, x2, y2, layer, width=0.25):
        self.canvas.setStrokeColorRGB(0, 0, 0)
        self.canvas.setLineWidth(max(0.06, width * 0.65) * self.mm)
        self.canvas.line(x1 * self.mm, y1 * self.mm, x2 * self.mm, y2 * self.mm)

    def rect(self, x1, y1, x2, y2, layer, width=0.25):
        self.canvas.setStrokeColorRGB(0, 0, 0)
        self.canvas.setLineWidth(max(0.06, width * 0.65) * self.mm)
        self.canvas.rect(
            x1 * self.mm,
            y1 * self.mm,
            (x2 - x1) * self.mm,
            (y2 - y1) * self.mm,
            stroke=1,
            fill=0,
        )

    def circle(self, x, y, radius, layer, width=0.25):
        self.canvas.setStrokeColorRGB(0, 0, 0)
        self.canvas.setLineWidth(max(0.06, width * 0.65) * self.mm)
        self.canvas.circle(x * self.mm, y * self.mm, radius * self.mm, stroke=1, fill=0)

    def text_box(
        self,
        x1,
        y1,
        x2,
        y2,
        value,
        layer,
        size=2.5,
        min_size=1.1,
        align="center",
    ):
        text = _safe_drawing_text(value)
        if not text:
            return
        available_width = max(0.1, (x2 - x1) - 1.0) * self.mm
        vertical_limit = max(0.1, ((y2 - y1) - 0.6) * 0.55)
        chosen = min(float(size), vertical_limit)
        if getattr(self, "strict_table_text", False):
            chosen = float(size)
        while chosen > 0.2 and not getattr(self, "strict_table_text", False):
            width = self.pdfmetrics.stringWidth(text, self.font_name, chosen * self.mm)
            if width <= available_width:
                break
            chosen = max(0.2, chosen * available_width / max(width, 0.1) * 0.98)
        font_size = chosen * self.mm
        self.canvas.setFont(self.font_name, font_size)
        self.canvas.setFillColorRGB(0, 0, 0)
        baseline = ((y1 + y2) / 2.0) * self.mm - font_size * 0.34
        if align == "left":
            self.canvas.drawString((x1 + 0.6) * self.mm, baseline, text)
        elif align == "right":
            self.canvas.drawRightString((x2 - 0.6) * self.mm, baseline, text)
        else:
            self.canvas.drawCentredString(((x1 + x2) / 2.0) * self.mm, baseline, text)

    def save(self):
        self.canvas.showPage()
        self.canvas.save()


def _load_ezdxf():
    try:
        import ezdxf
        return ezdxf
    except (ImportError, AttributeError):
        dependency_path = Path(__file__).resolve().parents[1] / "_python_deps"
        if str(dependency_path) not in sys.path:
            sys.path.insert(0, str(dependency_path))
        import ezdxf
        return ezdxf


def _repair_broken_material_dictionary(document) -> None:
    """Replace dangling material handles produced by some LibreDWG exports."""
    required = ("ByLayer", "ByBlock", "Global")
    try:
        broken = any(
            not hasattr(document.materials.get(name), "dxf")
            for name in required
        )
    except Exception:
        broken = True
    if broken:
        document.materials.clear()
        document.materials.create_required_entries()


def _normalize_external_frame(document, width_mm: float, height_mm: float) -> bool:
    """Fit a detected A-series outer rectangle to the common A3 origin."""
    from ezdxf import bbox
    from ezdxf.math import Matrix44

    modelspace = document.modelspace()
    target_ratio = width_mm / height_mm
    candidates: list[tuple[float, Any, Any]] = []
    for entity in modelspace.query("LWPOLYLINE"):
        bounds = bbox.extents([entity])
        if not bounds.has_data:
            continue
        source_width = bounds.extmax.x - bounds.extmin.x
        source_height = bounds.extmax.y - bounds.extmin.y
        if source_width <= 0 or source_height <= 0:
            continue
        ratio_error = abs(source_width / source_height / target_ratio - 1.0)
        if ratio_error <= 0.03:
            candidates.append((source_width * source_height, entity, bounds))
    if not candidates:
        return False

    _area, _frame_entity, frame_bounds = max(candidates, key=lambda item: item[0])
    min_x = frame_bounds.extmin.x
    min_y = frame_bounds.extmin.y
    max_x = frame_bounds.extmax.x
    max_y = frame_bounds.extmax.y
    tolerance = max(max_x - min_x, max_y - min_y) * 1e-6 + 1e-6
    for entity in list(modelspace):
        bounds = bbox.extents([entity])
        if not bounds.has_data:
            continue
        outside = (
            bounds.extmin.x < min_x - tolerance
            or bounds.extmin.y < min_y - tolerance
            or bounds.extmax.x > max_x + tolerance
            or bounds.extmax.y > max_y + tolerance
        )
        if outside:
            modelspace.delete_entity(entity)

    source_width = max_x - min_x
    source_height = max_y - min_y
    scale = min(width_mm / source_width, height_mm / source_height)
    translate_x = (width_mm - source_width * scale) / 2.0 - min_x * scale
    translate_y = (height_mm - source_height * scale) / 2.0 - min_y * scale
    transform = Matrix44.chain(
        Matrix44.scale(scale, scale, scale),
        Matrix44.translate(translate_x, translate_y, 0.0),
    )
    for entity in list(modelspace):
        try:
            entity.transform(transform)
        except Exception as exc:
            raise DrawingError(f"现场图框实体无法缩放到 A3：{entity.dxftype()} ({exc})") from exc
    return True


def _apply_pdf_font_to_styles(document, font_filename: str) -> None:
    """Local rendering fallback, for a PDF-only document copy, never CAD output."""
    family = {
        "notosanssc-vf.ttf": "Noto Sans SC", "msyh.ttc": "Microsoft YaHei",
        "meiryo.ttc": "Meiryo", "yugothm.ttc": "Yu Gothic",
    }.get(font_filename.lower(), "")
    for style in document.styles:
        style.dxf.font = font_filename
        style.dxf.bigfont = ""
        if family:
            style.set_extended_font_data(family, italic=False, bold=False)


class CadAdapter:
    def __init__(
        self,
        output_path: Path,
        width_mm: float,
        height_mm: float,
        base_dxf_path: Path | None = None,
    ):
        self.ezdxf = _load_ezdxf()
        self.output_path = output_path
        self.width_mm = width_mm
        self.height_mm = height_mm
        if base_dxf_path is not None:
            try:
                self.doc = self.ezdxf.readfile(base_dxf_path)
            except Exception as exc:
                try:
                    from ezdxf import recover

                    self.doc, _auditor = recover.readfile(base_dxf_path)
                except Exception:
                    raise DrawingError(f"无法读取由图框 DWG 转换得到的 DXF：{exc}") from exc
        else:
            self.doc = self.ezdxf.new("R2000", setup=False)
        _repair_broken_material_dictionary(self.doc)
        if base_dxf_path is not None:
            _normalize_external_frame(self.doc, width_mm, height_mm)
        # R2007 DXF stores text as UTF-8.  This preserves Chinese/Japanese text
        # and symbols such as the multiplication sign in generated CAD files.
        self.doc.dxfversion = "AC1021"
        self.doc.units = self.ezdxf.units.MM
        self.doc.header["$LIMMIN"] = (0.0, 0.0)
        self.doc.header["$LIMMAX"] = (width_mm, height_mm)
        self.modelspace = self.doc.modelspace()
        layer_colors = {
            "ALIGN_A3": 8,
            "BORDER": 7,
            "FIXED_INFO": 7,
            "TABLE_OOBARI": 3,
            "TABLE_KOBARI": 4,
            "REVISION": 1,
            "NOTE_INSERT": 6,
            "TEXT": 7,
        }
        for name, color in layer_colors.items():
            if name not in self.doc.layers:
                self.doc.layers.add(name, color=color)
        self.text_style = "MDG_CJK"
        if self.text_style not in self.doc.styles:
            self.doc.styles.add(self.text_style, font=_cjk_font_filename())
        style = self.doc.styles.get(self.text_style)
        # Reference the actual installed file used for measurements, not a
        # synthetic face-name filename which may trigger viewer substitution.
        # Keep the existing frame styles, bigfonts and ACAD font metadata intact.
        style.dxf.font = _cjk_font_filename()
        style.dxf.bigfont = ""
        style.dxf.height = 0
        style.dxf.width = 1
        style.dxf.flags = 0
        style.dxf.oblique = 0
        style.dxf.generation_flags = 0
        family = {
            "notosanssc-vf.ttf": "Noto Sans SC", "msyh.ttc": "Microsoft YaHei",
            "yugothm.ttc": "Yu Gothic", "meiryo.ttc": "Meiryo",
        }[style.dxf.font.lower()]
        style.set_xdata("ACAD", [(1000, family), (1071, 0)])

    def line(self, x1, y1, x2, y2, layer, width=0.25):
        from ezdxf.lldxf.const import VALID_DXF_LINEWEIGHTS
        requested = max(9, round(width * 65))
        lineweight = min(VALID_DXF_LINEWEIGHTS, key=lambda value: abs(value - requested))
        self.modelspace.add_line(
            (float(x1), float(y1)),
            (float(x2), float(y2)),
            dxfattribs={"layer": layer, "lineweight": lineweight},
        )

    def rect(self, x1, y1, x2, y2, layer, width=0.25):
        self.line(x1, y1, x2, y1, layer, width)
        self.line(x2, y1, x2, y2, layer, width)
        self.line(x2, y2, x1, y2, layer, width)
        self.line(x1, y2, x1, y1, layer, width)

    def circle(self, x, y, radius, layer, width=0.25):
        self.modelspace.add_circle(
            (float(x), float(y)),
            float(radius),
            dxfattribs={"layer": layer},
        )

    def text_box(
        self,
        x1,
        y1,
        x2,
        y2,
        value,
        layer,
        size=2.5,
        min_size=1.1,
        align="center",
    ):
        text = _safe_drawing_text(value)
        if not text:
            return
        available_width = max(0.1, (x2 - x1) - 1.0)
        vertical_limit = max(0.1, ((y2 - y1) - 0.6) * 0.55)
        chosen = min(float(size), vertical_limit)
        strict = getattr(self, "strict_table_text", False)
        if strict:
            chosen = float(size)
        effective_min = min(float(min_size), chosen)
        while not strict and chosen > effective_min and _display_units(text) * chosen > available_width:
            chosen = max(effective_min, chosen - 0.15)
        if align == "left":
            insert_x = x1 + 0.6
            h_align = 0
            align_point = (insert_x, (y1 + y2) / 2.0)
        elif align == "right":
            insert_x = x2 - 0.6
            h_align = 2
            align_point = (insert_x, (y1 + y2) / 2.0)
        else:
            insert_x = (x1 + x2) / 2.0
            h_align = 1
            align_point = (insert_x, (y1 + y2) / 2.0)
        entity = self.modelspace.add_text(
            text,
            dxfattribs={
                "layer": layer,
                "style": self.text_style,
                "height": float(chosen),
                "insert": (insert_x, (y1 + y2) / 2.0),
                "halign": h_align,
                "valign": 2,
            },
        )
        entity.dxf.align_point = align_point
        if getattr(self, "center_position_ink", False):
            from ezdxf import bbox
            from ezdxf.math import Vec3
            bounds = bbox.extents([entity])
            if bounds.has_data:
                offset = Vec3((x1 + x2) / 2, (y1 + y2) / 2, 0) - bounds.center
                entity.dxf.insert += offset
                entity.dxf.align_point += offset
        if strict:
            from ezdxf import bbox
            bounds = bbox.extents([entity])
            if bounds.has_data and (
                bounds.extmax.x - bounds.extmin.x > (x2 - x1) - 1.2 + 1e-5
                or bounds.extmax.y - bounds.extmin.y > (y2 - y1) - 0.6 + 1e-5
            ):
                raise DrawingError(f"单元格文字无法按统一字号放入：{text}。请扩大列宽或调整整表设置。")
        else:
            self._fit_text_entity(entity, x1, y1, x2, y2)

    @staticmethod
    def _fit_text_entity(entity, x1: float, y1: float, x2: float, y2: float) -> None:
        """Shrink a CAD text entity until its rendered bounds stay in the cell."""
        from ezdxf import bbox

        available_width = max(0.05, (x2 - x1) - 1.2)
        available_height = max(0.05, (y2 - y1) - 0.6)
        for _attempt in range(4):
            bounds = bbox.extents([entity])
            if not bounds.has_data:
                return
            rendered_width = max(0.001, bounds.extmax.x - bounds.extmin.x)
            rendered_height = max(0.001, bounds.extmax.y - bounds.extmin.y)
            if (
                rendered_width <= available_width + 1e-6
                and rendered_height <= available_height + 1e-6
            ):
                return
            scale = min(
                available_width / rendered_width,
                available_height / rendered_height,
            )
            current_height = float(entity.dxf.height)
            entity.dxf.height = max(0.2, current_height * scale * 0.97)

    def _text_placeholder(self, marker: str):
        # Ignore spacing/full-width variants, but keep case: the two date
        # markers deliberately distinguish creation date from revision history.
        wanted = normalized_text(marker).replace(" ", "")
        if not wanted:
            return None
        matches = [
            entity for entity in self.modelspace.query("TEXT")
            if normalized_text(entity.dxf.text).replace(" ", "") == wanted
        ]
        if len(matches) > 1:
            raise DrawingError(f"图框内有多处定位文字“{marker}”，请保留唯一的定位标记。")
        return matches[0] if matches else None

    def apply_external_metadata(
        self,
        sheet_name: str,
        metadata: DrawingMetadata,
        placeholders: dict[str, Any],
    ) -> bool:
        """Resolve every anchor before editing; never fall back to concept coordinates."""
        markers = {
            "drawing_name": ("图名", "図面名称"),
            "drawing_number": ("图号", "図面番号"),
            "version": ("版本", "R-X"),
            "creation_date": ("作成日", "20xx/xx/xx"),
            "revision_date": ("更新履历", "20XX/XX/XX"),
        }
        anchors: dict[str, Any] = {}
        missing: list[str] = []
        for key, (label, default_marker) in markers.items():
            marker = str(placeholders.get(key, default_marker))
            candidates = [marker, *placeholders.get(f"{key}_aliases", [])]
            for candidate in candidates:
                entity = self._text_placeholder(str(candidate))
                if entity is not None:
                    anchors[key] = entity
                    break
            if key not in anchors:
                missing.append(f"{label}（{marker}）")
        if missing:
            raise DrawingError(
                "现场图框缺少定位文字：" + "、".join(missing)
                + "。请在对应位置添加单行文字标记，或使用带文字标记的图框。"
                "为避免错位，已停止生成，不使用概念图框的固定坐标。"
            )
        if len({id(entity) for entity in anchors.values()}) != len(anchors):
            raise DrawingError("图框中多个字段指向同一定位文字，请检查图框标记设置。")

        # The supplied legacy frame stores R- and its old number as separate
        # TEXT entities. Only remove a unique adjacent numeric suffix, never
        # unrelated revision/legend numbers elsewhere in the drawing.
        version_entity = anchors["version"]
        legacy_suffix = None
        if normalized_text(version_entity.dxf.text) == "R-":
            origin = version_entity.dxf.insert
            height = float(version_entity.dxf.height)
            suffixes = [
                entity for entity in self.modelspace.query("TEXT")
                if normalized_text(entity.dxf.text).isdigit()
                and 0 < entity.dxf.insert.x - origin.x <= height * 6
                and abs(entity.dxf.insert.y - origin.y) <= height * 0.5
            ]
            if len(suffixes) != 1:
                raise DrawingError("旧图框的 R- 后方版本数字无法唯一定位，请将其改为单个 R-X 文字。")
            legacy_suffix = suffixes[0]

        spacing = float(placeholders.get("revision_spacing", 5.0))
        if not math.isfinite(spacing) or spacing <= 0:
            raise DrawingError("图框的履历行距必须大于 0。")
        anchors["drawing_name"].dxf.text = _safe_drawing_text(sheet_name)
        anchors["drawing_number"].dxf.text = _safe_drawing_text(metadata.drawing_number)
        version_entity.dxf.text = f"R-{_safe_drawing_text(metadata.version)}"
        if legacy_suffix is not None:
            self.modelspace.delete_entity(legacy_suffix)

        creation_date = _format_drawing_date(metadata.drawing_date)
        anchors["creation_date"].dxf.text = creation_date

        revision_entity = anchors["revision_date"]
        revision_dates = [
            _format_drawing_date(value)
            for value in metadata.revisions()
            if _format_drawing_date(value)
        ]
        if not revision_dates and creation_date:
            revision_dates = [creation_date]
        if revision_entity is not None:
            if revision_dates:
                revision_entity.dxf.text = revision_dates[0]
                for index, revision_date in enumerate(revision_dates[1:], start=1):
                    copied = revision_entity.copy()
                    copied.dxf.text = revision_date
                    self.modelspace.add_entity(copied)
                    copied.translate(0.0, spacing * index, 0.0)
            else:
                revision_entity.dxf.text = ""

        return True

    def save(self):
        from .cad_integrity import reset_model_view
        reset_model_view(self.doc)
        self.doc.saveas(self.output_path)
        _patch_dxf_extents(self.output_path, self.width_mm, self.height_mm)


def prepare_frame_dxf(frame_path: Path | None) -> Path | None:
    if frame_path is None:
        return None
    frame_path = frame_path.resolve()
    if not frame_path.exists():
        raise DrawingError(f"找不到现场图框文件：{frame_path}")
    if frame_path.suffix.lower() == ".dxf":
        return frame_path
    if frame_path.suffix.lower() != ".dwg":
        raise DrawingError("现场图框目前支持 .dwg 或 .dxf。")
    converter = _converter_root() / "dwg2dxf.exe"
    if not converter.exists():
        raise DrawingError("找不到 DWG 图框读取组件。")
    signature = (
        f"native-dxf-v3|{converter.parent.name}|{frame_path}|{frame_path.stat().st_mtime_ns}|"
        f"{frame_path.stat().st_size}"
    )
    cache_name = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20] + ".dxf"
    cache_dir = app_data_root() / "frame_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / cache_name
    if cached.exists() and cached.stat().st_size > 1000:
        _repair_invalid_zero_handles(cached)
        return cached
    command = [
        str(converter),
        "-y",
        "-o",
        str(cached),
        str(frame_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0 or not cached.exists() or cached.stat().st_size < 1000:
        details = (result.stdout + "\n" + result.stderr).strip()
        cached.unlink(missing_ok=True)
        raise DrawingError(f"现场 DWG 图框读取失败：{details[-1200:]}")
    _repair_invalid_zero_handles(cached)
    return cached


def _repair_invalid_zero_handles(path: Path) -> None:
    """Repair LibreDWG's occasional entity handle ``0`` in an ASCII DXF.

    DXF uses hexadecimal entity handles and ``0`` is not a valid entity handle.
    Some otherwise valid DWG files are exported by LibreDWG with a zero handle on
    an ``ENDBLK`` entity.  Assigning a fresh handle preserves the entity and lets
    ezdxf load the converted frame without altering the user's source DWG.
    """
    raw = path.read_bytes()
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.splitlines()
    if len(lines) < 2:
        return
    used_handles: set[int] = set()
    zero_value_indexes: list[int] = []
    for index in range(0, len(lines) - 1, 2):
        code = lines[index].strip()
        value = lines[index + 1].strip()
        if code not in {b"5", b"105"}:
            continue
        if value == b"0":
            zero_value_indexes.append(index + 1)
            continue
        try:
            used_handles.add(int(value, 16))
        except ValueError:
            continue
    if not zero_value_indexes:
        return
    next_handle = max(used_handles, default=0x100) + 1
    for value_index in zero_value_indexes:
        while next_handle in used_handles:
            next_handle += 1
        lines[value_index] = format(next_handle, "X").encode("ascii")
        used_handles.add(next_handle)
        next_handle += 1
    path.write_bytes(newline.join(lines) + newline)


def _patch_dxf_extents(path: Path, width_mm: float, height_mm: float):
    raw = path.read_bytes()
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.splitlines()

    def set_point(name: bytes, values: tuple[float, ...]):
        try:
            start = lines.index(name)
        except ValueError:
            return
        cursor = start + 1
        replacements = {10: values[0], 20: values[1]}
        if len(values) == 3:
            replacements[30] = values[2]
        while cursor + 1 < len(lines):
            code_text = lines[cursor].strip()
            if code_text == b"9":
                break
            try:
                code = int(code_text)
            except ValueError:
                cursor += 1
                continue
            if code in replacements:
                lines[cursor + 1] = str(float(replacements[code])).encode("ascii")
            cursor += 2

    set_point(b"$EXTMIN", (0.0, 0.0, 0.0))
    set_point(b"$EXTMAX", (width_mm, height_mm, 0.0))
    set_point(b"$PEXTMIN", (0.0, 0.0, 0.0))
    set_point(b"$PEXTMAX", (width_mm, height_mm, 0.0))
    path.write_bytes(newline.join(lines) + newline)


def _draw_fixed_frame(
    adapter: DrawingAdapter,
    template: SiteTemplate,
    sheet: SheetData,
    metadata: DrawingMetadata,
):
    paper = template.paper
    width = float(paper["width"])
    height = float(paper["height"])
    margin = float(paper["inner_margin"])
    bottom_top = float(paper["bottom_info_top"])
    revision_left = float(paper["revision_left"])
    revision_number_left = float(paper["revision_number_left"])
    revision_bottom = float(paper["revision_bottom"])
    revision_cell_height = float(paper["revision_cell_height"])
    revision_slots = int(paper["revision_slots"])
    inner_top = height - margin
    external_frame = template.frame_dwg_path is not None

    if external_frame and isinstance(adapter, CadAdapter):
        adapter.apply_external_metadata(
            sheet.name,
            metadata,
            template.frame_placeholders,
        )
        return

    if not external_frame:
        adapter.rect(0, 0, width, height, "ALIGN_A3", 0.18)
        adapter.rect(margin, margin, width - margin, inner_top, "BORDER", 0.5)
        adapter.line(margin, bottom_top, revision_left, bottom_top, "FIXED_INFO", 0.5)
        adapter.line(revision_left, margin, revision_left, inner_top, "REVISION", 0.5)
        adapter.line(revision_number_left, revision_bottom, revision_number_left, inner_top, "REVISION", 0.25)
        for index in range(revision_slots + 1):
            y = revision_bottom + index * revision_cell_height
            adapter.line(revision_left, y, width - margin, y, "REVISION", 0.18)

    for index, revision_date in enumerate(metadata.revisions()[:revision_slots]):
        y1 = revision_bottom + index * revision_cell_height
        y2 = y1 + revision_cell_height
        adapter.text_box(revision_left, y1, revision_number_left, y2, str(index + 1), "REVISION", 1.7, 1.2)
        adapter.text_box(revision_number_left, y1, width - margin, y2, revision_date, "REVISION", 1.7, 1.0)
    if not external_frame:
        adapter.text_box(
            revision_left,
            inner_top - 4,
            width - margin,
            inner_top,
            template.label("revision_history", "更新履历"),
            "REVISION",
            1.8,
            1.2,
        )

        # Bottom legend, company and title blocks for the built-in concept frame.
        for x in (155.0, 250.0, 330.0):
            adapter.line(x, margin, x, bottom_top, "FIXED_INFO", 0.35)
        adapter.line(360.0, margin, 360.0, 30.0, "FIXED_INFO", 0.35)
        adapter.line(margin, 30.0, revision_left, 30.0, "FIXED_INFO", 0.35)
        adapter.line(250.0, 42.0, revision_left, 42.0, "FIXED_INFO", 0.35)
        adapter.text_box(13, 45, 80, 53, template.label("legend", "图例 / 记号说明"), "TEXT", 3.0, 2.0, "left")
        adapter.circle(18.0, 37.0, 2.2, "FIXED_INFO", 0.25)
        adapter.text_box(15.8, 34.8, 20.2, 39.2, "B", "TEXT", 2.0, 1.5)
        adapter.text_box(22, 34, 150, 40, template.label("legend_note", "特殊符号与一般注记由现场模板确定"), "TEXT", 2.2, 1.2, "left")
        adapter.text_box(158, 45, 245, 53, template.label("site_info", "公司 / 现场信息"), "TEXT", 3.0, 2.0, "left")
        adapter.text_box(158, 33, 245, 41, template.name, "TEXT", 2.2, 1.2, "left")
        adapter.text_box(253, 44, 327, 53, template.label("drawing_name", "图纸名称"), "TEXT", 2.6, 1.8, "left")
    adapter.text_box(253, 31, 389, 42, sheet.name, "TEXT", 3.0, 1.4, "left")
    drawing_number = metadata.drawing_number
    version = metadata.version
    if not external_frame:
        drawing_number = f"{template.label('drawing_number', '图号')} {drawing_number}"
        version = f"{template.label('version', '版本')} {version}"
    adapter.text_box(253, 17, 327, 29, drawing_number, "TEXT", 2.1, 1.3, "left")
    adapter.text_box(332, 17, 357, 29, version, "TEXT", 2.0, 1.1, "left")
    adapter.text_box(362, 17, 389, 29, metadata.drawing_date, "TEXT", 1.8, 1.0, "left")

    note = None if external_frame else template.data.get("note_area")
    if note:
        x1 = float(note["x"])
        y1 = float(note["y"])
        x2 = x1 + float(note["width"])
        y2 = y1 + float(note["height"])
        adapter.rect(x1, y1, x2, y2, "NOTE_INSERT", 0.35)
        # Parallel 45-degree guide lines, clipped to the note rectangle.
        start_y = y1
        while start_y < y2:
            end_x = min(x2, x1 + (y2 - start_y))
            end_y = start_y + (end_x - x1)
            adapter.line(x1, start_y, end_x, end_y, "NOTE_INSERT", 0.12)
            start_y += 8.0
        start_x = x1 + 8.0
        while start_x < x2:
            end_x = min(x2, start_x + (y2 - y1))
            end_y = y1 + (end_x - start_x)
            adapter.line(start_x, y1, end_x, end_y, "NOTE_INSERT", 0.12)
            start_x += 8.0
        adapter.text_box(x1 + 1, y2 - 7, x2 - 1, y2 - 1, str(note["label"]), "NOTE_INSERT", 2.5, 1.4, "left")


def _draw_table(
    adapter: DrawingAdapter,
    table: dict[str, Any],
    groups: list[MemberGroup],
    table_font_size: float,
    material_plan=None,
):
    x = float(table["x"])
    top = float(table["top"])
    header_height = float(table["header_height"])
    columns = table["columns"]
    widths = [float(column["width"]) for column in columns]
    total_width = sum(widths)
    row_height, total_rows = _table_row_height(table, groups)
    body_rows_for_frame = total_rows or 1
    bottom = top - header_height - body_rows_for_frame * row_height
    layer = str(table.get("layer", "BORDER"))
    x_positions = [x]
    for width in widths:
        x_positions.append(x_positions[-1] + width)

    adapter.text_box(
        x,
        top + 1.0,
        x + total_width,
        top + 1.0 + header_height,
        str(table["title"]),
        "TEXT",
        table_font_size,
        0.65,
        "left",
    )
    adapter.rect(x, bottom, x + total_width, top, layer, 0.20)
    header_bottom = top - header_height
    adapter.line(x, header_bottom, x + total_width, header_bottom, layer, 0.20)
    for boundary in x_positions[1:-1]:
        adapter.line(boundary, bottom, boundary, top, layer, 0.13)
    for index, column in enumerate(columns):
        adapter.text_box(
            x_positions[index],
            header_bottom,
            x_positions[index + 1],
            top,
            str(column["label"]),
            "TEXT",
            table_font_size,
            0.65,
        )

    if not groups:
        adapter.line(x, bottom, x + total_width, bottom, layer, 0.12)
        return 0

    current_top = header_bottom
    rendered_rows = 0
    for group in groups:
        group_height = len(group.rows) * row_height
        group_bottom = current_top - group_height
        adapter.text_box(
            x_positions[0],
            group_bottom,
            x_positions[1],
            current_top,
            group.member_name,
            "TEXT",
            table_font_size,
            0.65,
        )
        for row_index, section_row in enumerate(group.rows):
            row_top = current_top - row_index * row_height
            row_bottom = row_top - row_height
            for column_index, column in enumerate(columns[1:], start=1):
                if column["field"] == "section" and material_plan is not None:
                    material_plan.factory.draw_cell(
                        adapter, material_plan, section_row, x_positions[column_index], row_bottom,
                        x_positions[column_index + 1], row_top, table_font_size,
                    )
                    continue
                value = section_row.value_for(str(column["field"]))
                adapter.center_position_ink = column["field"] == "position"
                adapter.text_box(
                    x_positions[column_index],
                    row_bottom,
                    x_positions[column_index + 1],
                    row_top,
                    value,
                    "TEXT",
                    table_font_size,
                    0.65,
                )
                adapter.center_position_ink = False
            if row_index < len(group.rows) - 1:
                adapter.line(x_positions[1], row_bottom, x + total_width, row_bottom, layer, 0.09)
            rendered_rows += 1
        adapter.line(x, group_bottom, x + total_width, group_bottom, layer, 0.14)
        current_top = group_bottom
    return rendered_rows


def _table_row_height(
    table: dict[str, Any],
    groups: list[MemberGroup],
) -> tuple[float, int]:
    max_height = float(table["max_height"])
    header_height = float(table["header_height"])
    preferred_row_height = float(table["row_height"])
    min_row_height = float(table["min_row_height"])
    total_rows = sum(len(group.rows) for group in groups)
    if not total_rows:
        return preferred_row_height, 0
    # The header is also one unit cell; every merged cell is N identical units.
    row_height = min(preferred_row_height, max_height / (total_rows + 1))
    if row_height + 1e-8 < min_row_height:
        raise DrawingError(
            f"{table['title']} 有 {total_rows} 行，按最小行高 {min_row_height} mm 仍无法放入当前区域。"
        )
    return row_height, total_rows


def _prepare_tables(sheet: SheetData, template: SiteTemplate, material_plan=None) -> list[tuple[dict, list[MemberGroup], float]]:
    """Measure at a fixed per-table font, then allocate columns without shrinking text."""
    from ezdxf.fonts import fonts
    font = fonts.make_font(_cjk_font_filename(), 1.0)
    tables = template.tables_for_categories(sheet.categories)
    if not tables:
        return []
    layout = template.data.get("dynamic_table_layout", {})
    left = float(layout.get("left", min(t["x"] for t in tables)))
    right = float(layout.get("right", float(template.paper.get("revision_left", 392)) - 6))
    gap = float(layout.get("horizontal_gap", 8))
    available = right - left - gap * (len(tables) - 1)
    preferred_size = float(layout.get("fixed_text_height", 1.0))
    fixed_unit = float(layout.get("fixed_row_height", 2.5))
    if not math.isfinite(fixed_unit) or fixed_unit <= 0 or preferred_size > (fixed_unit - 0.6) * 0.75:
        raise DrawingError("固定行高不足以容纳统一字号和留白，请调整整体参数。")
    if not math.isfinite(preferred_size) or preferred_size <= 0:
        raise DrawingError("表格字号必须大于零。")
    prepared = []
    minimums = []
    weights = []
    for table in tables:
        groups = sheet.groups_for_category(table["category"])
        # Material is source data for a mark in front of the section, never an
        # extra printed column. This also applies when the mark feature is off.
        table["columns"] = [c for c in table["columns"] if c["field"] != "material"]
        for column in table["columns"]:
            if column["field"] == "joint":
                column["label"] = "継手"
            elif column["field"] == "remarks":
                column["label"] = "備考"
        if material_plan is not None:
            if not any(c["field"] == "section" for c in table["columns"]):
                raise DrawingError(f"{table['title']} 缺少断面列，不能插入材质记号。")
        table["row_height"] = table["header_height"] = table["min_row_height"] = fixed_unit
        unit, _ = _table_row_height(table, groups)
        table["header_height"] = unit
        table["row_height"] = unit
        size = preferred_size
        if size <= 0:
            raise DrawingError(f"{table['title']} 的单位行高不足。")
        for column in table["columns"]:
            values = [str(column["label"])]
            if material_plan is not None and column["field"] == "section":
                measured = [font.text_width_ex(str(column["label"]), size)]
                measured.extend(material_plan.column_width(row, size) for group in groups for row in group.rows)
                width = max(measured) + 1.2
                for group in groups:
                    for row in group.rows:
                        rule = material_plan.bindings[row.source_row].rule
                        if material_plan.factory.geometry(rule).height * size > unit - 0.6 + 1e-5:
                            raise DrawingError(f"{sheet.name} 第 {row.source_row} 行材质符号超过固定行高，请调整整表参数。")
            else:
                values.extend(row.value_for(column["field"]) for group in groups for row in group.rows)
                width = max(font.text_width_ex(_safe_drawing_text(value), size) for value in values) + 1.2
            # Small numerical tolerance is not additional user-visible padding.
            minimums.append(width + 0.01)
            weights.append(float(column["width"]))
        prepared.append((table, groups, size))
    needed = sum(minimums)
    if needed > available + 1e-6:
        raise DrawingError(
            f"工作表“{sheet.name}”按统一字号扩列后需要 {needed:.1f} mm，"
            f"当前可用列宽合计 {available:.1f} mm。请调整整表字号或表格间距；"
            "不会单独缩小文字、越出 A3 或拆成多页。"
        )
    # Widths are automatic: honour every string's required width first, then
    # distribute spare space according to the site's column proportions.
    # A longer value widens its column, not changes that cell's font.
    total_weight = sum(weights)
    budget = min(available, max(total_weight, needed))
    spare = budget - needed
    index = 0
    x = left
    for table_index, (table, groups, size) in enumerate(prepared):
        table["x"] = x
        for column in table["columns"]:
            column["width"] = minimums[index] + spare * weights[index] / total_weight
            index += 1
        table_width = sum(c["width"] for c in table["columns"])
        note = template.data.get("note_area")
        if isinstance(note, dict) and x < float(note["x"]) + float(note["width"]) and x + table_width > float(note["x"]):
            limit = table["top"] - (float(note["y"]) + float(note["height"]) + 6)
            table["max_height"] = min(table["max_height"], limit)
            unit, _ = _table_row_height(table, groups)
            table["header_height"] = table["row_height"] = unit
            prepared[table_index] = (table, groups, size)
        x += table_width + gap
    return prepared


def _table_font_caps(
    table: dict[str, Any],
    groups: list[MemberGroup],
) -> list[float]:
    """Return the largest fitting sizes for all non-empty table cells."""
    row_height, _total_rows = _table_row_height(table, groups)
    columns = table["columns"]
    widths = [float(column["width"]) for column in columns]
    header_height = float(table["header_height"])
    header_caps = [
        _text_size_cap(column["label"], width, header_height, 2.4)
        for column, width in zip(columns, widths)
        if _safe_drawing_text(column.get("label", ""))
    ]
    body_caps: list[float] = []
    for group in groups:
        group_height = max(row_height, len(group.rows) * row_height)
        if _safe_drawing_text(group.member_name):
            body_caps.append(
                _text_size_cap(group.member_name, widths[0], group_height, 2.4)
            )
        for section_row in group.rows:
            for column, width in zip(columns[1:], widths[1:]):
                value = section_row.value_for(str(column["field"]))
                if _safe_drawing_text(value):
                    body_caps.append(_text_size_cap(value, width, row_height, 2.4))
    return [*header_caps, *body_caps]


def draw_sheet(
    adapter: DrawingAdapter,
    sheet: SheetData,
    template: SiteTemplate,
    metadata: DrawingMetadata,
    material_plan=None,
) -> int:
    _draw_fixed_frame(adapter, template, sheet, metadata)
    rendered_rows = 0
    used_categories: set[str] = set()
    table_groups = _prepare_tables(sheet, template, material_plan)
    if material_plan is not None:
        material_plan.cells.clear()
    for table, groups, table_font_size in table_groups:
        category = template.canonical_category(str(table["category"]))
        groups = sheet.groups_for_category(category)
        used_categories.add(category)
    adapter.strict_table_text = True
    try:
        for table, groups, table_font_size in table_groups:
            rendered_rows += _draw_table(adapter, table, groups, table_font_size, material_plan)
    finally:
        adapter.strict_table_text = False
    source_categories = {template.canonical_category(row.category) for row in sheet.source_rows}
    missing_categories = source_categories - used_categories
    if missing_categories:
        raise DrawingError(f"以下种类没有绘图区域：{', '.join(sorted(missing_categories))}")
    if rendered_rows != sheet.source_row_count:
        raise DrawingError(
            f"工作表“{sheet.name}”输出行数对账失败：读取 {sheet.source_row_count} 行，绘制 {rendered_rows} 行。"
        )
    return rendered_rows


def render_pdf(
    output_path: Path,
    sheet: SheetData,
    template: SiteTemplate,
    metadata: DrawingMetadata,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if template.material_symbols_enabled:
        with tempfile.TemporaryDirectory(prefix="._material_pdf_", dir=output_path.parent) as directory:
            source = Path(directory) / "sheet.dxf"
            count = render_dxf(source, sheet, template, metadata)
            render_cad_pdf(source, output_path, template)
            return count
    adapter = PdfAdapter(
        output_path,
        float(template.paper["width"]),
        float(template.paper["height"]),
    )
    count = draw_sheet(adapter, sheet, template, metadata)
    adapter.save()
    return count


def render_dxf(
    output_path: Path,
    sheet: SheetData,
    template: SiteTemplate,
    metadata: DrawingMetadata,
    material_plan=None,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if material_plan is None and template.material_symbols_enabled:
        from .material_drawing import prepare_material_job
        from .models import WorkbookData
        _library, plans = prepare_material_job(WorkbookData("", [sheet]), _cjk_font_filename())
        material_plan = plans[sheet.name]
    base_dxf = prepare_frame_dxf(template.frame_dwg_path)
    adapter = CadAdapter(
        output_path,
        float(template.paper["width"]),
        float(template.paper["height"]),
        base_dxf_path=base_dxf,
    )
    count = draw_sheet(adapter, sheet, template, metadata, material_plan)
    adapter.save()
    return count


def render_cad_pdf(
    dxf_path: Path,
    output_path: Path,
    template: SiteTemplate,
) -> None:
    from .text_pdf import TextPdfBook
    ezdxf = _load_ezdxf()
    try:
        document = ezdxf.readfile(dxf_path)
        width = float(template.paper["width"])
        height = float(template.paper["height"])
        book = TextPdfBook(output_path, width, height)
        book.add_cad_page(document, output_path.stem)
        book.save()
    except Exception as exc:
        raise DrawingError(f"现场 DWG 图框的 PDF 渲染失败：{exc}") from exc


def _write_dwg_compatibility_dxf(source_path: Path, target_path: Path) -> None:
    from .cad_integrity import write_compatibility_dxf
    try:
        write_compatibility_dxf(source_path, target_path)
    except Exception as exc:
        raise DrawingError(f"创建 DWG 兼容数据失败：{exc}") from exc


def convert_dxf_to_dwg(dxf_path: Path, dwg_path: Path) -> list[str]:
    from .cad_integrity import validate_roundtrip, validate_dwg_resources
    from .app_logging import get_logger

    converter_root = _converter_root()
    converter = converter_root / "dwgwrite.exe"
    if not converter.exists():
        raise DrawingError("找不到 DWG 转换组件；DXF 和 PDF 仍可正常生成。")
    dwg_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="._dwg_check_", dir=dwg_path.parent) as directory:
        staging = Path(directory)
        compatibility_path = staging / "input.dxf"
        candidate = staging / "candidate.dwg"
        roundtrip = staging / "roundtrip.dxf"
        raw_objects = staging / "objects.json"
        _write_dwg_compatibility_dxf(dxf_path, compatibility_path)
        command = [
            str(converter),
            "-y",
            "--as",
            "r2000",
            "-I",
            "DXF",
            "-o",
            str(candidate),
            str(compatibility_path),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        details = (result.stdout + "\n" + result.stderr).strip()
        get_logger().info("DWG 编码检查：%s", details)
        if (result.returncode or not candidate.exists() or candidate.stat().st_size < 1000
                or re.search(r"\b(error|warning)\b", details, re.I)):
            raise DrawingError(f"DWG 转换未通过检查，已保留 DXF：{details[-1200:]}")
        readback = subprocess.run(
            [str(converter_root / "dwg2dxf.exe"), "-y", "-o", str(roundtrip), str(candidate)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        read_messages = readback.stdout + "\n" + readback.stderr
        get_logger().info("DWG 回读检查：%s", read_messages)
        if readback.returncode or re.search(r"\b(error|warning)\b", read_messages, re.I):
            raise DrawingError("DWG 回读失败，未交付可能需要修复的 DWG：" + read_messages[-1000:])
        try:
            checked = validate_roundtrip(compatibility_path, roundtrip)
            raw = subprocess.run(
                [str(converter_root / "dwgread.exe"), "-O", "JSON", "-o", str(raw_objects), str(candidate)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if raw.returncode or re.search(r"\b(error|warning)\b", raw.stdout + raw.stderr, re.I):
                raise ValueError("DWG 对象引用检查失败：" + (raw.stdout + raw.stderr)[-1000:])
            checked.update(validate_dwg_resources(raw_objects))
        except Exception as exc:
            raise DrawingError(f"DWG 完整性检查失败，已保留 DXF：{exc}") from exc
        get_logger().info("DWG 文字/对象回读对账通过：%s", checked)
        candidate.replace(dwg_path)
    return []


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized_text(value))
    cleaned = cleaned.rstrip(". ")
    return cleaned or "图纸"
