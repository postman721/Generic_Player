# Changelog

## 1.3.1 — 2026-11-01

### Keyboard and playback UI regression fixes

- Add plain P as a play/pause key alongside Space and Alt+P. Keep playback
  letters and Space available after clicking player buttons, choosing an item
  in a noneditable selector, or focusing read-only lyrics. Enter activates a
  focused player button, including a button's menu.
- Preserve text editing, editable selectors, dialog confirmation, open-menu
  navigation, and native slider/selector/text-view arrow keys. Limit application
  key handling to the player and its own floating docks.
- Move focus to the media view on entering fullscreen and restore the previous
  usable control on return. Use a weak reference so a deleted control is not
  retained or accessed. Escape in queue search clears the filter and returns
  focus to the queue.
- Accept separate rapid P/Space/Alt+P presses and Play-button clicks. Limit the
  duplicate-event guard to physical media-toggle keys and continue ignoring
  auto-repeat for toggle actions.
- Handle dedicated MediaPlay and MediaPause using the accepted playback state
  while a decoder is still loading. Keep Stop available during an accepted
  load even when a queue selection refreshes the controls. Show the matching
  Play/Pause action and LOADING/PAUSED badge; start visualizer activity only
  when the backend actually plays.
- Remove redundant keyPressEvent handling for menu shortcuts. F11 and Ctrl+L
  now use their exact QAction bindings without also accepting modified keys
  such as Ctrl+F11 or Ctrl+Shift+L.
- Update the shortcut dialog, README, and man page. Preserve optional OSD
  discovery, metadata cards, MPRIS controls, and installation ownership rules.
  Keep regression verification outside the delivered source package.


## 1.3.0 — 2026-11-01

### Automatic and custom song notifications

- Default new notification preferences to Automatic, while preserving saved
  choices, including Off. Detect compatible running Wayland sockets, active
  desktop notification services, and verified installed Wayland clients.
  Explicit command or socket configuration takes priority over discovery.
- Recognize `osd`, `wayland-osd`, `wayland_osd`, and `wayland-volume-osd`
  in `/usr/bin`, `/usr/local/bin`, and `PATH`. Check client compatibility before
  invoking its normal media command; a matching filename alone is insufficient.
- Support GNOME Shell, KDE Plasma, and custom implementations through their
  public desktop notification service. Resolve its actual owner, identify the
  server, and adapt card text to its body and markup capabilities. Keep song
  details in the summary when a service does not support notification bodies.
- Add `GENERIC_PLAYER_OSD_ARGV` for custom command interfaces: a bounded JSON
  argument array with literal executable selection and validated metadata
  fields. Pass arguments directly without a shell and bound process lifetime,
  output, and retained work.
- Show the successfully selected service in the notification menu. Refresh
  that diagnostic after a retry or recovery, and report invalid explicit
  configuration without silently selecting a different service.
- Avoid empty-queue notifications and cancel stale pending cards on queue
  clear or mode changes. Track notification ownership and closure so expired
  IDs are not reused and a late reply can close only the player's own card.
- Preserve asynchronous delivery, finite fallback, metadata coalescing, and
  cleanup of owned processes, sockets, and D-Bus signal connections. Playback
  and MPRIS remain independent of every optional notification adapter.
- Document automatic discovery, custom examples, and timing limitations in
  the README and man page. Desktop cards request 5000 ms; the receiving
  service controls their actual display duration. Dependency checks inspect
  files and configuration without launching clients or contacting services.
- Keep quality checks outside the source release. The source download
  includes application code, desktop data, screenshots, and installation
  helpers, with no tests, test runner, TOML, or PyPI packaging configuration.

## 1.2.1 — 2026-10-31

### OSD command discovery and recovery

- Locate the optional `/usr/bin/osd` executable, compatible `osd` and
  `wayland-volume-osd` installations, and explicit command/socket overrides.
  Distinguish a missing executable from an unavailable notification socket.
- Search supported runtime locations independently so different launch
  environments do not hide an already-running, user-owned OSD socket.
- In the explicitly selected Wayland_OSD mode, recognize the installed CLI
  before using its normal media client to start a missing daemon. Continue
  sending complete artist/title/year cards through its JSON socket. Preserve
  an existing daemon's settings and keep OSD optional for installation.
- Add an explicit notification retry action and allow reselection of the
  current mode to retry a song after a failed delivery.
- Bound subprocess output, execution time and pending work; release owned
  processes and sockets when notifications are disabled or the player closes.
  Retain normal metadata deduplication and the default Off mode.
- Handle empty process reads after timeout or cancellation without allowing an
  exception to abort the Qt application. Discard unused client output and bound
  help reads before copying them into Python.
