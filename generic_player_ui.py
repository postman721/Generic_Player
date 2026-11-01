"""Native desktop listening experience, accessible controls, and session lifecycle."""
import math
import os
import time
import weakref
from pathlib import Path

from generic_player_qt import QtCore, QtGui, QtWidgets, QtMultimediaWidgets, Qt, QAction, USING_QT6
from generic_player_visualizer import AudioVisualizer
from generic_player_widgets import DepthCanvas, BrandMark, ElidedLabel, apply_button_icon, line_icon
from generic_player_version import __version__
from theme import ThemeManager

HORIZONTAL = Qt.Orientation.Horizontal if USING_QT6 else Qt.Horizontal
USER_ROLE = Qt.ItemDataRole.UserRole if USING_QT6 else Qt.UserRole
PLAIN_TEXT = Qt.TextFormat.PlainText if USING_QT6 else Qt.PlainText
ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter if USING_QT6 else Qt.AlignCenter


def label(text, name=None, parent=None):
    result = QtWidgets.QLabel(text, parent)
    result.setTextFormat(PLAIN_TEXT)
    if name:
        result.setObjectName(name)
    return result


def button(text, callback, tooltip=None, name=None, glyph=None):
    result = QtWidgets.QPushButton(text)
    result.setAccessibleName(tooltip or text)
    result.setToolTip(tooltip or text)
    if name:
        result.setObjectName(name)
    result._glyph = glyph
    if name == "transport_icon":
        result.setFixedSize(40, 40)
    result.clicked.connect(callback)
    result.setCursor(Qt.CursorShape.PointingHandCursor if USING_QT6 else Qt.PointingHandCursor)
    return result


