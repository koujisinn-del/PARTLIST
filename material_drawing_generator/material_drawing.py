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
from ezdxf.fonts import fonts
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


class SymbolFactory:
    """Cache unit geometry once; each drawing imports one block per appearance."""

    def __init__(self, font_filename: str):
        self.font_filename = font_filename
        self.font = fonts.make_font(font_filename, 1.0)
        self._geometry = {}
        self._text_sizes = {}
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

    def geometry(self, rule: SymbolRule) -> SymbolGeometry:
        name = rule.block_name
        if name in self._geometry:
            return self._geometry[name]
        if rule.is_unmarked:
            result = SymbolGeometry(0, 0, (), (), ())
            self._geometry[name] = result
            return result
        texts, lines, circles = [], [], []
        cursor = 0.0
        if rule.prefix:
            lo, hi = self._ink(rule.prefix)
            texts.append((rule.prefix, -lo.x, -(lo.y + hi.y) / 2))
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
        for a, b in zip(points, points[1:] + points[:1]):
            lines.append((*a, *b))
        if rule.inner:
            lo, hi = self._ink(rule.inner)
            # The polygon's incircle, rather than its bounding-box center, is
            # the safe text anchor in asymmetric shapes (e.g. triangles).
            cy = -(ylo + yhi) / 2 if rule.shape not in {"circle", "square"} else 0
            texts.append((rule.inner, cx - (lo.x + hi.x) / 2, cy - (lo.y + hi.y) / 2))
        width = cursor + 2 * rx
        result = SymbolGeometry(width, 2 * ry, tuple(texts), tuple(lines), tuple(circles))
        self._geometry[name] = result
        return result

    def combined_width(self, rule: SymbolRule, section: str, size: float) -> float:
        symbol_width = self.geometry(rule).width * size
        return symbol_width + (0.4 if symbol_width else 0) + self.font.text_width_ex(section, size)

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
            block.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "0"})
        for x, y, radius in geom.circles:
            block.add_circle((x, y), radius, dxfattribs={"layer": "0"})
        installed.add(name)
        return name

    def draw_cell(self, adapter, plan: SheetMaterials, row, x1, y1, x2, y2, size):
        from ezdxf.enums import TextEntityAlignment
        binding = plan.bindings[row.source_row]
        rule = binding.rule
        geom = self.geometry(rule)
        width = self.combined_width(rule, binding.section, size)
        if width > x2 - x1 - 1.2 + 1e-5 or geom.height * size > y2 - y1 - 0.6 + 1e-5:
            raise ValueError(f"{plan.name} 第 {row.source_row} 行：材质符号与断面无法放入单元格，请扩大整体行高或列宽。")
        left = (x1 + x2 - width) / 2
        center_y = (y1 + y2) / 2
        insert = None
        if not rule.is_unmarked:
            block_name = self.ensure_block(adapter.doc, rule, adapter.text_style)
            insert = adapter.modelspace.add_blockref(block_name, (left, center_y), dxfattribs={
                "xscale": size, "yscale": size, "zscale": size, "layer": "TEXT",
            })
        text_x = left + geom.width * size + (0.4 if not rule.is_unmarked else 0)
        entity = adapter.modelspace.add_text(binding.section, dxfattribs={
            "style": adapter.text_style, "height": size, "layer": "TEXT",
        })
        entity.set_placement((text_x, center_y), align=TextEntityAlignment.MIDDLE_LEFT)
        limits = bbox.extents([entity])
        offset = Vec3(text_x - limits.extmin.x, center_y - limits.center.y, 0)
        entity.dxf.insert += offset
        entity.dxf.align_point += offset
        # Plain baseline TEXT avoids the conversion issue of coincident anchors.
        from ezdxf.tools.text import TextLine, unified_alignment
        line = TextLine(binding.section, fonts.make_font(self.font_filename, size))
        h, v = unified_alignment(entity)
        entity.dxf.insert = line.baseline_vertices(entity.dxf.align_point, h, v)[0]
        entity.dxf.halign = entity.dxf.valign = 0
        entity.dxf.discard("align_point")
        entities = [entity] + ([insert] if insert is not None else [])
        limits = bbox.extents(entities)
        if (limits.extmin.x < x1 + 0.6 - 1e-5 or limits.extmax.x > x2 - 0.6 + 1e-5
                or limits.extmin.y < y1 + 0.3 - 1e-5 or limits.extmax.y > y2 - 0.3 + 1e-5):
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
            elif kind == "TEXT":
                geom = (entity.dxf.text, point(entity.dxf.insert), round(entity.dxf.height, 5))
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
