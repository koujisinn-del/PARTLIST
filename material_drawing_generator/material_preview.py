"""Small UI previews rendered from the same editable blocks used by CAD.

Only this dialog thumbnail is rasterized.  The symbol factory and all exported
CAD text, circles and lines remain unchanged.
"""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import math

import ezdxf
from ezdxf import bbox
from ezdxf.addons import text2path
from ezdxf.fonts import fonts
from ezdxf.npshapes import NumpyPath2d, to_matplotlib_path
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle, PathPatch

from .material_drawing import SymbolFactory
from .materials import SymbolRule


@lru_cache(maxsize=4)
def _factory(font_filename: str) -> SymbolFactory:
    return SymbolFactory(font_filename)


def symbol_preview_png(
    rule: SymbolRule,
    font_filename: str,
    height_px: int = 32,
    max_width_px: int = 160,
) -> bytes:
    """Return a black-on-white PNG, preserving CAD symbol proportions.

    Standard circle and square symbols share one drawing-unit-to-pixel scale.
    Larger custom symbols are reduced only when needed to fit the thumbnail.
    Cache by appearance instead of material name, so equal symbols share one
    rendering even when they belong to different materials or dialog openings.
    Unmarked materials return a blank image; the UI should display its own
    localized "none" text instead.
    """
    if not isinstance(rule, SymbolRule):
        raise TypeError("rule must be a SymbolRule")
    if not isinstance(font_filename, str) or not font_filename.strip():
        raise ValueError("font_filename must be a nonempty string")
    for name, value in (("height_px", height_px), ("max_width_px", max_width_px)):
        if type(value) is not int or not 8 <= value <= 4096:
            raise ValueError(f"{name} must be an integer between 8 and 4096")
    return _render_cached(
        rule.prefix, rule.inner, rule.shape, font_filename, height_px, max_width_px
    )


@lru_cache(maxsize=256)
def _render_cached(
    prefix: str,
    inner: str,
    shape: str,
    font_filename: str,
    height_px: int,
    max_width_px: int,
) -> bytes:
    rule = SymbolRule("PREVIEW", prefix=prefix, inner=inner, shape=shape)
    padding_px = min(3.0, height_px / 4, max_width_px / 4)
    scale = (height_px - 2 * padding_px) / 1.7
    width_px = min(8, max_width_px)
    center_x = center_y = 0.0
    block = None

    if not rule.is_unmarked:
        document = ezdxf.new("R2007")
        document.styles.new("PREVIEW", dxfattribs={"font": font_filename})
        factory = _factory(font_filename)
        block_name = factory.ensure_block(document, rule, "PREVIEW")
        block = document.blocks.get(block_name)
        bounds = bbox.extents(block)
        width, height = bounds.size.x, bounds.size.y
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise ValueError("Material symbol has invalid or empty bounds")
        scale = min(
            scale,
            (height_px - 2 * padding_px) / height,
            (max_width_px - 2 * padding_px) / width,
        )
        width_px = min(max_width_px, max(8, math.ceil(width * scale + 2 * padding_px)))
        center_x, center_y = bounds.center.x, bounds.center.y

    # Do not use pyplot or switch matplotlib's process-wide backend: rendering
    # this thumbnail must not interfere with the GUI or a parallel PDF export.
    dpi = 100
    figure = Figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor="white")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_axes((0, 0, 1, 1))
    axes.set_axis_off()
    axes.set_aspect("equal", adjustable="box")
    axes.set_xlim(center_x - width_px / (2 * scale), center_x + width_px / (2 * scale))
    axes.set_ylim(center_y - height_px / (2 * scale), center_y + height_px / (2 * scale))
    stroke_pt = 0.8

    try:
        if block is not None:
            for entity in block:
                kind = entity.dxftype()
                if kind == "TEXT":
                    text_path = text2path.make_path_from_entity(entity)
                    paths = [NumpyPath2d(part) for part in text_path.sub_paths()]
                    font = fonts.make_font(entity.font_name(), entity.dxf.height)
                    stroke_font = font.font_render_type == fonts.FontRenderType.STROKE
                    path = to_matplotlib_path(paths, detect_holes=not stroke_font)
                    axes.add_patch(PathPatch(
                        path,
                        facecolor="none" if stroke_font else "black",
                        edgecolor="black" if stroke_font else "none",
                        linewidth=stroke_pt if stroke_font else 0,
                    ))
                elif kind == "LINE":
                    start, end = entity.dxf.start, entity.dxf.end
                    axes.plot(
                        (start.x, end.x), (start.y, end.y),
                        color="black", linewidth=stroke_pt, solid_capstyle="butt",
                    )
                elif kind == "CIRCLE":
                    center = entity.dxf.center
                    axes.add_patch(Circle(
                        (center.x, center.y), entity.dxf.radius,
                        facecolor="none", edgecolor="black", linewidth=stroke_pt,
                    ))
                else:
                    raise ValueError(f"Unsupported material symbol entity: {kind}")
        output = BytesIO()
        canvas.print_png(output)
        return output.getvalue()
    finally:
        figure.clear()
