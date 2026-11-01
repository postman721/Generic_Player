"""Small native decorations that never intercept playback or keyboard input."""
from functools import lru_cache
from html import escape

from generic_player_qt import Qt, QtCore, QtGui, QtWidgets, USING_QT6

NO_PEN = Qt.PenStyle.NoPen if USING_QT6 else Qt.NoPen
NO_BRUSH = Qt.BrushStyle.NoBrush if USING_QT6 else Qt.NoBrush
ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft if USING_QT6 else Qt.AlignLeft
ALIGN_VCENTER = Qt.AlignmentFlag.AlignVCenter if USING_QT6 else Qt.AlignVCenter
ANTIALIAS = QtGui.QPainter.RenderHint.Antialiasing if USING_QT6 else QtGui.QPainter.Antialiasing


class DepthCanvas(QtWidgets.QWidget):
    """Paint soft shadows *behind* panels, without compositing their children.

    In particular, video and live audio drawing remain ordinary native widgets;
    a graphics effect on their parent would capture and redraw every frame.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._surfaces = []
        self._shadow = QtGui.QColor("#03080e")
        self._light = False

    def set_palette(self, palette):
        self._shadow = QtGui.QColor(palette["shadow"])
        self._light = QtGui.QColor(palette["background"]).lightness() > 150
        self.update()

    def add_surface(self, widget, radius=18):
        self._surfaces.append((widget, radius))
        ancestor = widget
        while ancestor is not None and ancestor is not self:
            ancestor.installEventFilter(self)
            ancestor = ancestor.parentWidget()

    def eventFilter(self, obj, event):
        events = QtCore.QEvent.Type if USING_QT6 else QtCore.QEvent
        if event.type() in (events.Move, events.Resize, events.Show, events.Hide):
            self.update()
        return False

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        option = QtWidgets.QStyleOption()
        option.initFrom(self)
        primitive = QtWidgets.QStyle.PrimitiveElement.PE_Widget if USING_QT6 else QtWidgets.QStyle.PE_Widget
        self.style().drawPrimitive(primitive, option, painter, self)
        painter.setRenderHint(ANTIALIAS)
        painter.setPen(NO_PEN)
        for widget, radius in self._surfaces:
            if not widget.isVisibleTo(self):
                continue
            origin = widget.mapTo(self, QtCore.QPoint(0, 0))
            bounds = QtCore.QRectF(origin.x(), origin.y() + 4, widget.width(), widget.height())
            # A few translucent contours give a soft falloff. The panel covers
            # the center, so this stays inexpensive and needs no blur buffers.
            for spread in range(12, 0, -2):
                color = QtGui.QColor(self._shadow)
                color.setAlpha((3 if self._light else 5) + (12 - spread) // 2)
                painter.setBrush(color)
                painter.drawRoundedRect(bounds.adjusted(-spread, -spread / 2, spread, spread / 2),
                                        radius + spread / 2, radius + spread / 2)
        painter.end()


class BrandMark(QtWidgets.QWidget):
    """Resolution-independent, static sound mark; not an audio meter."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(42, 42)
        self.setAccessibleName("Generic Player")
        self._palette = None

    def set_palette(self, palette):
        self._palette = palette
        self.update()

    def paintEvent(self, event):
        if self._palette is None:
            return
        p = self._palette
        painter = QtGui.QPainter(self)
        painter.setRenderHint(ANTIALIAS)
        bounds = QtCore.QRectF(1, 1, 40, 40)
        gradient = QtGui.QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        gradient.setColorAt(0, QtGui.QColor(p["raised_top"]))
        gradient.setColorAt(1, QtGui.QColor(p["active"]))
        painter.setBrush(gradient)
        painter.setPen(QtGui.QPen(QtGui.QColor(p["highlight"]), 1))
        painter.drawRoundedRect(bounds, 13, 13)
        cap = Qt.PenCapStyle.RoundCap if USING_QT6 else Qt.RoundCap
        painter.setPen(QtGui.QPen(QtGui.QColor(p["accent"]), 2.6,
                                 Qt.PenStyle.SolidLine if USING_QT6 else Qt.SolidLine, cap))
        for x, height in ((11, 8), (16, 16), (21, 24), (26, 12), (31, 18)):
            painter.drawLine(QtCore.QPointF(x, 21 - height / 2), QtCore.QPointF(x, 21 + height / 2))
        painter.end()


