from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

import ezdxf
from ezdxf import bbox

from material_drawing_generator.drawing import (
    DrawingMetadata, _converter_root, convert_dxf_to_dwg, render_cad_pdf, render_dxf,
)
from material_drawing_generator.models import SectionRow, SheetData, group_section_rows
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.service import generate_drawings


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DWG = ROOT / "docs" / "frame_template_example" / "frame_template_example_150x.dwg"
EXAMPLE_DXF = ROOT / "docs" / "frame_template_example" / "frame_template_example_150x.dxf"


class FrameTemplateExampleTests(unittest.TestCase):
    @staticmethod
    def write_excel(path: Path, dates: list[str]) -> None:
        def worksheet(rows):
            xml_rows = []
            for row_index, values in enumerate(rows, 1):
                cells = "".join(
                    f'<c r="{chr(65 + column)}{row_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                    for column, value in enumerate(values)
                )
                xml_rows.append(f'<row r="{row_index}">{cells}</row>')
            return '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>"

        drawing_rows = [
            ["图纸名称", "图纸编号", *[str(index) for index in range(len(dates))]],
            ["2F梁リスト", "B-204", *dates],
        ]
        part_rows = [
            ["部件名称", "种类", "断面", "材质"],
            ["G1", "大梁", "H-300X150", "SS400"],
        ]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="图纸列表" sheetId="1" r:id="rId1"/><sheet name="2F梁リスト" sheetId="2" r:id="rId2"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", worksheet(drawing_rows))
            archive.writestr("xl/worksheets/sheet2.xml", worksheet(part_rows))

    def test_example_assets_geometry_aliases_and_source_units(self):
        self.assertTrue(EXAMPLE_DWG.is_file())
        self.assertTrue(EXAMPLE_DXF.is_file())
        document = ezdxf.readfile(EXAMPLE_DXF)
        closed = [entity for entity in document.modelspace().query("LWPOLYLINE") if entity.closed]
        outer = max(closed, key=lambda entity: bbox.extents([entity]).size.x * bbox.extents([entity]).size.y)
        bounds = bbox.extents([outer])
        self.assertAlmostEqual(bounds.size.x, 63000)
        self.assertAlmostEqual(bounds.size.y, 44550)
        text = [entity.dxf.text for entity in document.modelspace().query("TEXT")]
        self.assertEqual(text.count("図面名称"), 1)
        self.assertEqual(text.count("図面番号"), 1)
        self.assertEqual(text.count("R-X"), 1)
        self.assertEqual(text.count("作成日"), 1)
        self.assertEqual(text.count("日付"), 1)
        placeholders = SiteTemplate.load(default_template_path()).frame_placeholders
        self.assertEqual(placeholders["creation_date_aliases"], ["作成日"])
        self.assertEqual(placeholders["revision_date_aliases"], ["日付", "提出日付"])
        self.assertEqual(placeholders["revision_spacing"], 5.0)

    @unittest.skipUnless((_converter_root() / "dwg2dxf.exe").is_file(), "LibreDWG reader unavailable")
    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").is_file(), "LibreDWG writer unavailable")
    def test_dwg_frame_metadata_pdf_and_dwg_outputs_leave_source_unchanged(self):
        original_hash = hashlib.sha256(EXAMPLE_DWG.read_bytes()).hexdigest()
        base = SiteTemplate.load(default_template_path())
        template = base.with_frame_dwg(EXAMPLE_DWG)
        row = SectionRow(2, "G1", "大梁", section="H-300X150", material="SS400")
        sheet = SheetData("2F梁リスト", 1, [row], group_section_rows([row], ["大梁"]))
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            results = []
            for dates in (["2026-10-26"], ["2026-09-01", "2026-10-26"]):
                suffix = str(len(dates))
                dxf_path = output / f"frame-{suffix}.dxf"
                metadata = DrawingMetadata(
                    drawing_number="B-204", version="3", drawing_date="2026-10-26", revision_dates=list(dates),
                )
                render_dxf(dxf_path, sheet, template, metadata)
                document = ezdxf.readfile(dxf_path)
                fields = list(document.modelspace().query("TEXT"))
                values = [entity.dxf.text for entity in fields]
                for value in ("2F梁リスト", "B-204", "R-3", "26.10.26"):
                    self.assertIn(value, values)
                self.assertNotIn("図面名称", values)
                self.assertNotIn("図面番号", values)
                self.assertNotIn("R-X", values)
                self.assertNotIn("作成日", values)
                self.assertNotIn("日付", values)
                outer = max(document.modelspace().query("LWPOLYLINE"),
                    key=lambda entity: bbox.extents([entity]).size.x * bbox.extents([entity]).size.y)
                border = bbox.extents([outer])
                self.assertAlmostEqual(border.size.x, 420.0, places=4)
                self.assertAlmostEqual(border.size.y, 297.0, places=4)
                history = sorted(
                    (round(entity.dxf.insert.y, 3), entity.dxf.text)
                    for entity in fields
                    if abs(entity.dxf.insert.x - 392.0) < 0.01 and entity.dxf.text[:2].isdigit()
                )
                expected_history = [
                    (26.0 + 5.0 * index, f"{value[2:4]}.{value[5:7]}.{value[8:10]}")
                    for index, value in enumerate(dates)
                ]
                self.assertEqual(history, expected_history)
                self.assertTrue(all(entity.dxftype() == "TEXT" for entity in fields))
                pdf_path = output / f"frame-{suffix}.pdf"
                render_cad_pdf(dxf_path, pdf_path, template)
                self.assertGreater(pdf_path.stat().st_size, 1000)
                dwg_path = output / f"frame-{suffix}.dwg"
                convert_dxf_to_dwg(dxf_path, dwg_path)
                self.assertGreater(dwg_path.stat().st_size, 1000)
                results.append((history, pdf_path, dwg_path))
            self.assertEqual(len(results[0][0]), 1)
            self.assertEqual(len(results[1][0]), 2)
        self.assertEqual(hashlib.sha256(EXAMPLE_DWG.read_bytes()).hexdigest(), original_hash)

    @unittest.skipUnless((_converter_root() / "dwg2dxf.exe").is_file(), "LibreDWG reader unavailable")
    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").is_file(), "LibreDWG writer unavailable")
    def test_excel_drawing_list_single_and_multiple_dates_fill_example(self):
        template = SiteTemplate.load(default_template_path()).with_frame_dwg(EXAMPLE_DWG)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for dates in (["2026-10-26"], ["2026-09-01", "2026-10-26"]):
                suffix = str(len(dates))
                workbook_path = root / f"input-{suffix}.xlsx"
                self.write_excel(workbook_path, list(dates))
                source_hash = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
                destination = root / f"output-{suffix}"
                result = generate_drawings(workbook_path, destination, template, {"pdf", "dxf", "dwg"})
                generated = result.sheets[0]
                self.assertEqual(generated.drawing_number, "B-204")
                self.assertEqual(generated.version, str(len(dates) - 1))
                self.assertEqual(generated.drawing_date, dates[-1])
                self.assertGreater(Path(result.pdf_path).stat().st_size, 1000)
                self.assertGreater(Path(generated.files["dwg"]).stat().st_size, 1000)
                document = ezdxf.readfile(generated.files["dxf"])
                texts = list(document.modelspace().query("TEXT"))
                values = [entity.dxf.text for entity in texts]
                self.assertIn("2F梁リスト", values)
                self.assertIn("B-204", values)
                self.assertIn(f"R-{len(dates) - 1}", values)
                self.assertIn("26.10.26", values)
                self.assertNotIn("作成日", values)
                self.assertNotIn("日付", values)
                history = sorted(
                    (round(entity.dxf.insert.y, 3), entity.dxf.text)
                    for entity in texts
                    if abs(entity.dxf.insert.x - 392.0) < 0.01 and entity.dxf.text[:2].isdigit()
                )
                expected = [
                    (26.0 + 5.0 * index, f"{value[2:4]}.{value[5:7]}.{value[8:10]}")
                    for index, value in enumerate(dates)
                ]
                self.assertEqual(history, expected)
                self.assertEqual(hashlib.sha256(workbook_path.read_bytes()).hexdigest(), source_hash)


if __name__ == "__main__":
    unittest.main()
