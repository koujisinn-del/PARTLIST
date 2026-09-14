import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape
from unittest.mock import patch

import ezdxf

from material_drawing_generator.models import SectionRow, SheetData, WorkbookData, group_section_rows
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.materials import MaterialLibrary, SymbolRule
from material_drawing_generator.material_drawing import prepare_material_job, validate_material_geometry, verify_material_cells, SymbolFactory
from material_drawing_generator.drawing import _cjk_font_filename, _prepare_tables, render_dxf, DrawingMetadata, convert_dxf_to_dwg, _converter_root
from material_drawing_generator.cad_integrity import write_compatibility_dxf, validate_roundtrip
from material_drawing_generator.xlsx_reader import _read_sheet


class MaterialOutputTests(unittest.TestCase):
    def template(self):
        template = SiteTemplate.load(default_template_path())
        template.data["material_symbols"] = {"enabled": True}
        return template

    def data(self, rows=None):
        if rows is None:
            rows = [SectionRow(i + 2, f"G{i}", "大梁", position="中央", section="H-900X300X16X28", material=rule.material) for i, rule in enumerate(MaterialLibrary.load().rules)]
        sheet = SheetData("材质试验", 1, rows, group_section_rows(rows, ["大梁"]))
        workbook = WorkbookData("synthetic.xlsx", [sheet])
        library, plans = prepare_material_job(workbook, _cjk_font_filename())
        return sheet, plans[sheet.name]

    def test_all_base_grades_are_blocks_and_plain_text(self):
        sheet, plan = self.data()
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "source.dxf"
            render_dxf(src, sheet, self.template(), DrawingMetadata(), plan)
            doc = ezdxf.readfile(src)
            report = verify_material_cells(doc, plan)
            self.assertEqual(report["verified_rows"], 16)
            self.assertEqual(report["insertions"], 14)
            self.assertLess(report["blocks"], report["insertions"])
            for name in {cell["block"] for cell in plan.cells if cell["block"]}:
                self.assertTrue(all(e.dxftype() in {"TEXT", "LINE", "CIRCLE"} for e in doc.blocks[name]))
                self.assertTrue(all(e.dxf.text.isascii() for e in doc.blocks[name].query("TEXT")))
            compat = Path(directory) / "compat.dxf"
            write_compatibility_dxf(src, compat)
            self.assertEqual(validate_roundtrip(compat, compat)["material_insertions"], 14)

    def test_shifted_symbol_or_changed_circle_is_rejected(self):
        sheet, plan = self.data()
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "source.dxf"
            render_dxf(src, sheet, self.template(), DrawingMetadata(), plan)
            doc = ezdxf.readfile(src)
            changed = copy.deepcopy(doc)
            ref = changed.modelspace().query("INSERT").first
            ref.dxf.insert += (1, 0, 0)
            with self.assertRaisesRegex(ValueError, "发生变化"):
                validate_material_geometry(doc, changed)
            changed = copy.deepcopy(doc)
            ref = changed.modelspace().query("INSERT").first
            circle = changed.blocks[ref.dxf.name].query("CIRCLE").first
            circle.dxf.radius *= 2
            with self.assertRaisesRegex(ValueError, "发生变化"):
                validate_material_geometry(doc, changed)

    def test_unknown_and_blank_material_have_source_location(self):
        for material in ("", "NOT_A_GRADE"):
            with self.subTest(material=material), self.assertRaisesRegex(ValueError, "材质试验 第 88 行"):
                self.data([SectionRow(88, "G1", "大梁", section="H-100", material=material)])

    def test_mixed_bh_cannot_silently_use_common_material(self):
        rows = [(1, {0: "部件名称", 1: "种类", 2: "断面", 3: "材质", 4: "Web材质"}),
                (2, {0: "G1", 1: "大梁", 2: "BH-100", 3: "SS400", 4: "TMCP325B"})]
        sheet = _read_sheet("2F", rows, self.template(), False)
        with self.assertRaisesRegex(ValueError, "混合材质 BH"):
            prepare_material_job(WorkbookData("fixture", [sheet]), _cjk_font_filename())

    def test_raw_enclosed_marks_are_preserved_without_notation_validation(self):
        rows = [(1, {0: "部件名称", 1: "种类", 2: "断面", 3: "材质"}), (2, {0: "G1", 1: "大梁", 2: "T🄱BH-1000", 3: "TMCP 385 B"})]
        sheet = _read_sheet("2F", rows, self.template(), False)
        self.assertEqual(sheet.source_rows[0].section, "T🄱BH-1000")
        self.assertEqual(sheet.source_rows[0].material, "TMCP 385 B")
        # Glyph support is a separate feasibility check, not a notation rule.
        with patch("material_drawing_generator.text_pdf.assert_glyphs"):
            _, plans = prepare_material_job(WorkbookData("synthetic", [sheet]), _cjk_font_filename())
        self.assertEqual(plans["2F"].bindings[2].section, "T🄱BH-1000")

    def test_channel_and_arbitrary_section_text_export_verbatim(self):
        sections = ["[-100X50X5X7.5", "特殊断面-100X50", "CUSTOM-123", "T(B)BH-100"]
        sheet, plan = self.data([SectionRow(i+2, f"G{i}", "大梁", section=value, material="SS400") for i,value in enumerate(sections)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sections.dxf"
            render_dxf(path, sheet, self.template(), DrawingMetadata(), plan)
            document = ezdxf.readfile(path)
            self.assertEqual(verify_material_cells(document, plan)["verified_rows"], len(sections))
            texts = [e.dxf.text for e in document.modelspace().query("TEXT")]
            for value in sections:
                self.assertIn(value, texts)

    def test_section_raw_text_is_preserved_with_material_mode_off(self):
        template = self.template()
        template.data["material_symbols"]["enabled"] = False
        raw = "  ［-100×50  T🄱  "
        sheet = _read_sheet("2F", [(1,{0:"部件名称",1:"种类",2:"断面"}), (2,{0:"G1",1:"大梁",2:raw})], template, False)
        self.assertEqual(sheet.source_rows[0].section, raw)

    def test_prefix_and_enclosure_participate_in_column_width(self):
        sheet, plan = self.data([SectionRow(2, "G1", "大梁", section="H-100", material="TMCP325B")])
        marked_width = plan.column_width(sheet.source_rows[0], 1)
        plain = plan.factory.font.text_width_ex("H-100", 1)
        self.assertGreater(marked_width, plain + 0.4)
        tables = _prepare_tables(sheet, self.template(), plan)
        columns = tables[0][0]["columns"]
        self.assertNotIn("material", {column["field"] for column in columns})

    def test_repeated_material_uses_one_definition(self):
        rows = [SectionRow(i + 2, f"G{i}", "大梁", section="H-100", material="TMCP325B") for i in range(10)]
        sheet, plan = self.data(rows)
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "source.dxf"
            render_dxf(src, sheet, self.template(), DrawingMetadata(), plan)
            result = verify_material_cells(ezdxf.readfile(src), plan)
            self.assertEqual((result["insertions"], result["blocks"]), (10, 1))

    def test_all_supported_custom_shapes_have_editable_geometry(self):
        factory = SymbolFactory(_cjk_font_filename())
        for shape in ("circle", "square", "triangle", "diamond", "pentagon", "hexagon", "star"):
            rule = SymbolRule("EXAMPLE", prefix="T", inner="B", shape=shape)
            geom = factory.geometry(rule)
            self.assertGreater(geom.width, 0)
            self.assertGreater(geom.height, 0)
            self.assertEqual([text for text, _, _ in geom.texts], ["T", "B"])
            self.assertTrue(geom.lines or geom.circles)

    def test_service_publication_and_unknown_grade_stops_before_excel_marking(self):
        from material_drawing_generator.service import generate_drawings
        sheet, _ = self.data()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("material_drawing_generator.service.read_workbook", return_value=WorkbookData("synthetic.xlsx", [sheet])):
                result = generate_drawings("synthetic.xlsx", root / "output", self.template(), {"dxf", "pdf"})
            report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
            self.assertEqual(report["material_symbols"][sheet.name]["verified_rows"], 16)
            self.assertTrue(Path(result.pdf_path).is_file())
            self.assertEqual(result.sheets[0].input_rows, result.sheets[0].output_rows)
            sheet.source_rows[0].material = "UNKNOWN"
            with patch("material_drawing_generator.service.read_workbook", return_value=WorkbookData("synthetic.xlsx", [sheet])), patch("material_drawing_generator.service.apply_submission_highlighting", side_effect=AssertionError("should not mark")):
                with self.assertRaisesRegex(ValueError, "未在基础库"):
                    generate_drawings("synthetic.xlsx", root / "invalid", self.template(), {"dxf"})
            self.assertFalse((root / "invalid").exists())

    def test_real_xlsx_two_sheets_through_pdf_dxf_job(self):
        from material_drawing_generator.service import generate_drawings
        import hashlib
        data = [["部件名称", "种类", "部位", "断面", "材质"],
                ["sb24a", "小梁", "全断面", "H-248X124X5X8", "TMCP 325 B"],
                ["sb24A", "小梁", "全断面", "H-248X124X5X8", "SS400"]]
        xml_rows = []
        for row_index, values in enumerate(data, 1):
            cells = ''.join(f'<c r="{chr(65 + i)}{row_index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for i, value in enumerate(values))
            xml_rows.append(f'<row r="{row_index}">{cells}</row>')
        sheet_xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(xml_rows) + '</sheetData></worksheet>'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.xlsx"
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
                archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
                archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="2F" sheetId="1" r:id="rId1"/><sheet name="3F" sheetId="2" r:id="rId2"/></sheets></workbook>')
                archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
                archive.writestr('xl/worksheets/sheet1.xml', sheet_xml)
                archive.writestr('xl/worksheets/sheet2.xml', sheet_xml)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            result = generate_drawings(source, root / 'result', self.template(), {'pdf', 'dxf'})
            self.assertEqual(len(result.sheets), 2)
            self.assertEqual([s.output_rows for s in result.sheets], [2, 2])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            report = json.loads(Path(result.report_path).read_text(encoding='utf-8'))
            self.assertEqual(report['pdf_pages'], ['2F', '3F'])
            for name in ('2F', '3F'):
                self.assertEqual(report['material_symbols'][name]['verified_rows'], 2)
                doc = ezdxf.readfile(root / 'result' / f'{name}.dxf')
                texts = [e.dxf.text for e in doc.modelspace().query('TEXT')]
                self.assertIn('sb24a', texts)
                self.assertIn('sb24A', texts)

    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").exists(), "DWG component unavailable")
    def test_real_dwg_conversion_of_all_base_symbols(self):
        sheet, plan = self.data()
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "source.dxf"
            render_dxf(src, sheet, self.template(), DrawingMetadata(), plan)
            dest = Path(directory) / "symbols.dwg"
            convert_dxf_to_dwg(src, dest)
            self.assertGreater(dest.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