- Normalize the OSD client's runtime-directory environment so an invalid
  relative XDG path cannot create an unreachable socket in the working folder.

## 1.2.0 — 2026-10-31

### Desktop controls and optional song notifications

- Replace the GLib/pydbus bridge with native Qt D-Bus MPRIS controls, using the
  existing Qt event loop. Publish typed metadata, actual playback changes and
  seek events; support multiple player instances without taking another name.
- Preserve artist, title, album, date and year from file tags through playlists
  and saved sessions. Refresh decoder metadata for live streams and discard
  stale dates when the stream changes songs. Unknown years remain unknown.
- Add optional song notifications under View: desktop notifications requesting
  five seconds, or a connection to an already-running Wayland_OSD. Notifications
  default to off and are independent of playback and playerctl. Wayland_OSD's
  shared hide timeout remains the user's setting; the README explains 5000 ms.
- Coalesce duplicate notification updates, bound text and pending work, and
  release socket, D-Bus and timer resources on shutdown. Never start or install
  an OSD daemon automatically.
- Preserve paused/stopped state during remote Next/Previous without briefly
  playing audio. Remote PlayPause handles each command; physical media keys
  retain their duplicate-event guard. Remove speculative playback-state hints.
- Retain queue-size, session-save, decoder-cleanup and installer ownership
  fixes. Remove obsolete test commands and dependency advice from source setup.
  Quality checks are maintained outside the delivered source; no tests or test
  runner are included in this release.

## 1.1.0 — 2026-10-31

### Code clarification and lean source downloads

- Document module responsibilities, playback callback guards, Qt resource lifetimes, lyrics cancellation, queue identities, input limits, and ownership-safe installation. Add a short code guide without expanding the main setup instructions.
- Share the Qt5/Qt6 playback-state presentation path and remove duplicate desktop playback snapshots when metadata already refreshes capabilities. Preserve playback notification and event polling when metadata conversion fails.
- Normalize M3U records as they are parsed, avoiding a second complete raw-entry list. A local 10,000-entry fixture used about 24% less peak Python allocation; no import-speed improvement is claimed.
- Compute shared visualizer colors and geometry once per paint rather than repeatedly inside drawing loops. All 192 checked before/after renders matched; no new persistent cache, timer, or lifecycle state was added.
- Remove the unused hardcoded online-lyrics demo from the application module.
- Keep regression tests and CI/release tooling in the development checkout and omit them from downloadable TAR.GZ/ZIP source packages. Retain all application source, desktop data, screenshots, documentation, and install/Debian helpers.
- Share a standalone dependency checker between source downloads and the development runner. Keep `make check`, `make install`, `make uninstall`, and `make deb` usable without test files; explain the development-checkout requirement when `make test` is requested in a release download.
- Correct the README's mismatched clone/extraction permission paths and explain when owner-access repair is needed.
- Require a completion receipt from the test worker, so an early zero exit cannot pass verification. One preliminary focused run ended before its summary; the cause remains unconfirmed, and incomplete runs now fail the gate.
- Prevent programmatic timeline resets and range clamps from emitting user-seek callbacks. This avoids seeking the backend again after Stop or while presenting a new duration, while preserving ordinary user seeks and the slider's previous signal-blocking state.

### Playback, input, and fullscreen follow-up

- Respect synchronous decoder errors, Stop, and source replacement during playback loading. Keep saved-position seeks valid without allowing obsolete load callbacks to overwrite the active track's status or lyrics.
- Honor Pause before deferred end-of-track handling, including Repeat one restarts.
- Keep menu shortcuts active in fullscreen, resolve the Alt+P menu-mnemonic conflict, and make Search reveal the queue while restoring the previous window state.
- Resolve context-menu Play actions by stable track identity so queue rebuilds or removals cannot leave callbacks holding deleted Qt items.
- Sanitize all M3U record separators in titles and encode affected local filenames as file URLs, preserving one track per record during export/import.
- Share local file-URL validation across command-line input, playlists, and desktop controls; preserve case-insensitive local authorities and reject ambiguous or malformed input consistently.
- Defer lyrics sidecar, tag, and online work until the panel is visible. Retain only the latest hidden track, reuse an in-flight or completed request when reopened, and recognize every supported media extension during filename lookup.
- Skip metadata extraction for duplicate local imports, including files already supplied by a playlist in the same drop.
- Cache eight small theme-color derivations with independent caller dictionaries. Bound theme preferences to 64 KiB and recover safely from excessive JSON nesting or malformed configuration.
- Stage and verify source archive candidates before replacing downloads, preserving existing archives when source reads, archive creation, or verification fail.
- Capture timed Python stack traces in native integration-test children without relaxing their assertions or 30-second deadline.

#### Remaining review findings

