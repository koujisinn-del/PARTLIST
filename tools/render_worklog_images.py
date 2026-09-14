from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "工作日志" / "项目工作日志_2026-09-01至2026-09-02.md"
OUTPUT = ROOT / "工作日志" / "图片版"

WIDTH = 1440
HEIGHT = 1920
MARGIN_X = 100
TOP = 90
BOTTOM = 105
CONTENT_WIDTH = WIDTH - MARGIN_X * 2

FONT_REGULAR = Path("C:/Windows/Fonts/msyh.ttc")
FONT_BOLD = Path("C:/Windows/Fonts/msyhbd.ttc")
FONT_MONO = Path("C:/Windows/Fonts/msyh.ttc")

COLORS = {
    "background": "#F7F9FC",
    "paper": "#FFFFFF",
    "title": "#153A61",
    "heading": "#176B87",
    "subheading": "#234E70",
    "text": "#202A35",
    "muted": "#667383",
    "accent": "#16A085",
    "line": "#D8E1EA",
    "code": "#EDF3F7",
}


def font(size: int, bold: bool = False):
    path = FONT_BOLD if bold and FONT_BOLD.exists() else FONT_REGULAR
    return ImageFont.truetype(str(path), size=size)


FONTS = {
    "title": font(48, True),
    "h2": font(34, True),
    "h3": font(29, True),
    "body": font(25),
    "body_bold": font(25, True),
    "small": font(21),
    "footer": font(19),
}


def text_width(draw: ImageDraw.ImageDraw, value: str, selected_font) -> float:
    return draw.textlength(value, font=selected_font)


def wrap_text(draw: ImageDraw.ImageDraw, value: str, selected_font, max_width: int) -> list[str]:
    value = value.strip()
    if not value:
        return [""]
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if current and text_width(draw, candidate, selected_font) > max_width:
            lines.append(current.rstrip())
            current = char.lstrip()
        else:
            current = candidate
    if current or not lines:
        lines.append(current.rstrip())
    return lines