class AlbumArtwork(QtWidgets.QWidget):
    """Keep one thumbnail; show a drawn record when artwork is unavailable.

    Separate limits bound compressed input, decoded image dimensions, and the
    retained pixmap. A small compressed file can still decode to a large image.
    """
    MAX_ARTWORK_BYTES = 12 * 1024 * 1024
    MAX_ARTWORK_PIXELS = 32 * 1024 * 1024
    THUMBNAIL_EDGE = 512  # Enough detail for the 122 px cover on a 4x display.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(132, 132)
        self.setAccessibleName("Album artwork")
        self._pixmap = QtGui.QPixmap()
        self._accent = QtGui.QColor("#7de2c3")
        self._surface = QtGui.QColor("#203340")

    def set_colors(self, accent, surface):
        self._accent, self._surface = QtGui.QColor(accent), QtGui.QColor(surface)
        self.update()

    @classmethod
    def _embedded_artwork(cls, path):
        # Let the metadata container (and any other pictures) go before image
        # decoding. Mutagen still owns its parsing allocations; this only
        # bounds the selected artwork that we hand to Qt and retain afterward.
        from mutagen import File
        audio = File(path)
        data = None
        if audio is not None:
            pictures = getattr(audio, "pictures", None)
            if pictures:
                data = pictures[0].data
            elif audio.tags:
                for key, value in audio.tags.items():
                    if str(key).startswith("APIC"):
                        data = value.data
                        break
                if data is None and audio.tags.get("covr"):
                    data = audio.tags["covr"][0]
        return data if data and len(data) <= cls.MAX_ARTWORK_BYTES else None

    @classmethod
    def _read_thumbnail(cls, source):
        """Read one centered cover, retaining at most a 512 px square pixmap."""
        if isinstance(source, (bytes, bytearray)):
            if not source or len(source) > cls.MAX_ARTWORK_BYTES:
                return QtGui.QPixmap()
            device = QtCore.QBuffer()
            device.setData(source)
        else:
            device = QtCore.QFile(str(source))
        read_only = QtCore.QIODevice.OpenModeFlag.ReadOnly if USING_QT6 else QtCore.QIODevice.ReadOnly
        if not device.open(read_only):
            return QtGui.QPixmap()
        try:
            if device.size() > cls.MAX_ARTWORK_BYTES:
                return QtGui.QPixmap()
            reader = QtGui.QImageReader(device)
            size = reader.size()
            if (not size.isValid() or size.isEmpty()
                    or size.width() * size.height() > cls.MAX_ARTWORK_PIXELS):
                return QtGui.QPixmap()
            # Check dimensions before decoding compressed images. Scaled reads
            # save decoder memory when supported (notably JPEG); other codecs
            # can still allocate their full, explicitly bounded source image.
            side = min(size.width(), size.height())
            reader.setClipRect(QtCore.QRect((size.width() - side) // 2,
                                            (size.height() - side) // 2, side, side))
            edge = min(side, cls.THUMBNAIL_EDGE)
            reader.setScaledSize(QtCore.QSize(edge, edge))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                return QtGui.QPixmap()
            return QtGui.QPixmap.fromImage(image)
        finally:
            device.close()

    def set_media(self, media):
        self._pixmap = QtGui.QPixmap()
        path = media.get("path", "")
        if media.get("type") != "stream" and os.path.isfile(path):
            try:
                data = self._embedded_artwork(path)
                if data:
                    self._pixmap = self._read_thumbnail(data)
            except Exception:
                pass  # Artwork is optional; malformed tags must not block playback.
            if self._pixmap.isNull():
                for filename in ("cover.jpg", "cover.png", "folder.jpg", "front.jpg"):
                    candidate = Path(path).parent / filename
                    try:
                        if candidate.is_file():
                            self._pixmap = self._read_thumbnail(candidate)
                            if not self._pixmap.isNull():
                                break
                    except OSError:
                        continue
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing if USING_QT6 else QtGui.QPainter.Antialiasing)
        bounds = QtCore.QRectF(self.rect()).adjusted(5, 3, -5, -7)
        painter.setPen(Qt.PenStyle.NoPen if USING_QT6 else Qt.NoPen)
        for spread in range(5, 0, -1):
            painter.setBrush(QtGui.QColor(0, 0, 0, 7))
            painter.drawRoundedRect(bounds.translated(0, 2).adjusted(-spread, -spread, spread, spread),
                                    16 + spread, 16 + spread)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(bounds, 16, 16)
        painter.setClipPath(clip)
        if not self._pixmap.isNull():
            smooth = QtGui.QPainter.RenderHint.SmoothPixmapTransform if USING_QT6 else QtGui.QPainter.SmoothPixmapTransform
            painter.setRenderHint(smooth)
            painter.drawPixmap(bounds, self._pixmap, QtCore.QRectF(self._pixmap.rect()))
        else:
            gradient = QtGui.QLinearGradient(bounds.topLeft(), bounds.bottomRight())
            gradient.setColorAt(0, self._surface)
            gradient.setColorAt(1, self._surface.darker(160))
            painter.fillRect(bounds, gradient)
            center = bounds.center()
            halo = QtGui.QRadialGradient(center, 76)
            glow = QtGui.QColor(self._accent)
            glow.setAlpha(24)
            halo.setColorAt(0.2, glow)
            glow.setAlpha(0)
            halo.setColorAt(1, glow)
            painter.fillRect(bounds, halo)
            record = QtGui.QLinearGradient(bounds.topLeft(), bounds.bottomRight())
            record.setColorAt(0, QtGui.QColor("#34434a"))
            record.setColorAt(0.42, QtGui.QColor("#0e171e"))
            record.setColorAt(0.68, QtGui.QColor("#25323b"))
            record.setColorAt(1, QtGui.QColor("#0b151a"))
            painter.setBrush(record)
            painter.drawEllipse(center, 51, 51)
            painter.setBrush(Qt.BrushStyle.NoBrush if USING_QT6 else Qt.NoBrush)
            for radius in (49, 45, 41, 37, 33, 29, 25, 21):
                color = QtGui.QColor(self._accent)
                color.setAlpha(36 if radius != 49 else 125)
                painter.setPen(QtGui.QPen(color, 1))
                painter.drawEllipse(center, radius, radius)
            painter.setPen(Qt.PenStyle.NoPen if USING_QT6 else Qt.NoPen)
            disc = QtGui.QLinearGradient(center - QtCore.QPointF(15, 15), center + QtCore.QPointF(15, 15))
            disc.setColorAt(0, self._accent.lighter(125))
            disc.setColorAt(1, self._accent)
            painter.setBrush(disc)
            painter.drawEllipse(center, 17, 17)
            painter.setBrush(QtGui.QColor("#152b31"))
            painter.drawEllipse(center, 4, 4)
        painter.setClipping(False)
        edge = QtGui.QColor(self._accent)
        edge.setAlpha(60)
        painter.setPen(QtGui.QPen(edge, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush if USING_QT6 else Qt.NoBrush)
        painter.drawRoundedRect(bounds, 16, 16)
        painter.end()


class QueueList(QtWidgets.QListWidget):
    """Import external URLs; let Qt reorder existing rows before syncing them."""
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            sources = [url.toLocalFile() if url.isLocalFile() else url.toString()
                       for url in event.mimeData().urls()]
            self.window._process_dropped_files(sources)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class QueueDelegate(QtWidgets.QStyledItemDelegate):
    """Paint queue metadata without allocating a widget for each track."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def sizeHint(self, option, index):
        return QtCore.QSize(240, 62)

    def paint(self, painter, option, index):
        # Filtering hides items rather than using a proxy model, so row numbers
        # still address the playlist even when some tracks are not visible.
        row = index.row()
        if row >= len(self.window.playlist):
            return
        media = self.window.playlist[row]
        p = ThemeManager.palette(self.window._current_theme)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing if USING_QT6 else QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(option.rect).adjusted(1, 1, -1, -1)
        selected = QtWidgets.QStyle.StateFlag.State_Selected if USING_QT6 else QtWidgets.QStyle.State_Selected
        hovered = QtWidgets.QStyle.StateFlag.State_MouseOver if USING_QT6 else QtWidgets.QStyle.State_MouseOver
        current = row == self.window.current_song_index
        highlighted = bool(option.state & selected) or current
        background = p["active"] if highlighted else p["raised"]
        painter.setPen(Qt.PenStyle.NoPen if USING_QT6 else Qt.NoPen)
        if highlighted or option.state & hovered:
            gradient = QtGui.QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0, QtGui.QColor(background))
            gradient.setColorAt(1, QtGui.QColor(p["surface_top"] if highlighted else p["raised"]))
            painter.setBrush(gradient)
            painter.setPen(QtGui.QPen(QtGui.QColor(p["highlight"] if highlighted else p["border"]), 1))
            painter.drawRoundedRect(rect, 9, 9)
        if current:
            painter.fillRect(QtCore.QRectF(rect.left() + 1, rect.top() + 15, 3, rect.height() - 30), QtGui.QColor(p["accent"]))
        painter.setPen(QtGui.QColor(p["accent"] if current else p["muted"]))
        number_font = QtGui.QFont(option.font)
        number_font.setPixelSize(11)
        painter.setFont(number_font)
        painter.drawText(QtCore.QRectF(rect.left() + 9, rect.top(), 25, rect.height()), int(ALIGN_CENTER),
                         "▶" if current and self.window.player_is_playing() else f"{row + 1:02}")
        x = int(rect.left() + 46)
        width = max(10, int(rect.width() - 114))
        title_font = QtGui.QFont(option.font)
        title_font.setPixelSize(13)
        title_font.setBold(current)
        painter.setFont(title_font)
        painter.setPen(QtGui.QColor(p["text"]))
        elide = Qt.TextElideMode.ElideRight if USING_QT6 else Qt.ElideRight
        title = QtGui.QFontMetrics(title_font).elidedText(media.get("title") or media.get("display", "Untitled"), elide, width)
        painter.drawText(x, int(rect.top() + 23), title)
        detail = media.get("artist") or ("Internet radio" if media.get("type") == "stream" else "Unknown artist")
        if media.get("album"):
            detail += " · " + media["album"]
        else:
            extension = Path(media.get("path", "")).suffix[1:].upper()
            if media.get("type") != "stream" and extension:
                detail += " · " + extension
        painter.setFont(number_font)
        painter.setPen(QtGui.QColor(p["muted"]))
        painter.drawText(x, int(rect.top() + 41), QtGui.QFontMetrics(number_font).elidedText(detail, elide, width))
        duration = int(media.get("duration_ms", 0)) // 1000
        time_label = f"{duration // 60}:{duration % 60:02}" if duration else ("LIVE" if media.get("type") == "stream" else "—")
        painter.drawText(QtCore.QRectF(rect.right() - 59, rect.top(), 48, rect.height()), int(ALIGN_CENTER), time_label)
        focus = QtWidgets.QStyle.StateFlag.State_HasFocus if USING_QT6 else QtWidgets.QStyle.State_HasFocus
        if option.state & focus:
            painter.setPen(QtGui.QPen(QtGui.QColor(p["accent"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush if USING_QT6 else Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        painter.restore()


class PlayerExperienceMixin:
    """MainWindow's presentation and session hooks, sharing its player/queue.

    Playback operations live on MainWindow. These hooks build its widgets and
    react to transport signals; construction and restoration gate session saves
    because assigning widget values can emit the same signals as user actions.
    """
    def _build_ui(self):
        self.status_bar = QtWidgets.QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.setSizeGripEnabled(True)
        central = DepthCanvas(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(16)
        self.header_widget = QtWidgets.QWidget()
        header = QtWidgets.QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        self.brand_mark = BrandMark()
        header.addWidget(self.brand_mark)
        brand = QtWidgets.QVBoxLayout()
        brand.setSpacing(4)
        brand.addWidget(label("Generic Player", "brand"))
        brand.addWidget(label("Your music. Your space.", "subtitle"))
        header.addLayout(brand)
        header.addStretch()
        self.lyrics_button = button("Lyrics", self.toggle_lyrics, "Show lyrics · Ctrl+L")
        self.lyrics_button.setCheckable(True)
        header.addWidget(self.lyrics_button)
        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.setAccessibleName("Color theme")
        self.theme_combo.setToolTip("Color theme")
        self.theme_combo.addItems(ThemeManager.themes())
        self.theme_combo.setCurrentText(self._current_theme)
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        header.addWidget(self.theme_combo)
        layout.addWidget(self.header_widget)

        self.content_splitter = QtWidgets.QSplitter(HORIZONTAL)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(18)
        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.tabBar().hide()
        self.music_tab = QtWidgets.QWidget()
        self.music_tab.setObjectName("queue_panel")
        self.music_tab.setMinimumWidth(290)
        self.tab_widget.addTab(self.music_tab, "Queue")
        PlayerExperienceMixin._setup_music_tab(self)
        self.content_splitter.addWidget(self.tab_widget)

        self.listening_panel = QtWidgets.QFrame()
        self.listening_panel.setObjectName("card")
        self.listening_panel.setMinimumWidth(340)
        now = QtWidgets.QVBoxLayout(self.listening_panel)
        now.setContentsMargins(20, 16, 20, 14)
        now.setSpacing(12)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(label("NOW PLAYING", "eyebrow"))
        top.addStretch()
        self.status_badge = label("READY", "status_badge")
        top.addWidget(self.status_badge)
        now.addLayout(top)
        self.track_header = QtWidgets.QWidget()
        self.track_header.setObjectName("hero_header")
        track = QtWidgets.QHBoxLayout(self.track_header)
        track.setContentsMargins(0, 0, 0, 0)
        track.setSpacing(18)
        self.album_artwork = AlbumArtwork()
        track.addWidget(self.album_artwork)
        details = QtWidgets.QVBoxLayout()
        details.setSpacing(8)
        self.track_title = ElidedLabel("Make room\nfor music", "track_title", lines=2)
        self.track_artist = ElidedLabel("Add a few favorites to get started.", "track_artist")
        self.track_album = ElidedLabel("LOCAL FILES  /  INTERNET RADIO", "eyebrow")
        details.addStretch()
        details.addWidget(self.track_title)
        details.addWidget(self.track_artist)
        details.addWidget(self.track_album)
        details.addStretch()
        track.addLayout(details, 1)
        now.addWidget(self.track_header)

        self.media_stack = QtWidgets.QStackedWidget()
        self.visualizer_card = QtWidgets.QFrame()
        self.visualizer_card.setObjectName("visualizer_card")
        visual_layout = QtWidgets.QVBoxLayout(self.visualizer_card)
        visual_layout.setContentsMargins(14, 14, 14, 10)
        visual_header = QtWidgets.QHBoxLayout()
        visual_header.addWidget(label("SOUND IN MOTION", "eyebrow"))
        visual_header.addStretch()
        self.visualizer_combo = QtWidgets.QComboBox()
        self.visualizer_combo.setAccessibleName("Visualizer mode")
        self.visualizer_combo.addItems(["Spectrum", "Waveform", "Off"])
        self.visualizer_combo.currentTextChanged.connect(self._set_visualizer_mode)
        visual_header.addWidget(self.visualizer_combo)
        visual_layout.addLayout(visual_header)
        self.visualizer = AudioVisualizer(self.player)
        visual_layout.addWidget(self.visualizer, 1)
        self.media_stack.addWidget(self.visualizer_card)
        self.video_widget = QtMultimediaWidgets.QVideoWidget()
        self.video_widget.setObjectName("video_widget")
        self.video_widget.setMinimumSize(240, 140)
        self.media_stack.addWidget(self.video_widget)
        self.player.setVideoOutput(self.video_widget)
        now.addWidget(self.media_stack, 1)
        self.now_hint = ElidedLabel("Drop files into your queue · Space to play", "muted")
        now.addWidget(self.now_hint)
        self.content_splitter.addWidget(self.listening_panel)
        self.content_splitter.setSizes([460, 600])
        layout.addWidget(self.content_splitter, 1)

        self.transport_widget = QtWidgets.QFrame()
        self.transport_widget.setObjectName("transport")
        transport = QtWidgets.QVBoxLayout(self.transport_widget)
        transport.setContentsMargins(18, 12, 18, 12)
        transport.setSpacing(12)
        timeline = QtWidgets.QHBoxLayout()
        timeline.setSpacing(12)
        self.current_time_label = label("00:00:00", "muted")
        timeline.addWidget(self.current_time_label)
        self.playback_slider = QtWidgets.QSlider(HORIZONTAL)
        self.playback_slider.setObjectName("timeline")
        self.playback_slider.setAccessibleName("Playback position")
        self.playback_slider.setRange(0, 0)
        # The slider uses milliseconds; Qt's defaults seek only 1 / 10 ms.
        self.playback_slider.setSingleStep(5000)
        self.playback_slider.setPageStep(30000)
        self.playback_slider.setEnabled(False)
        self.playback_slider.setTracking(False)
        self.playback_slider.valueChanged.connect(self.seek_position)
        self.playback_slider.sliderMoved.connect(lambda value: self.current_time_label.setText(self._millis_to_clock(value)))
        timeline.addWidget(self.playback_slider, 1)
        self.total_time_label = label("00:00:00", "muted")
        timeline.addWidget(self.total_time_label)
        transport.addLayout(timeline)
        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self.shuffle_button = button("Shuffle", self.toggle_shuffle, "Shuffle tracks · S", glyph="shuffle")
        self.shuffle_button.setCheckable(True)
        controls.addWidget(self.shuffle_button)
        self.repeat_button = button("Repeat", self.toggle_repeat, "Cycle repeat: off, queue, track · R", glyph="repeat")
        self.repeat_button.setCheckable(True)
        controls.addWidget(self.repeat_button)
        controls.addStretch()
        self.prev_button = button("", self.prev_song, "Previous track · Ctrl+Left", "transport_icon", "previous")
        controls.addWidget(self.prev_button)
        self.play_button = button("Play", lambda: self.play_pause_song(), "Play · Space", "play_button", "play")
        controls.addWidget(self.play_button)
        self.next_button = button("", self.next_song, "Next track · Ctrl+Right", "transport_icon", "next")
        controls.addWidget(self.next_button)
        self.stop_button = button("", self.stop_song, "Stop playback", "transport_icon", "stop")
        controls.addWidget(self.stop_button)
        controls.addStretch()
        self.mute_button = button("", self.toggle_mute, "Mute · M", "transport_icon", "volume")
        self.mute_button.setCheckable(True)
        controls.addWidget(self.mute_button)
        self.volume_slider = QtWidgets.QSlider(HORIZONTAL)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.setAccessibleName("Volume")
        self.volume_slider.valueChanged.connect(self.change_volume)
        controls.addWidget(self.volume_slider)
        self.volume_label = label("70%", "muted")
        self.volume_label.setMinimumWidth(30)
        controls.addWidget(self.volume_label)
        transport.addLayout(controls)
        layout.addWidget(self.transport_widget)
        central.add_surface(self.music_tab)
        central.add_surface(self.listening_panel)
        central.add_surface(self.transport_widget)
        self._build_menus()
        self._apply_ui_palette(ThemeManager.palette(self._current_theme))
        self._update_controls_enabled()

    def _apply_ui_palette(self, palette):
        self.centralWidget().set_palette(palette)
        self.brand_mark.set_palette(palette)
        for control in self.findChildren(QtWidgets.QPushButton):
            apply_button_icon(control, palette)
        if hasattr(self, "_search_action"):
            self._search_action.setIcon(line_icon("search", palette["muted"], palette["accent"], palette["muted"]))

    def _sync_transport_icons(self):
        """Keep action labels/icons aligned after restore, media keys and clicks."""
        palette = ThemeManager.palette(self._current_theme)
        # During asynchronous loading the button still acts on the accepted
        # request: it must offer Pause even before native playback begins.
        playing = self._desktop_playback_status() == "Playing"
        self.play_button.setText("Pause" if playing else "Play")
        self.play_button._glyph = "pause" if playing else "play"
        self.play_button.setAccessibleName("Pause" if playing else "Play")
        self.play_button.setToolTip("Pause · P / Space" if playing else "Play · P / Space")
        muted = self.mute_button.isChecked()
        self.mute_button._glyph = "muted" if muted else "volume"
        self.mute_button.setText("")
        self.mute_button.setAccessibleName("Unmute" if muted else "Mute")
        self.mute_button.setToolTip("Unmute · M" if muted else "Mute · M")
        self.shuffle_button.setAccessibleName("Shuffle on" if self.shuffle_mode else "Shuffle off")
        self.repeat_button.setAccessibleName({"one": "Repeat one track", "all": "Repeat queue"}.get(self.repeat_mode, "Repeat off"))
        for control in (self.play_button, self.mute_button, self.shuffle_button, self.repeat_button):
            apply_button_icon(control, palette)

    def _setup_music_tab(self):
        layout = QtWidgets.QVBoxLayout(self.music_tab)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(12)
        title_row = QtWidgets.QHBoxLayout()
        title_row.addWidget(label("Your queue", "section_title"))
        title_row.addStretch()
        self.queue_count_label = label("0 tracks", "queue_count")
        title_row.addWidget(self.queue_count_label)
        layout.addLayout(title_row)
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search tracks, artists, albums…")
        self.search_input.setAccessibleName("Search queue")
        self.search_input.setClearButtonEnabled(True)
        position = QtWidgets.QLineEdit.ActionPosition.LeadingPosition if USING_QT6 else QtWidgets.QLineEdit.LeadingPosition
        self._search_action = self.search_input.addAction(QtGui.QIcon(), position)
        self.search_input.textChanged.connect(self.filter_playlist)
        layout.addWidget(self.search_input)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(6)
        self.add_button = button("Files", self.add_songs, "Add media files · Ctrl+O", "primary", "plus")
        actions.addWidget(self.add_button)
        self.folder_button = button("+ Folder", self.add_folder, "Add a music folder · Ctrl+Shift+O")
        actions.addWidget(self.folder_button)
        self.stream_button = button("+ Stream", lambda checked: self.stream_panel.setVisible(checked), "Add an internet stream")
        self.stream_button.setCheckable(True)
        actions.addWidget(self.stream_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.stream_panel = QtWidgets.QWidget()
        stream = QtWidgets.QVBoxLayout(self.stream_panel)
        stream.setContentsMargins(0, 0, 0, 0)
        stream.setSpacing(6)
        self.custom_station_name = QtWidgets.QLineEdit()
        self.custom_station_name.setPlaceholderText("Station name")
        self.custom_station_name.setAccessibleName("Stream name")
        stream.addWidget(self.custom_station_name)
        url_row = QtWidgets.QHBoxLayout()
        self.custom_station_url = QtWidgets.QLineEdit()
        self.custom_station_url.setPlaceholderText("https://…")
        self.custom_station_url.setAccessibleName("Stream URL")
        self.custom_station_url.returnPressed.connect(self.add_custom_stream)
        url_row.addWidget(self.custom_station_url, 1)
        url_row.addWidget(button("Add", self.add_custom_stream, "Add this stream to the queue"))
        stream.addLayout(url_row)
        self.stream_panel.hide()
        layout.addWidget(self.stream_panel)
        self.empty_queue_label = label("A good queue starts with one song.\n\nAdd files or drop a folder here.", "empty_queue")
        self.empty_queue_label.setWordWrap(True)
        self.empty_queue_label.setAlignment(ALIGN_CENTER)
        layout.addWidget(self.empty_queue_label)
        self.playlist_widget = QueueList(self, self.music_tab)
        self.playlist_widget.setAccessibleName("Playback queue")
        selection = QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection if USING_QT6 else QtWidgets.QAbstractItemView.ExtendedSelection
        self.playlist_widget.setSelectionMode(selection)
        drag_mode = QtWidgets.QAbstractItemView.DragDropMode.InternalMove if USING_QT6 else QtWidgets.QAbstractItemView.InternalMove
        self.playlist_widget.setDragDropMode(drag_mode)
        self.playlist_widget.setDefaultDropAction(Qt.DropAction.MoveAction if USING_QT6 else Qt.MoveAction)
        self.playlist_widget.setAcceptDrops(True)
        self.playlist_widget.setDropIndicatorShown(True)
        self.playlist_widget.setItemDelegate(QueueDelegate(self))
        self.playlist_widget.setMouseTracking(True)
        self.playlist_widget.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel if USING_QT6 else QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.playlist_widget.itemDoubleClicked.connect(self.play_selected_song)
        self.playlist_widget.itemSelectionChanged.connect(self._update_controls_enabled)
        self.playlist_widget.model().rowsMoved.connect(self.on_playlist_reordered)
        self.playlist_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu if USING_QT6 else Qt.CustomContextMenu)
        self.playlist_widget.customContextMenuRequested.connect(self._queue_menu)
        layout.addWidget(self.playlist_widget, 1)
        footer = QtWidgets.QHBoxLayout()
        self.remove_button = button("Remove", self.remove_songs, "Remove selected tracks · Delete")
        footer.addWidget(self.remove_button)
        footer.addStretch()
        footer.addWidget(button("Import", self.load_playlist, "Import JSON or M3U playlist · Ctrl+I"))
        footer.addWidget(button("Save", self.save_playlist, "Save JSON or M3U playlist · Ctrl+S"))
        layout.addLayout(footer)
        options = QtWidgets.QHBoxLayout()
        options.addWidget(label("Speed", "muted"))
        self.rate_combo = QtWidgets.QComboBox()
        self.rate_combo.setAccessibleName("Playback speed")
        for rate in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
            self.rate_combo.addItem(f"{rate:g}×", rate)
        self.rate_combo.setCurrentIndex(2)
        self.rate_combo.currentIndexChanged.connect(self._set_playback_rate)
        options.addWidget(self.rate_combo)
        options.addStretch()
        self.sleep_button = QtWidgets.QPushButton("Sleep timer")
        self.sleep_button.setAccessibleName("Sleep timer")
        sleep_menu = QtWidgets.QMenu(self.sleep_button)
        for minutes in (0, 15, 30, 45, 60, 90):
            action = sleep_menu.addAction("Off" if minutes == 0 else f"{minutes} minutes")
            action.triggered.connect(lambda checked=False, value=minutes: self._set_sleep_timer(value))
        self.sleep_button.setMenu(sleep_menu)
        options.addWidget(self.sleep_button)
        layout.addLayout(options)

    def _build_menus(self):
        def action(menu, title, callback, shortcut=None):
            item = QAction(title, self)
            item.triggered.connect(callback)
            if shortcut:
                item.setShortcut(QtGui.QKeySequence(shortcut))
                item.setAutoRepeat(False)
                # The menu bar is hidden in fullscreen. Associate the same
                # action with the window so its shortcut stays available.
                self.addAction(item)
            menu.addAction(item)
            return item
        files = self.menuBar().addMenu("&File")
        action(files, "Add files…", self.add_songs, "Ctrl+O")
        action(files, "Add folder…", self.add_folder, "Ctrl+Shift+O")
        files.addSeparator()
        action(files, "Import playlist…", self.load_playlist, "Ctrl+I")
        action(files, "Save playlist…", self.save_playlist, "Ctrl+S")
        action(files, "Clear queue", self.clear_playlist)
        files.addSeparator()
        action(files, "Quit", self.close, "Ctrl+Q")
        # Alt+P belongs to Play/Pause, so use a different menu mnemonic.
        playback = self.menuBar().addMenu("P&layback")
        action(playback, "Play / pause", self._toggle_play_pause_shortcut, "Alt+P")
        action(playback, "Previous track", self.prev_song, "Ctrl+Left")
        action(playback, "Next track", self.next_song, "Ctrl+Right")
        action(playback, "Stop", self.stop_song)
        view = self.menuBar().addMenu("&View")
        action(view, "Search queue", self._focus_search, "Ctrl+F")
        action(view, "Lyrics", self.toggle_lyrics, "Ctrl+L")
        action(view, "Fullscreen", self._toggle_fullscreen, "F11")
        notifications = view.addMenu("Song notifications")
        self._osd_actions = {}
        group_type = QtGui.QActionGroup if USING_QT6 else QtWidgets.QActionGroup
        self._osd_action_group = group_type(self)
        for mode, title in (("auto", "Automatic"), ("off", "Off"),
                            ("desktop", "Desktop notifications (GNOME / KDE)"),
                            ("wayland", "Wayland OSD"), ("custom", "Custom command")):
            item = action(notifications, title, lambda checked=False, chosen=mode: self._set_osd_mode(chosen))
            item.setCheckable(True)
            item.setChecked(mode == self._osd_mode)
            self._osd_action_group.addAction(item)
            self._osd_actions[mode] = item
        self._osd_actions["auto"].setToolTip("Automatically find a running OSD, desktop notification service, or compatible installed client.")
        self._osd_actions["desktop"].setToolTip("Use GNOME, KDE Plasma, or another standard desktop notification service; request five-second song cards.")
        self._osd_actions["wayland"].setToolTip("Find osd, wayland-osd, wayland_osd, wayland-volume-osd, or an existing compatible socket.")
        self._osd_actions["custom"].setToolTip("Use the command adapter configured with GENERIC_PLAYER_OSD_ARGV; see README for examples.")
        notifications.addSeparator()
        self._osd_retry_action = action(notifications, "Retry current song notification",
                                       lambda: self._set_osd_mode(self._osd_mode))
        self._osd_retry_action.setEnabled(self._osd_mode != "off")
        self._osd_backend_action = notifications.addAction("Waiting for a song notification")
        self._osd_backend_action.setEnabled(False)
        themes = view.addMenu("Color theme")
        self._theme_actions = {}
        for name in ThemeManager.themes():
            item = action(themes, name, lambda checked=False, chosen=name: self.apply_theme(chosen))
            item.setCheckable(True)
            item.setChecked(name == self._current_theme)
            self._theme_actions[name] = item
        help_menu = self.menuBar().addMenu("&Help")
        action(help_menu, "Keyboard shortcuts", self._show_shortcuts, "F1")
        action(help_menu, "About Generic Player", self._show_about)

    def _focus_search(self):
        if self.isFullScreen():
            self._toggle_fullscreen()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _play_context_track(self, identity):
        # A popup runs a nested event loop. Queue updates can delete/rebuild
        # QListWidgetItems while it is open, so retain only the stable ID.
        for row in range(self.playlist_widget.count()):
            item = self.playlist_widget.item(row)
            if item.data(USER_ROLE) == identity:
                if not item.isHidden():
                    self.play_selected_song(item)
                return

    def _queue_menu(self, point):
        menu = QtWidgets.QMenu(self)
        chosen = self.playlist_widget.itemAt(point)
        if chosen:
            # Actions that use selectedItems() must act on the context target.
            # Keep an existing multi-selection when opening its context menu.
            if not chosen.isSelected():
                self.playlist_widget.clearSelection()
                self.playlist_widget.setCurrentItem(chosen)
                chosen.setSelected(True)
            play = menu.addAction("Play this track")
            play.triggered.connect(lambda checked=False, identity=chosen.data(USER_ROLE):
                                   self._play_context_track(identity))
            menu.addAction("Play next", self._queue_selected_next)
            menu.addAction("Remove selected", self.remove_songs)
            menu.addSeparator()
        menu.addAction("Add files…", self.add_songs)
        menu.addAction("Add folder…", self.add_folder)
        if self.playlist:
            menu.addSeparator()
            menu.addAction("Clear queue", self.clear_playlist)
        method = menu.exec if USING_QT6 else menu.exec_
        try:
            method(self.playlist_widget.viewport().mapToGlobal(point))
        finally:
            # A parented popup survives exec() unless it is explicitly freed.
            # Release its actions and their captured queue items on dismissal.
            menu.deleteLater()

    def _queue_selected_next(self):
        current = self.playlist[self.current_song_index] if 0 <= self.current_song_index < len(self.playlist) else None
        selected = {item.data(USER_ROLE) for item in self.playlist_widget.selectedItems() if not item.isHidden()}
        moving = [item for item in self.playlist if item.get("id") in selected and item is not current]
        if not moving:
            return
        moving_objects = {id(item) for item in moving}
        rest = [item for item in self.playlist if id(item) not in moving_objects]
        insert = next((i + 1 for i, item in enumerate(rest) if item is current), 0)
        self.playlist = rest[:insert] + moving + rest[insert:]
        self.current_song_index = next((i for i, item in enumerate(self.playlist) if item is current), -1)
        self._queue_changed()
        if self.shuffle_mode:
            # Explicit queue requests take precedence over the shuffled bag.
            # next() pops from the end; reverse here to preserve selection order.
            navigation = self._navigation()
            priority = [index for index, item in enumerate(self.playlist)
                        if id(item) in moving_objects]
            priority_indexes = set(priority)
            navigation.remaining = [index for index in navigation.remaining if index not in priority_indexes] + list(reversed(priority))
        self.status_bar.showMessage(f"Queued {len(moving)} track(s) to play next.", 4000)

    def _on_track_loaded(self, media):
        self.track_title.setText(media.get("title") or media.get("display") or "Untitled")
        self.track_artist.setText(media.get("artist") or ("Internet radio" if media.get("type") == "stream" else "Unknown artist"))
        media_label = "LIVE STREAM" if media.get("type") == "stream" else " ".join(filter(None, (
            Path(media.get("path", "")).suffix[1:].upper(),
            "VIDEO" if media.get("type") == "video" else "AUDIO")))
        self.track_album.setText(" · ".join(filter(None, (media.get("album") or media_label, media.get("year")))))
        self.album_artwork.set_media(media)
        self.media_stack.setCurrentWidget(self.video_widget if media.get("type") == "video" else self.visualizer_card)
        self.visualizer.reset()
        self.setWindowTitle(f"{media.get('display') or media.get('title', 'Playing')} — Generic Player")
        self.now_hint.setText("F11 for fullscreen · Esc to return" if media.get("type") == "video" else "Space to pause · ← / → to seek 5 seconds")
        self.playlist_widget.viewport().update()
        pending = getattr(self, "_resume_position", None)
        if pending and pending[0] == media.get("path"):
            self._resume_pending = pending[1]
        else:
            self._resume_pending = 0
        self._resume_position = None
        self.player.setPlaybackRate(float(self.rate_combo.currentData() or 1.0))
        # Some backends report seekability/duration synchronously during setSource.
        self._resume_when_ready()
        self._save_session()

    def _on_track_metadata_updated(self, media):
        """Refresh live tags without resetting artwork, playback or bookmarks."""
        self.track_title.setText(media.get("title") or "Untitled")
        self.track_artist.setText(media.get("artist") or ("Internet radio" if media.get("type") == "stream" else "Unknown artist"))
        self.track_album.setText(" · ".join(filter(None, (media.get("album"), media.get("year"))))
                                 or ("LIVE STREAM" if media.get("type") == "stream" else "LOCAL MEDIA"))
        self.setWindowTitle(f"{media.get('display') or media.get('title', 'Playing')} — Generic Player")
        row = self.playlist_widget.item(self.current_song_index)
        if row is not None:
            row.setText(media.get("display") or media.get("title") or "Untitled")
        self.filter_playlist(self.search_input.text())
        self.playlist_widget.viewport().update()

    def _resume_when_ready(self, *args):
        position = getattr(self, "_resume_pending", 0)
        if position and self.player.isSeekable() and self.player.duration() > 0:
            # Consume first: seeking may synchronously emit more player signals.
            self._resume_pending = 0
            self.seek_position(min(position, max(0, self.player.duration() - 500)))

    def _on_playback_ui_changed(self):
        if not hasattr(self, "visualizer"):
            return
        native_playing = self.player_is_playing()
        self.visualizer.set_playing(native_playing)
        status = self._desktop_playback_status()
        playing, paused = status == "Playing", status == "Paused"
        self.status_badge.setText("PLAYING" if native_playing else
                                 "LOADING" if playing else "PAUSED" if paused else "READY")
        self._sync_transport_icons()
        if self.current_media_type == "video":
            if playing or paused:
                self.media_stack.setCurrentWidget(self.video_widget)
            elif getattr(self, "_manual_stop", False):
                # Loading can briefly report Stopped before the first frame.
                # Only an explicit stop should replace the video panel.
                self.media_stack.setCurrentWidget(self.visualizer_card)
        self.playlist_widget.viewport().update()
        self._save_session()

    def _update_controls_enabled(self):
        if not hasattr(self, "play_button"):
            return
        enabled = bool(self.playlist)
        for control in (self.play_button, self.prev_button, self.next_button, self.shuffle_button, self.repeat_button):
            control.setEnabled(enabled)
        selected = any(not item.isHidden() for item in self.playlist_widget.selectedItems())
        self.remove_button.setEnabled(selected)
        # Loading may still report native StoppedState after Play was accepted.
        # Queue selection changes must not disable the way to cancel that load.
        self.stop_button.setEnabled(enabled and (self._desktop_playback_status() != "Stopped" or self.player.position() > 0))
        self.shuffle_button.setChecked(bool(self.shuffle_mode))
        self.repeat_button.setChecked(self.repeat_mode not in (False, "off"))
        self._sync_transport_icons()
        if not (0 <= self.current_song_index < len(self.playlist)):
            self.track_title.setText("Choose your\nnext track" if self.playlist else "Make room\nfor music")
            self.track_artist.setText("Select a track and press Play." if self.playlist else "Add a few favorites to get started.")
            self.track_album.setText("YOUR QUEUE IS READY" if self.playlist else "LOCAL FILES  /  INTERNET RADIO")
            self.album_artwork.set_media({})
            self.media_stack.setCurrentWidget(self.visualizer_card)
            self.visualizer.reset()
            self.status_badge.setText("READY")
            self.setWindowTitle("Generic Player")

    def _set_visualizer_mode(self, text):
        if hasattr(self, "visualizer"):
            self.visualizer.set_mode(text.lower())
            self._save_session()

    def _set_playback_rate(self, index):
        rate = float(self.rate_combo.itemData(index) or 1.0)
        self.player.setPlaybackRate(rate)
        self.status_bar.showMessage(f"Playback speed: {rate:g}×", 3000)
        if getattr(self, "mpris", None):
            self.mpris.notify_playback()
        self._save_session()

    def _set_sleep_timer(self, minutes):
        self._sleep_deadline = time.monotonic() + minutes * 60 if minutes else None
        if not hasattr(self, "_sleep_timer"):
            self._sleep_timer = QtCore.QTimer(self)
            self._sleep_timer.setInterval(1000)
            self._sleep_timer.timeout.connect(self._check_sleep_timer)
        if minutes:
            self._sleep_timer.start()
        else:
            self._sleep_timer.stop()
        self._check_sleep_timer()
        self.status_bar.showMessage(f"Playback will stop in {minutes} minutes." if minutes else "Sleep timer off.", 4000)

    def _check_sleep_timer(self):
        deadline = getattr(self, "_sleep_deadline", None)
        if deadline is None:
            self.sleep_button.setText("Sleep timer")
            return
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        self.sleep_button.setText(f"Sleep {remaining // 60}:{remaining % 60:02}")
        if remaining <= 0:
            self._sleep_deadline = None
            self._sleep_timer.stop()
            self.stop_song()
            self.sleep_button.setText("Sleep timer")
            self.status_bar.showMessage("Sleep timer finished. Playback stopped.")

    def _restore_session(self):
        """Restore controls and a bookmark without starting media playback."""
        from generic_player_settings import load_settings
        self._restoring_session = True
        state = load_settings()
        self.playlist = state["playlist"]
        self.current_song_index = state["current_song_index"]
        self.shuffle_mode = state["shuffle"]
        self.repeat_mode = state["repeat"]
        self._set_osd_mode(state["osd_mode"])
        self._refresh_playlist()
        self.shuffle_button.setChecked(self.shuffle_mode)
        self.shuffle_button.setText("Shuffle on" if self.shuffle_mode else "Shuffle")
        self.repeat_button.setChecked(self.repeat_mode != "off")
        self.repeat_button.setText({"off": "Repeat", "all": "Repeat all", "one": "Repeat one"}[self.repeat_mode])
        self.volume_slider.setValue(state["volume"])
        self.change_volume(state["volume"])
        self.mute_button.setChecked(state["muted"])
        self.toggle_mute()
        rate_index = self.rate_combo.findData(state["playback_rate"])
        self.rate_combo.setCurrentIndex(rate_index if rate_index >= 0 else 2)
        mode = state["visualizer"].capitalize()
        self.visualizer_combo.setCurrentText(mode if mode in ("Spectrum", "Waveform", "Off") else "Spectrum")
        if state["geometry"]:
            try:
                self.restoreGeometry(QtCore.QByteArray.fromBase64(state["geometry"].encode("ascii")))
                # A previous fullscreen session should reopen with accessible controls.
                self.setWindowState(Qt.WindowState.WindowNoState if USING_QT6 else Qt.WindowNoState)
            except (ValueError, UnicodeError):
                pass
        if 0 <= self.current_song_index < len(self.playlist):
            media = self.playlist[self.current_song_index]
            self.track_title.setText(media.get("title", "Ready to listen"))
            self.track_artist.setText(media.get("artist") or "Ready to resume")
            self.track_album.setText(" · ".join(filter(None, (media.get("album"), media.get("year")))) or "YOUR LAST SESSION")
            self.album_artwork.set_media(media)
            self._resume_position = (media["path"], state["position_ms"])
            self.current_time_label.setText(self._millis_to_clock(state["position_ms"]))
        self._restoring_session = False
        self._session_ready = True
        self._session_timer = QtCore.QTimer(self)
        self._session_timer.setInterval(15000)
        self._session_timer.timeout.connect(self._save_session)
        self._session_timer.start()
        self._update_controls_enabled()
        self.status_bar.showMessage("Session restored · press Play to resume." if self.playlist else "Ready · add files, a folder, or a stream.")

    def _save_session(self, immediate=False):
        if not getattr(self, "_session_ready", False) or getattr(self, "_restoring_session", False) or getattr(self, "_closing", False):
            return
        if not hasattr(self, "_session_save_timer"):
            self._session_save_timer = QtCore.QTimer(self)
            self._session_save_timer.setSingleShot(True)
            self._session_save_timer.setInterval(300)
            self._session_save_timer.timeout.connect(self._write_session)
        if immediate:
            self._session_save_timer.stop()
            self._write_session()
        else:
            # Coalesce volume drags and rapid queue edits into one atomic write.
            self._session_save_timer.start()

    def _write_session(self):
        if not getattr(self, "_session_ready", False) or getattr(self, "_restoring_session", False) or getattr(self, "_closing", False):
            return
        from generic_player_settings import save_settings
        position = self.player.position()
        pending = getattr(self, "_resume_position", None)
        # Until the source is loaded and seekable, the backend may still report
        # zero. Preserve the saved bookmark across unrelated preference edits.
        if pending and 0 <= self.current_song_index < len(self.playlist) and self.playlist[self.current_song_index]["path"] == pending[0]:
            position = pending[1]
        elif getattr(self, "_resume_pending", 0) and not getattr(self, "_manual_stop", False):
            position = self._resume_pending
        saved = save_settings({"playlist": self.playlist, "current_song_index": self.current_song_index,
                              "position_ms": position, "volume": self.volume_slider.value(),
                              "muted": self.mute_button.isChecked(), "shuffle": self.shuffle_mode,
                              "repeat": self.repeat_mode, "playback_rate": self.rate_combo.currentData(),
                              "visualizer": self.visualizer_combo.currentText(),
                              "osd_mode": self._osd_mode,
                              "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii")})
        if not saved:
            self.status_bar.showMessage("Could not save session settings. Check config permissions or reduce the queue size.", 6000)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            if getattr(self, "_maximized_before_fullscreen", False):
                self.showMaximized()
            else:
                self.showNormal()
            self._show_normal_ui()
            previous = getattr(self, "_focus_before_fullscreen", None)
            self._focus_before_fullscreen = None
            focus = previous() if previous is not None else None
            try:
                valid = (focus is not None and focus.isVisible() and focus.isEnabled()
                         and self._owns_keyboard_window(QtWidgets.QWidget.window(focus)))
            except RuntimeError:
                valid = False  # The focused widget may have been deleted while hidden.
            if not valid:
                focus = self.playlist_widget if self.playlist_widget.isVisible() else self.media_stack.currentWidget()
            focus.setFocus()
        else:
            focus = QtWidgets.QApplication.focusWidget()
            self._focus_before_fullscreen = (weakref.ref(focus) if focus is not None
                and self._owns_keyboard_window(QtWidgets.QWidget.window(focus)) else None)
            self._maximized_before_fullscreen = self.isMaximized()
            self.showFullScreen()
            self._hide_ui_for_fullscreen()
            # Hiding the queue otherwise lets Qt focus the visualizer combo;
            # Space then opens its popup instead of controlling playback.
            self.media_stack.currentWidget().setFocus()

    def _hide_ui_for_fullscreen(self):
        self.header_widget.hide()
        self.tab_widget.hide()
        self.transport_widget.hide()
        self.track_header.hide()
        self.now_hint.hide()
        self.menuBar().hide()
        self.status_bar.hide()
        self._lyrics_before_fullscreen = bool(getattr(self, "lyrics", None) and self.lyrics.isVisible())
        if getattr(self, "lyrics", None):
            self.lyrics.hide_panel()

    def _show_normal_ui(self):
        for widget in (self.header_widget, self.tab_widget, self.transport_widget, self.track_header, self.now_hint, self.menuBar(), self.status_bar):
            widget.show()
        if getattr(self, "_lyrics_before_fullscreen", False) and getattr(self, "lyrics", None):
            self.lyrics.show_panel()

    def _owns_keyboard_window(self, window):
        if window is self:
            return True
        if not isinstance(window, QtWidgets.QDockWidget):
            return False
        # QWidget.isAncestorOf stops at a floating window boundary even when
        # its QObject ownership still belongs to this player.
        owner = QtCore.QObject.parent(window)
        while owner is not None:
            if owner is self:
                return True
            owner = QtCore.QObject.parent(owner)
        return False

    def eventFilter(self, obj, event):
        key_press = QtCore.QEvent.Type.KeyPress if USING_QT6 else QtCore.QEvent.KeyPress
        if event.type() != key_press or getattr(self, "_closing", False):
            return False
        app = QtWidgets.QApplication
        # The application filter also sees unrelated dialogs and top-level
        # windows. Only this player and its floating docks own these keys.
        if not isinstance(obj, QtWidgets.QWidget) or app.activeModalWidget() is not None:
            return False
        # QueueList keeps its owning player in an attribute named "window".
        # Call QWidget's method explicitly so custom widget fields cannot
        # shadow the native top-level lookup inside this Qt callback.
        active, target = app.activeWindow(), QtWidgets.QWidget.window(obj)
        if not self._owns_keyboard_window(active) or not self._owns_keyboard_window(target):
            return False
        focus = app.focusWidget()
        keys = Qt.Key if USING_QT6 else Qt
        modifiers = Qt.KeyboardModifier if USING_QT6 else Qt
        pressed = event.key()
        if event.modifiers() & (modifiers.ControlModifier | modifiers.AltModifier | modifiers.MetaModifier):
            return False
        # Capital playback letters work, but Shift+arrows, Shift+Space and
        # other modified navigation retain their normal widget behavior.
        if event.modifiers() & modifiers.ShiftModifier and pressed not in (keys.Key_P, keys.Key_M, keys.Key_S, keys.Key_R):
            return False
        # Dedicated media keys remain useful while typing into queue search.
        media_keys = (keys.Key_MediaTogglePlayPause, keys.Key_MediaPlay, keys.Key_MediaPause,
                      keys.Key_MediaNext, keys.Key_MediaPrevious, keys.Key_MediaStop)
        if event.isAutoRepeat() and pressed in media_keys:
            return True
        if pressed == keys.Key_MediaTogglePlayPause:
            self._toggle_media_play_pause_shortcut()
        elif pressed == keys.Key_MediaPlay:
            if self._desktop_playback_status() != "Playing":
                self.play_pause_song()
        elif pressed == keys.Key_MediaPause:
            if self._desktop_playback_status() == "Playing":
                self._pause_playback()
                self._mpris_notify(playback=True)
                self._refresh_playback_ui()
        elif pressed in (keys.Key_MediaNext, keys.Key_MediaPrevious):
            self.next_song() if pressed == keys.Key_MediaNext else self.prev_song()
        elif pressed == keys.Key_MediaStop:
            self.stop_song()
        else:
            # Popups own arrows, Space and Escape. In particular, navigating a
            # menu must not seek playback or close fullscreen behind the menu.
            if app.activePopupWidget() is not None or isinstance(focus, (QtWidgets.QMenu, QtWidgets.QMenuBar)):
                return False
            if pressed == keys.Key_Escape and self.isFullScreen():
                self._toggle_fullscreen()
                return True
            if pressed == keys.Key_Escape and focus is self.search_input:
                self.search_input.clear()
                self.playlist_widget.setFocus()
                return True
            if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)) and not focus.isReadOnly():
                return False
            if isinstance(focus, QtWidgets.QComboBox) and focus.isEditable():
                return False
            if event.isAutoRepeat() and pressed in (keys.Key_P, keys.Key_Space, keys.Key_M, keys.Key_S, keys.Key_R, keys.Key_Return, keys.Key_Enter):
                return True
            return self._handle_playback_key(pressed, focus, keys)
        return True

    def _handle_playback_key(self, pressed, focus, keys):
        if pressed in (keys.Key_P, keys.Key_Space):
            self._toggle_play_pause_shortcut()
        elif pressed == keys.Key_M:
            self.mute_button.click()
        elif pressed == keys.Key_S:
            self.shuffle_button.click()
        elif pressed == keys.Key_R:
            self.repeat_button.click()
        elif pressed == keys.Key_Delete and focus is self.playlist_widget:
            self.remove_songs()
        elif pressed in (keys.Key_Return, keys.Key_Enter) and focus is self.playlist_widget:
            self.play_selected_song()
        elif pressed in (keys.Key_Return, keys.Key_Enter) and isinstance(focus, QtWidgets.QAbstractButton):
            # Space belongs to playback; Enter still activates a focused
            # button, including the sleep timer's menu, for keyboard access.
            menu = focus.menu() if isinstance(focus, QtWidgets.QPushButton) else None
            if menu is not None:
                menu.popup(focus.mapToGlobal(focus.rect().bottomLeft()))
            else:
                focus.click()
        elif pressed in (keys.Key_Left, keys.Key_Right) and not isinstance(focus, (QtWidgets.QSlider, QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox, QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
            self.seek_position(self.player.position() + (-5000 if pressed == keys.Key_Left else 5000))
        else:
            return False
        return True

    def _show_shortcuts(self):
        QtWidgets.QMessageBox.information(self, "Keyboard shortcuts",
            "P / Space / Alt+P   Play or pause\nCtrl+Left / Ctrl+Right   Previous / next track\n"
            "Left / Right   Seek 5 seconds\nM   Mute\nS   Shuffle\nR   Cycle repeat mode\n\n"
            "Ctrl+O   Add files\nCtrl+Shift+O   Add folder\nCtrl+I / Ctrl+S   Import / save playlist\n"
            "Ctrl+F   Search queue\nEsc in search   Clear filter and return to queue\n"
            "Enter   Play selected track or activate focused button\nDelete   Remove selected tracks\n\n"
            "Ctrl+L   Lyrics\nF11 / Esc   Fullscreen / return\nCtrl+Q   Quit\n\n"
            "Playback letter keys are inactive while typing. Drag tracks to reorder your queue.")

    def _show_about(self):
        QtWidgets.QMessageBox.about(self, "About Generic Player",
            f"Generic Player {__version__}\n\nA lightweight music and video player.\n"
            "Built with PyQt6 / PyQt5.\n\nCopyright JJ Posti · GPL v2\ntechtimejourney.net")
