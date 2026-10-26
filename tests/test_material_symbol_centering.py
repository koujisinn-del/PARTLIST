"""Verify built-in symbol letters share the section baseline and carry their frames."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ezdxf
from matplotlib.path import Path as MatplotlibPath
from ezdxf.addons import text2path
from ezdxf.npshapes import NumpyPath2d, to_matplotlib_path

from material_drawing_generator.drawing import (
    DrawingMetadata, _cjk_font_filename, _converter_root, convert_dxf_to_dwg, render_dxf,
)
from material_drawing_generator.material_drawing import (
    SymbolFactory, prepare_material_job, validate_material_geometry, verify_material_cells,
)
from material_drawing_generator.material_symbol_import import append_custom_rule, build_rule
from material_drawing_generator.materials import MaterialLibrary, SymbolRule
from material_drawing_generator.models import SectionRow, SheetData, WorkbookData, group_section_rows
from material_drawing_generator.template import SiteTemplate, default_template_path
from material_drawing_generator.text_pdf import TextBackend, TextPdfBook


def ink_bounds(entity):
    path = text2path.make_path_from_entity(entity)
    paths = [NumpyPath2d(part) for part in path.sub_paths()]
    bounds = to_matplotlib_path(paths, detect_holes=False).get_extents()
    return bounds.x0, bounds.y0, bounds.x1, bounds.y1


def frame_bounds(block):
    xs, ys = [], []
    for entity in block:
        if entity.dxftype() == "LINE":
            xs.extend((entity.dxf.start.x, entity.dxf.end.x))
            ys.extend((entity.dxf.start.y, entity.dxf.end.y))
        elif entity.dxftype() == "CIRCLE":
            center, radius = entity.dxf.center, entity.dxf.radius
            xs.extend((center.x - radius, center.x + radius))
            ys.extend((center.y - radius, center.y + radius))
    return min(xs), min(ys), max(xs), max(ys)


def assert_ink_bbox_inside_frame(testcase, block):
    text = next(entity for entity in block if entity.dxftype() == "TEXT")
    x0, y0, x1, y1 = ink_bounds(text)
    corners = [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
    circle = next((entity for entity in block if entity.dxftype() == "CIRCLE"), None)
    if circle is not None:
        center, radius = circle.dxf.center, circle.dxf.radius
        for x, y in corners:
            testcase.assertLess(( (x-center.x)**2 + (y-center.y)**2 )**0.5, radius - 1e-6)
        return
    vertices = [entity.dxf.start for entity in block if entity.dxftype() == "LINE"]
    points = [(point.x, point.y) for point in vertices]
    outline = MatplotlibPath(points + points[:1], closed=True)
    testcase.assertTrue(all(outline.contains_point(point, radius=-1e-6) for point in corners))


class MaterialSymbolCenteringTests(unittest.TestCase):
    def test_inner_visible_ink_is_centered_in_frame_while_text_baseline_stays_zero(self):
        font = _cjk_font_filename()
        for shape in ("circle", "square", "triangle", "diamond", "pentagon", "hexagon", "star"):
            for letter in ("N", "T", "B", "WWW"):
                with self.subTest(shape=shape, letter=letter):
                    factory = SymbolFactory(font)
                    rule = SymbolRule(f"{shape}-{letter}", inner=letter, shape=shape)
                    geometry = factory.geometry(rule)
                    doc = ezdxf.new("R2007")
                    doc.styles.new("PARTLIST", dxfattribs={"font": font})
                    name = factory.ensure_block(doc, rule, "PARTLIST")
                    block = doc.blocks.get(name)
                    text = next(entity for entity in block if entity.dxftype() == "TEXT")
                    x0, y0, x1, y1 = frame_bounds(block)
                    ix0, iy0, ix1, iy1 = ink_bounds(text)
                    self.assertAlmostEqual((ix0 + ix1) / 2, (x0 + x1) / 2, places=7)
                    if shape == "triangle" and letter == "WWW":
                        self.assertAlmostEqual(text.dxf.insert.y, 0.0, places=7)
                        self.assertGreater((y0 + y1) / 2, (iy0 + iy1) / 2)
                        vertices = [entity.dxf.start for entity in block if entity.dxftype() == "LINE"]
                        margins = []
                        for start, end in zip(vertices, vertices[1:] + vertices[:1]):
                            dx, dy = end.x - start.x, end.y - start.y
                            length = (dx * dx + dy * dy) ** 0.5
                            margins.extend(
                                (dx * (y - start.y) - dy * (x - start.x)) / length
                                for x, y in ((ix0, iy0), (ix0, iy1), (ix1, iy0), (ix1, iy1))
                            )
                        self.assertAlmostEqual(min(margins), 0.05, places=6)
                    else:
                        self.assertAlmostEqual((iy0 + iy1) / 2, (y0 + y1) / 2, places=7)
                        self.assertAlmostEqual(text.dxf.insert.y, 0.0, places=7)
                    self.assertGreaterEqual(ix0, x0 - 1e-8)
                    self.assertLessEqual(ix1, x1 + 1e-8)
                    self.assertGreaterEqual(iy0, y0 - 1e-8)
                    self.assertLessEqual(iy1, y1 + 1e-8)
                    assert_ink_bbox_inside_frame(self, block)
                    self.assertAlmostEqual(text.dxf.height, 1.0)
                    self.assertEqual(len(geometry.lines), len([e for e in block if e.dxftype() == "LINE"]))

    def test_prefix_stays_outside_the_frame_and_user_geometry_is_not_repositioned(self):
        font = _cjk_font_filename()
        factory = SymbolFactory(font)
        rule = SymbolRule("PREFIX", prefix="N", inner="B", shape="pentagon")
        geometry = factory.geometry(rule)
        prefix_position = geometry.texts[0][1:]
        lo, hi = factory._ink("N")
        self.assertAlmostEqual(prefix_position[0], -lo.x)
        self.assertAlmostEqual(prefix_position[1], 0.0)
        doc = ezdxf.new("R2007")
        doc.styles.new("PARTLIST", dxfattribs={"font": font})
        name = factory.ensure_block(doc, rule, "PARTLIST")
        block = doc.blocks.get(name)
        x0, _, _, _ = frame_bounds(block)
        prefix = next(entity for entity in block if entity.dxftype() == "TEXT" and entity.dxf.text == "N")
        self.assertLess(ink_bounds(prefix)[2], x0)
        self.assertAlmostEqual(prefix.dxf.insert.y, 0.0)

        imported = build_rule("USER-GEOMETRY", (), [
                                  ("LINE", 0.0, 0.0, 2.0, 0.0),
                                  ("TEXT", "T", 1.25, 0.3, 0.7, 0.0, 1.0),
                              ],
                              "text", font_filename=font)
        imported_geometry = SymbolFactory(font).geometry(imported)
        anchored_text = next(item for item in imported_geometry.primitives if item[0] == "TEXT")
        self.assertAlmostEqual(anchored_text[3], 0.0)
        legacy = SymbolRule("LEGACY-GEOMETRY", geometry=imported.geometry,
                            geometry_bounds=imported.geometry_bounds)
        self.assertEqual(SymbolFactory(font).geometry(legacy).primitives, imported.geometry)

    def _render_builtin(self, root):
        font = _cjk_font_filename()
        rule = SymbolRule("CENTERED-QA", ("CQ",), inner="B", shape="pentagon")
        custom = root / "custom.json"
        append_custom_rule(rule, custom)
        library = MaterialLibrary.load(custom_path=custom)
        row = SectionRow(2, "G1", "大梁", section="H-300X150X6.5X9", material="CQ")
        sheet = SheetData("CENTERED-QA", 1, [row], group_section_rows([row], ["大梁"]))
        workbook = WorkbookData("temporary.xlsx", [sheet])
        with patch("material_drawing_generator.material_drawing.MaterialLibrary.load", return_value=library):
            _loaded, plans = prepare_material_job(workbook, font)
        template = SiteTemplate.load(default_template_path())
        template.data["material_symbols"] = {"enabled": True}
        template.data.setdefault("dynamic_table_layout", {})["fixed_row_height"] = 4.0
        dxf_path = root / "centered-symbol.dxf"
        render_dxf(dxf_path, sheet, template, DrawingMetadata(), plans[sheet.name])
        return rule, dxf_path

    def test_all_default_material_texts_match_section_baseline_and_fit(self):
        font = _cjk_font_filename()
        library = MaterialLibrary.load()
        rows = [SectionRow(index + 2, f"G{index + 1}", "大梁", section="H-300X150X6.5X9",
                           material=rule.material) for index, rule in enumerate(library.rules)]
        sheet = SheetData("BASELINE-DEFAULTS", 1, rows, group_section_rows(rows, ["大梁"]))
        _loaded, plans = prepare_material_job(WorkbookData("temporary.xlsx", [sheet]), font)
        plan = plans[sheet.name]
        template = SiteTemplate.load(default_template_path())
        template.data["material_symbols"] = {"enabled": True}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "all-defaults.dxf"
            render_dxf(path, sheet, template, DrawingMetadata(), plan)
            document = ezdxf.readfile(path)
            verify_material_cells(document, plan)
            for cell in plan.cells:
                section = document.entitydb.get(cell["text_handle"])
                if not cell["block_handle"]:
                    continue
                reference = document.entitydb.get(cell["block_handle"])
                native_texts = [entity for entity in reference.virtual_entities()
                                if entity.dxftype() == "TEXT"]
                for entity in native_texts:
                    self.assertAlmostEqual(entity.dxf.insert.y, section.dxf.insert.y, places=6,
                                           msg=cell["material"])
                    self.assertAlmostEqual(entity.dxf.height, section.dxf.height,
                                           places=7, msg=cell["material"])

    def test_centered_text_remains_editable_in_pdf_dxf_and_dwg_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rule, dxf_path = self._render_builtin(root)
            drawing = ezdxf.readfile(dxf_path)
            insert = next(entity for entity in drawing.modelspace().query("INSERT") if entity.dxf.name == rule.block_name)
            symbol_text = next(
                entity for entity in drawing.blocks.get(rule.block_name).query("TEXT")
                if entity.dxf.text == "B"
            )
            self.assertAlmostEqual(symbol_text.dxf.height, 1.0)
            section = next(entity for entity in drawing.modelspace().query("TEXT")
                           if entity.dxf.text == "H-300X150X6.5X9")
            self.assertAlmostEqual(symbol_text.dxf.height * insert.dxf.yscale, section.dxf.height, places=7)
            self.assertAlmostEqual(
                symbol_text.dxf.insert.y * insert.dxf.yscale + insert.dxf.insert.y,
                section.dxf.insert.y,
                places=7,
            )

            pdf_path = root / "centered-symbol.pdf"
            pdf = TextPdfBook(pdf_path, 420, 297)
            emitted = []
            original = TextBackend.native_text

            def record(backend, text, *args, **kwargs):
                emitted.append(text)
                return original(backend, text, *args, **kwargs)

            with patch.object(TextBackend, "native_text", new=record):
                pdf.add_cad_page(drawing, "CENTERED-QA", expected_sections=[section.dxf.text])
                pdf.save()
            self.assertIn("B", emitted)
            self.assertIn(section.dxf.text, emitted)
            self.assertGreater(pdf_path.stat().st_size, 1000)

            dwgwrite = _converter_root() / "dwgwrite.exe"
            if not dwgwrite.is_file():
                self.skipTest("LibreDWG writer unavailable after DXF/PDF checks")
            dwg_path = root / "centered-symbol.dwg"
            convert_dxf_to_dwg(dxf_path, dwg_path)
            roundtrip = root / "roundtrip.dxf"
            result = subprocess.run(
                [str(_converter_root() / "dwg2dxf.exe"), "-y", "-o", str(roundtrip), str(dwg_path)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            before, after = ezdxf.readfile(dxf_path), ezdxf.readfile(roundtrip)
            validate_material_geometry(before, after)
            self.assertTrue(any(e.dxftype() == "TEXT" and e.dxf.text == "B"
                                for e in after.blocks.get(rule.block_name)))


if __name__ == "__main__":
    unittest.main()
