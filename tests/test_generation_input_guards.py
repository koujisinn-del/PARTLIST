from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape, quoteattr

import ezdxf

from material_drawing_generator.drawing import CadAdapter, DrawingError, DrawingMetadata, _converter_root, prepare_frame_dxf
from material_drawing_generator.excel_marker import HighlightResult
from material_drawing_generator.service import generate_drawings
from material_drawing_generator.template import SiteTemplate, default_template_path

ROOT = Path(__file__).resolve().parents[1]
FRAME_DXF = ROOT / "docs" / "frame_template_example" / "frame_template_example_150x.dxf"
LEGACY_FRAME_DWG = ROOT / "図面テンプレート.dwg"


def write_xlsx(path: Path, sheets: list[tuple[str, list[list[str]]]]) -> None:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + overrides + '</Types>'
    )
    sheet_nodes = "".join(
        f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _rows) in enumerate(sheets, 1)
    )
    relationships = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    def worksheet(rows):
        xml_rows = []
        for row_index, values in enumerate(rows, 1):
            cells = "".join(
                f'<c r="{chr(65 + column)}{row_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                for column, value in enumerate(values)
            )
            xml_rows.append(f'<row r="{row_index}">{cells}</row>')
        return '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheet_nodes}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}</Relationships>')
        for i, (_name, rows) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", worksheet(rows))


def one_sheet_rows(material="SS400", section="H-300X150"):
    return [["部件名称", "种类", "断面", "材质"], ["G1", "大梁", section, material]]


def all_markers(adapter, values=None):
    values = values or {
        "drawing_name": "NAME", "drawing_number": "NUMBER", "version": "VERSION",
        "creation_date": "CREATE", "revision_date": "REVISION",
    }
    for value in values.values():
        adapter.modelspace.add_text(value, dxfattribs={"insert": (10, 10), "height": 2})
    return values


