"""Strict, self-contained vector-symbol import and custom-library persistence."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import tempfile

import ezdxf
from ezdxf import bbox
from ezdxf.fonts import fonts

from .materials import MaterialLibrary, SymbolRule, material_lookup_key
from .runtime_paths import resource_root

MAX_ENTITIES = 5000
STANDARD_CIRCLE_DIAMETER = 1.7
_ALLOWED = {"LINE", "CIRCLE", "ARC", "TEXT", "LWPOLYLINE", "POLYLINE"}


def _flat(entity):
    kind = entity.dxftype()
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        try:
            return list(entity.virtual_entities())
        except Exception as exc:
            raise ValueError(f"无法完整展开 {kind}（可能含不支持的曲线）：{exc}") from exc
    return [entity]


def _validate_entity_appearance(entity, doc):
    """Reject CAD display attributes that the normalized black symbol cannot retain."""
    layer_name = str(entity.dxf.get("layer", "0"))
    try:
        layer = doc.layers.get(layer_name)
    except Exception as exc:
        raise ValueError(f"图元引用了无法读取的图层“{layer_name}”。") from exc
    flags = int(layer.dxf.get("flags", 0))
    if flags & 1:
        raise ValueError(f"图元位于冻结图层“{layer_name}”，为避免导入后改变可见状态，已拒绝。")
    if int(layer.dxf.get("color", 7)) < 0:
        raise ValueError(f"图元位于关闭图层“{layer_name}”，为避免改变可见状态，已拒绝。")
    if int(layer.dxf.get("plot", 1)) == 0:
        raise ValueError(f"图元位于不可打印图层“{layer_name}”，为避免改变图层语义，已拒绝。")
    entity_color = int(entity.dxf.get("color", 256))
    if entity_color < 0:
        raise ValueError(f"图元位于关闭状态（负颜色）的实体属性中，已拒绝。")
    entity_linetype = str(entity.dxf.get("linetype", "BYLAYER")).upper()
    if entity_linetype == "BYBLOCK":
        raise ValueError("暂不支持 BYBLOCK 线型；请改用连续线型后导入。")
    if entity_linetype == "BYLAYER":
        entity_linetype = str(layer.dxf.get("linetype", "CONTINUOUS")).upper()
    if entity_linetype != "CONTINUOUS":
        raise ValueError(f"图元使用非连续线型“{entity_linetype}”，归一化后无法保留线型，已拒绝。")


def inspect_drawing(path: Path, font_filename: str) -> tuple[list[dict], list[tuple[int, float, float, float]]]:
    """Read supported 2D entities; reject any unsupported content instead of dropping it."""
    path = Path(path)
    source = path
    if path.suffix.lower() == ".dwg":
        # LibreDWG may create an adjacent DXF; it only ever sees a temporary copy.
        from .drawing import prepare_frame_dxf
        with tempfile.TemporaryDirectory(prefix="partlist-symbol-") as folder:
            copied = Path(folder) / path.name
            shutil.copy2(path, copied)
            try:
                source = prepare_frame_dxf(copied)
            except Exception as exc:
                raise ValueError(f"DWG 符号文件读取失败：{exc}") from exc
            if source is None:
                raise ValueError("未生成 DWG 临时转换文件。")
            return _inspect_dxf(Path(source), font_filename)
    if path.suffix.lower() != ".dxf":
        raise ValueError("请选择 DWG 或 DXF 文件。")
    return _inspect_dxf(source, font_filename)


def _inspect_dxf(path: Path, font_filename: str):
    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:
        raise ValueError(f"无法读取 CAD 文件：{exc}") from exc
    entities = []
    base_circles = []
    unsupported = []
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind not in _ALLOWED:
            unsupported.append(kind)
            continue
        _validate_entity_appearance(entity, doc)
        if kind == "LWPOLYLINE":
            if entity.dxf.get("elevation", 0) != 0 or entity.dxf.extrusion != (0, 0, 1):
                raise ValueError("仅支持 XY 平面内的 LWPOLYLINE。")
            if entity.dxf.get("const_width", 0) != 0 or any(start or end for _x, _y, start, end, _bulge in entity.get_points("xyseb")):
                raise ValueError("暂不支持带线宽区域的 LWPOLYLINE，请改为普通零宽度线段/圆弧。")
        elif kind == "POLYLINE":
            if entity.is_3d_polyline or entity.is_polygon_mesh or entity.is_poly_face_mesh or abs(entity.dxf.elevation.z) > 1e-8:
                raise ValueError("仅支持二维普通 POLYLINE；三维线、网格和面网格不支持。")
            if entity.dxf.extrusion != (0, 0, 1):
                raise ValueError("仅支持 XY 平面内的 POLYLINE。")
            if entity.dxf.get("default_start_width", 0) or entity.dxf.get("default_end_width", 0) or any(
                vertex.dxf.get("start_width", 0) or vertex.dxf.get("end_width", 0) for vertex in entity.vertices
            ):
                raise ValueError("暂不支持带线宽区域的 POLYLINE，请改为普通零宽度线段/圆弧。")
        expanded = _flat(entity)
        entities.extend(expanded)
        if len(entities) > MAX_ENTITIES:
            raise ValueError(f"图元数量超过安全上限 {MAX_ENTITIES}。")
        if kind == "CIRCLE":
            c = entity.dxf.center
            if abs(c.z) > 1e-8 or abs(entity.dxf.extrusion.z - 1) > 1e-8 or abs(entity.dxf.extrusion.x) > 1e-8 or abs(entity.dxf.extrusion.y) > 1e-8:
                raise ValueError("仅支持 XY 平面内的圆。")
            base_circles.append((entity.dxf.radius * 2, c.x, c.y))
        if entity.dxf.get("invisible", 0):
            raise ValueError(f"CAD 含不可见的 {kind}，为避免遗漏不予导入。")
        if entity.dxf.get("thickness", 0) != 0:
            raise ValueError(f"CAD 含带厚度的 {kind}，仅支持二维对象。")
    if unsupported:
        raise ValueError("CAD 含不支持图元（未导入任何内容）：" + ", ".join(sorted(set(unsupported))))
    if not entities:
        raise ValueError("CAD 文件中没有可导入的矢量图元。")

    doc_styles = {style.dxf.name: style for style in doc.styles}
    for entity in entities:
        kind = entity.dxftype()
        if kind not in {"LINE", "CIRCLE", "ARC", "TEXT"}:
            raise ValueError(f"无法完整展开图元 {kind}。")
        if kind == "TEXT":
            if (entity.dxf.halign != 0 or entity.dxf.valign != 0 or entity.dxf.oblique != 0
                    or entity.dxf.width != 1 or entity.dxf.get("text_generation_flag", 0) != 0):
                raise ValueError("TEXT 暂仅支持左对齐、无倾斜/反向、宽度因子为 1 的普通单行文字。")
            text = entity.dxf.text
            if not text:
                raise ValueError("CAD 含空 TEXT，请移除空文字后再导入。")
            style = doc_styles.get(entity.dxf.style)
            if style is None:
                raise ValueError(f"找不到文字样式 {entity.dxf.style}。")
            style_width = float(style.dxf.get("width", 1.0))
            style_oblique = float(style.dxf.get("oblique", 0.0))
            style_flags = int(style.dxf.get("flags", 0))
            style_height = float(style.dxf.get("height", 0.0))
            if style_width != 1.0 or style_oblique != 0.0 or style_flags != 0 or style_height != 0.0:
                raise ValueError(
                    f"文字样式 {entity.dxf.style} 含非默认宽度/倾斜/镜像竖排标志/固定高度；"
                    "请改为宽度1、倾斜0、无标志且固定高度为0的普通文字样式。"
                )
            if style.dxf.get("bigfont", ""):
                raise ValueError(f"文字样式 {entity.dxf.style} 使用了 BigFont，当前不可完整保留。")
            source_font = str(style.dxf.get("font", ""))
            if Path(source_font.replace("\\", "/")).name.lower() != Path(font_filename).name.lower():
                raise ValueError(f"文字样式 {entity.dxf.style} 使用字体“{source_font or 'CAD 默认字体'}”，与应用可用字体 {Path(font_filename).name} 不一致；为保留字形请先在 CAD 中改用该字体。")
            try:
                fonts.make_font(font_filename, 1.0)
                from .text_pdf import assert_glyphs, pdf_font
                _, pdf_face, *_ = pdf_font(font_filename)
                assert_glyphs(text, pdf_face)
            except Exception as exc:
                raise ValueError(f"TEXT 字符或当前字体不可用：{exc}") from exc
        for point_name in ({"LINE": ("start", "end"), "CIRCLE": ("center",), "ARC": ("center",), "TEXT": ("insert",)}[kind]):
            point = entity.dxf.get(point_name)
            if point is not None and (abs(point.z) > 1e-8 or not all(math.isfinite(float(v)) for v in point)):
                raise ValueError(f"{kind} 非平面或坐标不是有限数值。")
        if kind in {"CIRCLE", "ARC"}:
            if not math.isfinite(entity.dxf.radius) or entity.dxf.radius <= 1e-8:
                raise ValueError(f"{kind} 半径无效。")
            normal = entity.dxf.extrusion
            if abs(normal.x) > 1e-8 or abs(normal.y) > 1e-8 or abs(normal.z - 1) > 1e-8:
                raise ValueError(f"{kind} 仅支持 XY 平面。")
        elif kind in {"LINE", "TEXT"}:
            normal = entity.dxf.get("extrusion", (0, 0, 1))
            if abs(normal[0]) > 1e-8 or abs(normal[1]) > 1e-8 or abs(normal[2] - 1) > 1e-8:
                raise ValueError(f"{kind} 仅支持 XY 平面。")
        numeric = []
        if kind == "LINE": numeric = [*entity.dxf.start, *entity.dxf.end]
        elif kind in {"CIRCLE", "ARC"}: numeric = [*entity.dxf.center, entity.dxf.radius]
        if kind == "ARC": numeric.extend((entity.dxf.start_angle, entity.dxf.end_angle))
        if kind == "TEXT": numeric = [*entity.dxf.insert, entity.dxf.height, entity.dxf.rotation, entity.dxf.width]
        if not all(math.isfinite(float(v)) for v in numeric):
            raise ValueError(f"{kind} 含非有限数值。")
        if kind == "TEXT" and entity.dxf.height <= 0:
            raise ValueError("TEXT 高度必须大于零。")

    # Materialize from the expanded supported entities to measure geometry accurately.
    measure = ezdxf.new("R2010")
    measure.styles.new("APP", dxfattribs={"font": font_filename})
    block = measure.blocks.new("IMPORT")
    for entity in entities:
        clone = entity.copy()
        clone.dxf.layer = "0"
        if clone.dxftype() == "TEXT":
            clone.dxf.style = "APP"
        block.add_entity(clone)
    bounds = bbox.extents(block)
    vals = (bounds.extmin.x, bounds.extmin.y, bounds.extmax.x, bounds.extmax.y)
    if not all(math.isfinite(v) for v in vals) or vals[2] - vals[0] <= 1e-8 or vals[3] - vals[1] <= 1e-8:
        raise ValueError("CAD 图形范围为空或数值无效。")
    # Compose all primitive definitions in drawing units before normalization.
    raw = []
    for entity in entities:
        kind = entity.dxftype()
        if kind == "LINE":
            a, b = entity.dxf.start, entity.dxf.end
            raw.append((kind, a.x, a.y, b.x, b.y))
        elif kind == "CIRCLE":
            c = entity.dxf.center
            raw.append((kind, c.x, c.y, entity.dxf.radius))
        elif kind == "ARC":
            c = entity.dxf.center
            raw.append((kind, c.x, c.y, entity.dxf.radius, entity.dxf.start_angle, entity.dxf.end_angle))
        elif kind == "TEXT":
            p = entity.dxf.insert
            raw.append((kind, entity.dxf.text, p.x, p.y, entity.dxf.height, entity.dxf.rotation, entity.dxf.width))

    return raw, [(i + 1, d, x, y) for i, (d, x, y) in enumerate(base_circles)]


class TextReferenceError(ValueError):
    """Keep command-line detail while allowing the GUI to translate the cause."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def text_reference_index(raw: list[tuple], selected: int | None = None) -> int:
    """Choose by nominal CAD text height, never by a particular glyph's bounds."""
    texts = [item for item in raw if item[0] == "TEXT"]
    if not texts:
        raise TextReferenceError("no_text", "图形没有普通 TEXT 文字，不能按字号缩放。请改用整体高度或基准圆；轮廓线组成的字母不作为文字识别。")
    heights = [item[4] for item in texts]
    if any(type(height) not in (int, float) or not math.isfinite(height) or height <= 0 for height in heights):
        raise TextReferenceError("invalid_text_height", "基准文字字高必须为大于零的有限数值。")
    if selected is None:
        if not all(math.isclose(height, heights[0], rel_tol=1e-9, abs_tol=0) for height in heights):
            raise TextReferenceError("select_text", "图形含不同字号的文字，请明确选择基准文字；其他文字保持原相对大小。")
        return 0
    if type(selected) is not int or not 0 <= selected < len(texts):
        raise TextReferenceError("invalid_text_reference", "请选择有效的基准文字。")
    return selected


