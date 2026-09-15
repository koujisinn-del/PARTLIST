from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import (
    DrawingListData,
    DrawingRecord,
    PreviewCellStyle,
    PreviewSheet,
    SectionRow,
    SheetData,
    WorkbookData,
    WorkbookPreview,
    group_section_rows,
    normalized_text,
)
from .template import SiteTemplate


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
BUILTIN_DATE_FORMATS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47, 50, 57}


class ExcelReadError(ValueError):
    pass


def _tag(name: str) -> str:
    return f"{{{NS_MAIN}}}{name}"


def _column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Z]+)", cell_reference.upper())
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _visible_rich_text(container: ET.Element) -> str:
    """Read displayed text but exclude Excel phonetic-guide (rPh) text."""
    texts = [node.text or "" for node in container.findall(_tag("t"))]
    for run in container.findall(_tag("r")):
        text_node = run.find(_tag("t"))
        if text_node is not None:
            texts.append(text_node.text or "")
    return "".join(texts)


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    result: list[str] = []
    for item in root.findall(_tag("si")):
        result.append(_visible_rich_text(item))
    return result


def _date_style_indexes(archive: zipfile.ZipFile) -> set[int]:
    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except KeyError:
        return set()
    custom_formats: dict[int, str] = {}
    num_formats = root.find(_tag("numFmts"))
    if num_formats is not None:
        for item in num_formats.findall(_tag("numFmt")):
            try:
                format_id = int(item.attrib.get("numFmtId", "0"))
            except ValueError:
                continue
            custom_formats[format_id] = item.attrib.get("formatCode", "")

    def looks_like_date(format_code: str) -> bool:
        cleaned = re.sub(r'"[^"]*"', "", format_code.casefold())
        cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
        cleaned = re.sub(r"\\.", "", cleaned)
        return bool(re.search(r"(^|[^a-z])[ymdhis]+([^a-z]|$)", cleaned))

    result: set[int] = set()
    cell_xfs = root.find(_tag("cellXfs"))
    if cell_xfs is None:
        return result
    for index, xf in enumerate(cell_xfs.findall(_tag("xf"))):
        try:
            format_id = int(xf.attrib.get("numFmtId", "0"))
        except ValueError:
            continue
        if format_id in BUILTIN_DATE_FORMATS or looks_like_date(custom_formats.get(format_id, "")):
            result.add(index)
    return result


def _cell_alignment_styles(archive: zipfile.ZipFile) -> dict[int, PreviewCellStyle]:
    """Return preview formatting declared by each Excel cell style."""
    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except KeyError:
        return {}
    cell_xfs = root.find(_tag("cellXfs"))
    if cell_xfs is None:
        return {}
    font_specs: list[dict[str, Any]] = []
    fonts = root.find(_tag("fonts"))
    if fonts is not None:
        for font in fonts.findall(_tag("font")):
            name_node = font.find(_tag("name"))
            size_node = font.find(_tag("sz"))
            try:
                font_size = float(size_node.attrib.get("val", "9")) if size_node is not None else 9.0
            except ValueError:
                font_size = 9.0
            font_specs.append(
                {
                    "name": name_node.attrib.get("val", "") if name_node is not None else "",
                    "size": font_size,
                    "bold": font.find(_tag("b")) is not None,
                    "italic": font.find(_tag("i")) is not None,
                }
            )
    result: dict[int, PreviewCellStyle] = {}
    for index, xf in enumerate(cell_xfs.findall(_tag("xf"))):
        alignment = xf.find(_tag("alignment"))
        try:
            font_id = int(xf.attrib.get("fontId", "0"))
        except ValueError:
            font_id = 0
        font = font_specs[font_id] if 0 <= font_id < len(font_specs) else {}
        result[index] = PreviewCellStyle(
            horizontal=alignment.attrib.get("horizontal", "") if alignment is not None else "",
            vertical=alignment.attrib.get("vertical", "") if alignment is not None else "",
            wrap_text=(
                alignment is not None
                and alignment.attrib.get("wrapText", "0") in {"1", "true", "True"}
            ),
            font_name=str(font.get("name", "")),
            font_size=float(font.get("size", 9.0)),
            bold=bool(font.get("bold", False)),
            italic=bool(font.get("italic", False)),
        )
    return result


