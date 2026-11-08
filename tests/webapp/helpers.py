"""Общие проверки для браузерных тестов Mini App."""

from __future__ import annotations

import re

RGB = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+)")


def luminance(color: str) -> float:
    """Относительная яркость цвета по WCAG."""
    match = RGB.search(color or "")
    if not match:
        raise AssertionError(f"не разобрал цвет: {color!r}")
    parts = []
    for raw in match.groups():
        c = int(raw) / 255
        parts.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = parts
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float:
    """Коэффициент контраста: 4.5 — минимум для обычного текста."""
    a, b = luminance(foreground), luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)