def build_rule(material: str, aliases: tuple[str, ...], raw: list[tuple], mode: str,
               circle_index: int | None = None, font_filename: str = "txt.shx", *,
               text_index: int | None = None) -> SymbolRule:
    if mode not in {"height", "circle", "text"}:
        raise ValueError("请选择有效缩放方式。")
    reference = text_reference_index(raw, text_index) if mode == "text" else None
    # Accurate bounds use a temporary block including editable text.
    doc = ezdxf.new("R2010")
    block = doc.blocks.new("SOURCE")
    doc.styles.new("APP", dxfattribs={"font": font_filename})
    for item in raw:
        kind, *v = item
        if kind == "LINE": block.add_line(v[:2], v[2:4])
        elif kind == "CIRCLE": block.add_circle(v[:2], v[2])
        elif kind == "ARC": block.add_arc(v[:2], v[2], v[3], v[4])
        elif kind == "TEXT": block.add_text(v[0], dxfattribs={"insert": v[1:3], "height": v[3], "rotation": v[4], "width": v[5], "style": "APP"})
    ext = bbox.extents(block)
    x1, y1, x2, y2 = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
    circles = [r for r in raw if r[0] == "CIRCLE"]
    if mode == "height":
        scale = STANDARD_CIRCLE_DIAMETER / (y2 - y1)
    elif mode == "circle":
        if circle_index is None or not 0 <= circle_index < len(circles):
            raise ValueError("请选择基准圆。")
        scale = STANDARD_CIRCLE_DIAMETER / (2 * circles[circle_index][3])
    else:
        # Output blocks are inserted at the section text height, so unit TEXT
        # height makes the selected letter exactly the same nominal size.
        texts = [item for item in raw if item[0] == "TEXT"]
        if not math.isclose(texts[reference][5] % 360, 0.0, abs_tol=1e-9):
            raise TextReferenceError(
                "reference_rotation",
                "所选基准文字必须水平旋转 0 度，才能与断面文字基线对齐。",
            )
        scale = 1.0 / texts[reference][4]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    normalized = []
    for item in raw:
        kind, *v = item
        if kind == "LINE": normalized.append((kind, (v[0]-x1)*scale, (v[1]-cy)*scale, (v[2]-x1)*scale, (v[3]-cy)*scale))
        elif kind == "CIRCLE": normalized.append((kind, (v[0]-x1)*scale, (v[1]-cy)*scale, v[2]*scale))
        elif kind == "ARC": normalized.append((kind, (v[0]-x1)*scale, (v[1]-cy)*scale, v[2]*scale, v[3], v[4]))
        elif kind == "TEXT": normalized.append((kind, v[0], (v[1]-x1)*scale, (v[2]-cy)*scale, v[3]*scale, v[4], v[5]))
    normalized_height = (y2 - y1) * scale
    return SymbolRule(
        material, aliases,
        geometry=tuple(normalized),
        geometry_bounds=(0, -normalized_height / 2, (x2-x1)*scale, normalized_height / 2),
        baseline_text_index=reference if mode == "text" else None,
    )


