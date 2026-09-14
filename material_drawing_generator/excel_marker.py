from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import DrawingListData
from .xlsx_reader import NS_MAIN


MARKER_TEXT = "MDG_CURRENT_SUBMISSION"


@dataclass(slots=True)
class HighlightResult:
    applied: bool
    highlighted_drawings: int = 0
    message: str = ""


def _tag(name: str) -> str:
    return f"{{{NS_MAIN}}}{name}"


def _column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _absolute_cell(reference: str) -> str:
    match = re.fullmatch(r"\s*([A-Za-z]+)(\d+)\s*", reference)
    if not match:
        raise ValueError(f"无效的本次提出日期单元格：{reference}")
    return f"${match.group(1).upper()}${match.group(2)}"


def _ensure_red_dxf(styles_root: ET.Element) -> int:
    dxfs = styles_root.find(_tag("dxfs"))
    if dxfs is None:
        dxfs = ET.Element(_tag("dxfs"), {"count": "0"})
        children = list(styles_root)
        insert_before = {
            _tag("tableStyles"),
            _tag("colors"),
            _tag("extLst"),
        }
        insert_at = next(
            (index for index, child in enumerate(children) if child.tag in insert_before),
            len(children),
        )
        styles_root.insert(insert_at, dxfs)
    for index, dxf in enumerate(dxfs.findall(_tag("dxf"))):
        fill = dxf.find(f"{_tag('fill')}/{_tag('patternFill')}/{_tag('fgColor')}")
        font_color = dxf.find(f"{_tag('font')}/{_tag('color')}")
        if (
            fill is not None
            and fill.attrib.get("rgb", "").upper() == "FFFFC7CE"
            and font_color is not None
            and font_color.attrib.get("rgb", "").upper() == "FF9C0006"
        ):
            return index
    dxf = ET.SubElement(dxfs, _tag("dxf"))
    font = ET.SubElement(dxf, _tag("font"))
    ET.SubElement(font, _tag("color"), {"rgb": "FF9C0006"})
    fill = ET.SubElement(dxf, _tag("fill"))
    pattern = ET.SubElement(fill, _tag("patternFill"), {"patternType": "solid"})
    ET.SubElement(pattern, _tag("fgColor"), {"rgb": "FFFFC7CE"})
    ET.SubElement(pattern, _tag("bgColor"), {"indexed": "64"})
    dxfs.attrib["count"] = str(len(dxfs.findall(_tag("dxf"))))
    return len(dxfs.findall(_tag("dxf"))) - 1


def _remove_previous_rules(sheet_root: ET.Element) -> None:
    for conditional in list(sheet_root.findall(_tag("conditionalFormatting"))):
        formulas = [node.text or "" for node in conditional.iter(_tag("formula"))]
        if any(MARKER_TEXT in formula for formula in formulas):
            sheet_root.remove(conditional)


def _insert_conditional_formatting(sheet_root: ET.Element, element: ET.Element) -> None:
    children = list(sheet_root)
    late_tags = {
        _tag("dataValidations"),
        _tag("hyperlinks"),
        _tag("printOptions"),
        _tag("pageMargins"),
        _tag("pageSetup"),
        _tag("headerFooter"),
        _tag("rowBreaks"),
        _tag("colBreaks"),
        _tag("customProperties"),
        _tag("cellWatches"),
        _tag("ignoredErrors"),
        _tag("smartTags"),
        _tag("drawing"),
        _tag("legacyDrawing"),
        _tag("picture"),
        _tag("oleObjects"),
        _tag("controls"),
        _tag("webPublishItems"),
        _tag("tableParts"),
        _tag("extLst"),
    }
    insert_at = next(
        (index for index, child in enumerate(children) if child.tag in late_tags),
        len(children),
    )
    sheet_root.insert(insert_at, element)


