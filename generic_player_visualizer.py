"""Analyze decoded PCM and paint it without changing playback.

Audio callbacks copy a bounded tail of the backend buffer and replace the latest
analysis result. The display timer only smooths those results; it never queues
audio or invents samples when playback stalls.
"""

import time
from functools import lru_cache

try:
    import numpy as np
except ImportError:
    np = None

from generic_player_qt import QtCore, QtGui, QtWidgets, QtMultimedia, USING_QT6

ANALYSIS_FRAMES = 4096
SPECTRUM_BANDS = 48
WAVEFORM_POINTS = 192


def _build_analysis_plan(frames, sample_rate, bands):
    """Prepare immutable FFT/window and waveform coordinates, never PCM data."""
    window = np.hanning(frames)
    scale = max(float(window.sum()) / 2, 1)
    frequencies = np.fft.rfftfreq(frames, 1.0 / sample_rate)
    edges = np.geomspace(35, max(36, min(18000, sample_rate / 2)), bands + 1)
    # The frequency bins are sorted. Left insertion points preserve the
    # original low-inclusive/high-exclusive bands, including empty low bands.
    bounds = np.searchsorted(frequencies, edges, side="left")
    active = np.flatnonzero(np.diff(bounds))
    first, last = int(bounds[0]), int(bounds[-1])
    starts = bounds[:-1][active] - first
    sample_positions = np.arange(frames, dtype=np.float64)
    waveform_positions = np.linspace(0, frames - 1, WAVEFORM_POINTS)
    for values in (window, active, starts, sample_positions, waveform_positions):
        values.setflags(write=False)
    return window, scale, first, last, active, starts, sample_positions, waveform_positions


@lru_cache(maxsize=4)
def _analysis_plan(frames, sample_rate):
    # Only the fixed 48-band display is cached. Frames are already bounded to
    # ANALYSIS_FRAMES by analyze_samples, so even changing audio formats cannot
    # retain an unbounded number or size of coefficient arrays (~265 KiB max).
    return _build_analysis_plan(frames, sample_rate, SPECTRUM_BANDS)


def analyze_samples(samples, sample_rate, bands=SPECTRUM_BANDS):
    """Return display bands (0..1), signed waveform and peak from mono PCM.

    The Hann window reduces spectral leakage; each logarithmic band keeps its
    strongest FFT bin. A -66 dB floor maps silence to zero. The waveform is
    interpolated independently, so spectrum smoothing cannot distort its shape.
    """
    if np is None:
        return [0.0] * bands, [0.0] * WAVEFORM_POINTS, 0.0
    # Slice before conversion/sanitization so a long caller-owned array does
    # not create equally long float and finite-value copies for a short FFT.
    values = np.asarray(samples).reshape(-1)[-ANALYSIS_FRAMES:]
    values = np.nan_to_num(values.astype(np.float32, copy=False))
    if len(values) < 8 or not np.isfinite(sample_rate) or sample_rate <= 0:
        return [0.0] * bands, [0.0] * WAVEFORM_POINTS, 0.0
    # nan_to_num's default copy keeps this in-place clipping off caller data.
    np.clip(values, -1.0, 1.0, out=values)
    peak = float(np.max(np.abs(values)))
    plan = (_analysis_plan(len(values), sample_rate) if bands == SPECTRUM_BANDS
            else _build_analysis_plan(len(values), sample_rate, bands))
    window, scale, first, last, active, starts, sample_positions, waveform_positions = plan
    magnitudes = np.abs(np.fft.rfft(values * window))
    magnitudes /= scale
    levels = np.zeros(bands)
    if active.size:
        levels[active] = np.maximum.reduceat(magnitudes[first:last], starts)
    np.maximum(levels, 1e-8, out=levels)
    np.log10(levels, out=levels)
    levels *= 20
    levels += 66
    levels /= 66
    np.clip(levels, 0, 1, out=levels)
    waveform = np.interp(waveform_positions, sample_positions, values)
    return levels.tolist(), waveform.tolist(), peak


