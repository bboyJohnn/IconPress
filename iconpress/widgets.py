"""Small custom widgets: switch, segmented control, colour and position pickers, and the
image preview / drop zone."""

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
    QIcon,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QGridLayout,
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

    Given a second image it becomes a before/after comparison with a divider to drag.
    With no image it turns into a drop zone that opens the file dialog on click.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._image: QImage | None = None
        self._after: QImage | None = None
        self._split = 0.5
        self._dragging = False
        self._drag_active = False
        self.setMinimumSize(260, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_image(self, image: QImage | None, after: QImage | None = None) -> None:
        self._image = image
        self._after = after if image is not None else None
        self.setCursor(Qt.CursorShape.ArrowCursor if image else Qt.CursorShape.PointingHandCursor)
        self.update()

    def is_comparing(self) -> bool:
        return self._after is not None

    def set_drag_active(self, active: bool) -> None:
        self._drag_active = active
        self.update()

    def _target(self) -> QRectF | None:
        if self._image is None:
            return None
        size = self._image.deviceIndependentSize()
        inner = QRectF(self.rect()).adjusted(9, 9, -9, -9)
        # Never enlarge photos past 1:1, but let tiny images (icons) grow to be visible
        limit = 1.0 if max(size.width(), size.height()) > 64 else 8.0
        scale = min(inner.width() / size.width(), inner.height() / size.height(), limit)
        w, h = size.width() * scale, size.height() * scale
        return QRectF(inner.center().x() - w / 2, inner.center().y() - h / 2, w, h)

    def _move_split(self, x: float) -> None:
        target = self._target()
        if target is not None and target.width() > 0:
            self._split = min(1.0, max(0.0, (x - target.left()) / target.width()))
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        target = self._target()
        if (self._after is not None and target is not None
                and event.button() == Qt.MouseButton.LeftButton
                and target.contains(event.position())):
            self._dragging = True
            self._move_split(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._move_split(event.position().x())
        elif self._after is not None:
            target = self._target()
            inside = target is not None and target.contains(event.position())
            self.setCursor(Qt.CursorShape.SplitHCursor if inside else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
        elif self._image is None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._image is None:
            self._paint_drop_zone(p, area)
        else:
            self._paint_image(p)
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
        paste = QKeySequence(QKeySequence.StandardKey.Paste).toString(
            QKeySequence.SequenceFormat.NativeText)
        p.drawText(QRectF(area.left() + 12, c.y() + 20, area.width() - 24, 40),
                   Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                   tr("or click to browse, or paste with {shortcut}", shortcut=paste))

    def _paint_image(self, p: QPainter) -> None:
        target = self._target()
        clip = QPainterPath()
        clip.addRoundedRect(target, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.fillRect(target, QBrush(_checkerboard()))
        if self._after is None:
            p.drawImage(target, self._image)
            p.restore()
            return
        x = target.left() + target.width() * self._split
        left = QRectF(target.left(), target.top(), x - target.left(), target.height())
        right = QRectF(x, target.top(), target.right() - x, target.height())
        for part, image in ((left, self._image), (right, self._after)):
            p.save()
            p.setClipRect(part, Qt.ClipOperation.IntersectClip)
            p.drawImage(target, image)
            p.restore()
        p.restore()

        # Divider with a round handle, and labels on both halves
        p.setPen(QPen(QColor(0, 0, 0, 60), 4))
        p.drawLine(QPointF(x, target.top()), QPointF(x, target.bottom()))
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, target.top()), QPointF(x, target.bottom()))
        handle = QPointF(x, target.center().y())
        p.setBrush(QColor("#ffffff"))
        p.setPen(QPen(QColor(0, 0, 0, 50), 1))
        p.drawEllipse(handle, 13, 13)
        p.setPen(QPen(QColor("#555555"), 1.6))
        for direction in (-1, 1):
            tip = handle.x() + direction * 7
            p.drawLine(QPointF(tip - direction * 4, handle.y() - 4), QPointF(tip, handle.y()))
            p.drawLine(QPointF(tip - direction * 4, handle.y() + 4), QPointF(tip, handle.y()))
        font = p.font()
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        metrics = QFontMetrics(font)
        for text, at_left in ((tr("Original"), True), (tr("Result"), False)):
            width = metrics.horizontalAdvance(text) + 16
            box = QRectF(target.left() + 8 if at_left else target.right() - 8 - width,
                         target.top() + 8, width, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 140))
            p.drawRoundedRect(box, 10, 10)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class ColorButton(QPushButton):
    """Shows a colour swatch and its hex code; clicking opens the colour picker."""

    changed = Signal(str)

    def __init__(self, colour: str = "#ffffff", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("colorButton")
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # "#FFFFFF" reads left to right
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        self.set_color(colour)

    def color(self) -> str:
        return self._colour

    def set_color(self, colour: str) -> None:
        value = QColor(colour)
        self._colour = (value if value.isValid() else QColor("#ffffff")).name()
        swatch = QPixmap(32, 32)
        swatch.fill(Qt.GlobalColor.transparent)
        p = QPainter(swatch)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 70), 2))
        p.setBrush(QColor(self._colour))
        p.drawRoundedRect(QRectF(2, 2, 28, 28), 7, 7)
        p.end()
        swatch.setDevicePixelRatio(2)
        self.setIcon(QIcon(swatch))
        self.setText(self._colour.upper())

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._colour), self.window(), tr("Choose a colour"))
        if chosen.isValid() and chosen.name() != self._colour:
            self.set_color(chosen.name())
            self.changed.emit(self._colour)


class PositionPicker(QWidget):
    """A 3 × 3 grid of buttons for picking where on the picture something goes."""

    changed = Signal(str)

    def __init__(self, positions: tuple[str, ...], parent: QWidget | None = None):
        super().__init__(parent)
        # Positions are on the picture itself, so the grid is never mirrored
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self._group = QButtonGroup(self)
        self._positions = list(positions)
        for index, _name in enumerate(positions):
            button = QPushButton(objectName="position")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            grid.addWidget(button, index // 3, index % 3)
            self._group.addButton(button, index)
        self._group.idToggled.connect(
            lambda index, on: on and self.changed.emit(self._positions[index]))

    def value(self) -> str:
        return self._positions[max(0, self._group.checkedId())]

    def set_value(self, position: str) -> None:
        if position in self._positions:
            self._group.button(self._positions.index(position)).setChecked(True)


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
