from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "图框概念样稿"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "_python_deps"))

import ezdxf  # noqa: E402

SHEET_W = 420.0
SHEET_H = 297.0


def rect_segments(x1: float, y1: float, x2: float, y2: float):
    return [
        (x1, y1, x2, y1),
        (x2, y1, x2, y2),
        (x2, y2, x1, y2),
        (x1, y2, x1, y1),
    ]


class DxfWriter:
    def __init__(self):
        self.entities: list[str] = []

    @staticmethod
    def pair(code: int, value) -> str:
        return f"{code}\n{value}\n"

    def line(self, x1, y1, x2, y2, layer="BORDER"):
        self.entities.append(
            self.pair(0, "LINE")
            + self.pair(8, layer)
            + self.pair(10, f"{x1:.4f}")
            + self.pair(20, f"{y1:.4f}")
            + self.pair(30, "0.0")
            + self.pair(11, f"{x2:.4f}")
            + self.pair(21, f"{y2:.4f}")
            + self.pair(31, "0.0")
        )

    def rect(self, x1, y1, x2, y2, layer="BORDER"):
        for segment in rect_segments(x1, y1, x2, y2):
            self.line(*segment, layer=layer)

    def text(self, x, y, value, height=2.5, layer="TEXT", rotation=0.0):
        # ASCII-only labels keep this CAD sample portable across codepages.
        self.entities.append(
            self.pair(0, "TEXT")
            + self.pair(8, layer)
            + self.pair(10, f"{x:.4f}")
            + self.pair(20, f"{y:.4f}")
            + self.pair(30, "0.0")
            + self.pair(40, f"{height:.4f}")
            + self.pair(1, value)
            + self.pair(50, f"{rotation:.4f}")
            + self.pair(7, "STANDARD")
        )

    def save(self, path: Path):
        p = self.pair
        header = (
            p(0, "SECTION")
            + p(2, "HEADER")
            + p(9, "$ACADVER")
            + p(1, "AC1009")
            + p(9, "$INSUNITS")
            + p(70, 4)
            + p(9, "$MEASUREMENT")
            + p(70, 1)
            + p(9, "$EXTMIN")
            + p(10, "0.0")
            + p(20, "0.0")
            + p(30, "0.0")
            + p(9, "$EXTMAX")
            + p(10, f"{SHEET_W:.4f}")
            + p(20, f"{SHEET_H:.4f}")
            + p(30, "0.0")
            + p(0, "ENDSEC")
        )
        layers = [
            ("ALIGN_A3", 8),
            ("BORDER", 7),
            ("FIXED_INFO", 7),
            ("TABLE_OOBARI", 3),
            ("TABLE_KOBARI", 4),
            ("REVISION", 1),
            ("NOTE_INSERT", 6),
            ("TEXT", 7),
        ]
        tables = p(0, "SECTION") + p(2, "TABLES")
        tables += p(0, "TABLE") + p(2, "LTYPE") + p(70, 1)
        tables += (
            p(0, "LTYPE")
            + p(2, "CONTINUOUS")
            + p(70, 0)
            + p(3, "Solid line")
            + p(72, 65)
            + p(73, 0)
            + p(40, "0.0")
        )
        tables += p(0, "ENDTAB")
        tables += p(0, "TABLE") + p(2, "LAYER") + p(70, len(layers))
        for name, color in layers:
            tables += (
                p(0, "LAYER")
                + p(2, name)
                + p(70, 0)
                + p(62, color)
                + p(6, "CONTINUOUS")
            )
        tables += p(0, "ENDTAB")
        tables += p(0, "TABLE") + p(2, "STYLE") + p(70, 1)
        tables += (
            p(0, "STYLE")
            + p(2, "STANDARD")
            + p(70, 0)
            + p(40, "0.0")
            + p(41, "1.0")
            + p(50, "0.0")
            + p(71, 0)
            + p(42, "2.5")
            + p(3, "txt")
            + p(4, "")
        )
        tables += p(0, "ENDTAB") + p(0, "ENDSEC")
        entities = p(0, "SECTION") + p(2, "ENTITIES") + "".join(self.entities) + p(0, "ENDSEC")
        path.write_text(header + tables + entities + p(0, "EOF"), encoding="ascii")


