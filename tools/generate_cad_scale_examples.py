"""Generate fictitious three-scale CAD examples through the real Excel pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from material_drawing_generator.service import generate_drawings
from material_drawing_generator.template import SiteTemplate, default_template_path

OUT = ROOT / "output" / "pdf" / "cad_scale_20261027"


def write_source(path: Path) -> None:
    rows = [["部件名称", "种类", "部位", "断面", "材质", "継手", "备注"]]
    rows.extend([
        "QA100", "大梁", f"P{i + 1}", f"H-{200 + i}X100X6X9",
        "SN400B" if i % 2 else "SN490B", "J1", "QA ONLY",
    ] for i in range(100))
    xml_rows = []
    for row_index, values in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{chr(65 + index)}{row_index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for index, value in enumerate(values)
        )
        xml_rows.append(f'<row r="{row_index}">{cells}</row>')
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="QA SCALE" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + '</sheetData></worksheet>')


def main() -> None:
    temporary = ROOT / ".test-temp" / "cad-scale-manager"
    temporary.mkdir(parents=True, exist_ok=True)
    source = temporary / "source.xlsx"
    write_source(source)
    protected = [source, ROOT / "material_libraries" / "base.json", ROOT / "material_libraries" / "custom.json", ROOT / "docs" / "frame_template_example" / "frame_template_example_150x.dwg"]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in protected]
    template = SiteTemplate.load(default_template_path()).with_frame_dwg(protected[-1])
    template.data["material_symbols"] = {"enabled": True}
    report = []
    for scale in (100, 150, 200):
        result = generate_drawings(source, OUT / str(scale), template, {"pdf", "dxf", "dwg"}, drawing_date="2026-10-27", cad_output_scale=scale)
        assert result.sheets[0].input_rows == result.sheets[0].output_rows == 100
        report.append({"cad_scale": scale, "rows": 100, "report": str(Path(result.report_path).relative_to(OUT))})
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in protected]
    (OUT / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