def _format_excel_date(serial: float, date_1904: bool) -> str:
    epoch = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
    value = epoch + timedelta(days=serial)
    if abs(serial - round(serial)) < 1e-9:
        return value.date().isoformat()
    return value.isoformat(sep=" ", timespec="seconds")


def _cell_value(
    cell: ET.Element,
    shared_strings: list[str],
    date_styles: set[int] | None = None,
    date_1904: bool = False,
) -> Any:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        inline = cell.find(_tag("is"))
        if inline is None:
            return ""
        return _visible_rich_text(inline)
    value_node = cell.find(_tag("v"))
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type in {"str", "e"}:
        return raw
    if cell_type == "b":
        return raw == "1"
    try:
        numeric = float(raw)
        try:
            style_index = int(cell.attrib.get("s", "0"))
        except ValueError:
            style_index = 0
        if date_styles and style_index in date_styles:
            return _format_excel_date(numeric, date_1904)
        return int(numeric) if numeric.is_integer() else numeric
    except ValueError:
        return raw


def _worksheet_rows(
    archive: zipfile.ZipFile,
    worksheet_path: str,
    shared_strings: list[str],
    date_styles: set[int] | None = None,
    date_1904: bool = False,
    max_rows: int | None = None,
    max_columns: int | None = None,
) -> list[tuple[int, dict[int, Any]]]:
    try:
        root = ET.fromstring(archive.read(worksheet_path))
    except KeyError as exc:
        raise ExcelReadError(f"Excel 内缺少工作表文件：{worksheet_path}") from exc
    rows: list[tuple[int, dict[int, Any]]] = []
    sheet_data = root.find(_tag("sheetData"))
    if sheet_data is None:
        return rows
    for row in sheet_data.findall(_tag("row")):
        if max_rows is not None and len(rows) >= max_rows:
            break
        row_number = int(row.attrib.get("r", len(rows) + 1))
        values: dict[int, Any] = {}
        for cell in row.findall(_tag("c")):
            column = _column_index(cell.attrib.get("r", "A1"))
            if max_columns is not None and column >= max_columns:
                continue
            value = _cell_value(cell, shared_strings, date_styles, date_1904)
            # Styled blank cells can extend to hundreds of unused columns.  They
            # have no semantic value and make the preview unnecessarily heavy.
            if value != "":
                values[column] = value
        rows.append((row_number, values))
    return rows


def _worksheet_merged_ranges(
    archive: zipfile.ZipFile,
    worksheet_path: str,
    rows: list[tuple[int, dict[int, Any]]],
    max_rows: int | None = None,
    max_columns: int | None = None,
) -> list[tuple[int, int, int, int]]:
    root = ET.fromstring(archive.read(worksheet_path))
    row_values = {row_number: values for row_number, values in rows}
    result: list[tuple[int, int, int, int]] = []
    for merge in root.findall(f".//{_tag('mergeCell')}"):
        reference = merge.attrib.get("ref", "")
        parts = reference.split(":", 1)
        if len(parts) != 2:
            continue
        start_match = re.fullmatch(r"([A-Z]+)(\d+)", parts[0].upper())
        end_match = re.fullmatch(r"([A-Z]+)(\d+)", parts[1].upper())
        if not start_match or not end_match:
            continue
        start_row = int(start_match.group(2))
        end_row = int(end_match.group(2))
        start_column = _column_index(parts[0])
        end_column = _column_index(parts[1])
        if max_rows is not None and start_row > max_rows:
            continue
        if max_columns is not None and start_column >= max_columns:
            continue
        end_row = min(end_row, max_rows) if max_rows is not None else end_row
        end_column = min(end_column, max_columns - 1) if max_columns is not None else end_column
        has_content = any(
            row_values.get(row_number, {}).get(column, "") != ""
            for row_number in range(start_row, end_row + 1)
            for column in range(start_column, end_column + 1)
        )
        if has_content:
            result.append((start_row, start_column, end_row, end_column))
    return result