- The intermittent offscreen Qt 6.12/FFmpeg video/fullscreen/Stop timeout recurred during the clarification review. Timed stacks localized the wait to the programmatic slider reset after native Stop returned. Unintended seek feedback from that reset is now prevented; the precise native lock mechanism remains unconfirmed.
- Session saving still normalizes the queue on the UI thread. A local 10,000-entry fixture took about 0.43 seconds for an atomic save; reducing that latency needs a separate persistence change that preserves snapshot consistency and bounded memory.

### Additional optimization and resource review

- Reuse a small, bounded cache of visualizer coefficients and reduce spectrum bands without allocating per-band masks; preserve the original spectrum, waveform, and peak calculations.
- Ignore queued PCM after pause, hiding, or shutdown; clear stale visualizer data and release audio probes safely even after partial initialization or backend deletion.
- Delete dismissed queue menus and their actions, including failure paths, so repeated context-menu use does not retain menu objects and track references.
- Make large Play next selections and shuffled queue traversal linear; build shuffle state only when needed and release it when shuffle is disabled.
- Bound long-running shuffle history while preserving recent Previous/Next navigation. The retained history window scales with queue length, with a minimum window of 1,000 choices.
- Stream folder candidates and playlist/session serialization; skip unsupported paths early and continue importing after an individual unreadable file.
- Handle unresolvable local paths, including symlink loops, as input errors during playlist import and desktop OpenUri requests.
- Enforce actual read/write byte limits for playlists (8 MiB) and sessions (12 MiB). Oversized or invalid saves preserve the previous file instead of creating an unreadable or empty replacement.
- Reject excessive M3U entries during parsing and iterate lines without creating a second full line list.
- Cancel obsolete resume seeks before changing sources and cancel deferred track completion when the listener seeks back into the current track.
- Keep the mute icon, tooltip, accessible name, and saved state synchronized when desktop controls change volume without moving the slider.
- Hash installed destinations in bounded chunks while retaining the existing ownership, modification, symlink, and rollback protections.
- Remove the README's link to the absent advanced guide; retain the simplified setup instructions and screenshots.

### Documentation and memory review

- Simplify the README while retaining Debian/Ubuntu, Arch, and Fedora setup, direct source launch, safe installation/removal, tests, and source-permission guidance; move detailed options into an advanced guide.
- Embed the attached Studio, Light, and compact screenshots using their actual paths, and include the screenshots directory in rebuilt source archives.
- Stream and cap decoded lyrics responses, bound returned/local lyric text, close HTTP responses on every path, and release completed or cancelled worker results promptly.
- Retain small, centered artwork thumbnails instead of full-resolution covers, reject excessive image dimensions before decoding, and keep the record fallback for unusable artwork.
- Copy and analyze only the newest 4096 complete PCM frames, avoiding large discarded buffer copies and correctly visualizing recent audio in oversized buffers.
- Avoid an observed native video Stop deadlock by invoking the synchronous Qt slot through a binding that releases the Python interpreter lock; apply the same path during shutdown and desktop-control fallback.

### Visual polish and compact layout

- Add native panel shadows, subtle surface gradients, highlighted borders, consistent focus states, and a drawn sound mark across all eight themes.
- Refine the artwork fallback with a shaded record, accent label, and rounded frame.
- Add crisp playback icons with descriptive tooltips and accessible action names; keep active shuffle, repeat, pause, and mute controls within a 920 × 660 window.
- Bound long metadata without losing the complete text: title uses two lines, artist and album elide to one line, and literal text is preserved in tooltips and accessibility labels.
- Refine real-audio spectrum bars with soft glow and peak caps, add a layered waveform, and improve idle states and responsive frequency labels without adding timers.
- Paint shadows behind the panels instead of attaching graphics effects to video or visualization parents.
- Preserve fixed dark text and clear blue highlighting in file dialogs; add compact-layout, metadata, and keyboard-mute regression coverage.

### Arch and Fedora manual installation

- Document verified Arch and Fedora runtime dependency commands alongside Debian/Ubuntu.
- Verify manual install, update, launcher, and uninstall with package managers, shared-cache tools, and SELinux commands unavailable, preserving the existing ownership protections.
- Add optional Debian-test exclusion through `make test TEST_ARGS=--skip-debian`; keep all application and manual-install tests enabled.
- Use distribution-neutral dependency diagnostics and a VP8/Vorbis video test fixture compatible with Fedora's standard FFmpeg packages.
- Add Arch and Fedora container CI jobs for runtime, application, and manual-install checks.

### Complete source checkout and Linux installation

