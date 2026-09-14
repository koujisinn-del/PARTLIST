"""Vector CAD geometry with embedded, selectable PDF text (never text outlines)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import ezdxf
from ezdxf.addons.drawing.backend import Backend
from ezdxf.addons.drawing.config import Configuration, ColorPolicy, BackgroundPolicy
from ezdxf.addons.drawing.frontend import UniversalFrontend
from ezdxf.addons.drawing.pipeline import RenderPipeline2d
from ezdxf.addons.drawing.properties import RenderContext
from ezdxf.fonts import fonts
from ezdxf.path import Command
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .cad_integrity import unicode_text


@lru_cache(maxsize=16)
def pdf_font(filename: str):
    path = Path(filename)
    if not path.is_file():
        path = Path("C:/Windows/Fonts") / path.name
    if not path.is_file():
        raise ValueError(f"找不到可嵌入的文字字体：{filename}。请在公司电脑安装对应字体。")
    name = "MDG_" + path.stem
    font = TTFont(name, str(path), subfontIndex=0)
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(font)
    cad_font = fonts.make_font(str(path), 1.0)
    cache = cad_font.glyph_cache
    units_per_em = cache.font["head"].unitsPerEm
    raw = cache.font_measurements
    return name, font, cad_font, units_per_em / raw.cap_height, raw.baseline / raw.cap_height


def assert_glyphs(text: str, font) -> None:
    missing = {ch for ch in text if not ch.isspace() and ord(ch) not in font.face.charToGlyph}
    if missing:
        raise ValueError("指定字体缺少字符：" + " ".join(sorted(missing)) + "。已停止输出，避免问号或方框。")


class TextBackend(Backend):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.text_count = 0

    def _style(self, properties):
        self.canvas.setStrokeColorRGB(0, 0, 0)
        self.canvas.setFillColorRGB(0, 0, 0)
        self.canvas.setLineWidth(max(0.06, properties.lineweight))

    def set_background(self, color):
        pass  # The page is opaque white.

    def draw_point(self, pos, properties):
        self._style(properties)
        self.canvas.circle(pos.x, pos.y, 0.03, stroke=0, fill=1)

    def draw_line(self, start, end, properties):
        self._style(properties)
        self.canvas.line(start.x, start.y, end.x, end.y)

    def _path(self, paths):
        out = self.canvas.beginPath()
        for path in paths:
            if not len(path):
                continue
            prev = path.start
            out.moveTo(prev.x, prev.y)
            for command in path.commands():
                end = command.end
                if command.type == Command.LINE_TO:
                    out.lineTo(end.x, end.y)
                elif command.type == Command.MOVE_TO:
                    out.moveTo(end.x, end.y)
                elif command.type == Command.CURVE4_TO:
                    a, b = command.ctrl1, command.ctrl2
                    out.curveTo(a.x, a.y, b.x, b.y, end.x, end.y)
                elif command.type == Command.CURVE3_TO:
                    a = prev + (command.ctrl - prev) * (2 / 3)
                    b = end + (command.ctrl - end) * (2 / 3)
                    out.curveTo(a.x, a.y, b.x, b.y, end.x, end.y)
                prev = end
        return out

    def draw_path(self, path, properties):
        self._style(properties)
        self.canvas.drawPath(self._path([path]), stroke=1, fill=0)

    def draw_filled_paths(self, paths, properties):
        self._style(properties)
        self.canvas.drawPath(self._path(paths), stroke=0, fill=1, fillMode=0)

    def draw_filled_polygon(self, points, properties):
        from ezdxf.npshapes import NumpyPath2d
        self.draw_filled_paths([NumpyPath2d.from_vertices(points.vertices(), close=True)], properties)

    def draw_image(self, image_data, properties):
        # Raster CAD references cannot meet this generator's text-only rule.
        raise ValueError("图框含位图引用，请使用文字与矢量图元的图框。")

    def clear(self):
        raise ValueError("不可清除正在生成的 PDF 页面。")

    def native_text(self, text, transform, properties, cap_height):
        if not text.strip():
            return
        text = unicode_text(text)
        filename = properties.font.filename
        name, font, _, scale, baseline = pdf_font(filename)
        assert_glyphs(text, font)
        c = self.canvas
        c.saveState()
        c.transform(transform[0, 0], transform[0, 1], transform[1, 0], transform[1, 1], transform[3, 0], transform[3, 1])
        c.setFillColorRGB(0, 0, 0)
        c.setFont(name, cap_height * scale)
        c.drawString(0, -baseline * cap_height, text)
        c.restoreState()
        self.text_count += 1


class TextPipeline(RenderPipeline2d):
    def draw_text(self, text, transform, properties, cap_height, dxftype="TEXT"):
        # Keep all CAD placement, alignment and MTEXT layout from the frontend,
        # but send characters to PDF instead of converting them to glyph paths.
        self.backend.native_text(text, transform, properties, cap_height)


class StrictFrontend(UniversalFrontend):
    def skip_entity(self, entity, message):
        raise ValueError(f"PDF 无法完整绘制 {entity.dxftype()}：{message}")


class TextPdfBook:
    def __init__(self, path: Path, width: float, height: float):
        self.path = path
        self.width, self.height = width, height
        self.canvas = Canvas(str(path), pagesize=(width * mm, height * mm), pageCompression=1)
        self.canvas.setTitle(path.stem)
        self.page_names = []

    def add_cad_page(self, document, name: str):
        # The rendering machine's embeddable CJK font must not overwrite the
        # original CAD font definitions. Work on a detached copy only.
        import copy
        from .drawing import _apply_pdf_font_to_styles, _cjk_font_filename
        document = copy.deepcopy(document)
        _apply_pdf_font_to_styles(document, _cjk_font_filename())
        c = self.canvas
        c.saveState()
        c.scale(mm, mm)
        backend = TextBackend(c)
        pipeline = TextPipeline(backend)
        frontend = StrictFrontend(
            RenderContext(document), pipeline,
            Configuration(color_policy=ColorPolicy.BLACK, background_policy=BackgroundPolicy.WHITE),
        )
        frontend.draw_layout(document.modelspace(), finalize=True)
        c.restoreState()
        c.showPage()
        self.page_names.append(name)

    def save(self):
        if not self.page_names:
            raise ValueError("PDF 没有可打印页面。")
        self.canvas.save()