def parse_blocks(source: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    in_code = False
    code_lines: list[str] = []
    table_lines: list[str] = []

    def flush_table():
        nonlocal table_lines
        if table_lines:
            rows = []
            for line in table_lines:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
                    continue
                rows.append("｜".join(cells))
            if rows:
                blocks.append(("table", "\n".join(rows)))
            table_lines = []

    for raw in source.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            flush_table()
            if in_code:
                content = "\n".join(code_lines)
                if "flowchart" in content:
                    content = (
                        "Excel部材表＋图纸列表  →  读取与校验  →  按Sheet分图\n"
                        "→ 按种类和部件排序  →  1～N行断面分组  →  绘制表格\n"
                        "现场模板＋DWG图框  →  统一A3左下原点  →  PDF / DXF / DWG\n"
                        "图纸列表  →  本次提出图纸标红并保存"
                    )
                blocks.append(("code", content))
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("|") and line.endswith("|"):
            table_lines.append(line)
            continue
        flush_table()
        stripped = line.strip()
        if not stripped:
            blocks.append(("space", ""))
        elif stripped.startswith("# "):
            blocks.append(("title", stripped[2:].strip()))
        elif stripped.startswith("## "):
            blocks.append(("h2", stripped[3:].strip()))
        elif stripped.startswith("### "):
            blocks.append(("h3", stripped[4:].strip()))
        elif stripped.startswith("- "):
            blocks.append(("bullet", stripped[2:].strip()))
        else:
            blocks.append(("body", stripped.replace("  ", " ")))
    flush_table()
    return blocks


def render() -> list[Path]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    blocks = parse_blocks(SOURCE.read_text(encoding="utf-8"))
    pages: list[Image.Image] = []
    image = Image.new("RGB", (WIDTH, HEIGHT), COLORS["background"])
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 35, WIDTH - 35, HEIGHT - 35), radius=28, fill=COLORS["paper"])
    y = TOP

    def new_page():
        nonlocal image, draw, y
        pages.append(image)
        image = Image.new("RGB", (WIDTH, HEIGHT), COLORS["background"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((35, 35, WIDTH - 35, HEIGHT - 35), radius=28, fill=COLORS["paper"])
        y = TOP

    def ensure(height: int):
        if y + height > HEIGHT - BOTTOM:
            new_page()

    for kind, value in blocks:
        if kind == "space":
            y += 11
            continue
        if kind == "title":
            lines = wrap_text(draw, value, FONTS["title"], CONTENT_WIDTH)
            needed = len(lines) * 64 + 34
            ensure(needed)
            for item in lines:
                draw.text((MARGIN_X, y), item, font=FONTS["title"], fill=COLORS["title"])
                y += 64
            draw.rounded_rectangle((MARGIN_X, y + 4, MARGIN_X + 180, y + 11), radius=4, fill=COLORS["accent"])
            y += 35
            continue
        if kind in {"h2", "h3"}:
            selected = FONTS[kind]
            line_height = 49 if kind == "h2" else 42
            lines = wrap_text(draw, value, selected, CONTENT_WIDTH - 25)
            needed = len(lines) * line_height + 30
            ensure(needed)
            draw.rounded_rectangle((MARGIN_X, y + 3, MARGIN_X + 8, y + line_height - 5), radius=3, fill=COLORS["accent"])
            for item in lines:
                draw.text((MARGIN_X + 25, y), item, font=selected, fill=COLORS["heading" if kind == "h2" else "subheading"])
                y += line_height
            y += 18
            continue
        if kind == "bullet":
            lines = wrap_text(draw, value, FONTS["body"], CONTENT_WIDTH - 60)
            needed = len(lines) * 38 + 10
            ensure(needed)
            draw.ellipse((MARGIN_X + 4, y + 12, MARGIN_X + 16, y + 24), fill=COLORS["accent"])
            for index, item in enumerate(lines):
                draw.text((MARGIN_X + 38, y), item, font=FONTS["body"], fill=COLORS["text"])
                y += 38
            y += 8
            continue
        if kind == "code":
            code_lines: list[str] = []
            for source_line in value.splitlines():
                code_lines.extend(wrap_text(draw, source_line, FONTS["small"], CONTENT_WIDTH - 55))
            needed = len(code_lines) * 34 + 40
            ensure(needed)
            draw.rounded_rectangle((MARGIN_X, y, WIDTH - MARGIN_X, y + needed), radius=16, fill=COLORS["code"])
            cursor = y + 20
            for item in code_lines:
                draw.text((MARGIN_X + 25, cursor), item, font=FONTS["small"], fill=COLORS["subheading"])
                cursor += 34
            y += needed + 22
            continue
        if kind == "table":
            rows = value.splitlines()
            rendered: list[list[str]] = []
            for row in rows:
                row_lines = wrap_text(draw, row, FONTS["small"], CONTENT_WIDTH - 42)
                rendered.append(row_lines)
            needed = sum(max(36, len(row_lines) * 31 + 12) for row_lines in rendered) + 12
            ensure(needed)
            for row_index, row_lines in enumerate(rendered):
                row_height = max(36, len(row_lines) * 31 + 12)
                fill = "#EAF3F5" if row_index == 0 else "#F8FAFC"
                draw.rounded_rectangle((MARGIN_X, y, WIDTH - MARGIN_X, y + row_height), radius=8, fill=fill, outline=COLORS["line"])
                cursor = y + 7
                for item in row_lines:
                    draw.text((MARGIN_X + 20, cursor), item, font=FONTS["small"], fill=COLORS["text"])
                    cursor += 31
                y += row_height + 5
            y += 16
            continue

        lines = wrap_text(draw, value, FONTS["body"], CONTENT_WIDTH)
        needed = len(lines) * 39 + 10
        ensure(needed)
        for item in lines:
            draw.text((MARGIN_X, y), item, font=FONTS["body"], fill=COLORS["text"])
            y += 39
        y += 9

    pages.append(image)
    paths: list[Path] = []
    total = len(pages)
    for page_number, page in enumerate(pages, start=1):
        footer_draw = ImageDraw.Draw(page)
        footer = f"部材表图纸生成器·项目工作日志    {page_number} / {total}"
        footer_draw.line((MARGIN_X, HEIGHT - 82, WIDTH - MARGIN_X, HEIGHT - 82), fill=COLORS["line"], width=2)
        footer_draw.text((MARGIN_X, HEIGHT - 67), footer, font=FONTS["footer"], fill=COLORS["muted"])
        path = OUTPUT / f"项目工作日志_{page_number:02d}.png"
        page.save(path, format="PNG", optimize=True)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for output_path in render():
        print(output_path)
