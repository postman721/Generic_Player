## Generic Player

A lightweight, keyboard-friendly media player written in Python (PyQt6 with automatic fallback to PyQt5).
A spiritual successor to my now-obsolete Albix Player, continuing its legacy with a cleaner, more maintainable codebase.

<img width="1016" height="729" alt="Image" src="https://github.com/user-attachments/assets/3b8f658d-a249-404f-9fba-0ebf64bf7408" />

Default style	

### Features
- Plays local audio/video files and internet streams.
- Playlist: add/remove, drag-reorder, save/load (JSON).
- Controls: Play/Pause, Stop, Next/Prev, Seek, Volume, Mute.
- Drag & drop files into the playlist.
- Optional lyrics pane (toggleable).
- MPRIS / playerctl integration (OSD + media keys).
- Multiple switchable themes.

<img width="1001" height="727" alt="Image" src="https://github.com/user-attachments/assets/38de3541-80a7-4101-8b5a-8193e85747c1" />

One of the alternative styles.

### Install (Debian/Ubuntu)

**PyQt6 (recommended)**
```bash
sudo apt-get update
sudo apt-get install \
  python3-pyqt6 python3-pyqt6.qtmultimedia \
  libqt6multimedia6 libqt6multimediawidgets6 \
  ffmpeg libavcodec-extra \
  python3-requests python3-mutagen \
  playerctl
```

**Wayland (Qt6)**
```bash
sudo apt-get install qt6-wayland
```

**PyQt5 alternative**
```bash
sudo apt-get install \
  python3-pyqt5 python3-pyqt5.qtmultimedia \
  libqt5multimedia5-plugins \
  python3-requests python3-mutagen \
  playerctl
```

(Qt5 often benefits from extra GStreamer codecs: `gstreamer1.0-plugins-*` + `gstreamer1.0-libav`.)

### Run
```bash
chmod +x *.py
python3 generic_player.py
```

### Shortcuts
- **Alt+P** — Play/Pause  
- **Space / Media keys** — Play/Pause  
- **F11** — Fullscreen (Esc or F11 to exit)  
- **Ctrl+L** — Toggle lyrics (if module present)
- **Physical key on keyboard for play/pause** — Play/Pause   


### Notes
- If a file won’t play, you likely need codecs (FFmpeg/libavcodec-extra for Qt6, or GStreamer plugins for Qt5).
- Online lyrics use a public endpoint; Lyric success may vary.
- Physical key on keyboard for Play/Pause: Might show wrong OSD status on Wayland compositors.

### License
GPL v2 — JJ Posti (techtimejourney.net) 2025