def append_custom_rule(rule: SymbolRule, custom_path: Path | None = None) -> MaterialLibrary:
    """Re-read, validate conflicts, then atomically append schema 1 data."""
    path = Path(custom_path) if custom_path else resource_root() / "material_libraries" / "custom.json"
    existing_bytes = path.read_bytes() if path.exists() else None
    current = MaterialLibrary.load(custom_path=path) if path.exists() else MaterialLibrary.load()
    names = {material_lookup_key(x) for old in current.rules for x in (old.material, *old.aliases)}
    if any(material_lookup_key(x) in names for x in (rule.material, *rule.aliases)):
        raise ValueError("材质全称或别名与现有基础/自定义规则重复（含规范化后的名称），未保存。")
    if path.exists():
        try:
            original_custom_records = json.loads(path.read_text(encoding="utf-8-sig"))["symbols"]
        except Exception as exc:
            raise ValueError(f"自定义材质库无法安全读取，未保存：{exc}") from exc
    else:
        original_custom_records = []
    payload = {"schema_version": 1, "symbols": original_custom_records + [rule.to_record()]}
    # Preserve only custom records by comparing against the immutable base rules.
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temp_path = None
    try:
        import tempfile as _tempfile
        with _tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as target:
            temp_path = Path(target.name)
            target.write(data); target.flush(); os.fsync(target.fileno())
        MaterialLibrary.load(custom_path=temp_path)
        now = path.read_bytes() if path.exists() else None
        if now != existing_bytes:
            raise ValueError("自定义材质库在保存期间已被修改；为避免覆盖并发变更，本次未保存，请重试。")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try: temp_path.unlink()
            except FileNotFoundError: pass
    return MaterialLibrary.load(custom_path=path)