def buffer_samples(buffer):
    """Own a bounded copy of the newest complete Qt5/Qt6 PCM frames.

    No NumPy view of the backend pointer escapes this function. The returned
    mono data can safely outlive the QAudioBuffer supplied by the callback.
    """
    if np is None or not buffer.isValid() or buffer.byteCount() <= 0:
        return None, 0
    fmt = buffer.format()
    channels = max(1, int(fmt.channelCount()))
    if USING_QT6:
        name = fmt.sampleFormat().name
        dtype = {"UInt8": "u1", "Int16": "i2", "Int32": "i4", "Float": "f4"}.get(name)
    else:
        bits = int(fmt.sampleSize())
        kind = fmt.sampleType()
        audio_format = QtMultimedia.QAudioFormat
        prefix = ">" if fmt.byteOrder() == audio_format.BigEndian else "<"
        if kind == audio_format.Float and bits in (32, 64):
            dtype = prefix + "f" + str(bits // 8)
        elif kind == audio_format.SignedInt and bits in (8, 16, 32):
            dtype = prefix + "i" + str(bits // 8)
        elif kind == audio_format.UnSignedInt and bits in (8, 16, 32):
            dtype = prefix + "u" + str(bits // 8)
        else:
            dtype = None
    if not dtype:
        return None, 0
    dtype = np.dtype(dtype)
    frame_bytes = dtype.itemsize * channels
    usable = int(buffer.byteCount())
    usable -= usable % frame_bytes
    count = min(usable, ANALYSIS_FRAMES * frame_bytes, 4 * 1024 * 1024)
    count -= count % frame_bytes
    if not count:
        return None, 0
    # Only the most recent complete frames contribute to the display. Read
    # that tail directly instead of copying/converting megabytes of old PCM.
    pointer = buffer.constData()
    tail = type(pointer)(int(pointer) + usable - count)
    raw = tail.asstring(count)
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if dtype.kind == "u":
        midpoint = float(2 ** (8 * dtype.itemsize - 1))
        samples = (samples - midpoint) / midpoint
    elif dtype.kind == "i":
        samples /= float(2 ** (8 * dtype.itemsize - 1))
    samples = samples.reshape(-1, channels)
    mono = samples.mean(axis=1)
    if channels > 1:
        # A stereo phase inversion can cancel a naïve mono downmix even though
        # both loudspeakers are playing. Preserve a real channel in that case.
        energy = np.mean(samples * samples, axis=0)
        strongest = int(np.argmax(energy))
        if float(np.mean(mono * mono)) < float(energy[strongest]) * 0.01:
            mono = samples[:, strongest]
    samples = mono
    return samples, int(fmt.sampleRate())


class AudioVisualizer(QtWidgets.QWidget):
    """Low-cost 30 fps visualizer using decoded audio from the actual player."""

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.mode = "spectrum"
        self.playing = False
        self.available = False
        self.backend = "unavailable"
        self._shutdown = False
        self._levels = [0.0] * SPECTRUM_BANDS
        self._targets = [0.0] * SPECTRUM_BANDS
        self._waveform = [0.0] * WAVEFORM_POINTS
        self._last_buffer_at = 0.0
        self._last_position_ms = 0
        self._peak = 0.0
        self._accent = QtGui.QColor("#7de2c3")
        self._muted = QtGui.QColor("#8293a5")
        self._grid = QtGui.QColor("#293846")
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.setMinimumHeight(100)
        self.setAccessibleName("Audio visualizer")
        self.setAccessibleDescription("Spectrum or waveform from the currently playing audio.")
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding if USING_QT6 else QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding if USING_QT6 else QtWidgets.QSizePolicy.Expanding)
        self._probe = None
        self.player.positionChanged.connect(self._position_changed)
        if np is not None:
            try:
                if USING_QT6 and hasattr(QtMultimedia, "QAudioBufferOutput"):
                    self._probe = QtMultimedia.QAudioBufferOutput(self)
                    self._probe.audioBufferReceived.connect(self._receive_buffer)
                    self.player.setAudioBufferOutput(self._probe)
                    self.available = True
                    self.backend = "Qt audio buffer"
                elif not USING_QT6:
                    self._probe = QtMultimedia.QAudioProbe(self)
                    self._probe.audioBufferProbed.connect(self._receive_buffer)
                    self._probe.flush.connect(self.reset)
                    self.available = bool(self._probe.setSource(self.player))
                    self.backend = "Qt audio probe" if self.available else "unavailable"
            except (AttributeError, RuntimeError, TypeError):
                self.available = False

    def set_colors(self, accent, muted, grid):
        self._accent, self._muted, self._grid = map(QtGui.QColor, (accent, muted, grid))
        self.update()

    def set_mode(self, mode):
        self.mode = mode if mode in ("spectrum", "waveform", "off") else "spectrum"
        if self.mode == "off":
            self.reset()
        self._update_timer()
        self.update()

    def set_playing(self, playing):
        self.playing = bool(playing) and not self._shutdown
        if not self.playing:
            self.reset()
        self._update_timer()
        self.update()

    def reset(self):
        """Discard audio from the previous playback position, retaining no PCM."""
        self._levels = [0.0] * SPECTRUM_BANDS
        self._targets = [0.0] * SPECTRUM_BANDS
        self._waveform = [0.0] * WAVEFORM_POINTS
        self._last_buffer_at = 0.0
        self._peak = 0.0
        self.update()

    def _position_changed(self, position):
        """Flush stale samples when stopped, rewound, or seeking to a new time."""
        previous = self._last_position_ms
        self._last_position_ms = int(position)
        if (position < previous or abs(position - previous) > 1000
                or (not self.playing and position != previous)):
            self.reset()

    def _receive_buffer(self, buffer):
        if self._shutdown or self.mode == "off" or not self.isVisible():
            return
        try:
            if not buffer.isValid() or buffer.byteCount() <= 0:
                self.reset()
                return
            # Buffers already queued by the backend may arrive after pause.
            # Keep empty/end buffers meaningful, but skip decoding paused PCM.
            if not self.playing:
                return
            samples, rate = buffer_samples(buffer)
            if samples is None:
                # The backend sends an empty buffer when decoded audio ends.
                self.reset()
                return
            self._targets, self._waveform, self._peak = analyze_samples(samples, rate)
            self._last_buffer_at = time.monotonic()
        except (ValueError, TypeError, RuntimeError, OverflowError):
            self.reset()

    def _update_timer(self):
        # Backend availability alone is not enough: hidden/paused/off views
        # need neither animation ticks nor their associated repaint work.
        if self.playing and self.mode != "off" and self.isVisible() and self.available:
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self):
        if time.monotonic() - self._last_buffer_at >= 0.3:
            # A stalled stream must not leave its last waveform looking live.
            self._targets = [0.0] * SPECTRUM_BANDS
            self._waveform = [0.0] * WAVEFORM_POINTS
            self._peak = 0.0
        targets = self._targets
        self._levels = [current + (target - current) * (0.68 if target > current else 0.2)
                        for current, target in zip(self._levels, targets)]
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_timer()

    def hideEvent(self, event):
        self._timer.stop()
        self.reset()
        super().hideEvent(event)

    def shutdown(self):
        """Stop accepting callbacks before disconnecting and releasing the tap."""
        if self._shutdown:
            return
        self._shutdown = True
        self.playing = False
        self.available = False
        self._timer.stop()
        self.reset()
        try:
            self.player.positionChanged.disconnect(self._position_changed)
        except (TypeError, RuntimeError):
            pass
        probe, self._probe = self._probe, None
        if probe is not None:
            connections = (("audioBufferReceived", self._receive_buffer),) if USING_QT6 else (
                ("audioBufferProbed", self._receive_buffer), ("flush", self.reset))
            for signal, callback in connections:
                try:
                    getattr(probe, signal).disconnect(callback)
                except (AttributeError, TypeError, RuntimeError):
                    pass
            try:
                if USING_QT6:
                    self.player.setAudioBufferOutput(None)
                else:
                    probe.setSource(None)
            except (AttributeError, TypeError, RuntimeError):
                # A failed attachment or an already-deleted player must not
                # strand the probe or prevent the rest of window shutdown.
                pass
            finally:
                try:
                    probe.deleteLater()
                except RuntimeError:
                    pass  # Its Qt parent may already have destroyed it.

    @staticmethod
    def _transparent(color, alpha):
        result = QtGui.QColor(color)
        result.setAlpha(alpha)
        return result

    def _paint_grid(self, painter, area):
        """A quiet instrument grid, kept separate from the measured signal."""
        painter.setPen(QtGui.QPen(self._transparent(self._grid, 90), 1))
        for row in range(1, 5):
            y = area.top() + area.height() * row / 5
            painter.drawLine(QtCore.QPointF(area.left(), y), QtCore.QPointF(area.right(), y))
        painter.setPen(QtGui.QPen(self._transparent(self._grid, 55), 1))
        for column in range(1, 4):
            x = area.left() + area.width() * column / 4
            painter.drawLine(QtCore.QPointF(x, area.top()), QtCore.QPointF(x, area.bottom()))
        painter.setPen(QtGui.QPen(self._transparent(self._muted, 75), 1))
        painter.drawLine(area.bottomLeft(), area.bottomRight())

    def _paint_spectrum(self, painter, area):
        # All heights still come directly from the smoothed, decoded FFT bands.
        # Native translucent strokes provide depth without a blur effect or a
        # separate animation/timer. Silent bands only have a quiet baseline tick.
        step = area.width() / len(self._levels)
        width = max(1.0, step * 0.65)
        radius = min(2.5, width / 2)
        left, bottom, available_height = area.left(), area.bottom(), area.height()
        # Every bar shares these colors and dimensions for this paint. Keep
        # them local: resizing or changing a theme needs no cache invalidation.
        baseline = self._transparent(self._accent, 65)
        glow = self._transparent(self._accent, 17)
        middle = self._transparent(self._accent, 205)
        foot = self._transparent(self._accent, 48)
        cap = self._transparent(self._accent.lighter(120), 235)
        no_pen = QtCore.Qt.PenStyle.NoPen if USING_QT6 else QtCore.Qt.NoPen
        painter.setPen(no_pen)
        for index, level in enumerate(self._levels):
            x = left + step * index + (step - width) / 2
            if level <= 0.005:
                painter.setBrush(baseline)
                painter.drawRoundedRect(QtCore.QRectF(x, bottom - 1, width, 1), 0.5, 0.5)
                continue
            height = max(1.0, min(1.0, level) * available_height)
            bar = QtCore.QRectF(x, bottom - height, width, height)
            if height > 4:
                painter.setBrush(glow)
                painter.drawRoundedRect(bar.adjusted(-1.5, -2, 1.5, 0), radius + 1, radius + 1)
            gradient = QtGui.QLinearGradient(bar.topLeft(), bar.bottomLeft())
            gradient.setColorAt(0, self._accent)
            gradient.setColorAt(0.28, middle)
            gradient.setColorAt(1, foot)
            painter.setBrush(QtGui.QBrush(gradient))
            painter.drawRoundedRect(bar, radius, radius)
            if height > 4:
                # A thin cap makes the actual top of each bar easy to resolve.
                painter.setBrush(cap)
                painter.drawRoundedRect(QtCore.QRectF(x, bar.top(), width, 1.5), 0.7, 0.7)

    def _paint_waveform(self, painter, area):
        path = QtGui.QPainterPath()
        left, width, height = area.left(), area.width(), area.height()
        center_y = area.center().y()
        intervals = max(1, len(self._waveform) - 1)
        for index, sample in enumerate(self._waveform):
            x = left + width * index / intervals
            y = center_y - sample * height * 0.44
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        # Fill towards the zero line, so the shape remains faithful to the PCM
        # waveform. Transparent, layered strokes give a crisp luminous edge.
        fill = QtGui.QPainterPath(path)
        fill.lineTo(area.right(), center_y)
        fill.lineTo(left, center_y)
        fill.closeSubpath()
        gradient = QtGui.QLinearGradient(area.topLeft(), area.bottomLeft())
        gradient.setColorAt(0, self._transparent(self._accent, 42))
        gradient.setColorAt(0.5, self._transparent(self._accent, 3))
        gradient.setColorAt(1, self._transparent(self._accent, 42))
        painter.fillPath(fill, QtGui.QBrush(gradient))
        round_cap = QtCore.Qt.PenCapStyle.RoundCap if USING_QT6 else QtCore.Qt.RoundCap
        round_join = QtCore.Qt.PenJoinStyle.RoundJoin if USING_QT6 else QtCore.Qt.RoundJoin
        for width, alpha in ((7.0, 18), (4.0, 36), (1.8, 255)):
            pen = QtGui.QPen(self._transparent(self._accent, alpha), width)
            pen.setCapStyle(round_cap)
            pen.setJoinStyle(round_join)
            painter.setPen(pen)
            painter.drawPath(path)

    def _idle_message(self):
        if self.mode == "off":
            return "Visualization off", "Choose Spectrum or Waveform to turn it on."
        if np is None:
            return "Visualization needs NumPy", "Install your distribution's NumPy package."
        if not self.available:
            return "Visualization unavailable", "Requires Qt 6.8+ or a Qt5 audio probe."
        if not self.playing:
            return "Ready to listen", "Play a track to see its sound."
        if not self._last_buffer_at or time.monotonic() - self._last_buffer_at > 2:
            return "Waiting for audio", "Visualization follows decoded playback."
        return None

    def _paint_idle(self, painter, area, title, description):
        center = QtCore.Qt.AlignmentFlag.AlignCenter if USING_QT6 else QtCore.Qt.AlignCenter
        font = QtGui.QFont(painter.font())
        font.setPixelSize(13)
        font.setWeight(QtGui.QFont.Weight.DemiBold if USING_QT6 else QtGui.QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(self._muted)
        metrics = QtGui.QFontMetrics(font)
        elide = QtCore.Qt.TextElideMode.ElideRight if USING_QT6 else QtCore.Qt.ElideRight
        title = metrics.elidedText(title, elide, max(1, int(area.width() - 12)))
        painter.drawText(QtCore.QRectF(area.left(), area.center().y() - 15, area.width(), 23),
                         int(center), title)
        font.setPixelSize(10)
        font.setWeight(QtGui.QFont.Weight.Normal if USING_QT6 else QtGui.QFont.Normal)
        painter.setFont(font)
        description = QtGui.QFontMetrics(font).elidedText(description, elide, max(1, int(area.width() - 12)))
        painter.drawText(QtCore.QRectF(area.left(), area.center().y() + 11, area.width(), 19),
                         int(center), description)
        if area.height() >= 110:
            painter.setPen(QtGui.QPen(self._transparent(self._accent, 135), 2))
            painter.drawLine(QtCore.QPointF(area.center().x() - 16, area.center().y() - 32),
                             QtCore.QPointF(area.center().x() + 16, area.center().y() - 32))

    def _paint_axis(self, painter, area, active):
        font = QtGui.QFont(painter.font())
        font.setPixelSize(10)
        font.setWeight(QtGui.QFont.Weight.Normal if USING_QT6 else QtGui.QFont.Normal)
        painter.setFont(font)
        painter.setPen(self._muted)
        left = QtCore.Qt.AlignmentFlag.AlignLeft if USING_QT6 else QtCore.Qt.AlignLeft
        right = QtCore.Qt.AlignmentFlag.AlignRight if USING_QT6 else QtCore.Qt.AlignRight
        center = QtCore.Qt.AlignmentFlag.AlignCenter if USING_QT6 else QtCore.Qt.AlignCenter
        vertical = QtCore.Qt.AlignmentFlag.AlignVCenter if USING_QT6 else QtCore.Qt.AlignVCenter
        footer = QtCore.QRectF(area.left(), self.height() - 23, area.width(), 18)
        if self.mode == "spectrum":
            painter.drawText(footer, int(left | vertical), "35 Hz")
            if area.width() >= 180:
                # Logarithmic frequency position, matching the FFT band edges.
                middle = QtCore.QRectF(area.left() + area.width() * 0.537 - 25,
                                      footer.top(), 50, footer.height())
                painter.drawText(middle, int(center), "1 kHz")
            painter.drawText(footer, int(right | vertical), "18 kHz")
        elif self.mode == "waveform":
            painter.drawText(footer, int(left | vertical), "WAVEFORM")
            if active:
                painter.drawText(footer, int(right | vertical), "DECODED AUDIO")

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing if USING_QT6 else QtGui.QPainter.Antialiasing)
        area = QtCore.QRectF(8, 12, max(1, self.width() - 16), max(1, self.height() - 44))
        message = self._idle_message()
        painter.save()
        if message:
            painter.setOpacity(0.45)
        self._paint_grid(painter, area)
        painter.restore()
        if message:
            self._paint_idle(painter, area, *message)
        elif self.mode == "waveform":
            self._paint_waveform(painter, area)
        elif self.mode == "spectrum":
            self._paint_spectrum(painter, area)
        self._paint_axis(painter, area, active=message is None)
        painter.end()