class EzdxfWriter:
    def __init__(self):
        # A clean R2000 document provides explicit model/paper-space ownership.
        self.doc = ezdxf.new("R2000", setup=False)
        self.doc.units = ezdxf.units.MM
        self.doc.header["$EXTMIN"] = (0.0, 0.0, 0.0)
        self.doc.header["$EXTMAX"] = (SHEET_W, SHEET_H, 0.0)
        self.doc.header["$LIMMIN"] = (0.0, 0.0)
        self.doc.header["$LIMMAX"] = (SHEET_W, SHEET_H)
        self.doc.header["$PEXTMIN"] = (0.0, 0.0, 0.0)
        self.doc.header["$PEXTMAX"] = (SHEET_W, SHEET_H, 0.0)
        self.doc.header["$PLIMMIN"] = (0.0, 0.0)
        self.doc.header["$PLIMMAX"] = (SHEET_W, SHEET_H)
        layer_colors = {
            "ALIGN_A3": 8,
            "BORDER": 7,
            "FIXED_INFO": 7,
            "TABLE_OOBARI": 3,
            "TABLE_KOBARI": 4,
            "REVISION": 1,
            "NOTE_INSERT": 6,
            "TEXT": 7,
        }
        for name, color in layer_colors.items():
            if name not in self.doc.layers:
                self.doc.layers.add(name, color=color)
        self.modelspace = self.doc.modelspace()

    def line(self, x1, y1, x2, y2, layer="BORDER"):
        self.modelspace.add_line(
            (float(x1), float(y1)),
            (float(x2), float(y2)),
            dxfattribs={"layer": layer},
        )

    def rect(self, x1, y1, x2, y2, layer="BORDER"):
        for segment in rect_segments(x1, y1, x2, y2):
            self.line(*segment, layer=layer)

    def text(self, x, y, value, height=2.5, layer="TEXT", rotation=0.0):
        self.modelspace.add_text(
            value,
            dxfattribs={
                "layer": layer,
                "insert": (float(x), float(y)),
                "height": float(height),
                "rotation": float(rotation),
            },
        )

    def save(self, path: Path):
        self.doc.saveas(path)
        # ezdxf intentionally leaves drawing extents at sentinel values because
        # CAD applications normally recalculate them on open.  LibreDWG's SVG
        # verifier uses the stored values, so write the known A3 extents here.
        lines = path.read_text(encoding="cp1252").splitlines()

        def set_point(name: str, values: tuple[float, ...]):
            start = lines.index(name)
            cursor = start + 1
            replacements = {10: values[0], 20: values[1]}
            if len(values) == 3:
                replacements[30] = values[2]
            while cursor + 1 < len(lines):
                code_text = lines[cursor].strip()
                if code_text == "9":
                    break
                try:
                    code = int(code_text)
                except ValueError:
                    cursor += 1
                    continue
                if code in replacements:
                    lines[cursor + 1] = str(float(replacements[code]))
                cursor += 2

        set_point("$EXTMIN", (0.0, 0.0, 0.0))
        set_point("$EXTMAX", (SHEET_W, SHEET_H, 0.0))
        set_point("$PEXTMIN", (0.0, 0.0, 0.0))
        set_point("$PEXTMAX", (SHEET_W, SHEET_H, 0.0))
        path.write_text("\n".join(lines) + "\n", encoding="cp1252")


