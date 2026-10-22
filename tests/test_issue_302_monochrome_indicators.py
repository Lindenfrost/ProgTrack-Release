"""Focused rendering and integration checks for issue #302."""

from __future__ import annotations

import os
import math
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QRadioButton,
    QStyle,
    QStyleFactory,
    QStyleOptionButton,
    QStyleOptionViewItem,
)

from Plugins.core.monochrome_indicators import (
    MonochromeIndicatorStyle,
    install_monochrome_indicator_style,
)
from Plugins.Sample_Track.sample_track_widget import OrganCheckbox, OrganLabel


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_application_style():
    app = _app()
    original_style = app.style()
    original_style_name = original_style.objectName()
    yield
    current_style = app.style()
    if current_style is not original_style and isinstance(
        current_style, MonochromeIndicatorStyle
    ):
        replacement = QStyleFactory.create(original_style_name)
        if replacement is not None:
            app.setStyle(replacement)
    if hasattr(app, "_progtrack_monochrome_indicator_style"):
        delattr(app, "_progtrack_monochrome_indicator_style")


def _render_primitive(element, option) -> QImage:
    app = _app()
    style = app.style()
    image = QImage(24, 24, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    style.drawPrimitive(element, option, painter)
    painter.end()
    return image


def _black_white_pixels(image: QImage, rect: QRect) -> set[int]:
    colors = set()
    black = QColor(Qt.GlobalColor.black).rgba()
    white = QColor(Qt.GlobalColor.white).rgba()
    for x in range(rect.left(), rect.right() + 1):
        for y in range(rect.top(), rect.bottom() + 1):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() == 0:
                continue
            rgba = pixel.rgba()
            assert rgba in {black, white}, (
                f"indicator painted a non-monochrome color {pixel.name()} at {(x, y)}"
            )
            colors.add(rgba)
    return colors


def test_application_wraps_existing_style_and_installation_is_idempotent():
    app = _app()
    original_style = app.style()
    original_name = original_style.objectName()
    original_width = original_style.pixelMetric(
        QStyle.PixelMetric.PM_IndicatorWidth
    )

    installed = install_monochrome_indicator_style(app)

    assert isinstance(installed, MonochromeIndicatorStyle)
    assert app.style() is installed
    assert installed.baseStyle().objectName() == original_name
    assert installed.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth) == original_width
    assert install_monochrome_indicator_style(app) is installed


def test_checkbox_radio_and_tristate_primitives_are_black_and_white():
    app = _app()
    install_monochrome_indicator_style(app)
    rect = QRect(4, 4, 16, 16)

    cases = (
        (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
         QStyle.StateFlag.State_Enabled,
         {QColor(Qt.GlobalColor.black).rgba(), QColor(Qt.GlobalColor.white).rgba()}),
        (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
         QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On,
         {QColor(Qt.GlobalColor.black).rgba(), QColor(Qt.GlobalColor.white).rgba()}),
        (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
         QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_NoChange,
         {QColor(Qt.GlobalColor.black).rgba(), QColor(Qt.GlobalColor.white).rgba()}),
        (QStyle.PrimitiveElement.PE_IndicatorRadioButton,
         QStyle.StateFlag.State_Enabled,
         {QColor(Qt.GlobalColor.black).rgba(), QColor(Qt.GlobalColor.white).rgba()}),
        (QStyle.PrimitiveElement.PE_IndicatorRadioButton,
         QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On,
         {QColor(Qt.GlobalColor.black).rgba(), QColor(Qt.GlobalColor.white).rgba()}),
        (QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
         QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On,
         {QColor(Qt.GlobalColor.black).rgba(), QColor(Qt.GlobalColor.white).rgba()}),
    )

    for element, state, expected_colors in cases:
        option = QStyleOptionButton()
        option.rect = rect
        option.state = state
        rendered = _render_primitive(element, option)
        assert _black_white_pixels(rendered, rect) == expected_colors

    item_option = QStyleOptionViewItem()
    item_option.rect = rect
    item_option.state = QStyle.StateFlag.State_Enabled
    item_option.checkState = Qt.CheckState.PartiallyChecked
    item_image = _render_primitive(
        QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
        item_option,
    )
    assert _black_white_pixels(item_image, rect) == {
        QColor(Qt.GlobalColor.black).rgba(),
        QColor(Qt.GlobalColor.white).rgba(),
    }


def test_disabled_hovered_and_pressed_indicators_keep_state_and_contrast():
    app = _app()
    install_monochrome_indicator_style(app)
    rect = QRect(4, 4, 16, 16)
    state_sets = (
        QStyle.StateFlag.State_On,
        QStyle.StateFlag.State_On | QStyle.StateFlag.State_MouseOver,
        QStyle.StateFlag.State_On | QStyle.StateFlag.State_Sunken,
        QStyle.StateFlag.State_On | QStyle.StateFlag.State_MouseOver
        | QStyle.StateFlag.State_Sunken,
    )
    for element in (
        QStyle.PrimitiveElement.PE_IndicatorCheckBox,
        QStyle.PrimitiveElement.PE_IndicatorRadioButton,
    ):
        for state in state_sets:
            option = QStyleOptionButton()
            option.rect = rect
            option.state = state
            image = _render_primitive(element, option)
            assert _black_white_pixels(image, rect) == {
                QColor(Qt.GlobalColor.black).rgba(),
                QColor(Qt.GlobalColor.white).rgba(),
            }


