from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from material_drawing_generator.drawing import (
    CadAdapter,
    DrawingError,
    DrawingMetadata,
    _format_drawing_date,
    _load_ezdxf,
    _normalize_external_frame,
    _patch_dxf_extents,
    _repair_broken_material_dictionary,
    _repair_invalid_zero_handles,
    _safe_drawing_text,
    _second_smallest,
    _write_dwg_compatibility_dxf,
    draw_sheet,
    safe_filename,
)
from material_drawing_generator.excel_marker import apply_submission_highlighting
from material_drawing_generator.models import SectionRow, SheetData, group_section_rows
from material_drawing_generator.site_template_store import SiteTemplateStore
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.xlsx_reader import read_workbook, read_workbook_preview


WORKSPACE = Path(__file__).resolve().parents[1]
SAMPLE = (
    WORKSPACE
    / "outputs"
    / "01a05d12-c37e-7963-95ee-47146f559f0a"
    / "示例部材表_可变断面行数.xlsx"
)
if (WORKSPACE / "test_data" / SAMPLE.name).exists():
    SAMPLE = WORKSPACE / "test_data" / SAMPLE.name
DRAWING_LIST_SAMPLE = SAMPLE.with_name("示例部材表_含图纸列表.xlsx")
ACTUAL_WORKBOOK = WORKSPACE / "テスト用部材データ.xlsx"
ACTUAL_WORKBOOK_0904 = WORKSPACE / "テスト用部材データ0904.xlsx"


def drawing_list_template() -> SiteTemplate:
    base = SiteTemplate.load(default_template_path())
    data = copy.deepcopy(base.data)
    data["drawing_list"]["submission_date_cell"] = "B1"
    return SiteTemplate(base.path, data)


