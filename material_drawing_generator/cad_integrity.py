"""Strict, text-preserving R2000 interchange for the bundled DWG writer."""
from __future__ import annotations

import io
import json
import math
import re
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf.addons import Importer
from ezdxf.lldxf.tags import group_tags
from ezdxf.lldxf.tagger import ascii_tags_loader
from ezdxf.lldxf.tagwriter import TagWriter
from ezdxf.lldxf.types import DXFTag


def unicode_text(value: str) -> str:
    return re.sub(r"\\U\+([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), value)


def _handle_code(code: int) -> bool:
    return code in (5, 105, 1005, 480, 481) or 320 <= code <= 369 or 390 <= code <= 399


def _restore_text_styles(source, target) -> None:
    # Importer removes XDATA and keeps its own default Standard entry. Both
    # operations can lose the font family used by AutoCAD to resolve a TTF.
    for style in target.styles:
        if style.dxf.name not in source.styles:
            continue
        original = source.styles.get(style.dxf.name)
        for key, value in original.dxf.all_existing_dxf_attribs().items():
            if key not in {"handle", "owner", "name"}:
                style.dxf.set(key, value)
        if original.has_xdata("ACAD"):
            style.set_xdata("ACAD", list(original.get_xdata("ACAD")))
        else:
            style.discard_xdata("ACAD")


def _preserve_text_alignment(document) -> None:
    """Avoid LibreDWG's omitted-same-point TEXT alignment encoding bug.

    Resolve the first point to the actual text baseline instead of putting all
    generated text at the origin and relying on the CAD viewer to recompute it.
    MDG text is emitted as baseline-left TEXT at this resolved position: it is
    still visually centered, still editable, and needs no deferred alignment.
    The frame's own alignment modes remain unchanged. Includes block content.
    """
    for entity in document.entitydb.values():
        if not entity.is_alive or entity.dxftype() not in {"TEXT", "ATTRIB", "ATTDEF"}:
            continue
        h, v = entity.dxf.halign, entity.dxf.valign
        if (h == 0 and v == 0) or h in (3, 5):
            continue
        anchor = entity.dxf.get("align_point", entity.dxf.insert)
        entity.dxf.align_point = anchor
        generated = entity.dxf.style == "MDG_CJK"
        if generated or (entity.dxf.insert.x == anchor.x and entity.dxf.insert.y == anchor.y):
            from ezdxf.tools.text import TextLine, unified_alignment
            from ezdxf.entities import get_font_name
            from ezdxf.fonts import fonts
            from ezdxf.tools.text import plain_text
            # Match the font resolution used by the cell-bounds check.
            font = fonts.make_font(get_font_name(entity), entity.dxf.height, entity.dxf.width)
            line = TextLine(plain_text(unicode_text(entity.dxf.text)), font)
            halign, valign = unified_alignment(entity)
            flags = entity.dxf.text_generation_flag
            baseline = line.baseline_vertices(
                anchor, halign, valign, math.radians(entity.dxf.rotation),
                (-1 if flags & 2 else 1, -1 if flags & 4 else 1),
            )[0]
            entity.dxf.insert = baseline
            if generated:
                entity.dxf.halign = 0
                entity.dxf.valign = 0
                entity.dxf.discard("align_point")
            elif baseline.x == anchor.x and baseline.y == anchor.y:
                # A zero-length line can still hit the omitted-point bug.
                entity.dxf.insert = (anchor.x + 1e-4, anchor.y, baseline.z)


def reset_model_view(document) -> None:
    """Discard the source frame's distant camera after paper normalization."""
    from ezdxf import zoom, bbox
    zoom.extents(document.modelspace(), factor=1.08)
    for viewport in document.viewports.get_config("*Active"):
        viewport.dxf.target = (0, 0, 0)
        viewport.dxf.direction = (0, 0, 1)
        viewport.dxf.view_twist = 0
        viewport.dxf.view_mode = 0
    bounds = bbox.extents(document.modelspace())
    if bounds.has_data:
        document.header["$EXTMIN"] = bounds.extmin
        document.header["$EXTMAX"] = bounds.extmax


def write_compatibility_dxf(source_path: Path, target_path: Path) -> None:
    source = ezdxf.readfile(source_path)
    target = ezdxf.new("R2000")
    target.encoding = "cp932"
    target.units = source.units
    importer = Importer(source, target)
    importer.import_modelspace()
    importer.finalize()
    _restore_text_styles(source, target)
    for name in ("$EXTMIN", "$EXTMAX", "$LIMMIN", "$LIMMAX"):
        if name in source.header:
            target.header[name] = source.header[name]
    target.header["$DIMSTYLE"] = "Standard"

    # Importer does not remap the source's plot-style pointer. In the new
    # database it can point at a dictionary instead of a plot-style object.
    normal = target.rootdict["ACAD_PLOTSTYLENAME"]["Normal"].dxf.handle
    for layer in target.layers:
        layer.dxf.plotstyle_handle = normal
    source_snapshot = _entity_snapshot(source)
    imported_snapshot = _entity_snapshot(target)
    if source_snapshot != imported_snapshot:
        missing = list((source_snapshot - imported_snapshot).items())[:3]
        extra = list((imported_snapshot - source_snapshot).items())[:3]
        raise ValueError(
            "DWG 兼容转换前的文字/图元对账不一致，已保留 DXF，未交付损坏的 DWG。"
            f"缺失示例：{missing}；多余示例：{extra}"
        )
    _preserve_text_alignment(target)
    reset_model_view(target)

    # ezdxf creates post-R2000 defaults even in R2000 drawings. Exclude only
    # these non-graphical dictionaries/objects from the interchange stream.
    unsupported_objects = {
        "MATERIAL", "MLEADERSTYLE", "VISUALSTYLE", "ACDBSECTIONVIEWSTYLE",
        "ACDBDETAILVIEWSTYLE", "FIELD", "FIELDLIST", "SUN",
        "RASTERVARIABLES",
    }
    removed = {
        e.dxf.handle for e in target.objects
        if e.dxftype() in unsupported_objects
    }
    for name in (
        "ACAD_MATERIAL", "ACAD_MLEADERSTYLE", "ACAD_VISUALSTYLE",
        "ACAD_SECTIONVIEWSTYLE", "ACAD_DETAILVIEWSTYLE", "ACAD_FIELDLIST",
        "ACAD_SUN", "ACAD_RASTERVARIABLES",
    ):
        if name in target.rootdict:
            removed.add(target.rootdict[name].dxf.handle)

    stream = io.StringIO()
    target.write(stream)
    records = list(group_tags(ascii_tags_loader(io.StringIO(stream.getvalue()))))
    largest = max(int(e.dxf.handle, 16) for e in target.entitydb.values() if e.is_alive)
    next_handle = largest + 1
    remap: dict[str, str] = {}
    model_handle = target.modelspace().block_record_handle
    acad_handle = target.appids.get("ACAD").dxf.handle
    # LibreDWG reserves 0xB for VX_CONTROL and pre-creates Model_Space at 0x1F.
    # Remap *all* references, not just entity IDs, before importing into DWG.
    # LibreDWG also writes ACAD font EED with a fixed APPID reference 0x12.
    # A normal ezdxf R2000 drawing instead places DICTIONARYWDFLT at 0x12.
    # DXF readback hides this mismatch by assigning the ACAD name regardless.
    for reserved in ("B", "1F", "12"):
        if reserved in target.entitydb and reserved not in removed and reserved != model_handle:
            if reserved == acad_handle:
                continue
            remap[reserved] = f"{next_handle:X}"
            next_handle += 1
    remap[model_handle] = "1F"
    remap[acad_handle] = "12"

    result = io.StringIO()
    writer = TagWriter(result)
    header_name = ""
    for record in records:
        if any(t.code in (5, 105) and t.value in removed for t in record):
            continue
        if record[0].value == "CLASS" and any(
            t.value in unsupported_objects for t in record
        ):
            continue
        kept = []
        for tag in record:
            if tag.code == 9:
                header_name = str(tag.value)
            if _handle_code(tag.code) and tag.value in removed:
                if kept and kept[-1].code in (3, 9):
                    kept.pop()
                continue
            if _handle_code(tag.code):
                value = remap.get(str(tag.value), str(tag.value))
                if tag.code == 5 and header_name == "$HANDSEED":
                    value = f"{next_handle + 16:X}"
                    header_name = ""
                tag = DXFTag(tag.code, value)
            kept.append(tag)
        writer.write_tags(kept)
    # Japanese uses its actual R2000 code page. Characters outside it retain
    # DXF Unicode escapes; never encode with errors='replace' (question marks).
    target_path.write_text(
        unicode_text(result.getvalue()), encoding="cp932", errors="dxfreplace"
    )


def validate_raw_handles(path: Path) -> None:
    from ezdxf.filemanagement import dxf_file_info
    info = dxf_file_info(path)
    handles = Counter()
    with path.open(encoding=info.encoding, errors="strict") as stream:
        for record in group_tags(ascii_tags_loader(stream)):
            if record[0].value in ("SECTION", "ENDSEC", "EOF"):
                continue
            for tag in record:
                if tag.code in (5, 105):
                    handles[str(tag.value).upper()] += 1
    invalid = [h for h, count in handles.items() if count > 1 or h == "0"]
    if invalid:
        raise ValueError("DWG 回读出现无效或重复对象编号：" + ", ".join(invalid[:8]))


def validate_dwg_resources(json_path: Path) -> dict:
    """Check DWG object references *before* a DXF reader repairs/names them."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    objects = data["OBJECTS"]
    by_handle = {obj["handle"][2]: obj for obj in objects if "handle" in obj}
    checked = 0
    for obj in objects:
        for eed in obj.get("eed", []):
            pointer = eed.get("handle")
            if pointer is None:
                continue
            handle = pointer[3] if len(pointer) > 3 else pointer[2]
            target = by_handle.get(handle, {})
            if target.get("object") != "APPID":
                raise ValueError(f"DWG 字体/扩展资料引用了无效应用对象：{handle:X} ({target.get('object', 'missing')})")
            if obj.get("object") == "STYLE" and target.get("name") != "ACAD":
                raise ValueError("DWG 字体资料未关联到 ACAD 字体应用对象。")
            checked += 1
        if obj.get("entity") in {"TEXT", "ATTRIB", "ATTDEF", "MTEXT"}:
            pointer = obj.get("style")
            if pointer:
                handle = pointer[3] if len(pointer) > 3 else pointer[2]
                if by_handle.get(handle, {}).get("object") != "STYLE":
                    raise ValueError("DWG 文字引用的字体样式对象无效。")
    return {"dwg_extended_data_links": checked}


def _entity_snapshot(document) -> Counter:
    result = Counter()
    pending = [document.modelspace()]
    seen = set()
    while pending:
        block = pending.pop()
        if block.name in seen:
            continue
        seen.add(block.name)
        for entity in block:
            if entity.dxftype() == "INSERT":
                pending.append(document.blocks.get(entity.dxf.name))
            kind = entity.dxftype()
            name = unicode_text(block.name)
            layer = unicode_text(entity.dxf.layer)
            if kind in {"TEXT", "ATTRIB", "ATTDEF", "MTEXT"}:
                value = entity.text if kind == "MTEXT" else entity.dxf.text
                point = tuple(round(v, 5) for v in entity.dxf.insert)
                height = entity.dxf.char_height if kind == "MTEXT" else entity.dxf.height
                style = document.styles.get(entity.dxf.get("style", "Standard"))
                style_info = _style_signature(style)
                visible = (entity.dxf.get("invisible", 0), entity.dxf.get("color", 256))
                if kind == "MTEXT":
                    placement = (entity.dxf.attachment_point, round(entity.dxf.width, 5),
                                 round(entity.get_rotation(), 5))
                else:
                    h, v = entity.dxf.halign, entity.dxf.valign
                    anchor = (tuple(round(c, 5) for c in entity.dxf.get("align_point", (0, 0, 0)))
                              if h or v else None)
                    placement = (h, v, anchor, round(entity.dxf.rotation, 5),
                                 round(entity.dxf.width, 5), round(entity.dxf.oblique, 5),
                                 entity.dxf.text_generation_flag,
                                 tuple(round(c, 5) for c in entity.dxf.extrusion))
                result[(name, kind, layer, unicode_text(value), point, round(height, 5),
                        placement, style_info, visible)] += 1
            else:
                # Geometry is also checked by count/type/layer. Editable text
                # is checked individually, including its position and height.
                result[(name, kind, layer)] += 1
    return result


def _style_signature(style) -> tuple:
    return (unicode_text(style.dxf.name), unicode_text(style.dxf.font),
            unicode_text(style.dxf.bigfont), style.dxf.flags & 5,
            round(style.dxf.height, 5), round(style.dxf.width, 5),
            round(style.dxf.oblique, 5), style.dxf.generation_flags,
            tuple((tag.code, unicode_text(tag.value) if isinstance(tag.value, str) else tag.value)
                  for tag in style.get_xdata("ACAD")) if style.has_xdata("ACAD") else ())


def validate_roundtrip(source_path: Path, reread_path: Path) -> dict:
    validate_raw_handles(reread_path)
    expected = ezdxf.readfile(source_path)
    actual = ezdxf.readfile(reread_path)
    from .material_drawing import validate_material_geometry
    material_checks = validate_material_geometry(expected, actual)
    before = _entity_snapshot(actual)
    audit = actual.audit()
    if audit.errors or audit.fixes:
        messages = [e.message for e in [*audit.errors, *audit.fixes]]
        raise ValueError("DWG 回读需要修复：" + "；".join(messages[:5]))
    if before != _entity_snapshot(expected):
        missing = list((_entity_snapshot(expected) - before).items())[:3]
        raise ValueError(f"DWG 回读与原 DXF 的文字/实体不一致：{missing}")
    for layer in actual.layers:
        handle = layer.dxf.get("plotstyle_handle")
        if handle and (handle not in actual.entitydb or actual.entitydb[handle].dxftype() != "ACDBPLACEHOLDER"):
            raise ValueError(f"DWG 图层 {layer.dxf.name} 的打印样式引用无效。")
        if layer.dxf.name in expected.layers:
            original = expected.layers.get(layer.dxf.name)
            if (layer.dxf.color, layer.dxf.flags & 3, layer.dxf.plot) != (
                original.dxf.color, original.dxf.flags & 3, original.dxf.plot
            ):
                raise ValueError(f"DWG 图层 {layer.dxf.name} 的显示/打印状态发生变化。")
    return {"entities": len(actual.modelspace()), "text_entities": len(actual.modelspace().query("TEXT MTEXT")), **material_checks}
