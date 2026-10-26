"""Validated policy for optional whole-table typography fallback."""

from __future__ import annotations


AUTO_TEXT_SHRINK_STEPS = (0, 1, 2)
AUTO_TEXT_SHRINK_FACTORS = {0: 1.0, 1: 0.9, 2: 0.8}


def validate_auto_text_shrink_steps(value: object) -> int:
    if type(value) is not int or value not in AUTO_TEXT_SHRINK_STEPS:
        raise ValueError("整表字号降档策略必须是整数 0、1 或 2。")
    return value