- Document cloning and running the full Git repository directly; source archives are optional.
- Add Makefile commands for running, dependency checks, tests, manual installation/removal, and Debian package creation.
- Install private application source under `/opt/generic-player` with a working `generic-player` launcher independent of the current directory.
- Track manual-install ownership and checksums, preflight conflicts, reject unsafe paths, preserve modified or unrelated files, and roll back ordinary installation I/O failures.
- Keep manual-install ownership separate from Debian package ownership, with collision checks before package installation.
- Add populated `data/applications`, `data/icons`, `data/man`, and `data/metainfo` directories and install them under the selected shared-data prefix.
- Build and verify a rootless Debian package with declared system dependencies, executable launcher, consistent file modes, and root-owned payload.
- Parse help/version and invalid command-line arguments before loading Qt; support safely decoded local file URLs and `--` for option-like filenames.
- Include the Makefile and all desktop/build data in the optional source archives and build Debian artifacts in the release workflow.

### UI and test reliability follow-up

- Keep Space on focused buttons, arrows and Escape in menus, and normal typing in search; dedicated media keys continue to control playback while typing.
- Target the right-clicked queue entry without losing an existing selected group.
- Keep Stop available when a track is paused at its beginning, and make timeline arrow/page steps seek five/thirty seconds.
- Restore maximized windows after fullscreen and label video tracks correctly.
- Clear stale waveform and peak data when decoded audio stops arriving; use theme text colors in lyrics headings and source labels.
- Preserve dark file-dialog item text and clear selection colors across every theme, with a permanent palette regression.
- Cancel pending session-resume seeks on Stop and preserve the exact queue entry when selecting a missing file from a playlist with repeated sources.
- Align desktop Previous and stopped-state Next commands with the player's transport state.
- Preserve colon, leading-space, and comment-like filenames in exported M3U playlists, and recover from excessively nested JSON settings or playlists.
- Add a source-aware test runner with dependency diagnostics, isolated settings, checked native subprocesses, explicit skip reports, and failure for empty or all-skipped suites.
- Document GitHub-download permissions, optional executable entrypoints, focused test commands, and common test failures.
- Assign executable archive permissions only to the three interpreter-bearing entrypoints; helper modules and test files remain readable source files.

### Source revision

- Run directly with `python3 generic_player.py` after installing system dependencies.
- Simplified release output to plain TAR.GZ and ZIP source archives, with a standard-library archive builder and checksums.
- Removed package-build configuration, generated metadata, and the installed-console-launcher requirement.
- File and folder dialogs use dark item text, distinct hover feedback, and a clear selection highlight across all player themes.

### Added

- Redesigned native listening view with searchable queue, Now Playing information, artwork, persistent transport controls, and eight coordinated themes.
- Actual decoded-audio spectrum and waveform visualization; on/off control and reduced work while paused or hidden.
- Folder import, working drag reorder, Play next, JSON/M3U/M3U8 playlist import/export, and additional audio/video filename formats.
- Repeat off/all/one and shuffle navigation that avoids repeating tracks within a cycle and preserves Previous history.
- Session restore, playback speed from 0.5× to 2×, sleep timer, expanded shortcuts, shortcut help, and fullscreen visualization.
- Shared Qt binding selection with `GENERIC_PLAYER_QT_API=5` or `6` for diagnostics and compatibility testing.
- Source release script, checksums, application icon, and system-dependency CI workflows.

### Fixed

- Tracks advancing up to one second before their actual end.
- Duplicate or stale end-of-track callbacks advancing multiple tracks, including after Stop or source replacement.
- Qt5 end-of-media status comparisons and backends that do not emit an end signal after reaching the exact duration.
- Active-track identity drift when removing or reordering queue entries.
- Playback/source state left behind when replacing or clearing a playlist.
- Malformed playlist items, unsafe stream schemes, corrupt audio tags, and partial settings/playlist writes.
- Seek controls fighting a dragged timeline or allowing invalid live-stream seeks.
- Missing optional MPRIS dependencies preventing startup.
- Lyrics changes blocking the UI or deleting a still-running QThread; stale results after rapid track changes and shutdown.
- Artist/title parsing and lyrics URL escaping for numbered filenames and punctuation.
- MPRIS explicit Play/Pause behavior, stream URLs, stable track IDs, seek bounds, Position, capability flags, repeat, shuffle, rate, volume, and shutdown cleanup.

### Compatibility

- Python 3.10 or newer.
- Qt6 visualization uses `QAudioBufferOutput` from Qt 6.8+ with the FFmpeg backend. Older Qt6 remains usable without visualization.
- Qt5 visualization requires backend audio-probe support.
- Audio device output and live desktop media integration require the target desktop environment; headless checks cannot prove those integrations.

## 1.0 — 2025-12-25

- Initial Generic Player release with local audio/video playback, streams, JSON playlists, optional lyrics, desktop media controls, and themes.