def _add_rules(sheet_root: ET.Element, drawing_list: DrawingListData, dxf_id: int) -> None:
    records = drawing_list.records
    if not records:
        return
    first_row = min(record.source_row for record in records)
    last_row = max(record.source_row for record in records)
    version_first = min(column for _, column in drawing_list.version_columns)
    version_last = max(column for _, column in drawing_list.version_columns)
    first_version_name = _column_name(version_first)
    last_version_name = _column_name(version_last)
    submission_cell = _absolute_cell(drawing_list.submission_date_cell)

    name_range = f"{_column_name(drawing_list.name_column)}{first_row}:{_column_name(drawing_list.name_column)}{last_row}"
    number_range = f"{_column_name(drawing_list.number_column)}{first_row}:{_column_name(drawing_list.number_column)}{last_row}"
    identity_formula = (
        f'AND(N("{MARKER_TEXT}")=0,'
        f'IFERROR(LOOKUP(2,1/(${first_version_name}{first_row}:${last_version_name}{first_row}<>""),'
        f'${first_version_name}{first_row}:${last_version_name}{first_row})={submission_cell},FALSE))'
    )
    identity = ET.Element(
        _tag("conditionalFormatting"),
        {"sqref": f"{name_range} {number_range}"},
    )
    identity_rule = ET.SubElement(
        identity,
        _tag("cfRule"),
        {"type": "expression", "dxfId": str(dxf_id), "priority": "1"},
    )
    ET.SubElement(identity_rule, _tag("formula")).text = identity_formula
    _insert_conditional_formatting(sheet_root, identity)

    priority = 2
    for record in records:
        if record.latest_column is None:
            continue
        cell = f"{_column_name(record.latest_column)}{record.source_row}"
        date_formula = (
            f'AND(N("{MARKER_TEXT}")=0,{cell}={submission_cell},{cell}<>"")'
        )
        dates = ET.Element(_tag("conditionalFormatting"), {"sqref": cell})
        date_rule = ET.SubElement(
            dates,
            _tag("cfRule"),
            {"type": "expression", "dxfId": str(dxf_id), "priority": str(priority)},
        )
        ET.SubElement(date_rule, _tag("formula")).text = date_formula
        _insert_conditional_formatting(sheet_root, dates)
        priority += 1


def apply_submission_highlighting(
    workbook_path: str | Path,
    drawing_list: DrawingListData | None,
) -> HighlightResult:
    if drawing_list is None:
        return HighlightResult(False, message="Excel 中没有图纸列表。")
    if not drawing_list.submission_date_cell:
        return HighlightResult(False, message="本次提出日期单元格尚未配置。")
    if not drawing_list.submission_date:
        return HighlightResult(False, message="本次提出日期单元格为空。")
    path = Path(workbook_path).resolve()
    ET.register_namespace("", NS_MAIN)
    with zipfile.ZipFile(path, "r") as source:
        try:
            styles_root = ET.fromstring(source.read("xl/styles.xml"))
            sheet_root = ET.fromstring(source.read(drawing_list.worksheet_path))
        except KeyError as exc:
            raise ValueError("Excel 缺少标红所需的样式或图纸列表文件。") from exc
        dxf_id = _ensure_red_dxf(styles_root)
        _remove_previous_rules(sheet_root)
        _add_rules(sheet_root, drawing_list, dxf_id)
        replacements = {
            "xl/styles.xml": ET.tostring(styles_root, encoding="utf-8", xml_declaration=True),
            drawing_list.worksheet_path: ET.tostring(
                sheet_root,
                encoding="utf-8",
                xml_declaration=True,
            ),
        }
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-",
            suffix=path.suffix,
            dir=path.parent,
        )
        os.close(temporary_fd)
        temporary_path = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary_path, "w") as target:
                for info in source.infolist():
                    payload = replacements.get(info.filename, source.read(info.filename))
                    target.writestr(copy.copy(info), payload)
            try:
                os.replace(temporary_path, path)
            except PermissionError:
                # Some managed Windows folders allow file writes but block atomic replacement.
                # Direct overwrite is still safe here because the complete ZIP was validated first.
                shutil.copyfile(temporary_path, path)
                temporary_path.unlink(missing_ok=True)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    highlighted = len(drawing_list.highlighted_cells()) // 3
    return HighlightResult(
        True,
        highlighted_drawings=highlighted,
        message=f"已把本次提出的 {highlighted} 张图纸标红并保存到 Excel。",
    )
