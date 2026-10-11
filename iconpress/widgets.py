"""Small custom widgets: switch, segmented control and the image preview / drop zone."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from . import theme
from .i18n import tr


class Switch(QAbstractButton):
    """An on/off toggle in the style of macOS and iOS."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=140,
                                       easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._move)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(40, 24)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 (Qt API)
        super().setChecked(checked)
        self._anim.stop()
        self._move(1.0 if checked else 0.0)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _move(self, value: float) -> None:
        self._pos = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        track = QRectF(0, 0, 40, 24).adjusted(1, 1, -1, -1)
        off, on = theme.color("field_border"), theme.color("accent")
        mix = QColor.fromRgbF(*(a + (b - a) * self._pos for a, b in
                                zip(off.getRgbF(), on.getRgbF(), strict=True)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob = track.height() - 4
        pos = 1 - self._pos if self.isRightToLeft() else self._pos  # "on" is at the start in RTL
        x = track.left() + 2 + (track.width() - knob - 4) * pos
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, track.top() + 2.6, knob, knob))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, track.top() + 2, knob, knob))


class SegmentedControl(QFrame):
    """A row of mutually exclusive buttons; ``changed`` emits the chosen value."""

    changed = Signal(object)

    def __init__(self, options: list[tuple[str, object]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._values: list[object] = []
        for index, (text, value) in enumerate(options):
            button = QPushButton(text)
            button.setCheckable(True)
            # The checked segment is bold; reserve room so its text is never clipped
            bold = QFont(button.font())
            bold.setWeight(QFont.Weight.DemiBold)
            button.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(text) + 28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button)
            self._group.addButton(button, index)
            self._values.append(value)
        self._group.button(0).setChecked(True)
        self._group.idToggled.connect(self._on_toggled)

    def _on_toggled(self, index: int, checked: bool) -> None:
        if checked:
            self.changed.emit(self._values[index])

    def value(self) -> object:
        return self._values[self._group.checkedId()]

    def set_value(self, value: object) -> None:
        if value in self._values:
            self._group.button(self._values.index(value)).setChecked(True)


def pil_to_qimage(img) -> QImage:
    """Convert a Pillow image to a QImage that owns its pixels."""
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimage = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return qimage.copy()


class PreviewView(QWidget):
    """Shows an image fitted to the widget on a transparency checkerboard.

    With no image it turns into a drop zone that opens the file dialog on click.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._image: QImage | None = None
        self._drag_active = False
        self.setMinimumSize(260, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_image(self, image: QImage | None) -> None:
        self._image = image
        self.setCursor(Qt.CursorShape.ArrowCursor if image else Qt.CursorShape.PointingHandCursor)
        self.update()

    def set_drag_active(self, active: bool) -> None:
        self._drag_active = active
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._image is None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._image is None:
            self._paint_drop_zone(p, area)
        else:
            self._paint_image(p, area)
        if self._drag_active:
            pen = QPen(theme.color("accent"), 2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(area.adjusted(1, 1, -1, -1), 10, 10)

    def _paint_drop_zone(self, p: QPainter, area: QRectF) -> None:
        accent = theme.color("accent")
        fill = QColor(accent)
        fill.setAlpha(28 if self._drag_active else 12)
        p.setBrush(fill)
        pen = QPen(accent if self._drag_active else theme.color("field_border"), 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.drawRoundedRect(area.adjusted(1, 1, -1, -1), 10, 10)

        c = area.center()
        # A stylised picture: a rounded frame with a sun and a mountain
        icon = QRectF(c.x() - 26, c.y() - 58, 52, 42)
        p.setPen(QPen(accent, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(icon, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QPointF(icon.left() + 15, icon.top() + 13), 4.5, 4.5)
        mountain = QPainterPath()
        mountain.moveTo(icon.left() + 6, icon.bottom() - 6)
        mountain.lineTo(icon.left() + 22, icon.top() + 20)
        mountain.lineTo(icon.left() + 32, icon.bottom() - 14)
        mountain.lineTo(icon.left() + 37, icon.top() + 24)
        mountain.lineTo(icon.right() - 6, icon.bottom() - 6)
        mountain.closeSubpath()
        p.drawPath(mountain)

        font = p.font()
        font.setPixelSize(15)
        font.setBold(True)
        p.setFont(font)
        p.setPen(theme.color("text"))
        p.drawText(QRectF(area.left(), c.y() - 4, area.width(), 22),
                   Qt.AlignmentFlag.AlignCenter, tr("Drop images here"))
        font.setPixelSize(13)
        font.setBold(False)
        p.setFont(font)
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(area.left(), c.y() + 20, area.width(), 20),
                   Qt.AlignmentFlag.AlignCenter,
                   tr("or click to browse — PNG, JPEG, WebP, GIF, TIFF…"))

    def _paint_image(self, p: QPainter, area: QRectF) -> None:
        image = self._image
        size = image.deviceIndependentSize()
        inner = area.adjusted(8, 8, -8, -8)
        # Never enlarge photos past 1:1, but let tiny images (icons) grow to be visible
        limit = 1.0 if max(size.width(), size.height()) > 64 else 8.0
        scale = min(inner.width() / size.width(), inner.height() / size.height(), limit)
        w, h = size.width() * scale, size.height() * scale
        target = QRectF(inner.center().x() - w / 2, inner.center().y() - h / 2, w, h)

        clip = QPainterPath()
        clip.addRoundedRect(target, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.fillRect(target, QBrush(_checkerboard()))
        p.drawImage(target, image)
        p.restore()


def _checkerboard() -> QPixmap:
    tile = QPixmap(16, 16)
    tile.fill(theme.color("checker_a"))
    p = QPainter(tile)
    b = theme.color("checker_b")
    p.fillRect(0, 0, 8, 8, b)
    p.fillRect(8, 8, 8, 8, b)
    p.end()
    return tile


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """The IconPress logo, drawn in code so the app needs no image files."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256
    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0, QColor("#5ac8fa"))
    gradient.setColorAt(1, QColor("#0a6cff"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)

    white = QColor("#ffffff")
    frame = QRectF(56 * s, 68 * s, 144 * s, 120 * s)
    p.setPen(QPen(white, 14 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(frame, 22 * s, 22 * s)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(white)
    p.drawEllipse(QPointF(98 * s, 106 * s), 13 * s, 13 * s)
    mountain = QPainterPath()
    mountain.moveTo(70 * s, 176 * s)
    mountain.lineTo(118 * s, 128 * s)
    mountain.lineTo(146 * s, 154 * s)
    mountain.lineTo(160 * s, 140 * s)
    mountain.lineTo(188 * s, 176 * s)
    mountain.closeSubpath()
    p.drawPath(mountain)
    p.end()
    return pixmap
