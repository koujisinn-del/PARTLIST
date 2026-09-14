"""Regression tests for the LT 2024 blank-text report of 2026-09-06."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import ezdxf

from material_drawing_generator.cad_integrity import (
    _style_signature, write_compatibility_dxf, validate_roundtrip, validate_dwg_resources,
)
from material_drawing_generator.drawing import CadAdapter, _converter_root, convert_dxf_to_dwg
from material_drawing_generator.text_pdf import TextPdfBook


class TextVisibilityTests(unittest.TestCase):
    @unittest.skipUnless((_converter_root() / "dwgwrite.exe").is_file(), "Optional approved DWG converter is not installed")
    def test_equal_alignment_points_survive_real_dwg_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            doc = ezdxf.new("R2007")
            for i, (h, v, anchor) in enumerate([
                (1, 2, (80, 130)), (0, 2, (90, 140)), (2, 2, (100, 150)),
                (1, 0, (110, 160)), (1, 2, (0, 0)), (0, 0, (120, 170)),
            ]):
                text = doc.modelspace().add_text(str(i), dxfattribs={
                    "height": 2, "insert": anchor, "halign": h, "valign": v,
                    "align_point": anchor,
                })
            block = doc.blocks.new("NESTED")
            block.add_text("日本語", dxfattribs={
                "insert": (20, 30), "align_point": (20, 30), "halign": 1, "valign": 2,
            })
            doc.modelspace().add_blockref("NESTED", (5, 5))
            source, output = root / "source.dxf", root / "result.dwg"
            doc.saveas(source)
            convert_dxf_to_dwg(source, output)
            reread = root / "reread.dxf"
            subprocess.run([str(_converter_root() / "dwg2dxf.exe"), "-y", "-o", str(reread), str(output)],
                           capture_output=True, check=True)
            result = ezdxf.readfile(reread)
            for original, actual in zip(doc.modelspace().query("TEXT"), result.modelspace().query("TEXT")):
                self.assertEqual(original.dxf.halign, actual.dxf.halign)
                self.assertEqual(original.dxf.valign, actual.dxf.valign)
                if original.dxf.halign or original.dxf.valign:
                    self.assertEqual(original.dxf.align_point, actual.dxf.align_point)
                else:
                    self.assertEqual(original.dxf.insert, actual.dxf.insert)
            self.assertEqual(result.blocks.get("NESTED").query("TEXT").first.dxf.align_point, (20, 30, 0))

    def test_alignment_style_and_visibility_corruption_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, compat, changed = [root / name for name in ("source.dxf", "compat.dxf", "changed.dxf")]
            adapter = CadAdapter(source, 420, 297)
            adapter.text_box(20, 120, 50, 127, "梁 A1", "TEXT")
            adapter.save()
            write_compatibility_dxf(source, compat)
            for mode in ("anchor", "style", "layer", "width", "hidden"):
                with self.subTest(mode=mode):
                    doc = ezdxf.readfile(compat)
                    text = doc.modelspace().query("TEXT").first
                    if mode == "anchor":
                        text.dxf.insert = (0, 0, 0)
                    elif mode == "style":
                        doc.styles.get(text.dxf.style).dxf.font = "txt.shx"
                    elif mode == "layer":
                        doc.layers.get("TEXT").off()
                    elif mode == "width":
                        text.dxf.width = 0.5
                    else:
                        text.dxf.invisible = 1
                    doc.saveas(changed)
                    with self.assertRaises(ValueError):
                        validate_roundtrip(compat, changed)

    def test_generated_text_has_explicit_baseline_and_identical_visual_bounds(self):
        from ezdxf import bbox
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, compat = root / "source.dxf", root / "compat.dxf"
            adapter = CadAdapter(source, 420, 297)
            for i, align in enumerate(("center", "left", "right")):
                adapter.text_box(20, 100 + i * 10, 90, 107 + i * 10, "梁 G1 中央", "TEXT", 1.2, align=align)
            adapter.save()
            expected = [bbox.extents([e]) for e in adapter.modelspace.query("TEXT")]
            write_compatibility_dxf(source, compat)
            for bounds, text in zip(expected, ezdxf.readfile(compat).modelspace().query("TEXT")):
                self.assertEqual((text.dxf.halign, text.dxf.valign), (0, 0))
                self.assertGreater(text.dxf.insert.x, 0)
                self.assertGreater(text.dxf.insert.y, 90)
                actual = bbox.extents([text])
                self.assertTrue(bounds.extmin.isclose(actual.extmin, abs_tol=1e-6))
                self.assertTrue(bounds.extmax.isclose(actual.extmax, abs_tol=1e-6))

    def test_acad_application_handle_and_binary_font_links_are_validated(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, compat = root / "a.dxf", root / "b.dxf"
            adapter = CadAdapter(source, 420, 297)
            adapter.text_box(20, 50, 90, 57, "日本語 中文", "TEXT")
            adapter.save()
            write_compatibility_dxf(source, compat)
            self.assertEqual(ezdxf.readfile(compat).appids.get("ACAD").dxf.handle, "12")
            raw = root / "objects.json"
            bad = {"OBJECTS": [
                {"object": "DICTIONARYWDFLT", "handle": [0, 1, 18]},
                {"object": "STYLE", "handle": [0, 1, 40], "eed": [{"handle": [5, 1, 18]}]},
            ]}
            raw.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "无效应用对象"):
                validate_dwg_resources(raw)

    def test_frame_font_metadata_survives_import_and_pdf_does_not_mutate_cad(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = ezdxf.new("R2007")
            original.styles.get("Standard").dxf.bigfont = "KELF1-K.shx"
            style = original.styles.new("MS UI Gothic", dxfattribs={"font": "MS UI Gothic.ttf"})
            style.set_xdata("ACAD", [(1000, "MS UI Gothic"), (1071, 0)])
            original.modelspace().add_text("図面名称", dxfattribs={"style": "MS UI Gothic", "insert": (20, 20)})
            # Make the default style used, too: Importer normally retains its own.
            original.modelspace().add_text("A1", dxfattribs={"insert": (30, 20)})
            frame = root / "frame.dxf"
            original.saveas(frame)
            source = root / "source.dxf"
            adapter = CadAdapter(source, 420, 297, frame)
            for name in ("Standard", "MS UI Gothic"):
                self.assertEqual(_style_signature(original.styles.get(name)), _style_signature(adapter.doc.styles.get(name)))
            before = [_style_signature(s) for s in adapter.doc.styles]
            book = TextPdfBook(root / "text.pdf", 420, 297)
            book.add_cad_page(adapter.doc, "test")
            book.save()
            self.assertEqual(before, [_style_signature(s) for s in adapter.doc.styles])
            adapter.save()
            compat = root / "compat.dxf"
            write_compatibility_dxf(source, compat)
            reread = ezdxf.readfile(compat)
            for name in ("Standard", "MS UI Gothic"):
                self.assertEqual(_style_signature(original.styles.get(name)), _style_signature(reread.styles.get(name)))

    def test_fit_and_aligned_text_keep_both_meaningful_points(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            doc = ezdxf.new("R2007")
            for h in (3, 5):
                doc.modelspace().add_text("ABC", dxfattribs={
                    "insert": (20, 30), "align_point": (60, 30), "halign": h,
                })
            source, compat = root / "a.dxf", root / "b.dxf"
            doc.saveas(source)
            write_compatibility_dxf(source, compat)
            for text in ezdxf.readfile(compat).modelspace().query("TEXT"):
                self.assertEqual(text.dxf.insert, (20, 30, 0))
                self.assertEqual(text.dxf.align_point, (60, 30, 0))


if __name__ == "__main__":
    unittest.main()