def build_dxf():
    dxf = EzdxfWriter()

    # A3 reference frame: origin is the lower-left paper corner, units are millimetres.
    dxf.rect(0, 0, 420, 297, "ALIGN_A3")
    dxf.rect(10, 10, 410, 287, "BORDER")

    # Main regions: bottom information strip and right revision strip.
    dxf.line(10, 55, 392, 55, "FIXED_INFO")
    dxf.line(392, 10, 392, 287, "REVISION")
    dxf.line(397, 55, 397, 287, "REVISION")

    # 58 revision-date cells, each 4 mm high.
    for idx in range(59):
        y = 55 + idx * 4
        dxf.line(392, y, 410, y, "REVISION")
    dxf.text(393.0, 57.0, "01", 1.7, "REVISION")
    dxf.text(398.2, 57.0, "2026/01/01", 1.7, "REVISION")
    dxf.text(393.0, 61.0, "02", 1.7, "REVISION")
    dxf.text(398.2, 61.0, "2026/02/02", 1.7, "REVISION")

    # Bottom fixed information/title block.
    dxf.line(155, 10, 155, 55, "FIXED_INFO")
    dxf.line(250, 10, 250, 55, "FIXED_INFO")
    dxf.line(330, 10, 330, 55, "FIXED_INFO")
    dxf.line(10, 30, 392, 30, "FIXED_INFO")
    dxf.line(250, 42, 392, 42, "FIXED_INFO")
    dxf.line(360, 10, 360, 30, "FIXED_INFO")
    dxf.text(14, 48, "LEGEND / SYMBOL NOTES", 3.0, "TEXT")
    dxf.text(159, 48, "COMPANY / PROJECT INFORMATION", 3.0, "TEXT")
    dxf.text(254, 48, "DRAWING TITLE", 3.0, "TEXT")
    dxf.text(254, 34, "SHEET NAME (FROM EXCEL)", 2.5, "TEXT")
    dxf.text(254, 24, "SITE:", 2.5, "TEXT")
    dxf.text(333, 24, "VERSION:", 2.5, "TEXT")
    dxf.text(362, 24, "DATE:", 2.5, "TEXT")
    dxf.text(14, 24, "SYMBOL A = ...    SYMBOL B = ...", 2.4, "TEXT")
    dxf.text(159, 24, "COMPANY / DESIGNER / CHECKER", 2.4, "TEXT")

    # Main table A: sample output groups have one merged member-name cell over three detail rows.
    x1, x2, y1, y2 = 16, 190, 63, 277
    dxf.rect(x1, y1, x2, y2, "TABLE_OOBARI")
    columns = [16, 42, 60, 116, 140, 164, 190]
    for x in columns[1:-1]:
        dxf.line(x, y1, x, y2, "TABLE_OOBARI")
    dxf.line(x1, 267, x2, 267, "TABLE_OOBARI")
    dxf.text(18, 270, "MEMBER", 2.5, "TEXT")
    dxf.text(44, 270, "POS", 2.5, "TEXT")
    dxf.text(62, 270, "SECTION", 2.5, "TEXT")
    dxf.text(118, 270, "MAT.", 2.5, "TEXT")
    dxf.text(142, 270, "JOINT", 2.5, "TEXT")
    dxf.text(166, 270, "REMARK", 2.5, "TEXT")
    row_h = 6
    y = 267
    group = 0
    while y - 3 * row_h >= y1:
        group += 1
        for sub in (1, 2):
            yy = y - sub * row_h
            dxf.line(columns[1], yy, x2, yy, "TABLE_OOBARI")
        y -= 3 * row_h
        dxf.line(x1, y, x2, y, "TABLE_OOBARI")
    dxf.text(18, 256, "G1", 3.0, "TEXT")
    dxf.text(44, 262, "L", 2.3, "TEXT")
    dxf.text(44, 256, "C", 2.3, "TEXT")
    dxf.text(44, 250, "R", 2.3, "TEXT")
    dxf.text(62, 262, "H-700x300...", 2.2, "TEXT")
    dxf.text(62, 256, "H-800x300...", 2.2, "TEXT")
    dxf.text(62, 250, "H-700x300...", 2.2, "TEXT")
    dxf.text(16, 280.5, "TABLE A / OOBARI (ROW + COLUMN SIZE EDITABLE)", 2.8, "TEXT")

    # Main table B, separately configurable from table A.
    x1b, x2b, y1b, y2b = 198, 302, 92, 277
    dxf.rect(x1b, y1b, x2b, y2b, "TABLE_KOBARI")
    cols_b = [198, 220, 237, 271, 287, 302]
    for x in cols_b[1:-1]:
        dxf.line(x, y1b, x, y2b, "TABLE_KOBARI")
    dxf.line(x1b, 267, x2b, 267, "TABLE_KOBARI")
    dxf.text(200, 270, "MEMBER", 2.3, "TEXT")
    dxf.text(222, 270, "POS", 2.3, "TEXT")
    dxf.text(239, 270, "SECTION", 2.3, "TEXT")
    dxf.text(273, 270, "MAT", 2.3, "TEXT")
    dxf.text(289, 270, "JNT", 2.3, "TEXT")
    row_h_b = 5.5
    y = 267
    while y - 3 * row_h_b >= y1b:
        for sub in (1, 2):
            yy = y - sub * row_h_b
            dxf.line(cols_b[1], yy, x2b, yy, "TABLE_KOBARI")
        y -= 3 * row_h_b
        dxf.line(x1b, y, x2b, y, "TABLE_KOBARI")
    dxf.text(200, 256, "B1", 2.7, "TEXT")
    dxf.text(222, 262, "L", 2.1, "TEXT")
    dxf.text(222, 256, "C", 2.1, "TEXT")
    dxf.text(222, 250, "R", 2.1, "TEXT")
    dxf.text(198, 280.5, "TABLE B / KOBARI (INDEPENDENT SETTINGS)", 2.8, "TEXT")

    # Supplemental DWG/note insertion zone.
    nx1, ny1, nx2, ny2 = 310, 63, 386, 128
    dxf.rect(nx1, ny1, nx2, ny2, "NOTE_INSERT")
    for offset in range(-50, 90, 8):
        x_start = max(nx1, nx1 + offset)
        y_start = ny1 + max(0, -offset)
        x_end = min(nx2, nx1 + offset + (ny2 - ny1))
        y_end = ny1 + min(ny2 - ny1, ny2 - ny1 + offset)
        if x_start < x_end and y_start < y_end:
            dxf.line(x_start, y_start, x_end, y_end, "NOTE_INSERT")
    dxf.text(312, 121, "SUPPLEMENT NOTE / DETAIL", 2.8, "TEXT")
    dxf.text(312, 116, "INSERT EXTERNAL DWG BY A3 ORIGIN", 2.2, "TEXT")

    # Coordinates/alignment reminder placed in unused main space.
    dxf.text(310, 276, "A3: 420 x 297 mm", 3.0, "TEXT")
    dxf.text(310, 270, "ORIGIN: LOWER-LEFT (0,0)", 2.5, "TEXT")
    dxf.text(310, 264, "NOTE DWG USES SAME A3 FRAME", 2.5, "TEXT")

    dxf_path = OUT / "A3_部材表图框_概念样稿.dxf"
    dxf.save(dxf_path)
    return dxf_path


