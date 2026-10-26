"""Generate fictitious, CAD-backed examples of same-page continuation tables."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf

from material_drawing_generator.drawing import (
    DrawingMetadata, _cjk_font_filename, _prepare_tables,
    convert_dxf_to_dwg, render_dxf,
)
from material_drawing_generator.material_drawing import prepare_material_job, verify_material_cells
from material_drawing_generator.models import SectionRow, SheetData, WorkbookData, group_section_rows
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.text_pdf import TextPdfBook

OUT = ROOT / "output" / "pdf" / "table_continuation_20261027"


def example_sheet(name: str, groups: list[tuple[str, int]]) -> SheetData:
    rows = []
    for member, count in groups:
        for index in range(count):
            rows.append(SectionRow(
                source_row=len(rows) + 2, member_name=member, category="大梁",
                position=f"P{index + 1}", section=f"H-{200 + index}X100X6X9",
                material="SN400B" if index % 2 else "SN490B", joint="J1",
                remarks="QA ONLY",
            ))
    return SheetData(name, 1, rows, group_section_rows(rows, ["大梁"]))


def main() -> None:
    template = SiteTemplate.load(default_template_path())
    template.data["material_symbols"] = {"enabled": True}
    library_paths = [ROOT / "material_libraries" / name for name in ("base.json", "custom.json")]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in library_paths]
    samples = [
        ("whole_groups", example_sheet("整组续表试验 QA ONLY", [("G1", 35), ("g1", 50), ("G2", 25)])),
        ("long_group", example_sheet("超长部件续表试验 QA ONLY", [("G100", 100)])),
    ]
    _, plans = prepare_material_job(WorkbookData("fictitious-example", [sheet for _, sheet in samples]), _cjk_font_filename())
    OUT.mkdir(parents=True, exist_ok=True)
    book = TextPdfBook(OUT / "table_continuation.pdf", 420, 297)
    report = []
    for number, (stem, sheet) in enumerate(samples, start=1):
        plan = plans[sheet.name]
        tables = _prepare_tables(sheet, template, plan)
        assert len(tables) > 1, "Example must exercise continuation tables"
        expected_rows = [row.source_row for _, groups, _ in tables for group in groups for row in group.rows]
        assert sorted(expected_rows) == sorted(row.source_row for row in sheet.source_rows)
        dxf = OUT / f"{stem}.dxf"
        count = render_dxf(dxf, sheet, template, DrawingMetadata(drawing_number=f"QA-CONT-{number:02d}", drawing_date="2026-10-27"), plan)
        assert count == sheet.source_row_count
        doc = ezdxf.readfile(dxf)
        validation = verify_material_cells(doc, plan)
        convert_dxf_to_dwg(dxf, OUT / f"{stem}.dwg")
        book.add_cad_page(doc, sheet.name, expected_sections=[row.section for row in sheet.source_rows])
        report.append({
            "example": stem, "input_rows": count, "verified_rows": validation["verified_rows"],
            "tables": [{"title": table["title"], "rows": sum(len(group.rows) for group in groups),
                        "members": [group.member_name for group in groups],
                        "font_height": size, "row_height": table["row_height"]}
                       for table, groups, size in tables],
            "dwg_roundtrip": "passed",
        })
    book.save()
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in library_paths]
    (OUT / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
