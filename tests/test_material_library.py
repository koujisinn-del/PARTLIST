import json
from pathlib import Path
import tempfile
import unittest

from material_drawing_generator.materials import MaterialLibrary, SymbolRule, material_lookup_key


class MaterialLibraryTests(unittest.TestCase):
    def test_reference_legend_grades_and_explicit_no_mark(self):
        library = MaterialLibrary.load()
        expected = {
            "SS400": ("", "", "none"), "STK400": ("", "", "none"),
            "SM490A": ("", "", "circle"), "STKR490": ("", "", "circle"),
            "BCR295": ("R", "", "square"), "SN400B": ("N", "B", "square"),
            "SN490B": ("N", "B", "circle"), "SN490C": ("N", "C", "circle"),
            "TMCP325B": ("T", "B", "circle"), "TMCP325C": ("T", "C", "circle"),
            "TMCP385B": ("T", "B", "square"), "TMCP385C": ("T", "C", "square"),
            "BCP325B": ("P", "B", "circle"), "BCP325C": ("P", "C", "circle"),
            "BCP385B": ("P", "B", "square"), "BCP385C": ("P", "C", "square"),
        }
        for grade, appearance in expected.items():
            with self.subTest(grade=grade):
                rule = library.resolve(grade)
                self.assertEqual((rule.prefix, rule.inner, rule.shape), appearance)
        self.assertTrue(library.resolve("SS400").is_unmarked)
        self.assertFalse(library.resolve("SM490A").is_unmarked)
        self.assertEqual(len(library.fingerprint), 64)

    def test_lookup_accepts_only_deliberate_normalization(self):
        library = MaterialLibrary.load()
        original = "　ｔｍｃｐ ３２５ Ｂ　"
        self.assertEqual(library.resolve(original).material, "TMCP325B")
        self.assertEqual(original, "　ｔｍｃｐ ３２５ Ｂ　")
        self.assertEqual(material_lookup_key("Ⓑ🄱🅱"), "Ⓑ🄱🅱")
        with self.assertRaisesRegex(ValueError, "未在基础库"):
            library.resolve("TMCP325Ⓑ")

    def test_unknown_and_empty_are_not_unmarked(self):
        library = MaterialLibrary.load()
        with self.assertRaisesRegex(ValueError, "材质为空"):
            library.resolve("　 ")
        with self.assertRaisesRegex(ValueError, "未在基础库"):
            library.resolve("SS490-UNKNOWN")

    def test_custom_addition_alias_fingerprint_and_reused_geometry(self):
        base = MaterialLibrary.load()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.json"
            path.write_text(json.dumps({"schema_version": 1, "symbols": [{
                "material": "CUSTOM1", "aliases": ["custom-one"],
                "prefix": "T", "inner": "B", "shape": "triangle",
            }]}), encoding="utf-8")
            custom = MaterialLibrary.load(custom_path=path)
            self.assertEqual(custom.resolve("CUSTOM-ONE").material, "CUSTOM1")
            self.assertEqual(len(custom.rules), len(base.rules) + 1)
            self.assertNotEqual(custom.fingerprint, base.fingerprint)
            self.assertEqual(custom.fingerprint, MaterialLibrary.load(custom_path=path).fingerprint)
        self.assertEqual(base.resolve("SM490A").block_name, base.resolve("STKR490").block_name)
        self.assertNotEqual(base.resolve("SN490B").block_name, base.resolve("SN400B").block_name)

    def test_custom_cannot_shadow_base_canonical_or_alias(self):
        cases = [
            {"material": "ｓｓ４００", "aliases": [], "shape": "circle"},
            {"material": "CUSTOM", "aliases": ["SS 400"], "shape": "circle"},
            {"material": "CUSTOM", "aliases": ["CUSTOM"], "shape": "circle"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.json"
            for record in cases:
                with self.subTest(record=record):
                    path.write_text(json.dumps({"schema_version": 1, "symbols": [record]}), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "不能覆盖"):
                        MaterialLibrary.load(custom_path=path)

    def test_invalid_geometry_and_ambiguous_no_mark_fail(self):
        for shape in ("triangle", "diamond", "pentagon", "hexagon", "star"):
            self.assertEqual(SymbolRule("CUSTOM", prefix="T", inner="B", shape=shape).shape, shape)
        with self.assertRaisesRegex(ValueError, "不支持的外框"):
            SymbolRule("CUSTOM", shape="unknown")
        with self.assertRaisesRegex(ValueError, "无记号"):
            SymbolRule("CUSTOM", prefix="T", shape="none")
        with self.assertRaisesRegex(ValueError, "英文字母"):
            SymbolRule("CUSTOM", inner="Ⓑ", shape="circle")


if __name__ == "__main__":
    unittest.main()