def _worksheet_preview_layout(
    archive: zipfile.ZipFile,
    worksheet_path: str,
    rows: list[tuple[int, dict[int, Any]]],
    alignment_styles: dict[int, PreviewCellStyle],
    max_rows: int,
    max_columns: int,
) -> tuple[dict[int, float], float, dict[tuple[int, int], PreviewCellStyle]]:
    """Read only the source sizing/alignment needed by the on-screen preview."""
    root = ET.fromstring(archive.read(worksheet_path))
    default_row_height = 15.0
    sheet_format = root.find(_tag("sheetFormatPr"))
    if sheet_format is not None:
        try:
            default_row_height = float(sheet_format.attrib.get("defaultRowHeight", "15"))
        except ValueError:
            pass

    visible_cells = {
        (row_number, column)
        for row_number, values in rows
        for column in values
    }
    row_heights: dict[int, float] = {}
    cell_styles: dict[tuple[int, int], PreviewCellStyle] = {}
    sheet_data = root.find(_tag("sheetData"))
    if sheet_data is None:
        return row_heights, default_row_height, cell_styles
    for row in sheet_data.findall(_tag("row")):
        try:
            row_number = int(row.attrib.get("r", "0"))
        except ValueError:
            continue
        if row_number < 1 or row_number > max_rows:
            continue
        if "ht" in row.attrib:
            try:
                row_heights[row_number] = float(row.attrib["ht"])
            except ValueError:
                pass
        for cell in row.findall(_tag("c")):
            column = _column_index(cell.attrib.get("r", "A1"))
            coordinate = (row_number, column)
            if column >= max_columns or coordinate not in visible_cells:
                continue
            try:
                style_index = int(cell.attrib.get("s", "0"))
            except ValueError:
                continue
            style = alignment_styles.get(style_index)
            if style is not None:
                cell_styles[coordinate] = style
    return row_heights, default_row_height, cell_styles


def _sheet_catalog(
    archive: zipfile.ZipFile,
) -> tuple[list[tuple[str, str, str]], bool]:
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationships = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_root.findall(f"{{{NS_REL_PACKAGE}}}Relationship")
    }
    workbook_properties = workbook_root.find(_tag("workbookPr"))
    date_1904 = bool(
        workbook_properties is not None
        and workbook_properties.attrib.get("date1904", "0") in {"1", "true", "True"}
    )
    sheets_node = workbook_root.find(_tag("sheets"))
    if sheets_node is None:
        raise ExcelReadError("Excel 中没有工作表。")
    result: list[tuple[str, str, str]] = []
    for sheet_node in sheets_node.findall(_tag("sheet")):
        sheet_name = sheet_node.attrib.get("name", "Sheet")
        state = sheet_node.attrib.get("state", "visible")
        relation_id = sheet_node.attrib.get(f"{{{NS_REL_DOC}}}id")
        target = relationships.get(relation_id or "")
        if not target:
            raise ExcelReadError(f"无法定位工作表：{sheet_name}")
        if target.startswith("/"):
            worksheet_path = target.lstrip("/")
        else:
            worksheet_path = posixpath.normpath(posixpath.join("xl", target))
        result.append((sheet_name, state, worksheet_path))
    return result, date_1904


def _normalized_candidates(values: list[str]) -> set[str]:
    return {normalized_text(value).casefold() for value in values if normalized_text(value)}