class ElidedLabel(QtWidgets.QLabel):
    """Elide only the painted text; keep full metadata for tooltips and access.

    Layout uses the current font and content width on every paint, including
    changes caused by themes or display scaling. It never changes QLabel.text().
    """
    def __init__(self, text, name, lines=1, parent=None):
        super().__init__(parent)
        self._lines = lines
        self.setObjectName(name)
        self.setTextFormat(Qt.TextFormat.PlainText if USING_QT6 else Qt.PlainText)
        policies = QtWidgets.QSizePolicy.Policy if USING_QT6 else QtWidgets.QSizePolicy
        self.setSizePolicy(policies.Ignored, policies.Fixed)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        super().setText(text)
        # Qt auto-detects rich text in tooltips. Escape metadata so filenames
        # containing angle brackets or ampersands are displayed literally.
        self.setToolTip("<qt>" + escape(text).replace("\n", "<br>") + "</qt>")
        self.setAccessibleName(text)
        self.updateGeometry()

    def sizeHint(self):
        return QtCore.QSize(120, self.fontMetrics().height() * self._lines)

    def minimumSizeHint(self):
        return QtCore.QSize(0, self.sizeHint().height())

    def paintEvent(self, event):
        bounds = self.contentsRect()
        text = self.text().replace("\n", "\u2028")
        metrics = self.fontMetrics()
        layout = QtGui.QTextLayout(text, self.font())
        option = QtGui.QTextOption()
        wrap = QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere if USING_QT6 else QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere
        option.setWrapMode(wrap)
        layout.setTextOption(option)
        layout.beginLayout()
        lines = []
        for _ in range(self._lines):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(0, bounds.width()))
            lines.append((line.textStart(), line.textLength()))
        layout.endLayout()
        # QTextLine indexes QString's UTF-16 code units, not Python code
        # points. Preserve supplementary characters (for example emoji) when
        # extracting a wrapped line; plain str slicing can clip/drop letters.
        encoded = text.encode("utf-16-le", errors="surrogatepass")
        painter = QtGui.QPainter(self)
        painter.setFont(self.font())
        roles = QtGui.QPalette.ColorRole if USING_QT6 else QtGui.QPalette
        elide = Qt.TextElideMode.ElideRight if USING_QT6 else Qt.ElideRight
        y = bounds.y() + max(0, (bounds.height() - len(lines) * metrics.height()) // 2)
        for index, (start, length) in enumerate(lines):
            last = index == len(lines) - 1
            segment = encoded[start * 2:] if last else encoded[start * 2:(start + length) * 2]
            segment = segment.decode("utf-16-le", errors="replace")
            segment = segment.replace("\u2028", " ").rstrip()
            if last:
                segment = metrics.elidedText(segment, elide, bounds.width())
            rect = QtCore.QRect(bounds.x(), y + index * metrics.height(), bounds.width(), metrics.height())
            self.style().drawItemText(painter, rect, int(ALIGN_LEFT | ALIGN_VCENTER),
                                      self.palette(), self.isEnabled(), segment, roles.WindowText)
        painter.end()


@lru_cache(maxsize=192)
def line_icon(glyph, foreground, accent, disabled):
    """Return a cached icon with normal, checked and disabled appearances.

    Callers treat this icon as read-only and pass it to Qt controls. Color strings
    form part of the key, so theme changes need no explicit cache invalidation;
    the size limit also bounds retained native pixmaps across theme switches.
    """
    icon = QtGui.QIcon()
    modes = QtGui.QIcon.Mode if USING_QT6 else QtGui.QIcon
    states = QtGui.QIcon.State if USING_QT6 else QtGui.QIcon
    for mode, state, ink in ((modes.Normal, states.Off, foreground),
                             (modes.Normal, states.On, accent),
                             (modes.Disabled, states.Off, disabled),
                             (modes.Disabled, states.On, disabled)):
        pixmap = QtGui.QPixmap(48, 48)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent if USING_QT6 else Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(ANTIALIAS)
        color = QtGui.QColor(ink)
        cap = Qt.PenCapStyle.RoundCap if USING_QT6 else Qt.RoundCap
        join = Qt.PenJoinStyle.RoundJoin if USING_QT6 else Qt.RoundJoin
        pen = QtGui.QPen(color, 1.8, Qt.PenStyle.SolidLine if USING_QT6 else Qt.SolidLine, cap, join)
        painter.setPen(pen)
        painter.setBrush(NO_BRUSH)

        def path(points):
            shape = QtGui.QPainterPath(QtCore.QPointF(*points[0]))
            for point in points[1:]:
                shape.lineTo(*point)
            painter.drawPath(shape)

        if glyph in ("play", "next", "previous"):
            painter.setBrush(color)
            painter.setPen(NO_PEN)
            if glyph == "previous":
                points = ((17, 5), (7, 12), (17, 19))
            else:
                points = ((7, 5), (17, 12), (7, 19))
            painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*point) for point in points]))
            if glyph != "play":
                painter.setPen(pen)
                x = 5 if glyph == "previous" else 19
                painter.drawLine(QtCore.QPointF(x, 5), QtCore.QPointF(x, 19))
        elif glyph in ("pause", "stop"):
            painter.setPen(NO_PEN)
            painter.setBrush(color)
            if glyph == "stop":
                painter.drawRoundedRect(QtCore.QRectF(6, 6, 12, 12), 2, 2)
            else:
                for x in (6, 14):
                    painter.drawRoundedRect(QtCore.QRectF(x, 5, 4, 14), 1, 1)
        elif glyph in ("volume", "muted"):
            path(((4, 9), (8, 9), (13, 5), (13, 19), (8, 15), (4, 15), (4, 9)))
            if glyph == "muted":
                path(((17, 9), (22, 15)))
                path(((22, 9), (17, 15)))
            else:
                painter.drawArc(QtCore.QRectF(11, 7, 8, 10), -65 * 16, 130 * 16)
                painter.drawArc(QtCore.QRectF(10, 4, 13, 16), -58 * 16, 116 * 16)
        elif glyph == "shuffle":
            path(((3, 6), (6, 6), (17, 18), (21, 18)))
            path(((3, 18), (6, 18), (17, 6), (21, 6)))
            path(((18, 3), (21, 6), (18, 9)))
            path(((18, 15), (21, 18), (18, 21)))
        elif glyph == "repeat":
            path(((4, 10), (4, 7), (6, 5), (20, 5), (17, 2)))
            path(((17, 8), (20, 5)))
            path(((20, 14), (20, 17), (18, 19), (4, 19), (7, 22)))
            path(((7, 16), (4, 19)))
        elif glyph == "plus":
            path(((5, 12), (19, 12)))
            path(((12, 5), (12, 19)))
        elif glyph == "search":
            painter.drawEllipse(QtCore.QRectF(4, 4, 12, 12))
            path(((14, 14), (20, 20)))
        painter.end()
        icon.addPixmap(pixmap, mode, state)
    return icon


def apply_button_icon(button, palette):
    glyph = getattr(button, "_glyph", None)
    if glyph:
        primary = button.objectName() in ("play_button", "primary")
        ink = palette["on_accent"] if primary else palette["text"]
        button.setIcon(line_icon(glyph, ink, palette["accent"], palette["muted"]))
        button.setIconSize(QtCore.QSize(20, 20) if button.objectName() != "primary" else QtCore.QSize(16, 16))
