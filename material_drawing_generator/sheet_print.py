"""Print the drawing-list sheet, retaining values, merges and source alignment."""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics

from .models import PreviewCellStyle, PreviewSheet
from .xlsx_reader import (
    _tag, _sheet_catalog, _load_shared_strings, _date_style_indexes,
    _cell_alignment_styles, _worksheet_rows, _worksheet_merged_ranges,
    _worksheet_preview_layout,
)
from .text_pdf import pdf_font, assert_glyphs


def read_print_sheet(path: Path, name: str):
    with zipfile.ZipFile(path) as archive:
        catalog, date_1904 = _sheet_catalog(archive)
        sheet_path = next(p for n, _, p in catalog if n == name)
        rows = _worksheet_rows(archive, sheet_path, _load_shared_strings(archive), _date_style_indexes(archive), date_1904)
        rows = [(r, values) for r, values in rows if values]
        merges = _worksheet_merged_ranges(archive, sheet_path, rows)
        last_row = max([r for r, _ in rows] + [m[2] for m in merges], default=1)
        last_col = max([c for _, values in rows for c in values] + [m[3] for m in merges], default=0) + 1
        heights, default_height, styles = _worksheet_preview_layout(
            archive, sheet_path, rows, _cell_alignment_styles(archive), last_row, last_col,
        )
        sheet = PreviewSheet(name, rows, merged_ranges=merges, row_heights=heights,
                             default_row_height=default_height, cell_styles=styles)
        root = ET.fromstring(archive.read(sheet_path))
        cols = root.find(_tag("cols"))
        widths = [8.43] * last_col
        if cols is not None:
            for col in cols:
                start = int(col.get("min", "1")) - 1
                end = min(last_col, int(col.get("max", "1")))
                for index in range(start, end):
                    widths[index] = float(col.get("width", "8.43"))
        return sheet, widths, last_row


def _wrap(text, name, size, width):
    result = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for char in paragraph:
            if current and pdfmetrics.stringWidth(current + char, name, size) > width:
                result.append(current)
                current = char
            else:
                current += char
        result.append(current)
    return result


def add_drawing_list_page(book, source_path: Path, sheet_name: str, font_filename: str, date_columns=(), first_data_row=1):
    sheet, widths, last_row = read_print_sheet(source_path, sheet_name)
    name, font, _, _, _ = pdf_font(font_filename)
    c = book.canvas
    # Excel column widths are character units, row heights are points.
    widths = [(w * 7 + 5) * 0.75 for w in widths]
    heights = [sheet.row_heights.get(r, sheet.default_row_height) for r in range(1, last_row + 1)]
    scale = min((book.width - 20) * mm / sum(widths), (book.height - 20) * mm / sum(heights))
    x_positions = [0.0]
    y_positions = [0.0]
    for width in widths:
        x_positions.append(x_positions[-1] + width)
    for height in heights:
        y_positions.append(y_positions[-1] + height)
    merged = {}
    covered = set()
    for r1, c1, r2, c2 in sheet.merged_ranges:
        merged[(r1, c1)] = (r2, c2)
        covered.update((r, col) for r in range(r1, r2 + 1) for col in range(c1, c2 + 1) if (r, col) != (r1, c1))
    values = {r: v for r, v in sheet.rows}
    c.saveState()
    c.translate(10 * mm, (book.height - 10) * mm)
    c.scale(scale, scale)
    c.setLineWidth(0.35)
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0, 0, 0)
    for row in range(1, last_row + 1):
        for col in range(len(widths)):
            if (row, col) in covered:
                continue
            end_row, end_col = merged.get((row, col), (row, col))
            x, top = x_positions[col], -y_positions[row - 1]
            width = x_positions[end_col + 1] - x
            height = y_positions[end_row] - y_positions[row - 1]
            c.rect(x, top - height, width, height, stroke=1, fill=0)
            value = values.get(row, {}).get(col, "")
            if value == "":
                continue
            text = str(value)
            if col in date_columns and row >= first_data_row:
                from .drawing import _format_drawing_date
                text = _format_drawing_date(text)
            assert_glyphs(text, font)
            style = sheet.cell_styles.get((row, col), PreviewCellStyle())
            size = style.font_size
            # Source alignment and explicit newlines remain. Long headers can
            # wrap inside their own (possibly merged) cell, never across it.
            lines = _wrap(text, name, size, max(1, width - 3))
            leading = size * 1.16
            if len(lines) * leading > height - 2:
                size *= max(0.1, (height - 2) / (len(lines) * leading))
                lines = _wrap(text, name, size, max(1, width - 3))
                leading = size * 1.16
            block_height = len(lines) * leading
            if style.vertical == "top":
                y = top - 1.5 - size
            elif style.vertical in ("center", "distributed", "justify"):
                y = top - (height - block_height) / 2 - size
            else:
                y = top - height + 1.5 + (len(lines) - 1) * leading
            c.setFont(name, size)
            for line in lines:
                if style.horizontal in ("center", "centerContinuous"):
                    c.drawCentredString(x + width / 2, y, line)
                elif style.horizontal == "right" or (not style.horizontal and isinstance(value, (int, float))):
                    c.drawRightString(x + width - 1.5, y, line)
                else:
                    c.drawString(x + 1.5, y, line)
                y -= leading
    c.restoreState()
    c.showPage()
    book.page_names.append(sheet_name)