def build_svg():
    def sy(y):
        return SHEET_H - y

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1260" height="891" viewBox="0 0 420 297">',
        '<rect width="420" height="297" fill="#f4f6f8"/>',
        '<rect x="0" y="0" width="420" height="297" fill="white" stroke="#9aa3ad" stroke-width="0.5"/>',
        '<style>text{font-family:"Microsoft YaHei","Yu Gothic",sans-serif} .thin{fill:none;stroke:#17202a;stroke-width:.35} .fixed{fill:none;stroke:#17202a;stroke-width:.55} .blue{fill:#e9f5ff;fill-opacity:.55;stroke:#1677b8;stroke-width:.5} .cyan{fill:#e9fbfb;fill-opacity:.55;stroke:#168a93;stroke-width:.5} .rev{fill:#fff3e6;stroke:#d56700;stroke-width:.4} .note{fill:#f7ecff;fill-opacity:.8;stroke:#8e44ad;stroke-width:.55}</style>',
    ]

    def rect(x1, y1, x2, y2, cls="thin"):
        parts.append(f'<rect class="{cls}" x="{x1}" y="{sy(y2)}" width="{x2-x1}" height="{y2-y1}"/>')

    def line(x1, y1, x2, y2, cls="thin"):
        parts.append(f'<line class="{cls}" x1="{x1}" y1="{sy(y1)}" x2="{x2}" y2="{sy(y2)}"/>')

    def text(x, y, value, size=3, color="#17202a", weight="400"):
        parts.append(f'<text x="{x}" y="{sy(y)}" font-size="{size}" fill="{color}" font-weight="{weight}">{escape(value)}</text>')

    rect(0, 0, 420, 297, "thin")
    rect(10, 10, 410, 287, "fixed")
    line(10, 55, 392, 55, "fixed")
    line(392, 10, 392, 287, "fixed")

    # Revision region.
    rect(392, 55, 410, 287, "rev")
    line(397, 55, 397, 287)
    for idx in range(59):
        line(392, 55 + idx * 4, 410, 55 + idx * 4)
    text(393, 58, "1", 1.7, "#a84300")
    text(398, 58, "2026/01/01", 1.5, "#a84300")
    text(393, 62, "2", 1.7, "#a84300")
    text(398, 62, "2026/02/02", 1.5, "#a84300")
    text(393, 283, "更新履历", 2.2, "#a84300", "700")

    # Bottom fixed info block.
    for x in (155, 250, 330):
        line(x, 10, x, 55, "fixed")
    line(360, 10, 360, 30, "fixed")
    line(10, 30, 392, 30, "fixed")
    line(250, 42, 392, 42, "fixed")
    text(14, 49, "图例 / 记号说明", 3.2, "#17202a", "700")
    text(14, 38, "○B：特殊符号示例　　継手符号：……", 2.5)
    text(14, 23, "固定注记、材料说明及一般事项", 2.5)
    text(159, 49, "公司 / 现场信息", 3.2, "#17202a", "700")
    text(159, 38, "现场名称・公司名称・担当者・检查者", 2.4)
    text(159, 23, "可设置为现场模板固定内容", 2.4)
    text(254, 49, "图纸名称", 3.2, "#17202a", "700")
    text(254, 35, "从 Excel Sheet 名称读取", 2.5)
    text(254, 23, "现场", 2.4)
    text(333, 23, "版本", 2.4)
    text(362, 23, "日期", 2.4)

    # Table A.
    rect(16, 63, 190, 277, "blue")
    cols = [16, 42, 60, 116, 140, 164, 190]
    for x in cols[1:-1]:
        line(x, 63, x, 277)
    line(16, 267, 190, 267)
    headers = [(18, "部件"), (44, "部位"), (62, "断面"), (118, "材质"), (142, "接合"), (166, "备注")]
    for x, label in headers:
        text(x, 270, label, 2.5, "#075985", "700")
    y = 267
    while y - 18 >= 63:
        line(42, y - 6, 190, y - 6)
        line(42, y - 12, 190, y - 12)
        y -= 18
        line(16, y, 190, y)
    text(18, 256, "G1", 3, "#075985", "700")
    for ypos, pos, sec in [(262, "左", "H-700×300…"), (256, "中", "H-800×300…"), (250, "右", "H-700×300…")]:
        text(45, ypos, pos, 2.4)
        text(62, ypos, sec, 2.2)
    text(16, 281, "大梁表：行高、列宽独立设置", 3, "#075985", "700")

    # Table B.
    rect(198, 92, 302, 277, "cyan")
    cols_b = [198, 220, 237, 271, 287, 302]
    for x in cols_b[1:-1]:
        line(x, 92, x, 277)
    line(198, 267, 302, 267)
    for x, label in [(200, "部件"), (222, "部位"), (239, "断面"), (273, "材质"), (289, "接合")]:
        text(x, 270, label, 2.2, "#09686f", "700")
    y = 267
    while y - 16.5 >= 92:
        line(220, y - 5.5, 302, y - 5.5)
        line(220, y - 11, 302, y - 11)
        y -= 16.5
        line(198, y, 302, y)
    text(200, 256, "B1", 2.8, "#09686f", "700")
    text(198, 281, "小梁表：独立于大梁表调整", 3, "#09686f", "700")

    # Supplement note insertion zone.
    rect(310, 63, 386, 128, "note")
    for offset in range(-50, 90, 8):
        x_start = max(310, 310 + offset)
        y_start = 63 + max(0, -offset)
        x_end = min(386, 310 + offset + 65)
        y_end = 63 + min(65, 65 + offset)
        if x_start < x_end and y_start < y_end:
            line(x_start, y_start, x_end, y_end, "note")
    text(312, 121, "楼层绑定注记 / 详图插入区", 3, "#6c3483", "700")
    text(312, 115, "外部 DWG 使用同一 A3 原点定位", 2.3, "#6c3483")

    text(310, 276, "A3：420 × 297 mm", 3, "#34495e", "700")
    text(310, 269, "统一原点：纸张左下角 (0,0)", 2.6, "#34495e")
    text(310, 263, "图框与注记 DWG 可直接按原点合并", 2.6, "#34495e")
    parts.append("</svg>")
    svg_path = OUT / "A3_部材表图框_概念样稿.svg"
    svg_path.write_text("\n".join(parts), encoding="utf-8")
    return svg_path


