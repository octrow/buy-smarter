"""Theme: every rendered text/background pair meets WCAG AA in both palettes."""

import pytest
from PySide6.QtCore import Qt

from buysmarter.gui import theme


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_formula_matches_the_wcag_reference_values():
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)


@pytest.mark.parametrize("name", ["LIGHT", "DARK"])
def test_every_text_pair_is_at_least_4_5_to_1(name):
    palette = getattr(theme, name)
    failing = {
        (fg, bg): round(contrast(palette[fg], palette[bg]), 2)
        for fg, bg in theme.TEXT_PAIRS
        if contrast(palette[fg], palette[bg]) < 4.5
    }
    assert failing == {}


@pytest.mark.parametrize("name", ["LIGHT", "DARK"])
def test_input_outlines_and_the_check_mark_are_visible(name):
    p = getattr(theme, name)
    assert contrast(p["border_strong"], p["surface"]) >= 3.0  # WCAG 1.4.11 non-text
    assert contrast(p["on_accent"], p["accent"]) >= 3.0


def test_both_palettes_define_the_same_tokens():
    assert theme.LIGHT.keys() == theme.DARK.keys()


def test_the_scheme_picks_the_palette_and_every_token_reaches_the_stylesheet():
    assert theme.palette_for(Qt.ColorScheme.Dark) is theme.DARK
    assert theme.palette_for(Qt.ColorScheme.Light) is theme.LIGHT
    assert theme.palette_for(Qt.ColorScheme.Unknown) is theme.LIGHT
    qss = theme.build_qss(theme.DARK)
    unused = {k for k, v in theme.DARK.items() if v not in qss}
    assert unused == set()


def test_the_bundled_font_loads(qapp):
    from PySide6.QtGui import QFontDatabase

    theme.load_font()
    assert theme.FONT_FAMILY in QFontDatabase.families()


def test_the_app_restyles_when_the_os_switches_between_light_and_dark(qapp):
    from PySide6.QtGui import QGuiApplication

    hints = QGuiApplication.styleHints()
    try:
        theme.apply_theme(qapp)
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        assert theme.DARK["window"] in qapp.styleSheet()
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
        assert theme.LIGHT["window"] in qapp.styleSheet()
    finally:
        qapp.setStyleSheet("")


def test_the_check_icon_url_survives_spaces_and_backslashes_in_the_install_path():
    qss = theme.build_qss(theme.LIGHT)
    url = qss.split("image: url(", 1)[1].split(")", 1)[0]
    assert url.startswith('"') and url.endswith('check-light.svg"')
    assert "\\" not in url