def _parse_drawing_list(
    sheet_name: str,
    worksheet_path: str,
    rows: list[tuple[int, dict[int, Any]]],
    template: SiteTemplate,
) -> DrawingListData:
    settings = template.drawing_list
    name_headers = _normalized_candidates(list(settings.get("name_headers", [])))
    number_headers = _normalized_candidates(list(settings.get("number_headers", [])))
    header_row = 0
    name_column = -1
    number_column = -1
    header_values: dict[int, Any] = {}
    for row_number, values in rows[:40]:
        found_name = next(
            (column for column, value in values.items() if normalized_text(value).casefold() in name_headers),
            None,
        )
        found_number = next(
            (column for column, value in values.items() if normalized_text(value).casefold() in number_headers),
            None,
        )
        if found_name is not None and found_number is not None:
            header_row = row_number
            name_column = found_name
            number_column = found_number
            header_values = values
            break
    if not header_row:
        raise ExcelReadError(
            f"工作表“{sheet_name}”被识别为图纸列表，但找不到图纸名称和图纸编号表头。"
        )
    version_columns: list[tuple[str, int]] = []
    version_header_row = header_row

    # Some drawing lists use a two-row header: name/number on the first row and
    # 0, 1, 2 ... revision numbers on the next row.  Prefer the nearby row with
    # the most numeric revision labels.
    for candidate_row, candidate_values in rows:
        if candidate_row < header_row or candidate_row > header_row + 5:
            continue
        candidates: list[tuple[str, int]] = []
        for column in sorted(candidate_values):
            if column <= number_column:
                continue
            label = normalized_text(candidate_values[column])
            match = re.fullmatch(r"(\d+)(?:\s*[\(\uff08].*[\)\uff09])?", label)
            if match:
                candidates.append((match.group(1), column))
        if len(candidates) > len(version_columns):
            version_columns = candidates
            version_header_row = candidate_row

    if not version_columns:
        for column in sorted(header_values):
            if column <= number_column:
                continue
            version_name = normalized_text(header_values[column])
            if version_name:
                version_columns.append((version_name, column))
    if not version_columns:
        raise ExcelReadError(f"工作表“{sheet_name}”没有版本日期列。")
    records: list[DrawingRecord] = []
    for row_number, values in rows:
        if row_number <= version_header_row:
            continue
        drawing_name = normalized_text(values.get(name_column, ""))
        drawing_number = normalized_text(values.get(number_column, ""))
        if not drawing_name and not drawing_number:
            continue
        if not drawing_name:
            raise ExcelReadError(f"图纸列表第 {row_number} 行缺少图纸名称。")
        version_dates = [
            (version, normalized_text(values.get(column, "")), column)
            for version, column in version_columns
            if normalized_text(values.get(column, ""))
        ]
        records.append(
            DrawingRecord(
                source_row=row_number,
                drawing_name=drawing_name,
                drawing_number=drawing_number,
                version_dates=version_dates,
            )
        )
    submission_date_cell = normalized_text(settings.get("submission_date_cell", ""))
    submission_date = ""
    if submission_date_cell:
        target_column = _column_index(submission_date_cell)
        match = re.search(r"(\d+)$", submission_date_cell)
        target_row = int(match.group(1)) if match else 0
        for row_number, values in rows:
            if row_number == target_row:
                submission_date = normalized_text(values.get(target_column, ""))
                break
    return DrawingListData(
        sheet_name=sheet_name,
        worksheet_path=worksheet_path,
        header_row=header_row,
        name_column=name_column,
        number_column=number_column,
        version_columns=version_columns,
        records=records,
        submission_date_cell=submission_date_cell,
        submission_date=submission_date,
    )


def _normalized_header_map(headers: dict[str, list[str]]) -> dict[str, set[str]]:
    return {
        field: {normalized_text(alias).casefold() for alias in aliases}
        for field, aliases in headers.items()
    }


def _detect_header_row(
    rows: list[tuple[int, dict[int, Any]]],
    headers: dict[str, set[str]],
    compound_headers: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, int]]:
    best: tuple[int, dict[str, int]] | None = None
    best_partial: tuple[int, dict[str, int]] | None = None
    for row_number, values in rows[:30]:
        found: dict[str, int] = {}
        for column, value in values.items():
            normalized = normalized_text(value).casefold()
            for field, aliases in headers.items():
                if normalized and normalized in aliases and field not in found:
                    found[field] = column
            for rule in compound_headers or []:
                aliases = {
                    normalized_text(alias).casefold()
                    for alias in rule.get("aliases", [])
                    if normalized_text(alias)
                }
                if normalized not in aliases:
                    continue
                for offset, field in enumerate(rule.get("fields", [])):
                    if isinstance(field, str) and field and field not in found:
                        found[field] = column + offset
        if found and (best_partial is None or len(found) > len(best_partial[1])):
            best_partial = (row_number, found)
        if "member_name" in found and "category" in found:
            if best is None or len(found) > len(best[1]):
                best = (row_number, found)
    if best is None:
        field_labels = {"member_name": "部件名称", "category": "种类"}
        found_text = "未识别到任何必要表头"
        missing = ["部件名称", "种类"]
        row_text = "前 30 行"
        if best_partial is not None:
            row_number, found = best_partial
            row_text = f"第 {row_number} 行"
            recognized = [
                f"{field_labels[field]}（{_column_label(column)} 列）"
                for field, column in found.items()
                if field in field_labels
            ]
            if recognized:
                found_text = "已识别 " + "、".join(recognized)
            missing = [label for field, label in field_labels.items() if field not in found]
        raise ExcelReadError(
            "无法识别部材表的必要表头。列号不必固定，但必须有可识别的"
            "“部件名称”和“种类”。\n"
            f"{row_text}{found_text}；缺少：{'、'.join(missing)}。"
        )
    return best