def build_png():
    scale = 4
    width = int(SHEET_W * scale)
    height = int(SHEET_H * scale)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img, "RGBA")

    def p(x, y):
        return int(x * scale), int((SHEET_H - y) * scale)

    def box(x1, y1, x2, y2, outline=(25, 35, 45, 255), fill=None, w=2):
        draw.rectangle([p(x1, y2), p(x2, y1)], outline=outline, fill=fill, width=w)

    def line(x1, y1, x2, y2, fill=(25, 35, 45, 255), w=1):
        draw.line([p(x1, y1), p(x2, y2)], fill=fill, width=w)

    fonts = Path("C:/Windows/Fonts/msyh.ttc")
    fallback = Path("C:/Windows/Fonts/arial.ttf")
    font_path = fonts if fonts.exists() else fallback

    def font(mm, bold=False):
        bold_path = Path("C:/Windows/Fonts/msyhbd.ttc")
        chosen = bold_path if bold and bold_path.exists() else font_path
        return ImageFont.truetype(str(chosen), max(8, int(mm * scale * 0.95)))

    def label(x, y, value, mm=2.5, color=(23, 32, 42, 255), bold=False):
        px, py = p(x, y)
        draw.text((px, py), value, font=font(mm, bold), fill=color, anchor="ls")

    box(0, 0, 420, 297, outline=(150, 160, 170, 255), w=2)
    box(10, 10, 410, 287, w=3)
    line(10, 55, 392, 55, w=3)
    line(392, 10, 392, 287, w=3)

    box(392, 55, 410, 287, outline=(213, 103, 0, 255), fill=(255, 243, 230, 230), w=2)
    line(397, 55, 397, 287, fill=(213, 103, 0, 255), w=1)
    for idx in range(59):
        line(392, 55 + idx * 4, 410, 55 + idx * 4, fill=(213, 103, 0, 255), w=1)
    label(393, 58, "1", 1.7, (168, 67, 0, 255))
    label(398, 58, "2026/01/01", 1.4, (168, 67, 0, 255))
    label(393, 62, "2", 1.7, (168, 67, 0, 255))
    label(398, 62, "2026/02/02", 1.4, (168, 67, 0, 255))
    label(393, 283, "更新履历", 2.0, (168, 67, 0, 255), True)

    for x in (155, 250, 330):
        line(x, 10, x, 55, w=2)
    line(360, 10, 360, 30, w=2)
    line(10, 30, 392, 30, w=2)
    line(250, 42, 392, 42, w=2)
    label(14, 49, "图例 / 记号说明", 3.2, bold=True)
    label(14, 38, "○B：特殊符号示例　 継手符号：……", 2.3)
    label(14, 23, "固定注记、材料说明及一般事项", 2.3)
    label(159, 49, "公司 / 现场信息", 3.2, bold=True)
    label(159, 38, "现场名称・公司名称・担当者・检查者", 2.2)
    label(159, 23, "可保存为现场模板固定内容", 2.2)
    label(254, 49, "图纸名称", 3.2, bold=True)
    label(254, 35, "从 Excel Sheet 名称读取", 2.3)
    label(254, 23, "现场", 2.3)
    label(333, 23, "版本", 2.3)
    label(362, 23, "日期", 2.3)

    box(16, 63, 190, 277, outline=(22, 119, 184, 255), fill=(233, 245, 255, 180), w=2)
    cols = [16, 42, 60, 116, 140, 164, 190]
    for x in cols[1:-1]:
        line(x, 63, x, 277, fill=(22, 119, 184, 255), w=1)
    line(16, 267, 190, 267, fill=(22, 119, 184, 255), w=2)
    for x, text_value in [(18, "部件"), (44, "部位"), (62, "断面"), (118, "材质"), (142, "接合"), (166, "备注")]:
        label(x, 270, text_value, 2.5, (7, 89, 133, 255), True)
    y = 267
    while y - 18 >= 63:
        line(42, y - 6, 190, y - 6, fill=(22, 119, 184, 255), w=1)
        line(42, y - 12, 190, y - 12, fill=(22, 119, 184, 255), w=1)
        y -= 18
        line(16, y, 190, y, fill=(22, 119, 184, 255), w=1)
    label(18, 256, "G1", 3, (7, 89, 133, 255), True)
    for ypos, pos, section in [(262, "左", "H-700×300…"), (256, "中", "H-800×300…"), (250, "右", "H-700×300…")]:
        label(45, ypos, pos, 2.3)
        label(62, ypos, section, 2.1)
    label(16, 281, "大梁表：同名三行，输出时合并名称单元格", 3, (7, 89, 133, 255), True)

    box(198, 92, 302, 277, outline=(22, 138, 147, 255), fill=(233, 251, 251, 180), w=2)
    cols_b = [198, 220, 237, 271, 287, 302]
    for x in cols_b[1:-1]:
        line(x, 92, x, 277, fill=(22, 138, 147, 255), w=1)
    line(198, 267, 302, 267, fill=(22, 138, 147, 255), w=2)
    for x, text_value in [(200, "部件"), (222, "部位"), (239, "断面"), (273, "材质"), (289, "接合")]:
        label(x, 270, text_value, 2.1, (9, 104, 111, 255), True)
    y = 267
    while y - 16.5 >= 92:
        line(220, y - 5.5, 302, y - 5.5, fill=(22, 138, 147, 255), w=1)
        line(220, y - 11, 302, y - 11, fill=(22, 138, 147, 255), w=1)
        y -= 16.5
        line(198, y, 302, y, fill=(22, 138, 147, 255), w=1)
    label(200, 256, "B1", 2.8, (9, 104, 111, 255), True)
    label(198, 281, "小梁表：行高、列宽单独保存", 3, (9, 104, 111, 255), True)

    box(310, 63, 386, 128, outline=(142, 68, 173, 255), fill=(247, 236, 255, 220), w=2)
    for offset in range(-50, 90, 8):
        x_start = max(310, 310 + offset)
        y_start = 63 + max(0, -offset)
        x_end = min(386, 310 + offset + 65)
        y_end = 63 + min(65, 65 + offset)
        if x_start < x_end and y_start < y_end:
            line(x_start, y_start, x_end, y_end, fill=(142, 68, 173, 120), w=1)
    label(312, 121, "楼层绑定注记 / 详图插入区", 3, (108, 52, 131, 255), True)
    label(312, 115, "外部 DWG 使用同一 A3 原点定位", 2.2, (108, 52, 131, 255))

    label(310, 276, "A3：420 × 297 mm", 3, (52, 73, 94, 255), True)
    label(310, 269, "统一原点：纸张左下角 (0,0)", 2.5, (52, 73, 94, 255))
    label(310, 263, "图框与注记 DWG 可直接按原点合并", 2.5, (52, 73, 94, 255))

    png_path = OUT / "A3_部材表图框_概念样稿.png"
    img.save(png_path, dpi=(150, 150))
    return png_path


if __name__ == "__main__":
    print(build_dxf())
    print(build_svg())
    print(build_png())
