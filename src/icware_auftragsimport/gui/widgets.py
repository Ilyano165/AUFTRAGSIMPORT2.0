"""Kleine, wiederverwendbare Bausteine: Statuszeichen, Hinweise, Formularzeilen."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ..app.presentation import STATUS_LOOK, Glyph, StatusLook, Tone
from ..domain.findings import Severity
from ..domain.status import OrderStatus
from .theme import TONES

GLYPH_SIZE = 10.0
SEVERITY_TONE = {
    Severity.ERROR: Tone.DANGER,
    Severity.WARNING: Tone.WARNING,
    Severity.INFO: Tone.INFO,
}
SEVERITY_GLYPH = {
    Severity.ERROR: Glyph.SQUARE,
    Severity.WARNING: Glyph.TRIANGLE,
    Severity.INFO: Glyph.RING,
}


def tone_color(tone: Tone) -> QColor:
    """Vordergrundfarbe eines Tons."""
    return QColor(TONES[tone][0])


def paint_glyph(
    painter: QPainter, center: QPointF, glyph: Glyph, color: QColor, size: float = GLYPH_SIZE
) -> None:
    """Zeichnet ein Statuszeichen vektoriell (scharf bei jeder Skalierung)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    half = size / 2
    box = QRectF(center.x() - half, center.y() - half, size, size)
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if glyph in (Glyph.DOT, Glyph.SQUARE, Glyph.TRIANGLE):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if glyph is Glyph.DOT:
            painter.drawEllipse(box.adjusted(1, 1, -1, -1))
        elif glyph is Glyph.SQUARE:
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 1.5, 1.5)
        else:
            path = QPainterPath(QPointF(center.x(), box.top()))
            path.lineTo(box.right(), box.bottom() - 0.5)
            path.lineTo(box.left(), box.bottom() - 0.5)
            path.closeSubpath()
            painter.drawPath(path)
    else:
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if glyph is Glyph.RING:
            painter.drawEllipse(box.adjusted(1.5, 1.5, -1.5, -1.5))
        elif glyph is Glyph.CHECK:
            path = QPainterPath(QPointF(box.left() + 1, center.y()))
            path.lineTo(center.x() - 1, box.bottom() - 1.5)
            path.lineTo(box.right() - 0.5, box.top() + 1.5)
            painter.drawPath(path)
        elif glyph is Glyph.CLOCK:
            painter.drawEllipse(box.adjusted(1.5, 1.5, -1.5, -1.5))
            painter.drawLine(center, QPointF(center.x(), box.top() + 3))
            painter.drawLine(center, QPointF(box.right() - 3, center.y()))
        else:
            painter.drawLine(
                QPointF(box.left() + 1.5, center.y()), QPointF(box.right() - 1.5, center.y())
            )
    painter.restore()


class StatusDelegate(QStyledItemDelegate):
    """Statusspalte: Zeichen plus Text in der Statusfarbe."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        raw = index.data(Qt.ItemDataRole.UserRole + 1)
        status = (
            OrderStatus(raw)
            if isinstance(raw, str) and raw in OrderStatus._value2member_map_
            else None
        )
        if status is None:
            super().paint(painter, option, index)
            return
        look = STATUS_LOOK[status]
        self.initStyleOption(option, index)
        option.text = ""
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, widget)
        rect = option.rect
        color = tone_color(look.tone)
        paint_glyph(painter, QPointF(rect.left() + 14, rect.center().y() + 0.5), look.glyph, color)
        painter.save()
        painter.setPen(color)
        text_rect = rect.adjusted(26, 0, -4, 0)
        elided = option.fontMetrics.elidedText(
            look.label, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(
            text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elided
        )
        painter.restore()

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        """Breite des längsten Statustexts."""
        widest = max(
            option.fontMetrics.horizontalAdvance(look.label) for look in STATUS_LOOK.values()
        )
        return QSize(widest + 34, super().sizeHint(option, index).height())


class StatusChip(QWidget):
    """Status im Kopf der Detailansicht."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._look: StatusLook | None = None
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_status(self, status: OrderStatus | None) -> None:
        """Zeigt einen Status an oder nichts."""
        self._look = STATUS_LOOK[status] if status else None
        self.setToolTip(self._look.description if self._look else "")
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        """Breite aus Text und Zeichen."""
        if self._look is None:
            return QSize(0, 0)
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self._look.label) + 34, metrics.height() + 10)

    def paintEvent(self, event: object) -> None:
        """Zeichnet Hintergrund, Zeichen und Text."""
        if self._look is None:
            return
        foreground, background = TONES[self._look.tone]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 4, 4)
        color = QColor(foreground)
        paint_glyph(painter, QPointF(rect.left() + 13, rect.center().y()), self._look.glyph, color)
        painter.setPen(color)
        painter.drawText(
            rect.adjusted(24, 0, -8, 0), int(Qt.AlignmentFlag.AlignVCenter), self._look.label
        )
        painter.end()


class ElidedLabel(QLabel):
    """Einzeilige Beschriftung, die bei Platzmangel mit „…“ gekürzt wird."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        """Darf beliebig schmal werden."""
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event: object) -> None:
        """Zeichnet den gekürzten Text; der volle Text steht im Tooltip."""
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text
        )
        painter.end()
        self.setToolTip(self.text() if text != self.text() else "")


class Notice(QLabel):
    """Ruhiger Hinweisstreifen (Info, Erfolg, Warnung, Fehler)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Notice")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.hide()

    def show_text(self, tone: Tone, text: str) -> None:
        """Zeigt einen Hinweis im angegebenen Ton."""
        self.setProperty("tone", tone.value)
        self.setText(text)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(bool(text))


def section_label(text: str) -> QLabel:
    """Überschrift eines Abschnitts innerhalb eines Bereichs."""
    label = QLabel(text)
    label.setObjectName("SectionLabel")
    return label


def muted(text: str = "") -> QLabel:
    """Sekundärer Text."""
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def row(*widgets: QWidget, stretch: tuple[int, ...] = ()) -> QWidget:
    """Mehrere Felder in einer Formularzeile, etwa Straße und Hausnummer."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for position, widget in enumerate(widgets):
        layout.addWidget(widget, stretch[position] if position < len(stretch) else 0)
    return container


def set_field_state(widget: QWidget, state: str, tooltip: str = "") -> None:
    """Markiert ein Eingabefeld als ``review`` (unsicher) oder ``missing`` (fehlt)."""
    widget.setProperty("state", state)
    widget.setToolTip(tooltip)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
