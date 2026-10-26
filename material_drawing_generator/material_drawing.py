"""One common material rule set, reusable CAD blocks and per-cell checks.

No inference of steel grades from dimensions, legacy marks, or plate thickness.
Only the material column selects a rule. Geometry is shared by CAD and PDF.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math
import weakref

import ezdxf
from ezdxf import bbox
from ezdxf.addons import text2path
from ezdxf.fonts import fonts
from ezdxf.npshapes import NumpyPath2d, to_matplotlib_path
from ezdxf.math import Vec3

from .materials import MaterialLibrary, SymbolRule


@dataclass(frozen=True)
class MaterialBinding:
    source_row: int
    original_material: str
    section: str
    rule: SymbolRule


@dataclass
class SheetMaterials:
    name: str
    bindings: dict[int, MaterialBinding]
    library_hash: str
    factory: "SymbolFactory"
    cells: list[dict] = field(default_factory=list)

    def column_width(self, row, size: float) -> float:
        item = self.bindings[row.source_row]
        return self.factory.combined_width(item.rule, item.section, size)


def place_text_middle_left_baseline(entity, x: float, center_y: float,
                                    font_filename: str, size: float) -> float:
    """Apply the production section-text placement and return its baseline y."""
    from ezdxf.enums import TextEntityAlignment
    from ezdxf.tools.text import TextLine, unified_alignment

    text = entity.dxf.text
    entity.set_placement((x, center_y), align=TextEntityAlignment.MIDDLE_LEFT)
    limits = bbox.extents([entity])
    offset = Vec3(x - limits.extmin.x, center_y - limits.center.y, 0)
    entity.dxf.insert += offset
    entity.dxf.align_point += offset
    line = TextLine(text, fonts.make_font(font_filename, size))
    horizontal, vertical = unified_alignment(entity)
    entity.dxf.insert = line.baseline_vertices(entity.dxf.align_point, horizontal, vertical)[0]
    entity.dxf.halign = entity.dxf.valign = 0
    entity.dxf.discard("align_point")
    return float(entity.dxf.insert.y)


def prepare_material_job(workbook, font_filename: str) -> tuple[MaterialLibrary, dict[str, SheetMaterials]]:
    """Validate the entire job before exporting files or marking the workbook."""
    library = MaterialLibrary.load()
    factory = SymbolFactory(font_filename)
    plans = {}
    errors = []
    for sheet in workbook.sheets:
        bindings = {}
        for row in sheet.source_rows:
            try:
                if any(str(row.extra.get(key, "")).strip() for key in ("web_material", "top_flange_material", "bottom_flange_material")):
                    raise ValueError("已填写 Web/Flange 分部材质，混合材质 BH 表示尚待确认，本次不会按普通单一材质输出。")
                rule = library.resolve(row.material)
                section = row.section
                # Section notation is user-authored display text, not a schema.
                # Preserve it verbatim; only rendering feasibility is checked.
                if any(ch in section for ch in "\n\r"):
                    raise ValueError("断面含换行；请使用单行断面尺寸。")
                from .text_pdf import pdf_font, assert_glyphs
                _, text_font, *_ = pdf_font(font_filename)
                for field_name in ("member_name", "position", "section", "joint", "remarks"):
                    assert_glyphs(row.value_for(field_name), text_font)
                bindings[row.source_row] = MaterialBinding(row.source_row, row.material, section, rule)
            except ValueError as exc:
                errors.append(f"{sheet.name} 第 {row.source_row} 行（{row.member_name}）：{exc}")
        plans[sheet.name] = SheetMaterials(sheet.name, bindings, library.fingerprint, factory)
    if errors:
        raise ValueError("材质检查未通过：\n" + "\n".join(errors[:20]) + (f"\n另有 {len(errors) - 20} 项。" if len(errors) > 20 else ""))
    return library, plans


@dataclass(frozen=True)
class SymbolGeometry:
    width: float
    height: float
    texts: tuple[tuple[str, float, float], ...]
    lines: tuple[tuple[float, float, float, float], ...]
    circles: tuple[tuple[float, float, float], ...]
    primitives: tuple[tuple, ...] = ()


class SymbolFactory:
    """Cache unit geometry once; each drawing imports one block per appearance."""

    def __init__(self, font_filename: str):
        self.font_filename = font_filename
        self.font = fonts.make_font(font_filename, 1.0)
        self._geometry = {}
        self._text_sizes = {}
        self._text_ink_bounds = {}
        self._installed = weakref.WeakKeyDictionary()
        self._measure_doc = ezdxf.new("R2007")
        self._measure_doc.styles.new("MEASURE", dxfattribs={"font": font_filename})

    def _ink(self, text: str):
        if text not in self._text_sizes:
            entity = self._measure_doc.modelspace().add_text(text, dxfattribs={"style": "MEASURE", "height": 1})
            extents = bbox.extents([entity])
            self._text_sizes[text] = (extents.extmin, extents.extmax)
            self._measure_doc.modelspace().delete_entity(entity)
        return self._text_sizes[text]

    def _ink_outline(self, text: str):
        """Measure visible glyph contours at unit height, not font metric boxes."""
        if text not in self._text_ink_bounds:
            entity = self._measure_doc.modelspace().add_text(
                text, dxfattribs={"style": "MEASURE", "height": 1}
            )
            try:
                path = text2path.make_path_from_entity(entity)
                paths = [NumpyPath2d(part) for part in path.sub_paths()]
                if not paths:
                    raise ValueError(f"文字“{text}”没有可测量的字形轮廓。")
                bounds = to_matplotlib_path(paths, detect_holes=False).get_extents()
                values = (bounds.x0, bounds.y0, bounds.x1, bounds.y1)
                if not all(math.isfinite(float(value)) for value in values):
                    raise ValueError(f"文字“{text}”的字形轮廓范围无效。")
                self._text_ink_bounds[text] = tuple(float(value) for value in values)
            finally:
                self._measure_doc.modelspace().delete_entity(entity)
        return self._text_ink_bounds[text]

    @staticmethod
    def _nearest_polygon_text_center_y(points, ink_bounds, center_x, target_y, margin=0.05):
        """Find the closest vertical ink-box center that clears every polygon edge."""
        area = sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
        )
        if abs(area) <= 1e-12:
            raise ValueError("材质符号外框面积无效，无法定位框内文字。")
        orientation = 1 if area > 0 else -1
        ink_x0, ink_y0, ink_x1, ink_y1 = ink_bounds
        ink_center_x = (ink_x0 + ink_x1) / 2
        ink_center_y = (ink_y0 + ink_y1) / 2
        x_offsets = (ink_x0 - ink_center_x, ink_x1 - ink_center_x)
        y_offsets = (ink_y0 - ink_center_y, ink_y1 - ink_center_y)
        lower, upper = -math.inf, math.inf
        for start, end in zip(points, points[1:] + points[:1]):
            edge_x, edge_y = end[0] - start[0], end[1] - start[1]
            length = math.hypot(edge_x, edge_y)
            if length <= 1e-12:
                raise ValueError("材质符号外框含零长度边，无法定位框内文字。")
            inward_x = orientation * -edge_y / length
            inward_y = orientation * edge_x / length
            for dx in x_offsets:
                for dy in y_offsets:
                    constant = inward_x * (center_x + dx - start[0]) - margin
                    if abs(inward_y) <= 1e-12:
                        if constant < -1e-9:
                            raise ValueError("材质符号文字宽度超过外框，无法保留必要内边距。")
                        continue
                    bound = start[1] - dy - constant / inward_y
                    if inward_y > 0:
                        lower = max(lower, bound)
                    else:
                        upper = min(upper, bound)
        if lower > upper + 1e-9:
            raise ValueError("材质符号文字高度超过外框，无法保留必要内边距。")
        return min(max(target_y, lower), upper)

    @staticmethod
    def _translate_primitive_y(item, offset):
        kind, *values = item
        if kind == "LINE":
            values[1] += offset
            values[3] += offset
        elif kind in {"CIRCLE", "ARC"}:
            values[1] += offset
        elif kind == "TEXT":
            values[2] += offset
        return (kind, *values)

    def geometry(self, rule: SymbolRule) -> SymbolGeometry:
        name = rule.block_name
        if name in self._geometry:
            return self._geometry[name]
        if rule.is_unmarked:
            result = SymbolGeometry(0, 0, (), (), ())
            self._geometry[name] = result
            return result
        if rule.geometry:
            self._validate_imported_geometry(rule)
            x1, y1, x2, y2 = rule.geometry_bounds
            primitives = rule.geometry
            if rule.baseline_text_index is not None:
                texts = [item for item in primitives if item[0] == "TEXT"]
                offset = -texts[rule.baseline_text_index][3]
                primitives = tuple(self._translate_primitive_y(item, offset) for item in primitives)
                y1, y2 = y1 + offset, y2 + offset
            result = SymbolGeometry(x2 - x1, y2 - y1, (), (), (), primitives)
            self._geometry[name] = result
            return result
        texts, lines, circles = [], [], []
        cursor = 0.0
        if rule.prefix:
            lo, hi = self._ink(rule.prefix)
            texts.append((rule.prefix, -lo.x, 0.0))
            cursor = hi.x - lo.x + 0.20
        inner_w = inner_h = 0
        if rule.inner:
            lo, hi = self._ink(rule.inner)
            inner_w, inner_h = hi.x - lo.x, hi.y - lo.y
        # Enclosing shape accommodates the text rectangle plus whitespace.
        # Polygons need a larger circumradius; their inradius must contain it.
        half = max(0.80, inner_w / 2 + 0.15, inner_h / 2 + 0.15)
        if rule.shape == "square":
            rx = max(0.8, inner_w / 2 + 0.15)
            ry = max(0.8, inner_h / 2 + 0.15)
            cx = cursor + rx
            points = [(cx - rx, -ry), (cx + rx, -ry), (cx + rx, ry), (cx - rx, ry)]
        elif rule.shape == "circle":
            radius = max(0.85, math.hypot(inner_w / 2 + 0.1, inner_h / 2 + 0.1))
            cx = cursor + radius
            circles.append((cx, 0, radius))
            rx = ry = radius
            points = []
        else:
            sides = {"triangle": 3, "diamond": 4, "pentagon": 5, "hexagon": 6, "star": 5}[rule.shape]
            inner_radius = max(half, math.hypot(inner_w / 2, inner_h / 2) + 0.15)
            radius = inner_radius / (0.48 if rule.shape == "star" else math.cos(math.pi / sides))
            count = 10 if rule.shape == "star" else sides
            points = []
            for i in range(count):
                r = radius * (0.48 if rule.shape == "star" and i % 2 else 1)
                angle = math.pi / 2 + i * 2 * math.pi / count
                points.append((r * math.cos(angle), r * math.sin(angle)))
            # Center the complete shape's bounding box around y=0.
            xlo, xhi = min(x for x, _ in points), max(x for x, _ in points)
            ylo, yhi = min(y for _, y in points), max(y for _, y in points)
            cx = cursor - xlo
            points = [(x + cx, y - (ylo + yhi) / 2) for x, y in points]
            rx, ry = (xhi - xlo) / 2, (yhi - ylo) / 2
        if rule.inner:
            ink_x0, ink_y0, ink_x1, ink_y1 = self._ink_outline(rule.inner)
            if points:
                frame_center_x = (min(x for x, _ in points) + max(x for x, _ in points)) / 2
            else:
                frame_center_x = cx
            ink_center_y = (ink_y0 + ink_y1) / 2
            frame_center_y = ink_center_y
            if rule.shape == "triangle":
                safe_ink_center_y = self._nearest_polygon_text_center_y(
                    points, (ink_x0, ink_y0, ink_x1, ink_y1), frame_center_x, 0.0
                )
                frame_center_y = ink_center_y - safe_ink_center_y
            texts.append((rule.inner, frame_center_x - (ink_x0 + ink_x1) / 2, 0.0))
        else:
            # With no enclosed letter, align the frame to a unit-cap text line.
            frame_center_y = sum(self._ink_outline("N")[1::2]) / 2
            if rule.prefix:
                texts[0] = (rule.prefix, texts[0][1], 0.0)
        if points:
            points = [(x, y + frame_center_y) for x, y in points]
            for a, b in zip(points, points[1:] + points[:1]):
                lines.append((*a, *b))
        else:
            circles = [(x, y + frame_center_y, radius) for x, y, radius in circles]
        width = cursor + 2 * rx
        result = SymbolGeometry(width, 2 * ry, tuple(texts), tuple(lines), tuple(circles))
        self._geometry[name] = result
        return result

    def combined_width(self, rule: SymbolRule, section: str, size: float) -> float:
        symbol_width = self.geometry(rule).width * size
        return symbol_width + (0.4 if symbol_width else 0) + self.font.text_width_ex(section, size)

    @staticmethod
    def _add_imported_primitive(block, item, style: str) -> None:
        kind, *values = item
        if kind == "LINE":
            block.add_line(values[:2], values[2:4], dxfattribs={"layer": "0", "lineweight": 13})
        elif kind == "CIRCLE":
            block.add_circle(values[:2], values[2], dxfattribs={"layer": "0", "lineweight": 13})
        elif kind == "ARC":
            block.add_arc(values[:2], values[2], values[3], values[4], dxfattribs={"layer": "0", "lineweight": 13})
        elif kind == "TEXT":
            text, x, y, height, rotation, width_factor = values
            block.add_text(text, dxfattribs={"insert": (x, y), "height": height, "rotation": rotation,
                "width": width_factor, "style": style, "layer": "0"})
        else:
            raise ValueError(f"材质规则含不支持图元：{kind}")

    def _validate_imported_geometry(self, rule: SymbolRule) -> None:
        """Measure embedded primitives with the active font and verify saved bounds."""
        doc = ezdxf.new("R2007")
        doc.styles.new("MDG_IMPORT_CHECK", dxfattribs={"font": self.font_filename})
        block = doc.blocks.new("MDG_IMPORT_CHECK")
        for item in rule.geometry:
            self._add_imported_primitive(block, item, "MDG_IMPORT_CHECK")
        extents = bbox.extents(block)
        actual = (extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y)
        expected = rule.geometry_bounds
        if not all(math.isfinite(float(value)) for value in actual):
            raise ValueError(f"材质“{rule.material}”的导入图形实际边界无效。")
        scale = max(1.0, *(abs(float(value)) for value in (*actual, *expected)))
        tolerance = 1e-5 * scale
        if (abs(expected[0]) > tolerance or abs(expected[1] + expected[3]) > tolerance
                or any(abs(float(a) - float(e)) > tolerance for a, e in zip(actual, expected))):
            raise ValueError(
                f"材质“{rule.material}”保存的边界与实际图元/当前字体测量结果不一致；"
                "为避免出图越框或裁切，请重新导入并预览该符号。"
            )

    def ensure_block(self, document, rule: SymbolRule, style: str) -> str:
        name = rule.block_name
        installed = self._installed.setdefault(document, set())
        if name in document.blocks:
            if name not in installed:
                raise ValueError(f"图框含同名材质块 {name}，请移除旧材质块后重新生成，避免误用。")
            return name
        block = document.blocks.new(name, base_point=(0, 0, 0))
        geom = self.geometry(rule)
        for text, x, y in geom.texts:
            block.add_text(text, dxfattribs={"insert": (x, y), "height": 1, "style": style, "layer": "0"})
        for x1, y1, x2, y2 in geom.lines:
            block.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "0", "lineweight": 13})
        for x, y, radius in geom.circles:
            block.add_circle((x, y), radius, dxfattribs={"layer": "0", "lineweight": 13})
        for item in geom.primitives:
            self._add_imported_primitive(block, item, style)
        installed.add(name)
        return name

    def draw_cell(self, adapter, plan: SheetMaterials, row, x1, y1, x2, y2, size, vertical_padding: float = 0.3):
        binding = plan.bindings[row.source_row]
        rule = binding.rule
        geom = self.geometry(rule)
        width = self.combined_width(rule, binding.section, size)
        vertical_padding = float(vertical_padding)
        if vertical_padding < 0 or not math.isfinite(vertical_padding):
            raise ValueError("材质符号单元格上下留白必须是非负有限数值。")
        vertical_clearance = vertical_padding * 2
        if width > x2 - x1 - 1.2 + 1e-5 or geom.height * size > y2 - y1 - vertical_clearance + 1e-5:
            raise ValueError(f"{plan.name} 第 {row.source_row} 行：材质符号与断面无法放入单元格，请扩大整体行高或列宽。")
        left = (x1 + x2 - width) / 2
        center_y = (y1 + y2) / 2
        text_x = left + geom.width * size + (0.4 if not rule.is_unmarked else 0)
        entity = adapter.modelspace.add_text(binding.section, dxfattribs={
            "style": adapter.text_style, "height": size, "layer": "TEXT",
        })
        section_baseline_y = place_text_middle_left_baseline(
            entity, text_x, center_y, self.font_filename, size
        )
        insert = None
        if not rule.is_unmarked:
            block_name = self.ensure_block(adapter.doc, rule, adapter.text_style)
            baseline_anchored = not rule.geometry or rule.baseline_text_index is not None
            insert_y = section_baseline_y if baseline_anchored else center_y
            insert = adapter.modelspace.add_blockref(block_name, (left, insert_y), dxfattribs={
                "xscale": size, "yscale": size, "zscale": size, "layer": "TEXT",
            })
        entities = [entity] + ([insert] if insert is not None else [])
        limits = bbox.extents(entities)
        available_y0, available_y1 = y1 + vertical_padding, y2 - vertical_padding
        if limits.size.y > available_y1 - available_y0 + 1e-5:
            raise ValueError(
                f"{plan.name} 第 {row.source_row} 行：材质符号与断面组合需要垂直空间 "
                f"{limits.size.y:.3f}，当前可用 {available_y1 - available_y0:.3f}，未压缩字号或外框。"
            )
        vertical_offset = center_y - limits.center.y
        if entity is not None:
            entity.translate(0, vertical_offset, 0)
        if insert is not None:
            insert.translate(0, vertical_offset, 0)
        limits = bbox.extents(entities)
        if (limits.extmin.x < x1 + 0.6 - 1e-5 or limits.extmax.x > x2 - 0.6 + 1e-5
                or limits.extmin.y < available_y0 - 1e-5 or limits.extmax.y > available_y1 + 1e-5):
            raise ValueError(f"{plan.name} 第 {row.source_row} 行：材质符号或断面越出单元格，未交付。")
        plan.cells.append({
            "excel_row": row.source_row, "member": row.member_name, "material_input": binding.original_material,
            "material": rule.material, "symbol": rule.to_record(), "block": "" if rule.is_unmarked else rule.block_name,
            "section": binding.section, "box": [x1, y1, x2, y2], "text_handle": entity.dxf.handle,
            "block_handle": insert.dxf.handle if insert is not None else "",
        })


def verify_material_cells(document, plan: SheetMaterials) -> dict:
    """Read-back check of every material occurrence, including explicit no-mark."""
    if len(plan.cells) != len(plan.bindings):
        raise ValueError(f"{plan.name} 的材质输出数量不一致。")
    expected = Counter()
    for cell in plan.cells:
        text = document.entitydb.get(cell["text_handle"])
        if text is None or text.dxftype() != "TEXT" or text.dxf.text != cell["section"]:
            raise ValueError(f"{plan.name} 第 {cell['excel_row']} 行断面回读不一致。")
        entities = [text]
        if cell["block"]:
            ref = document.entitydb.get(cell["block_handle"])
            if ref is None or ref.dxftype() != "INSERT" or ref.dxf.name != cell["block"]:
                raise ValueError(f"{plan.name} 第 {cell['excel_row']} 行材质块回读不一致。")
            expected[cell["block"]] += 1
            entities.append(ref)
        bounds = bbox.extents(entities)
        x1, y1, x2, y2 = cell["box"]
        if bounds.extmin.x < x1 - 1e-5 or bounds.extmax.x > x2 + 1e-5 or bounds.extmin.y < y1 - 1e-5 or bounds.extmax.y > y2 + 1e-5:
            raise ValueError(f"{plan.name} 第 {cell['excel_row']} 行回读后越框。")
    actual = Counter(e.dxf.name for e in document.modelspace().query("INSERT") if e.dxf.name.startswith("MDG_MAT_"))
    if actual != expected:
        raise ValueError(f"{plan.name} 的材质块存在遗漏或多余引用。")
    return {"library_sha256": plan.library_hash, "verified_rows": len(plan.cells), "insertions": sum(actual.values()), "blocks": len(actual), "cells": plan.cells}


def material_geometry_signature(document):
    """Snapshot symbol references and exact editable shapes across DWG conversion."""
    def point(value):
        return tuple(round(float(v), 5) for v in value)
    references = []
    names = set()
    for entity in document.modelspace().query("INSERT"):
        if entity.dxf.name.startswith("MDG_MAT_"):
            names.add(entity.dxf.name)
            references.append((entity.dxf.name, point(entity.dxf.insert), point((entity.dxf.xscale, entity.dxf.yscale, entity.dxf.zscale)), round(entity.dxf.rotation, 5), point(entity.dxf.extrusion), entity.dxf.layer, entity.dxf.color, entity.dxf.invisible))
    definitions = []
    for name in sorted(names):
        block = document.blocks.get(name)
        entries = []
        for entity in block:
            kind = entity.dxftype()
            if kind == "LINE":
                geom = (point(entity.dxf.start), point(entity.dxf.end))
            elif kind == "CIRCLE":
                geom = (point(entity.dxf.center), round(entity.dxf.radius, 5))
            elif kind == "ARC":
                geom = (point(entity.dxf.center), round(entity.dxf.radius, 5), round(entity.dxf.start_angle, 5), round(entity.dxf.end_angle, 5))
            elif kind == "TEXT":
                geom = (entity.dxf.text, point(entity.dxf.insert), round(entity.dxf.height, 5), round(entity.dxf.rotation, 5), round(entity.dxf.width, 5), round(entity.dxf.oblique, 5), entity.dxf.style, entity.dxf.halign, entity.dxf.valign)
            else:
                raise ValueError(f"材质块 {name} 中存在未支持图元 {kind}。")
            entries.append((kind, geom, entity.dxf.layer, entity.dxf.color, entity.dxf.invisible))
        definitions.append((name, point(block.block.dxf.base_point), tuple(sorted(entries))))
    return tuple(sorted(references)), tuple(definitions)


def validate_material_geometry(expected, actual):
    before, after = material_geometry_signature(expected), material_geometry_signature(actual)
    if before != after:
        raise ValueError("材质块的插入位置、比例、外框或字母在 DWG 转换后发生变化。")
    return {"material_insertions": len(after[0]), "material_blocks": len(after[1]), "material_geometry_sha256": hashlib.sha256(json.dumps(after).encode()).hexdigest()}
