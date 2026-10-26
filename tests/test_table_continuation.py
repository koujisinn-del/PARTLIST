from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from material_drawing_generator.drawing import DrawingMetadata, DrawingError, _prepare_tables, draw_sheet
from material_drawing_generator.models import SectionRow, SheetData, WorkbookData, group_section_rows
from material_drawing_generator.template import SiteTemplate, default_template_path


class TableContinuationTests(unittest.TestCase):
    def setUp(self):
        self.template = SiteTemplate.load(default_template_path())

    def make_sheet(self, groups):
        rows = []
        source_row = 2
        for name, count, category in groups:
            for index in range(count):
                rows.append(SectionRow(source_row, name, category, position=f"P{index}", section=f"H-{source_row}"))
                source_row += 1
        return SheetData("2F", 1, rows, group_section_rows(rows, self.template.category_order))

    @staticmethod
    def rows_in(columns):
        return [row for _table, groups, _size in columns for group in groups for row in group.rows]

    def test_group_boundaries_are_kept_when_continuing(self):
        sheet = self.make_sheet([("G1", 50, "大梁"), ("g2", 40, "大梁"), ("G3", 20, "大梁")])
        columns = _prepare_tables(sheet, self.template)
        self.assertEqual(len(columns), 2)
        first, second = columns
        self.assertEqual(first[0]["title"], "大梁部材リスト")
        self.assertEqual(second[0]["title"], "大梁部材リスト (2)")
        self.assertEqual([group.member_name for group in first[1]], ["G1"])
        self.assertEqual([(g.member_name, len(g.rows)) for g in second[1]], [("g2", 40), ("G3", 20)])
        self.assertEqual([row.source_row for row in self.rows_in(columns)], [row.source_row for row in sheet.source_rows])
        for table, _groups, _size in columns:
            self.assertEqual([column["label"] for column in table["columns"]], [column["label"] for column in first[0]["columns"]])
            self.assertEqual(table["row_height"], 2.5)

    def test_single_oversized_group_splits_in_order_and_repeats_exact_member_name(self):
        sheet = self.make_sheet([("BeamAb", 110, "大梁")])
        columns = _prepare_tables(sheet, self.template)
        self.assertGreaterEqual(len(columns), 2)
        fragments = [group for _table, groups, _size in columns for group in groups]
        self.assertTrue(all(group.member_name == "BeamAb" for group in fragments))
        self.assertEqual([row.source_row for row in self.rows_in(columns)], [row.source_row for row in sheet.source_rows])
        self.assertEqual(sum(len(group.rows) for group in fragments), 110)
        self.assertEqual(columns[1][0]["title"], "大梁部材リスト (2)")

    def test_note_area_is_recomputed_at_each_continuation_position(self):
        sheet = self.make_sheet([("G1", 100, "大梁")])
        layout = self.template.data["dynamic_table_layout"]
        layout["left"], layout["right"] = 300.0, 500.0
        columns = _prepare_tables(sheet, self.template)
        self.assertGreaterEqual(len(columns), 2)
        self.assertTrue(all(table["x"] < 386 and table["x"] + sum(c["width"] for c in table["columns"]) > 310 for table, _, _ in columns[:2]))
        self.assertEqual([table["max_height"] for table, _, _ in columns[:2]], [143.0, 143.0])
        self.assertEqual([len(group.rows) for group in columns[0][1]], [56])

    def test_right_of_note_area_uses_full_vertical_space(self):
        sheet = self.make_sheet([("G1", 100, "大梁")])
        layout = self.template.data["dynamic_table_layout"]
        layout["left"], layout["right"] = 250.0, 300.0
        columns = _prepare_tables(sheet, self.template)
        self.assertEqual(columns[0][0]["max_height"], 214.0)
        self.assertEqual(len(columns[0][1][0].rows), 84)

    def test_horizontal_shortage_and_zero_row_capacity_fail_clearly(self):
        sheet = self.make_sheet([("G1", 100, "大梁")])
        reference = _prepare_tables(sheet, SiteTemplate.load(default_template_path()))
        fixed_width = sum(column["width"] for column in reference[0][0]["columns"])
        narrow = SiteTemplate.load(default_template_path())
        narrow.data["dynamic_table_layout"]["right"] = 16.0 + fixed_width + 0.5
        with self.assertRaisesRegex(DrawingError, "续表需要更多横向空间"):
            _prepare_tables(sheet, narrow)

        no_rows = SiteTemplate.load(default_template_path())
        layout = no_rows.data["dynamic_table_layout"]
        layout["top"], layout["bottom"] = 277.0, 272.5
        with self.assertRaisesRegex(DrawingError, "没有可用数据行空间"):
            _prepare_tables(sheet, no_rows)

    def test_service_preflights_non_material_continuation_before_excel_marking(self):
        from material_drawing_generator.service import generate_drawings
        sheet = self.make_sheet([("G1", 100, "大梁")])
        reference = _prepare_tables(sheet, SiteTemplate.load(default_template_path()))
        fixed_width = sum(column["width"] for column in reference[0][0]["columns"])
        narrow = SiteTemplate.load(default_template_path())
        narrow.data["dynamic_table_layout"]["right"] = 16.0 + fixed_width + 0.5
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch("material_drawing_generator.service.read_workbook", return_value=WorkbookData("synthetic.xlsx", [sheet])), patch(
                "material_drawing_generator.service.apply_submission_highlighting",
                side_effect=AssertionError("Excel marker must not run before layout validation"),
            ):
                with self.assertRaisesRegex(DrawingError, "续表需要更多横向空间"):
                    generate_drawings("synthetic.xlsx", output, narrow, {"dxf"})
            self.assertFalse(output.exists())

    def test_actual_draw_has_no_lost_rows_and_keeps_groups(self):
        sheet = self.make_sheet([("G1", 50, "大梁"), ("G2", 50, "大梁")])

        class Capture:
            def __init__(self):
                self.texts = []
                self.lines = []
            def line(self, *args, **kwargs): self.lines.append(args)
            def rect(self, *args, **kwargs): pass
            def circle(self, *args, **kwargs): pass
            def text_box(self, x1, y1, x2, y2, value, layer, size=2.5, *args):
                if str(value) in {"G1", "G2", "H-2", "H-101", "大梁部材リスト", "大梁部材リスト (2)"}:
                    self.texts.append((str(value), size, y1, y2))

        adapter = Capture()
        self.assertEqual(draw_sheet(adapter, sheet, self.template, DrawingMetadata()), 100)
        values = [value for value, *_ in adapter.texts]
        self.assertIn("大梁部材リスト", values)
        self.assertIn("大梁部材リスト (2)", values)
        self.assertEqual(values.count("G1"), 1)
        self.assertEqual(values.count("G2"), 1)
        self.assertEqual({size for _value, size, *_ in adapter.texts}, {1.0})


if __name__ == "__main__":
    unittest.main()
