"""High-contrast checkbox and radio indicators for the ProgTrack runtime.

The active Qt platform style remains responsible for indicator geometry,
widget layout, focus cues, and every non-indicator primitive. Only the three
indicator primitives are painted here so system accent colors cannot obscure
their state.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QProxyStyle, QStyle


_INDICATOR_ELEMENTS = frozenset(
    {
        QStyle.PrimitiveElement.PE_IndicatorCheckBox,
        QStyle.PrimitiveElement.PE_IndicatorRadioButton,
        QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
    }
)


class MonochromeIndicatorStyle(QProxyStyle):
    """Paint state marks in black and white, delegating all other style work."""

    @staticmethod
    def _state(option) -> Qt.CheckState:
        check_state = getattr(option, "checkState", None)
        if check_state == Qt.CheckState.PartiallyChecked:
            return Qt.CheckState.PartiallyChecked
        if check_state == Qt.CheckState.Checked:
            return Qt.CheckState.Checked
        if option.state & QStyle.StateFlag.State_NoChange:
            return Qt.CheckState.PartiallyChecked
        if option.state & QStyle.StateFlag.State_On:
            return Qt.CheckState.Checked
        return Qt.CheckState.Unchecked

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        if element not in _INDICATOR_ELEMENTS:
            return super().drawPrimitive(element, option, painter, widget)

        rect = QRectF(option.rect).adjusted(1.0, 1.0, -1.0, -1.0)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(QColor(Qt.GlobalColor.black), 1.0))
        painter.setBrush(QColor(Qt.GlobalColor.white))

        if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            painter.drawEllipse(rect)
            if self._state(option) == Qt.CheckState.Checked:
                dot = rect.adjusted(
                    rect.width() * 0.30,
                    rect.height() * 0.30,
                    -rect.width() * 0.30,
                    -rect.height() * 0.30,
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(Qt.GlobalColor.black))
                painter.drawEllipse(dot)
        else:
            painter.drawRect(rect)
            state = self._state(option)
            if state == Qt.CheckState.Checked:
                path = QPainterPath()
                path.moveTo(rect.left() + rect.width() * 0.20,
                            rect.top() + rect.height() * 0.52)
                path.lineTo(rect.left() + rect.width() * 0.43,
                            rect.top() + rect.height() * 0.74)
                path.lineTo(rect.left() + rect.width() * 0.82,
                            rect.top() + rect.height() * 0.27)
                painter.setPen(QPen(QColor(Qt.GlobalColor.black), 2.0,
                                    Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.SquareCap,
                                    Qt.PenJoinStyle.MiterJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            elif state == Qt.CheckState.PartiallyChecked:
                y = rect.center().y()
                painter.setPen(QPen(QColor(Qt.GlobalColor.black), 2.0,
                                    Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.SquareCap))
                painter.drawLine(
                    QPointF(rect.left() + rect.width() * 0.22, y),
                    QPointF(rect.right() - rect.width() * 0.22, y),
                )

        painter.restore()


def install_monochrome_indicator_style(app: QApplication) -> MonochromeIndicatorStyle:
    """Wrap the active platform style once, before ProgTrack widgets exist."""
    installed = getattr(app, "_progtrack_monochrome_indicator_style", None)
    if isinstance(installed, MonochromeIndicatorStyle):
        return installed

    style = MonochromeIndicatorStyle(app.style())
    app.setStyle(style)
    # Keep a Python reference as well as Qt ownership for bindings that defer
    # wrapper cleanup until the application object is collected.
    app._progtrack_monochrome_indicator_style = style
    return style
