"""Uniformly scale final CAD model space while keeping PDF geometry at A3 size."""
from __future__ import annotations

import copy
import math

from ezdxf import bbox, units
from ezdxf.math import Matrix44


CAD_OUTPUT_SCALES = (100, 150, 200)
DEFAULT_CAD_OUTPUT_SCALE = 150


def validate_cad_output_scale(value: object) -> int:
    """Accept only the three supported integer CAD output multipliers."""
    if type(value) is not int or value not in CAD_OUTPUT_SCALES:
        choices = ", ".join(str(item) for item in CAD_OUTPUT_SCALES)
        raise ValueError(f"CAD 输出倍率必须是 {choices} 之一（整数）。")
    return value


def scale_document(document, scale: object, paper_width: float, paper_height: float):
    """Return a scaled clone; block definitions stay unit-sized and editable."""
    factor = validate_cad_output_scale(scale)
    if not all(math.isfinite(float(value)) and float(value) > 0 for value in (paper_width, paper_height)):
        raise ValueError("CAD 纸面宽度和高度必须是大于零的有限数值。")

    scaled = copy.deepcopy(document)
    modelspace = scaled.modelspace()
    entities = list(modelspace)
    from ezdxf.transform import inplace

    errors = inplace(entities, Matrix44.scale(factor))
    if errors:
        details = "; ".join(
            f"{entry.entity.dxftype()} {entry.entity.dxf.get('handle', '?')}: {entry.message}"
            for entry in errors[:8]
        )
        raise ValueError(f"CAD 图元无法完整按倍率 {factor} 变换，已停止输出：{details}")
    if len(list(modelspace)) != len(entities):
        raise ValueError("CAD 模型空间图元数量在倍率变换后发生变化，已停止输出。")

    scaled.units = units.MM
    scaled.header["$INSUNITS"] = units.MM
    scaled.header["$MEASUREMENT"] = 1
    # Dash patterns are defined in drawing units. Entity ltscale remains a
    # relative multiplier, so scale only the drawing-wide value once.
    scaled.header["$LTSCALE"] = float(scaled.header.get("$LTSCALE", 1.0)) * factor
    bounds = bbox.extents(modelspace)
    if bounds.has_data:
        scaled.header["$EXTMIN"] = bounds.extmin
        scaled.header["$EXTMAX"] = bounds.extmax
    else:
        scaled.header["$EXTMIN"] = (0, 0, 0)
        scaled.header["$EXTMAX"] = (0, 0, 0)
    modelspace.dxf.limmin = (0, 0)
    modelspace.dxf.limmax = (float(paper_width) * factor, float(paper_height) * factor)
    scaled.header["$LIMMIN"] = modelspace.dxf.limmin
    scaled.header["$LIMMAX"] = modelspace.dxf.limmax

    from .cad_integrity import reset_model_view

    reset_model_view(scaled)
    # reset_model_view changes extents and camera but intentionally does not
    # know the requested page canvas; keep the model-space limits explicit.
    bounds = bbox.extents(modelspace)
    if bounds.has_data:
        modelspace.dxf.extmin = bounds.extmin
        modelspace.dxf.extmax = bounds.extmax
    modelspace.dxf.limmin = (0, 0)
    modelspace.dxf.limmax = (float(paper_width) * factor, float(paper_height) * factor)
    scaled.header["$LIMMIN"] = modelspace.dxf.limmin
    scaled.header["$LIMMAX"] = modelspace.dxf.limmax
    scaled.header["$EXTMIN"] = modelspace.dxf.extmin
    scaled.header["$EXTMAX"] = modelspace.dxf.extmax
    scaled.units = units.MM
    scaled.header["$INSUNITS"] = units.MM
    return scaled