class GenerationInputGuardsTests(unittest.TestCase):
    def test_alias_candidates_are_collected_and_same_entity_aliases_are_deduplicated_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = CadAdapter(Path(directory) / "frame.dxf", 420, 297)
            markers = all_markers(adapter)
            placeholders = {
                "drawing_name": " NAME ", "drawing_name_aliases": ["N AME", "NAME"],
                "drawing_number": "NUMBER", "version": "VERSION",
                "creation_date": "CREATE", "revision_date": "REVISION",
            }
            adapter.apply_external_metadata("2F", DrawingMetadata("B-2", "3", "2026-10-27", ["2026-10-26"]), placeholders)
            texts = {entity.dxf.text for entity in adapter.modelspace.query("TEXT")}
            self.assertIn("2F", texts)
            self.assertIn("B-2", texts)
            self.assertIn("R-3", texts)
            self.assertIn("26.10.27", texts)
            self.assertIn("26.10.26", texts)
            self.assertNotIn("NAME", texts)

    def test_distinct_alias_matches_and_exact_duplicates_fail_before_any_text_edit(self):
        for alternate in ("CREATE_ALIAS", "CREATE"):
            with self.subTest(alternate=alternate), tempfile.TemporaryDirectory() as directory:
                adapter = CadAdapter(Path(directory) / "frame.dxf", 420, 297)
                markers = all_markers(adapter)
                if alternate == "CREATE_ALIAS":
                    adapter.modelspace.add_text(alternate, dxfattribs={"insert": (20, 10), "height": 2})
                else:
                    adapter.modelspace.add_text("CREATE", dxfattribs={"insert": (20, 10), "height": 2})
                before = [entity.dxf.text for entity in adapter.modelspace.query("TEXT")]
                placeholders = {
                    **{key: value for key, value in markers.items()},
                    "creation_date_aliases": [alternate],
                }
                with self.assertRaisesRegex(DrawingError, "候选定位文字|多处定位文字"):
                    adapter.apply_external_metadata("changed", DrawingMetadata("B", "2", "2026-10-27"), placeholders)
                self.assertEqual([entity.dxf.text for entity in adapter.modelspace.query("TEXT")], before)

    def test_bundled_legacy_frame_with_two_creation_aliases_is_rejected_atomically(self):
        if not LEGACY_FRAME_DWG.is_file() or not (_converter_root() / "dwg2dxf.exe").is_file():
            self.skipTest("legacy frame or LibreDWG reader unavailable")
        original_hash = hashlib.sha256(LEGACY_FRAME_DWG.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            base_dxf = prepare_frame_dxf(LEGACY_FRAME_DWG)
            adapter = CadAdapter(Path(directory) / "output.dxf", 420, 297, base_dxf_path=base_dxf)
            before = [entity.dxf.text for entity in adapter.modelspace.query("TEXT")]
            with self.assertRaisesRegex(DrawingError, "作成日|创建日期"):
                adapter.apply_external_metadata(
                    "changed", DrawingMetadata(), SiteTemplate.load(default_template_path()).frame_placeholders
                )
            self.assertEqual([entity.dxf.text for entity in adapter.modelspace.query("TEXT")], before)
        self.assertEqual(hashlib.sha256(LEGACY_FRAME_DWG.read_bytes()).hexdigest(), original_hash)

    def test_cross_field_shared_anchor_still_fails_atomically_and_dates_remain_case_sensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = CadAdapter(Path(directory) / "frame.dxf", 420, 297)
            adapter.modelspace.add_text("SHARED", dxfattribs={"insert": (0, 0), "height": 2})
            adapter.modelspace.add_text("VER", dxfattribs={"insert": (0, 0), "height": 2})
            adapter.modelspace.add_text("20xx/xx/xx", dxfattribs={"insert": (0, 0), "height": 2})
            adapter.modelspace.add_text("20XX/XX/XX", dxfattribs={"insert": (0, 0), "height": 2})
            before = [entity.dxf.text for entity in adapter.modelspace.query("TEXT")]
            placeholders = {
                "drawing_name": "SHARED", "drawing_number": "SHARED", "version": "VER",
                "creation_date": "20xx/xx/xx", "revision_date": "20XX/XX/XX",
            }
            with self.assertRaisesRegex(DrawingError, "多个字段指向同一"):
                adapter.apply_external_metadata("changed", DrawingMetadata(), placeholders)
            self.assertEqual([entity.dxf.text for entity in adapter.modelspace.query("TEXT")], before)

    def test_colliding_sheet_names_fail_before_output_creation_or_excel_marking(self):
        template = SiteTemplate.load(default_template_path())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "collision.xlsx"
            write_xlsx(workbook, [("A<B", one_sheet_rows()), ("A_B", one_sheet_rows(section="C-200"))])
            output = root / "existing-output"
            output.mkdir()
            sentinel = output / "KEEP.txt"
            sentinel.write_text("keep", encoding="utf-8")
            library_paths = [ROOT / "material_libraries" / "base.json", ROOT / "material_libraries" / "custom.json"]
            library_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in library_paths if path.exists()}
            with patch("material_drawing_generator.service.apply_submission_highlighting", side_effect=AssertionError("must not mark Excel")):
                with self.assertRaisesRegex(ValueError, "A<B.*A_B|A_B.*A<B"):
                    generate_drawings(workbook, output, template, {"dxf"})
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            absent_output = root / "not-created"
            with patch("material_drawing_generator.service.apply_submission_highlighting", side_effect=AssertionError("must not mark Excel")):
                with self.assertRaisesRegex(ValueError, "A<B.*A_B|A_B.*A<B"):
                    generate_drawings(workbook, absent_output, template, {"dxf"})
            self.assertFalse(absent_output.exists())
            self.assertEqual(library_hashes, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in library_hashes})

    def test_frame_input_path_is_protected_for_dxf_and_dwg_backup_but_pdf_only_is_allowed(self):
        if not FRAME_DXF.is_file():
            self.skipTest("frame example unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "input.xlsx"
            write_xlsx(workbook, [("S1", one_sheet_rows())])
            output = root / "output"
            output.mkdir()
            frame_copy = output / "S1.dxf"
            frame_copy.write_bytes(FRAME_DXF.read_bytes())
            original_hash = hashlib.sha256(frame_copy.read_bytes()).hexdigest()
            template = SiteTemplate.load(default_template_path()).with_frame_dwg(frame_copy)
            formats_to_check = [{"dxf"}]
            if (_converter_root() / "dwgwrite.exe").exists():
                formats_to_check.append({"dwg"})
            for formats in formats_to_check:
                with patch("material_drawing_generator.service.apply_submission_highlighting", side_effect=AssertionError("must not mark Excel")):
                    with self.assertRaisesRegex(ValueError, "输入图框"):
                        generate_drawings(workbook, output, template, formats)
                self.assertEqual(hashlib.sha256(frame_copy.read_bytes()).hexdigest(), original_hash)
                self.assertFalse((output / "S1.dwg").exists())
            with patch("material_drawing_generator.service.apply_submission_highlighting", return_value=HighlightResult(False)):
                result = generate_drawings(workbook, output, template, {"pdf"})
            self.assertTrue(Path(result.pdf_path).is_file())
            self.assertFalse((output / "S1.dwg").exists())
            self.assertTrue((output / "S1.dxf").is_file())
            self.assertEqual(hashlib.sha256(frame_copy.read_bytes()).hexdigest(), original_hash)

    def test_noncolliding_multisheet_material_pdf_dxf_outputs_are_complete(self):
        template = SiteTemplate.load(default_template_path())
        template.data["material_symbols"] = {"enabled": True}
        template.data["cad_output_scale"] = 100
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "safe.xlsx"
            write_xlsx(workbook, [("BeamA", one_sheet_rows("BCR295", "H-100")), ("BeamB", one_sheet_rows("SS400", "C-200"))])
            before = hashlib.sha256(workbook.read_bytes()).hexdigest()
            output = root / "safe-output"
            result = generate_drawings(workbook, output, template, {"pdf", "dxf"}, cad_output_scale=100)
            self.assertEqual({sheet.name for sheet in result.sheets}, {"BeamA", "BeamB"})
            report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
            self.assertEqual(len(report["pdf_pages"]), 2)
            self.assertEqual(report["cad_output_scale"], 100)
            self.assertEqual(set(report["material_symbols"]), {"BeamA", "BeamB"})
            for sheet, expected in zip(result.sheets, ("H-100", "C-200")):
                dxf_path = Path(sheet.files["dxf"])
                self.assertTrue(dxf_path.is_file())
                self.assertEqual(sheet.output_rows, 1)
                document = ezdxf.readfile(dxf_path)
                self.assertTrue(any(entity.dxf.text == expected for entity in document.modelspace().query("TEXT")))
                self.assertEqual(report["material_symbols"][sheet.name]["verified_rows"], 1)
            self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
