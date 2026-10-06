# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

Nothing yet.

## [1.5.0] - 2026-10-05

### Added

- **Play on a phone - no app needed.** When you host, your PC now also serves a
  web version of the player side. Phones open the invite link (or scan the QR
  code from the new **📱 Phones** button in the lobby), join, claim a
  character and record their lines right in the browser. Takes are encoded as
  the same WAV the desktop records, so they assemble exactly like desktop
  takes. Phone and desktop players mix freely in one room. Reconnects after a
  reload or a locked screen keep the phone's character.
- **One invite link for everyone.** The lobby's Copy button now copies a link
  (`https://.../r/CODE`) that works on phones and in the desktop app's invite
  box. The old invite code still works ("Code for older desktop apps").
- The relay streams pack files individually with HTTP Range support (needed
  for video on iPhone Safari).

### Changed

- Players now stay in a room for 90 s without a sign of life (was 40 s), so a
  phone whose screen locks briefly doesn't lose its character.

## [1.4.0] - 2026-10-03

### Added

- **Auto-captions in DubMaker.** An "✨ Auto-captions" button transcribes every
  region's audio and fills the subtitles for you (faster-whisper). The speech
  model is downloaded once on first use (~150 MB) into `models/` next to the
  app, so the download stays small and it works offline afterwards. If the
  speech library is missing (source install), it points you to Setup.bat.

## [1.3.0] - 2026-10-03

### Added

- **Reconnect to a room.** Each player keeps a stable key, so if your app
  closes or crashes you can rejoin the same room and keep your claimed
  characters and recordings.
- **Roll back an update.** Each packaged update now snapshots the previous build
  first; a "Roll back to vX" button on the menu restores it and relaunches if an
  update ever breaks something. Your packs, recordings and settings are kept.
- **DubMaker updates itself too**, the same way DubStage does.

### Fixed

- **Leaving a room actually removes you.** Players who leave (or whose app
  closes) are dropped from the lobby and their character is freed for others,
  instead of lingering as a "ghost" that blocked the character.

## [1.2.1] - 2026-10-02

### Fixed

- **Online assembly no longer silently drops the dubs.** The host now rebuilds
  the scene from the room's own pack on the server (exactly what everyone
  recorded against) plus every uploaded take, instead of merging into the host's
  in-memory copy. Every assemble writes a `last_assemble.txt` report next to the
  app (pack, per-line take status, matched counts, final mix level) so a bad
  result can be diagnosed instead of guessed at.

## [1.2.0] - 2026-10-02

### Added

- **Host online games straight from your own machine — no setup, no
  port-forwarding.** In DubStage → **Play online**, press **🚀 Host on my
  machine**. DubStage runs the relay in-process and exposes it through a
  Cloudflare *quick tunnel* (`cloudflared`), so a public `https://…trycloudflare.com`
  address appears without any account, router changes or configuration. The
  `cloudflared` helper is fetched once into `tools/` on first use, like ffmpeg.
- **Single invite code.** Hosting now produces one invite code that carries the
  server address *and* the room code together; a **📋 Copy** button in the lobby
  puts it on the clipboard. Players paste that one code into **Join** — no server
  address to type. A bare room code still works together with the server field
  under **Use my own / a hosted server**.
- Players joining a freshly opened self-hosted room retry patiently (up to ~90 s)
  while the tunnel's DNS propagates, instead of failing on the first attempt.
- **Shared updates in a room.** Everyone's app version is shared in the lobby,
  and anyone running an older version than the newest person in the room sees an
  **Update** button that updates in place with no extra steps. The auto-updater
  now points at this project's own repository.
- **The packaged (.exe) build now updates itself.** It downloads the new build
  archive attached to the GitHub release, and a helper waits for the app to
  close, swaps the programs and `_internal/` folder, and relaunches — your
  `packs/`, `dubs/`, `tools/` and settings are left untouched. If a release has
  no build archive attached, it falls back to opening the releases page.
- Online assembly reports what it gathered and warns instead of silently
  building a dub-less video; failed take uploads are surfaced in the lobby.