def _column_label(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _read_sheet(
    sheet_name: str,
    rows: list[tuple[int, dict[int, Any]]],
    template: SiteTemplate,
    descending: bool,
) -> SheetData:
    from .models import source_text
    display_text = source_text if template.material_symbols_enabled else normalized_text
    normalized_headers = _normalized_header_map(template.headers)
    header_row, field_columns = _detect_header_row(
        rows,
        normalized_headers,
        template.compound_headers,
    )
    if "section" not in field_columns:
        raise ExcelReadError(
            f"工作表“{sheet_name}”已识别部件和种类，但没有识别到断面列；"
            "请检查 Excel 断面表头或现场模板的列名别称，不能输出只有部件名称的图纸。"
        )
    if field_columns.get("material") == field_columns["section"]:
        raise ExcelReadError(
            f"工作表“{sheet_name}”的断面和材质被映射到同一列，"
            "请分开这两列或修正合并表头配置。"
        )
    member_column = field_columns["member_name"]
    category_column = field_columns["category"]
    data_rows = [(row_number, values) for row_number, values in rows if row_number > header_row]
    if any(normalized_text(values.get(member_column, "")) for _, values in data_rows) and not any(
        normalized_text(values.get(category_column, "")) for _, values in data_rows
    ):
        raise ExcelReadError(
            f"工作表“{sheet_name}”已识别种类列（{_column_label(category_column)} 列），"
            "但数据行全部为空。请填写大梁、小梁等种类，否则程序无法决定放入哪个表格。"
        )
    source_rows: list[SectionRow] = []
    errors: list[str] = []
    warnings: list[str] = []
    for row_number, values in rows:
        if row_number <= header_row:
            continue
        recognized_values = {
            field: values.get(column, "") for field, column in field_columns.items()
        }
        if not any(normalized_text(value) for value in recognized_values.values()):
            continue
        member_name = display_text(recognized_values.get("member_name", ""))
        category = normalized_text(recognized_values.get("category", ""))
        if not member_name:
            errors.append(f"第 {row_number} 行有数据但没有部件名称。")
            continue
        if not category:
            errors.append(f"第 {row_number} 行的部件“{member_name}”没有种类。")
            continue
        canonical_category = template.canonical_category(category)
        known_fields = {
            "member_name",
            "category",
            "position",
            "section",
            "material",
            "joint",
            "remarks",
        }
        extra = {
            field: value
            for field, value in recognized_values.items()
            if field not in known_fields
        }
        source_rows.append(
            SectionRow(
                source_row=row_number,
                member_name=member_name,
                category=canonical_category,
                position=display_text(recognized_values.get("position", "")),
                section=source_text(recognized_values.get("section", "")),
                material=display_text(recognized_values.get("material", "")),
                joint=display_text(recognized_values.get("joint", "")),
                remarks=display_text(recognized_values.get("remarks", "")),
                extra=extra,
            )
        )
    if errors:
        sample = "\n".join(f"- {error}" for error in errors[:20])
        suffix = "" if len(errors) <= 20 else f"\n- 另有 {len(errors) - 20} 项错误"
        raise ExcelReadError(f"工作表“{sheet_name}”数据校验失败：\n{sample}{suffix}")
    if not source_rows:
        warnings.append("没有可生成的部件数据。")
    elif all(not source_text(row.section).strip() for row in source_rows):
        raise ExcelReadError(
            f"工作表“{sheet_name}”已识别断面列，但所有部件的断面内容都为空；"
            "请检查 Excel 表头与数据列位置，避免生成空白断面图纸。"
        )
    groups = group_section_rows(
        source_rows,
        category_order=template.category_order,
        descending=descending,
    )
    sheet = SheetData(
        name=sheet_name,
        header_row=header_row,
        source_rows=source_rows,
        groups=groups,
        warnings=warnings,
    )
    if sheet.source_row_count != sheet.output_subrow_count:
        raise ExcelReadError(
            f"工作表“{sheet_name}”行数对账失败：读取 {sheet.source_row_count} 行，"
            f"输出模型 {sheet.output_subrow_count} 行。"
        )
    return sheet


def read_workbook(
    source_path: str | Path,
    template: SiteTemplate,
    descending: bool = False,
) -> WorkbookData:
    path = Path(source_path).resolve()
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ExcelReadError("第一阶段支持 .xlsx 和 .xlsm；旧版 .xls 请先另存为 .xlsx。")
    if not path.exists():
        raise ExcelReadError(f"找不到 Excel 文件：{path}")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ExcelReadError("Excel 文件无法打开，文件可能损坏或并非有效的 xlsx。") from exc
    with archive:
        shared_strings = _load_shared_strings(archive)
        date_styles = _date_style_indexes(archive)
        catalog, date_1904 = _sheet_catalog(archive)
        sheets: list[SheetData] = []
        skipped: list[str] = []
        drawing_list: DrawingListData | None = None
        drawing_list_names = _normalized_candidates(
            list(template.drawing_list.get("sheet_names", []))
        )
        for sheet_name, state, worksheet_path in catalog:
            if state != "visible":
                skipped.append(sheet_name)
                continue
            raw_rows = _worksheet_rows(
                archive,
                worksheet_path,
                shared_strings,
                date_styles,
                date_1904,
            )
            if normalized_text(sheet_name).casefold() in drawing_list_names:
                drawing_list = _parse_drawing_list(
                    sheet_name,
                    worksheet_path,
                    raw_rows,
                    template,
                )
                continue
            sheets.append(_read_sheet(sheet_name, raw_rows, template, descending))
    if not sheets:
        raise ExcelReadError("Excel 中没有可处理的可见工作表。")
    return WorkbookData(str(path), sheets, skipped, drawing_list)


def read_workbook_preview(
    source_path: str | Path,
    template: SiteTemplate,
) -> WorkbookPreview:
    path = Path(source_path).resolve()
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ExcelReadError("支持预览 .xlsx 和 .xlsm 文件。")
    if not path.exists():
        raise ExcelReadError(f"找不到 Excel 文件：{path}")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ExcelReadError("Excel 文件无法打开，文件可能损坏或并非有效的 xlsx。") from exc
    with archive:
        shared_strings = _load_shared_strings(archive)
        date_styles = _date_style_indexes(archive)
        alignment_styles = _cell_alignment_styles(archive)
        catalog, date_1904 = _sheet_catalog(archive)
        drawing_list_names = _normalized_candidates(
            list(template.drawing_list.get("sheet_names", []))
        )
        previews: list[PreviewSheet] = []
        drawing_list: DrawingListData | None = None
        for sheet_name, state, worksheet_path in catalog:
            if state != "visible":
                continue
            rows = _worksheet_rows(
                archive,
                worksheet_path,
                shared_strings,
                date_styles,
                date_1904,
                max_rows=500,
                max_columns=40,
            )
            merged_ranges = _worksheet_merged_ranges(
                archive,
                worksheet_path,
                rows,
                max_rows=500,
                max_columns=40,
            )
            row_heights, default_row_height, cell_styles = _worksheet_preview_layout(
                archive,
                worksheet_path,
                rows,
                alignment_styles,
                max_rows=500,
                max_columns=40,
            )
            preview = PreviewSheet(
                name=sheet_name,
                rows=rows,
                merged_ranges=merged_ranges,
                row_heights=row_heights,
                default_row_height=default_row_height,
                cell_styles=cell_styles,
            )
            if normalized_text(sheet_name).casefold() in drawing_list_names:
                try:
                    drawing_list = _parse_drawing_list(
                        sheet_name,
                        worksheet_path,
                        rows,
                        template,
                    )
                    preview.highlighted_cells = drawing_list.highlighted_cells()
                except ExcelReadError:
                    # Preview remains useful even before a site's drawing-list
                    # columns have been configured.  Generation performs the
                    # strict validation and reports the exact problem.
                    drawing_list = None
            previews.append(preview)
    return WorkbookPreview(str(path), previews, drawing_list)