def test_real_controls_keep_geometry_state_and_application_callbacks():
    app = _app()
    install_monochrome_indicator_style(app)
    checkbox = QCheckBox("Keep current value")
    checkbox.setStyleSheet(
        "QCheckBox::indicator { width: 14px; height: 14px; }"
    )
    radio_a = QRadioButton("A")
    radio_b = QRadioButton("B")
    radio_group = QButtonGroup()
    radio_group.addButton(radio_a)
    radio_group.addButton(radio_b)
    radio_b.setChecked(True)
    changes = []
    checkbox.toggled.connect(changes.append)

    checkbox.show()
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    option = QStyleOptionButton()
    checkbox.initStyleOption(option)
    indicator_rect = checkbox.style().subElementRect(
        QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox
    )
    assert indicator_rect.width() == 14
    assert indicator_rect.height() == 14
    assert checkbox.isChecked()
    assert changes == [True]
    assert not radio_a.isChecked()
    assert radio_b.isChecked()
    radio_a.show()
    radio_b.show()
    QTest.keyClick(radio_a, Qt.Key.Key_Space)
    assert radio_a.isChecked()
    assert not radio_b.isChecked()
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    assert not checkbox.isChecked()
    assert changes == [True, False]


def test_geometry_only_stylesheet_still_uses_monochrome_indicator_painting():
    app = _app()
    install_monochrome_indicator_style(app)
    checkbox = QCheckBox("Sex filter")
    checkbox.setStyleSheet(
        "QCheckBox::indicator { width: 14px; height: 14px; }"
    )
    checkbox.setChecked(True)
    checkbox.resize(150, 30)
    checkbox.show()
    app.processEvents()

    option = QStyleOptionButton()
    checkbox.initStyleOption(option)
    indicator = checkbox.style().subElementRect(
        QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox
    )
    assert (indicator.width(), indicator.height()) == (14, 14)
    rendered = checkbox.grab().toImage()
    # QWidget geometry is in logical pixels; grab() stores physical pixels.
    # Sample the same logical indicator interior at fractional/high DPR rather
    # than accidentally sampling the surrounding native gray background.
    interior = indicator.adjusted(1, 1, -1, -1)
    ratio = rendered.devicePixelRatio()
    assert ratio == checkbox.devicePixelRatioF()
    left, top = math.ceil(interior.x() * ratio), math.ceil(interior.y() * ratio)
    right = math.floor((interior.x() + interior.width()) * ratio)
    bottom = math.floor((interior.y() + interior.height()) * ratio)
    physical = QRect(left, top, right - left, bottom - top)
    assert physical.isValid() and rendered.rect().contains(physical)
    colors = _black_white_pixels(rendered, physical)
    assert QColor(Qt.GlobalColor.black).rgba() in colors
    assert QColor(Qt.GlobalColor.white).rgba() in colors
    checkbox.close()


def test_indicators_render_monochrome_at_two_times_device_pixel_ratio():
    app = _app()
    install_monochrome_indicator_style(app)
    image = QImage(48, 48, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(2.0)
    image.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionButton()
    option.rect = QRect(4, 4, 16, 16)
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On

    painter = QPainter(image)
    app.style().drawPrimitive(
        QStyle.PrimitiveElement.PE_IndicatorRadioButton, option, painter
    )
    painter.end()

    assert _black_white_pixels(image, QRect(8, 8, 32, 32)) == {
        QColor(Qt.GlobalColor.black).rgba(),
        QColor(Qt.GlobalColor.white).rgba(),
    }


def test_application_entrypoint_installs_style_before_window_and_warning_is_not_indicator_color():
    main_source = (_REPO_ROOT / "ProgTrack.v.0.2.3.py").read_text(encoding="utf-8")
    assert main_source.index("install_monochrome_indicator_style(app)") < main_source.index(
        "win = ProgTrackApp()", main_source.index("if __name__ == '__main__':")
    )

    sample_source = (_REPO_ROOT / "Plugins/Sample_Track/sample_track_widget.py").read_text(
        encoding="utf-8"
    )
    assert 'QCheckBox::indicator { background-color: #ff6666; }' not in sample_source
    assert 'self._lbl.setStyleSheet("color: red; font-weight: bold;")' in sample_source


def test_sample_track_active_organ_label_keeps_its_red_warning_cue():
    _app()
    widget = OrganLabel({"key": "test", "abbrev": "ORG", "name": "Test organ"})
    widget.set_checked(True)
    widget.set_warning(True)

    assert "color: red" in widget._lbl.styleSheet()
    assert "font-weight: bold" in widget._lbl.styleSheet()

    widget.set_warning(False)
    assert "color: black" in widget._lbl.styleSheet()
    assert widget.is_checked()


def test_sample_track_unused_checkbox_variant_has_no_accent_indicator_override():
    _app()
    widget = OrganCheckbox({"abbrev": "ORG", "name": "Test organ"})
    widget.set_checked(True)
    widget.set_warning(True)

    assert widget._chk.styleSheet() == ""
    assert "color: red" in widget._lbl.styleSheet()
    assert "font-weight: bold" in widget._lbl.styleSheet()