### Changed

- The relay can now run inside DubStage (`dubstage_server.start_background`) in
  addition to standing alone on a hosted server; harmless connection resets from
  tunnels/browsers no longer print tracebacks.

## [1.1.0] - 2026-08-15

### Added

- **Update notice inside both tools.** On start they ask GitHub once whether a
  newer release exists — at most every six hours, and the answer is remembered
  so the banner also appears when no request is made. The banner names the new
  version, expands to show that release's changelog, and updates the tools on
  one click: the archive is downloaded, checked, the app closes, the files are
  replaced and the app starts again.
- `packs/`, `dubs/`, `tools/` and the settings files are never touched by an
  update. Only known project files are replaced, everything else in the archive
  is discarded before the swap, and the previous `.pyw`, `.py`, `.bat` and `.md`
  files are copied to a backup folder in `%TEMP%` first. A log of every step
  lands next to it.
- Guard rails on the way in: HTTPS only, GitHub hosts only, this repository
  only, a size limit on the download, no archive paths pointing outside the
  target folder, and every `.py`/`.pyw` from the archive is compiled before
  anything is replaced — a truncated download cannot leave a broken install.
- The check can be switched off by setting `"check_updates": false` in
  `dubforge_settings.json` or `dubstage_settings.json`. Nothing but the release
  information is ever requested, and nothing is sent.

### Fixed

- **YouTube downloads failed with HTTP 403 on newer ffmpeg builds.** With
  `--download-sections`, yt-dlp hands the video URL to ffmpeg and lets it fetch
  the data; that request does not carry what the URL was signed for, and Google
  refuses it. DubForge now falls back to downloading the whole video with
  yt-dlp's own downloader and cutting it locally with the existing trim path —
  slower, but it works regardless of the ffmpeg build. The fast section
  download is still tried first.

- **"Update yt-dlp" updated a different yt-dlp than the one that runs.** The
  button always went through `pip` for the interpreter running DubForge, while
  the version shown — and the binary actually used for downloads — comes from
  `shutil.which`, which finds any `yt-dlp.exe` in `PATH` first. With both
  present, pip reported success and nothing changed. The button now updates
  whatever `ytdlp()` resolves to: a standalone build updates itself with `-U`,
  a pip launcher falls back to pip from *its own* installation. The startup log
  and the yt-dlp line now name the file in use, and if the version is unchanged
  after an update the dialog says so and explains why instead of claiming
  success.

- **DubForge was unusable in a non-maximised window.** The three steps were
  packed straight into the window, so anything past the bottom edge — step 3,
  "Build pack", the progress bar and the log — was simply gone, with no way to
  reach it. The content now sits in a scrollable canvas. It is stretched to the
  window as long as there is room, so a maximised window looks exactly as
  before; below that it scrolls.
- The mouse wheel scrolls the page, except over widgets that scroll or count on
  their own: the waveform still zooms, the clip list and the log scroll
  themselves and hand the wheel back to the page once they hit their end, and
  the spinbox keeps counting.
- Minimum window size lowered from 980×740 to 900×480, and both tools now open
  no taller than the screen allows. At 1180×880 DubForge did not fit on a 1080p
  display once the taskbar and title bar were subtracted — which is how the
  problem arose in the first place.
- Scrollbars were unstyled and showed up pale grey against the dark interface.

## [1.0.1] - 2026-08-11

### Added

- **yt-dlp maintenance.** DubForge reports the installed yt-dlp version and its
  age at startup, warns beyond 60 days, and offers a one-click update. The
  updater detects how yt-dlp was installed: a standalone binary in `tools/`
  updates itself with `-U`, a pip installation goes through pip. If a download
  fails and the version is older than 30 days, the log points at it as the
  likely cause — YouTube changes its delivery constantly and a stale yt-dlp is
  by far the most common reason for failures.