class GroupingTests(unittest.TestCase):
    def test_variable_group_sizes_and_source_order(self):
        rows = [
            SectionRow(2, "G2", "大梁", position="端部"),
            SectionRow(3, "G1", "大梁", position="全断面"),
            SectionRow(4, "G2", "大梁", position="中央"),
            SectionRow(5, "B1", "小梁", position="1"),
            SectionRow(6, "B1", "小梁", position="2"),
            SectionRow(7, "B1", "小梁", position="3"),
            SectionRow(8, "B1", "小梁", position="4"),
            SectionRow(9, "B1", "小梁", position="5"),
        ]
        groups = group_section_rows(rows, ["大梁", "小梁"])
        self.assertEqual([group.member_name for group in groups], ["G1", "G2", "B1"])
        self.assertEqual([len(group.rows) for group in groups], [1, 2, 5])
        self.assertEqual([row.position for row in groups[1].rows], ["端部", "中央"])

    def test_descending_sort_keeps_category_order(self):
        rows = [
            SectionRow(2, "G2", "大梁"),
            SectionRow(3, "G10", "大梁"),
            SectionRow(4, "B1", "小梁"),
        ]
        groups = group_section_rows(rows, ["大梁", "小梁"], descending=True)
        self.assertEqual([group.member_name for group in groups], ["G10", "G2", "B1"])

    def test_unknown_categories_keep_excel_encounter_order(self):
        rows = [
            SectionRow(2, "G1", "大梁"),
            SectionRow(3, "E1", "耐震梁"),
            SectionRow(4, "BR1", "ブレース"),
        ]
        groups = group_section_rows(rows, ["大梁", "小梁"])
        self.assertEqual([group.category for group in groups], ["大梁", "耐震梁", "ブレース"])

    def test_safe_filename(self):
        self.assertEqual(safe_filename('16F:梁/部材表?'), "16F_梁_部材表_")

    def test_zero_handle_repair_preserves_non_cp1252_dxf_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "frame.dxf"
            target.write_bytes(b"0\r\nSECTION\r\n5\r\n0\r\n999\r\n\x81\r\n0\r\nEOF\r\n")
            _repair_invalid_zero_handles(target)
            repaired = target.read_bytes()
            self.assertIn(b"5\r\n101\r\n", repaired)
            self.assertIn(b"999\r\n\x81\r\n", repaired)

    def test_broken_material_handles_are_recreated(self):
        document = _load_ezdxf().new("R2000")
        document.materials.object_dict._data["ByLayer"] = "BADHANDLE"
        _repair_broken_material_dictionary(document)
        for name in ("ByLayer", "ByBlock", "Global"):
            self.assertTrue(hasattr(document.materials.get(name), "dxf"))

    def test_extent_patch_preserves_non_cp1252_dxf_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "drawing.dxf"
            target.write_bytes(
                b"9\n$EXTMAX\n10\n1.0\n20\n2.0\n30\n0.0\n9\n$NEXT\n999\n\x81\n"
            )
            _patch_dxf_extents(target, 420.0, 297.0)
            repaired = target.read_bytes()
            self.assertIn(b"$EXTMAX\n10\n420.0\n20\n297.0\n30\n0.0", repaired)
            self.assertIn(b"999\n\x81\n", repaired)

    def test_dwg_compatibility_copy_keeps_geometry_without_extra_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.dxf"
            target_path = Path(directory) / "compatibility.dxf"
            source = _load_ezdxf().new("R2010")
            frame = source.blocks.new("FRAME")
            frame.add_line((0, 0), (420, 0))
            source.modelspace().add_blockref("FRAME", (0, 0))
            source.modelspace().add_text("図面名称")
            source.objects.add_xrecord()
            source.saveas(source_path)

            _write_dwg_compatibility_dxf(source_path, target_path)

            compatibility = _load_ezdxf().readfile(target_path)
            self.assertEqual(compatibility.dxfversion, "AC1015")
            self.assertEqual(
                [entity.dxftype() for entity in compatibility.modelspace()],
                ["INSERT", "TEXT"],
            )
            self.assertEqual(len(compatibility.blocks.get("FRAME").query("LINE")), 1)
            self.assertNotIn(
                "XRECORD",
                {entity.dxftype() for entity in compatibility.objects},
            )

    def test_external_frame_is_scaled_to_a3_without_distorting_circle(self):
        from ezdxf import bbox

        document = _load_ezdxf().new("R2010")
        modelspace = document.modelspace()
        modelspace.add_lwpolyline(
            [(0, 0), (168200, 0), (168200, 118800), (0, 118800)],
            close=True,
        )
        modelspace.add_line((0, 0), (1112379, 0))
        modelspace.add_circle((84100, 59400), 4000)

        self.assertTrue(_normalize_external_frame(document, 420.0, 297.0))
        self.assertEqual(len(modelspace), 2)
        bounds = bbox.extents(modelspace)
        self.assertLessEqual(bounds.extmax.x, 420.0 + 1e-6)
        self.assertLessEqual(bounds.extmax.y, 297.0 + 1e-6)
        circle = modelspace.query("CIRCLE").first
        self.assertIsNotNone(circle)
        assert circle is not None
        self.assertAlmostEqual(circle.dxf.radius, 4000 * (420 / 168200), places=6)

    def test_safe_drawing_text_preserves_engineering_mark(self):
        self.assertEqual(_safe_drawing_text("T🄱BH-1000"), "T🄱BH-1000")
        self.assertEqual(_second_smallest([0.7, 1.1, 1.5], 2.0), 1.1)

    def test_external_frame_placeholders_use_drawing_list_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = CadAdapter(Path(directory) / "metadata.dxf", 420.0, 297.0)
            modelspace = adapter.modelspace
            modelspace.add_text("図面名称", dxfattribs={"insert": (350, 16)})
            modelspace.add_text("図面番号", dxfattribs={"insert": (392, 25)})
            modelspace.add_text("R-X", dxfattribs={"insert": (346, 9)})
            modelspace.add_text("20xx/xx/xx", dxfattribs={"insert": (362, 12)})
            modelspace.add_text("20XX/XX/XX", dxfattribs={"insert": (408, 9)})
            metadata = DrawingMetadata(
                drawing_number="F-2",
                version="7",
                drawing_date="26.03.03",
                revision_dates=["25.03.14", "2025-06-02", "26.03.03"],
            )

            self.assertTrue(
                adapter.apply_external_metadata(
                    "2Fリスト",
                    metadata,
                    SiteTemplate.load(default_template_path()).frame_placeholders,
                )
            )
            texts = list(modelspace.query("TEXT"))
            values = [entity.dxf.text for entity in texts]
            self.assertIn("2Fリスト", values)
            self.assertIn("F-2", values)
            self.assertNotIn("図面番号", values)
            self.assertIn("R-7", values)
            self.assertIn("26.03.03", values)
            history = sorted(
                (
                    round(entity.dxf.insert.y, 3),
                    entity.dxf.text,
                )
                for entity in texts
                if entity.dxf.insert.x == 408
            )
            self.assertEqual(
                history,
                [(9.0, "25.03.14"), (14.0, "25.06.02"), (19.0, "26.03.03")],
            )
            self.assertEqual(_format_drawing_date("2025-6-2"), "25.06.02")

    def test_legacy_frame_replaces_split_version_and_uses_original_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = CadAdapter(Path(directory) / "legacy.dxf", 420.0, 297.0)
            modelspace = adapter.modelspace
            original = {
                "M5FL　部材リスト": (351.25, 15.1),
                "図面番号": (392.18, 25.3),
                "R-": (346.33, 9.46),
                "20xx/xx/xx": (362.36, 11.86),
                "20XX/XX/XX": (407.69, 9.11),
            }
            for text, point in original.items():
                modelspace.add_text(text, dxfattribs={"insert": point, "height": 0.91})
            suffix = modelspace.add_text("1", dxfattribs={"insert": (347.88, 9.46)})
            legend_number = modelspace.add_text("1", dxfattribs={"insert": (24.75, 22.4)})
            metadata = DrawingMetadata("F-6", "13", "26.02.18", ["24.06.19", "26.02.18"])
            template = SiteTemplate.load(default_template_path()).with_frame_dwg("legacy.dwg")
            draw_sheet(adapter, SheetData("3Fリスト", 1, [], []), template, metadata)

            texts = {entity.dxf.text: entity for entity in modelspace.query("TEXT")}
            for value, marker in (
                ("3Fリスト", "M5FL　部材リスト"),
                ("F-6", "図面番号"),
                ("R-13", "R-"),
                ("24.06.19", "20XX/XX/XX"),
            ):
                self.assertEqual(tuple(texts[value].dxf.insert)[:2], original[marker])
                self.assertNotIn(marker, texts)
            self.assertFalse(suffix.is_alive)
            self.assertTrue(legend_number.is_alive)
            self.assertEqual(len(modelspace.query("TEXT")), 7)
            self.assertNotIn("13", texts)  # No concept-frame version overlay.

    def test_missing_external_marker_stops_before_any_metadata_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = CadAdapter(Path(directory) / "missing.dxf", 420.0, 297.0)
            for text in ("図面名称", "図面番号", "R-X", "20xx/xx/xx"):
                adapter.modelspace.add_text(text)
            before = [entity.dxf.text for entity in adapter.modelspace.query("TEXT")]
            template = SiteTemplate.load(default_template_path()).with_frame_dwg("unknown.dwg")
            with self.assertRaisesRegex(DrawingError, "更新履历"):
                draw_sheet(
                    adapter, SheetData("3Fリスト", 1, [], []), template,
                    DrawingMetadata("F-6", "13", "26.02.18", ["26.02.18"]),
                )
            self.assertEqual(before, [entity.dxf.text for entity in adapter.modelspace.query("TEXT")])

    def test_placeholder_normalization_preserves_date_marker_case(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = CadAdapter(Path(directory) / "markers.dxf", 420.0, 297.0)
            title = adapter.modelspace.add_text(" 図面　名称 ")
            creation = adapter.modelspace.add_text("20xx/xx/xx")
            history = adapter.modelspace.add_text("20XX/XX/XX")
            self.assertIs(adapter._text_placeholder("図面名称"), title)
            self.assertIs(adapter._text_placeholder("20xx/xx/xx"), creation)
            self.assertIs(adapter._text_placeholder("20XX/XX/XX"), history)
            adapter.modelspace.add_text("図面名称")
            with self.assertRaisesRegex(DrawingError, "多处"):
                adapter._text_placeholder("図面名称")

    def test_cad_text_is_shrunk_inside_narrow_cell(self):
        from ezdxf import bbox

        with tempfile.TemporaryDirectory() as directory:
            adapter = CadAdapter(Path(directory) / "fit.dxf", 420.0, 297.0)
            adapter.text_box(
                10.0,
                10.0,
                16.0,
                14.0,
                "軸ブレース補強",
                "TEXT",
                1.5,
                0.65,
            )
            entity = adapter.modelspace.query("TEXT").last
            self.assertIsNotNone(entity)
            assert entity is not None
            bounds = bbox.extents([entity])
            self.assertLessEqual(bounds.extmax.x - bounds.extmin.x, 4.8 + 1e-6)
            self.assertLessEqual(bounds.extmax.y - bounds.extmin.y, 3.4 + 1e-6)

    def test_table_title_header_and_content_share_one_font_size(self):
        template = SiteTemplate.load(default_template_path())
        rows = [SectionRow(2, "G1", "大梁", position="中央", section="H-500")]
        sheet = SheetData(
            "2F",
            1,
            rows,
            group_section_rows(rows, template.category_order),
        )

        class CaptureAdapter:
            def __init__(self):
                self.texts: list[tuple[str, float]] = []

            def line(self, *args, **kwargs):
                pass

            def rect(self, *args, **kwargs):
                pass

            def circle(self, *args, **kwargs):
                pass

            def text_box(
                self,
                x1,
                y1,
                x2,
                y2,
                value,
                layer,
                size=2.5,
                min_size=1.1,
                align="center",
            ):
                self.texts.append((str(value), float(size)))

        adapter = CaptureAdapter()
        draw_sheet(adapter, sheet, template, DrawingMetadata())
        table_values = {"大梁部材リスト", "部件", "部位", "断面", "G1", "中央", "H-500"}
        sizes = [size for value, size in adapter.texts if value in table_values]
        self.assertGreaterEqual(len(sizes), 7)
        self.assertEqual(len(set(sizes)), 1)

    def test_unknown_categories_create_dynamic_table_areas(self):
        template = SiteTemplate.load(default_template_path())
        categories = ["大梁", "耐震梁", "ブレース"]
        tables = template.tables_for_categories(categories)
        self.assertEqual([table["category"] for table in tables], categories)
        self.assertEqual(len(tables), 3)
        self.assertEqual([table["x"] for table in tables], sorted(table["x"] for table in tables))
        last_table = tables[-1]
        last_right = float(last_table["x"]) + sum(
            float(column["width"]) for column in last_table["columns"]
        )
        self.assertLessEqual(last_right, 386.0 + 1e-8)

        rows = [
            SectionRow(2, "G1", "大梁", position="中央", section="H-500"),
            SectionRow(3, "E1", "耐震梁", position="全断面", section="H-300"),
            SectionRow(4, "BR1", "ブレース", position="全断面", section="L-100"),
        ]
        groups = group_section_rows(rows, template.category_order)
        sheet = SheetData("2F", 1, rows, groups)

        class NoOpAdapter:
            def line(self, *args, **kwargs):
                pass

            def rect(self, *args, **kwargs):
                pass

            def circle(self, *args, **kwargs):
                pass

            def text_box(self, *args, **kwargs):
                pass

        self.assertEqual(draw_sheet(NoOpAdapter(), sheet, template, DrawingMetadata()), 3)


class SiteTemplateStoreTests(unittest.TestCase):
    def test_named_template_save_and_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site_templates.json"
            store = SiteTemplateStore("zh", path=path)
            layout = copy.deepcopy(SiteTemplate.load(default_template_path()).data)
            ui = {
                "output_path": "C:/drawing-output",
                "frame_dwg_path": "C:/frames/site-a.dwg",
                "sort_descending": True,
                "output_pdf": True,
                "output_dxf": False,
                "output_dwg": True,
            }

            store.save("现场 A", layout, ui)
            self.assertTrue(store.exists("现场 A"))
            self.assertIn("现场 A", store.names())
            saved = store.get("现场 A")
            assert saved is not None
            self.assertEqual(saved["layout"]["frame_dwg"], "")
            self.assertEqual(saved["ui"]["frame_dwg_path"], "C:/frames/site-a.dwg")

            ui["output_path"] = "C:/updated-output"
            store.save("现场 A", layout, ui)
            reopened = SiteTemplateStore("zh", path=path).get("现场 A")
            assert reopened is not None
            self.assertEqual(reopened["ui"]["output_path"], "C:/updated-output")

            store.save("现场 10", layout, ui)
            store.save("现场 2", layout, ui)
            names = store.names()
            self.assertLess(names.index("现场 2"), names.index("现场 10"))


class ExcelIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(ACTUAL_WORKBOOK_0904.exists(), "0904 测试用 Excel 不在工作区")
    def test_compound_beam_size_header_maps_position_and_section(self):
        template = SiteTemplate.load(default_template_path())
        workbook = read_workbook(ACTUAL_WORKBOOK_0904, template)
        self.assertGreaterEqual(len(workbook.sheets), 1)
        first = workbook.sheets[0].source_rows[0]
        self.assertEqual(first.member_name, "2GX1")
        self.assertEqual(first.category, "大梁")
        self.assertEqual(first.position, "中央")
        self.assertEqual(first.section, "T🄱BH-1000X350X19X28")

    @unittest.skipUnless(ACTUAL_WORKBOOK.exists(), "正式测试用 Excel 不在工作区")
    def test_actual_workbook_preview_is_bounded_and_parses_two_row_header(self):
        template = SiteTemplate.load(default_template_path())
        preview = read_workbook_preview(ACTUAL_WORKBOOK, template)
        self.assertEqual(
            [sheet.name for sheet in preview.sheets],
            ["図面リスト", "2Fリスト", "3Fリスト"],
        )
        self.assertEqual([len(sheet.rows) for sheet in preview.sheets], [63, 165, 217])
        self.assertTrue(all(sheet.max_column <= 40 for sheet in preview.sheets))
        drawing_header = preview.sheets[0].rows[0][1]
        second_floor_header = preview.sheets[1].rows[0][1]
        third_floor_header = preview.sheets[2].rows[0][1]
        self.assertEqual(drawing_header[0], "梁伏図")
        self.assertIsInstance(drawing_header[22], str)
        self.assertEqual(second_floor_header[0], "梁マーク")
        self.assertEqual(second_floor_header[1], "部材種")
        self.assertEqual(second_floor_header[2], "梁サイズ")
        self.assertEqual(third_floor_header[0], "梁マーク")
        self.assertNotIn("ハリ", second_floor_header[0])
        self.assertIn((1, 2, 1, 3), preview.sheets[1].merged_ranges)
        self.assertIn((1, 1, 1, 2), preview.sheets[2].merged_ranges)
        drawing_sheet = preview.sheets[0]
        self.assertAlmostEqual(drawing_sheet.row_heights[1], 22.7)
        merged_title_style = drawing_sheet.cell_styles[(1, 1)]
        self.assertEqual(merged_title_style.horizontal, "center")
        self.assertEqual(merged_title_style.vertical, "center")
        self.assertTrue(merged_title_style.wrap_text)
        self.assertIsNotNone(preview.drawing_list)
        assert preview.drawing_list is not None
        self.assertEqual(preview.drawing_list.header_row, 2)
        self.assertEqual(preview.drawing_list.version_columns[:3], [("0", 2), ("1", 3), ("2", 4)])
        self.assertEqual(preview.drawing_list.records[0].latest_version, "18")

    def test_sample_workbook_reconciles_all_rows(self):
        template = SiteTemplate.load(default_template_path())
        workbook = read_workbook(SAMPLE, template)
        self.assertEqual([sheet.name for sheet in workbook.sheets], ["16F 梁部材表", "17F 梁部材表"])
        self.assertEqual([sheet.source_row_count for sheet in workbook.sheets], [17, 9])
        self.assertEqual(
            [[len(group.rows) for group in sheet.groups] for sheet in workbook.sheets],
            [[1, 2, 3, 4, 5, 2], [5, 1, 3]],
        )
        self.assertTrue(
            all(sheet.source_row_count == sheet.output_subrow_count for sheet in workbook.sheets)
        )

    def test_drawing_list_versions_and_submission_highlight(self):
        template = drawing_list_template()
        workbook = read_workbook(DRAWING_LIST_SAMPLE, template)
        self.assertEqual([sheet.name for sheet in workbook.sheets], ["16F 梁部材表", "17F 梁部材表"])
        self.assertIsNotNone(workbook.drawing_list)
        drawing_list = workbook.drawing_list
        assert drawing_list is not None
        record = drawing_list.record_for_sheet("16F 梁部材表")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.drawing_number, "A-016")
        self.assertEqual(record.latest_version, "1")
        self.assertEqual(record.latest_date, "2026-09-02")
        self.assertEqual(drawing_list.highlighted_cells(), {(4, 0), (4, 1), (4, 3)})

    def test_highlighting_is_saved_to_same_workbook(self):
        template = drawing_list_template()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "drawing-list.xlsx"
            shutil.copyfile(DRAWING_LIST_SAMPLE, target)
            workbook = read_workbook(target, template)
            result = apply_submission_highlighting(target, workbook.drawing_list)
            self.assertTrue(result.applied)
            self.assertEqual(result.highlighted_drawings, 1)
            preview = read_workbook_preview(target, template)
            self.assertIsNotNone(preview.drawing_list)
            assert preview.drawing_list is not None
            self.assertEqual(preview.drawing_list.highlighted_cells(), {(4, 0), (4, 1), (4, 3)})


if __name__ == "__main__":
    unittest.main()