- **Logo** in light and dark variants (`docs/logo.png`, `docs/logo-dark.png`),
  switched automatically via `<picture>` and `prefers-color-scheme`. The dark
  variant lifts the wordmark to a light tone and the wave from `#4b24ed` to
  `#7c5cff`; on GitHub's dark background the original values reach only 1.10
  and 2.50 contrast, below the 3.0 minimum for graphics.
- **Screenshots** in the README.
- **`.gitattributes`** — LF inside the repository, CRLF on checkout for `.bat`
  and `.cmd`. Keeps `LICENSE` from showing up as fully rewritten whenever line
  endings differ between systems.

### Fixed

- **Read-only dropdowns were unreadable.** Their colours come from a ttk state
  table that `configure()` does not reach, so the light default background
  survived. Now set through `map()` with an explicit `readonly` entry, in both
  tools. The popup list is a plain Tk widget that ttk does not style at all and
  is now coloured via `option_add` — it would have stayed white.
- **Batch files had LF-only line endings.** cmd.exe handles those unreliably,
  particularly around labels and `goto`. All `.bat` files converted to CRLF and
  pinned via `.gitattributes`.
- Status text announced "converting to OGV" although MP4 has been written since
  the format switch; two dialogs still spoke of copying "into the game".

### Removed

- `Push to GitHub.bat` is no longer part of the repository. It is a maintenance
  helper, not part of the project. The push script now untracks anything that
  matches `.gitignore` but is still tracked, so the file stays on disk while
  disappearing from GitHub.

## [1.0.0] - 2026-08-10

First public release. Two Windows desktop tools, German and English interface,
switchable at runtime.

### DubForge — building packs

- Source from a YouTube link or a local file, limited to a chosen time span.
  The stream-copy cut is verified against the expected duration and re-encoded
  when it lands off target, because keyframe seeking is frequently inaccurate.
- Optional vocal separation with Demucs; falls back to the original audio when
  it is unavailable, losing only the backing track.
- Automatic clip detection from the loudness envelope, with adjustable
  sensitivity and maximum clip length. Long segments are split at their
  quietest point.
- Waveform editor: drag edges to trim, drag empty space for a new clip, split,
  rename, delete, listen. Mouse wheel zooms.
- Subtitles per clip. Enter saves and moves to the next clip, so a whole pack
  can be captioned without touching the mouse.
- Clips exported at −1 dBFS peak so that loudness does not distort the
  comparison later.
- Video written as MP4/H.264. Roughly four times faster to encode than the
  previous Theora path and about 40 % smaller.

### DubStage — recording

- Line-by-line workflow: hear the original, record over it, play your own take
  back, as often as you like. Any line can be left empty and keeps the original
  voice.
- **Comparison strip** — the original as a silhouette with your take drawn over
  it on a shared time axis, live while recording. Both curves are normalised to
  their own peak, so what you judge is timing and rhythm rather than level.
- Subtitles shown below the video, and running along as real subtitles during
  the final playback.
- Finale plays the whole scene with your recordings mixed over the backing
  track; export as MP4.
- Microphone test with level readout and playback.
- Video is split into JPEG frames once and cached instead of being decoded
  during playback, at 25 fps and 960 px. Playback timing derives each frame
  deadline from the start time rather than adding a fixed delay, which is the
  difference between a nominal 25 fps and 19 effective.

### Packs

- A pack is a plain folder. Each clip carries its start time in the file name
  (`07_MyLine_44-048.wav` = 44.048 s), subtitles live in `_captions.json`.
  No database and no binary index, so packs stay readable and hand-editable.

### Robustness

- Recording can never leave the interface stuck: button states are set before
  any drawing happens, the frame loop tolerates drawing errors, a watchdog ends
  the recording even if the loop stalls, and every phase carries a deadline
  after which the interface is released.
- Array lengths are aligned before mixing a take with the backing track.
  `int(len(x) / sr * sr)` does not reliably return `len(x)`; for roughly 8 % of
  clip lengths it lands one sample short, which previously raised mid-playback
  and froze the interface.

[Unreleased]: https://github.com/xmrius/dubstage/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/xmrius/dubstage/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/xmrius/dubstage/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/xmrius/dubstage/releases/tag/v1.0.0
