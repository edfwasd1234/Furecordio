# -*- coding: utf-8 -*-
"""
dubstage_core.py  --  Spiel-Logik fuer DubStage / game logic.

Video laeuft, Zeile nachsprechen, am Ende laeuft die ganze Szene
mit der eigenen Stimme. Keine GUI hier drin.
"""

import io
import os
import re
import glob
import shutil
import struct
import tempfile
import wave

import numpy as np

import dubforge_core as pc

APP_DIR = pc.APP_DIR          # frozen-aware (neben der .exe bzw. dieser Datei)
PACKS_DIR = os.path.join(APP_DIR, "packs")
CACHE_DIR = os.path.join(tempfile.gettempdir(), "dubstage_cache")

SR = 44100                 # Arbeits-Samplerate / working sample rate
FRAME_FPS = 25             # Bilder pro Sekunde fuer die Wiedergabe
FRAME_W = 960              # Breite der Einzelbilder - nie hochskalieren


# ==========================================================================
#  Packs finden / find packs
# ==========================================================================

class Line(object):
    """Eine Zeile des Dub-Packs / one line of a dub pack."""

    def __init__(self, path, start, index):
        self.path = path
        self.file = os.path.basename(path)
        self.start = float(start)
        self.index = index
        self.name = self._label()
        self.caption = ""       # Untertitel / subtitle
        self.character = ""     # Figur / character (fuer Mehrspieler)
        self.audio = None       # Original-Sample / original sample
        self.duration = 0.0
        self.take = None        # Aufnahme des Spielers / player recording

    def _label(self):
        stem = os.path.splitext(self.file)[0]
        stem = re.sub(r"^\d+[_\-]", "", stem)
        stem = re.sub(r"_\d+-\d{1,3}$", "", stem)
        return stem.replace("_", " ").strip() or self.file

    @property
    def end(self):
        return self.start + self.duration


class DubPack(object):

    def __init__(self, folder):
        self.folder = folder
        self.name = os.path.basename(folder)
        self.video = None
        self.backing = None
        self.lines = []
        self.characters = []    # geordnete Figurenliste / ordered characters
        self.frames = []
        self.fps = FRAME_FPS
        self.video_duration = 0.0

    def __repr__(self):
        return "<DubPack %s, %d Zeilen>" % (self.name, len(self.lines))


_TS_IN_NAME = re.compile(r"_(\d+)-(\d{1,3})(?:\.[A-Za-z0-9]+)?$")


def timestamp_from_name(filename):
    """'07_MyClip_44-048.wav' -> 44.048 ; sonst None."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _TS_IN_NAME.search(stem)
    if not m:
        return None
    whole, frac = m.group(1), m.group(2)
    return float(whole) + float(frac) / (10 ** len(frac))


def timestamps_from_txt(folder):
    """Liest _TIMESTAMPS.txt als Rueckfallebene / fallback."""
    path = os.path.join(folder, "_TIMESTAMPS.txt")
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"(\S+\.wav)\s+(-?[\d.]+)", line)
                if m:
                    out[m.group(1)] = float(m.group(2))
    except Exception:
        pass
    return out


AUDIO_EXT = (".wav", ".mp3", ".ogg", ".flac")
VIDEO_EXT = (".mp4", ".ogv", ".mkv", ".webm", ".mov", ".avi")


def load_pack(folder):
    """Baut ein DubPack aus einem Ordner. None, wenn kein Dub-Pack."""
    if not os.path.isdir(folder):
        return None
    video = None
    for ext in VIDEO_EXT:                       # mp4 bevorzugt, ogv weiter ok
        cand = os.path.join(folder, "dub_video" + ext)
        if os.path.isfile(cand):
            video = cand
            break
    if not video:
        return None

    pack = DubPack(folder)
    pack.video = video

    from_txt = timestamps_from_txt(folder)
    entries = []
    for f in sorted(os.listdir(folder)):
        low = f.lower()
        if not low.endswith(AUDIO_EXT):
            continue
        if low.startswith("_backing_track"):
            pack.backing = os.path.join(folder, f)
            continue
        if f.startswith("_"):
            continue
        ts = timestamp_from_name(f)
        if ts is None:
            ts = from_txt.get(f)
        entries.append((f, ts))

    known = [e for e in entries if e[1] is not None]
    if not known:
        # Ohne Zeitstempel koennen wir nicht synchronisieren.
        return None

    known.sort(key=lambda e: e[1])
    captions = pc.read_captions(folder)
    char_names, char_map = pc.read_characters(folder)
    for i, (f, ts) in enumerate(known):
        line = Line(os.path.join(folder, f), ts, i)
        line.caption = captions.get(f, "")
        line.character = char_map.get(f, "")
        pack.lines.append(line)
    # Geordnete Figurenliste: erst die aus der Datei, dann noch nicht
    # genannte, wie sie in den Zeilen vorkommen.
    ordered = [c for c in char_names]
    for line in pack.lines:
        if line.character and line.character not in ordered:
            ordered.append(line.character)
    pack.characters = ordered
    return pack


def save_pack_characters(pack):
    """Schreibt die aktuelle Figuren-Zuordnung des Packs als _characters.json.
    Damit lassen sich auch bestehende Packs ohne Neubau taggen."""
    mapping = {}
    for line in pack.lines:
        if getattr(line, "character", ""):
            mapping[line.file] = line.character
    ordered = getattr(pack, "characters", None)
    return pc.write_characters(pack.folder, mapping, ordered)


def find_packs(extra_dirs=None):
    """Sucht Dub-Packs im eigenen packs-Ordner und in zusaetzlichen Ordnern."""
    roots = []

    def add_root(p):
        if p and os.path.isdir(p) and os.path.normpath(p) not in \
                [os.path.normpath(r) for r in roots]:
            roots.append(p)

    add_root(PACKS_DIR)
    for p in (extra_dirs or []):
        add_root(p)

    packs = []
    seen = set()
    for root in roots:
        for entry in sorted(os.listdir(root)):
            folder = os.path.join(root, entry)
            key = os.path.normpath(folder).lower()
            if key in seen or not os.path.isdir(folder):
                continue
            seen.add(key)
            p = load_pack(folder)
            if p:
                packs.append(p)
    return packs


# ==========================================================================
#  Audio
# ==========================================================================

def read_wav_mono(path, sr=SR):
    """Liest beliebiges Audio als Mono-Float-Array (ueber ffmpeg)."""
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        pc.run([pc.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                "-i", path, "-ac", "1", "-ar", str(sr),
                "-c:a", "pcm_s16le", tmp], check=True)
        with wave.open(tmp, "rb") as w:
            raw = w.readframes(w.getnframes())
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_wav_mono(path, data, sr=SR):
    data = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return path


def wav_bytes(data, sr=SR):
    """Kodiert ein Mono-Float-Array als 16-bit-PCM-WAV im Speicher (fuer den
    Upload einzelner Takes an den Server)."""
    data = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


def take_from_wav_bytes(raw, sr=SR):
    """Gegenstueck zu wav_bytes: WAV-Bytes -> Mono-Float-Array."""
    got = _read_pcm_wav(io.BytesIO(raw))
    if got is not None and got[1] == sr:
        return got[0]
    # Fremdes Format/Rate: ueber eine Temp-Datei mit ffmpeg lesen.
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        return read_wav_mono(tmp, sr)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def normalize(data, peak=0.97):
    data = np.asarray(data, dtype=np.float32)
    m = float(np.max(np.abs(data))) if len(data) else 0.0
    if m < 1e-6:
        return data
    return data * (peak / m)


def load_pack_audio(pack, sr=SR, progress=None):
    """Laedt alle Original-Samples und den Backing Track."""
    total = len(pack.lines) + 1
    for i, line in enumerate(pack.lines):
        line.audio = read_wav_mono(line.path, sr)
        line.duration = len(line.audio) / float(sr)
        if progress:
            progress((i + 1) / float(total))
    pack.backing_audio = (read_wav_mono(pack.backing, sr)
                          if pack.backing else None)
    pack.video_duration = pc.probe_duration(pack.video)
    if progress:
        progress(1.0)
    return pack


# ==========================================================================
#  Analyse / analysis
# ==========================================================================

def trim_silence(data, sr=SR, rel=0.08, pad=0.04, frame_ms=20):
    """
    Schneidet Stille vorn und hinten weg. Damit kostet ein spaeter
    Einsatz keine Punkte - nur der Verlauf selbst zaehlt.
    """
    data = np.asarray(data, dtype=np.float32)
    hop = max(1, int(sr * frame_ms / 1000.0))
    usable = (len(data) // hop) * hop
    if usable < hop * 2:
        return data
    rms = np.sqrt((data[:usable].reshape(-1, hop) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak < 1e-6:
        return data
    active = np.nonzero(rms >= peak * rel)[0]
    if not len(active):
        return data
    a = max(0, int(active[0] * hop - pad * sr))
    b = min(len(data), int((active[-1] + 1) * hop + pad * sr))
    return data[a:b]


def rms_db(data):
    data = np.asarray(data, dtype=np.float32)
    if not len(data):
        return -120.0
    r = float(np.sqrt((data ** 2).mean()))
    return 20.0 * np.log10(max(r, 1e-9))


# ==========================================================================
#  Video-Frames
# ==========================================================================

def frames_dir_for(pack):
    key = re.sub(r"[^\w]+", "_", os.path.normpath(pack.folder))[-90:]
    return os.path.join(CACHE_DIR, key)


def extract_frames(pack, fps=FRAME_FPS, width=FRAME_W, log=None, force=False):
    """
    Zerlegt das OGV vorab in JPEGs. Umgeht damit jede Codec-Frage
    bei der Wiedergabe - genau der Punkt, an dem das Original haengt.
    """
    outdir = frames_dir_for(pack)
    stamp = os.path.join(outdir, "done.txt")
    want = "%d %d" % (int(fps), int(width))
    if force and os.path.isdir(outdir):
        shutil.rmtree(outdir, ignore_errors=True)
    if os.path.isfile(stamp):
        try:
            with open(stamp, "r", encoding="utf-8") as f:
                have = f.read().strip()
        except Exception:
            have = ""
        if have == want:
            pack.frames = sorted(glob.glob(os.path.join(outdir, "f*.jpg")))
            pack.fps = float(fps)
            if pack.frames:
                return pack.frames
        # andere Aufloesung -> neu erzeugen
        shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir, exist_ok=True)
    pc.run([pc.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", pack.video,
            "-vf", "fps=%d,scale=%d:-2" % (int(fps), int(width)),
            "-q:v", "5", os.path.join(outdir, "f%05d.jpg")], log=log)
    pack.frames = sorted(glob.glob(os.path.join(outdir, "f*.jpg")))
    pack.fps = float(fps)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(want)
    return pack.frames


def frame_at(pack, seconds):
    """Index des Bildes zum Zeitpunkt / frame index for a time."""
    if not pack.frames:
        return None
    i = int(round(seconds * pack.fps))
    return max(0, min(len(pack.frames) - 1, i))


# ==========================================================================
#  Dub-Mix rendern
# ==========================================================================

def fit_len(data, n):
    """
    Bringt ein Array auf genau n Werte. Noetig, weil
    int(len(x)/sr*sr) nicht immer wieder len(x) ergibt - sonst knallt
    das Mischen von Aufnahme und Backing Track bei ~8% aller Cliplaengen.
    """
    data = np.asarray(data, dtype=np.float32)
    n = max(0, int(n))
    if len(data) == n:
        return data
    out = np.zeros(n, dtype=np.float32)
    m = min(n, len(data))
    out[:m] = data[:m]
    return out


def slice_audio(data, start, duration, sr=SR):
    """Schneidet einen Bereich heraus, mit Nullen aufgefuellt."""
    if data is None:
        return np.zeros(int(duration * sr), dtype=np.float32)
    a = int(max(0.0, start) * sr)
    n = int(duration * sr)
    out = np.zeros(n, dtype=np.float32)
    chunk = data[a:a + n]
    out[:len(chunk)] = chunk
    return out


def _active_rms(x, sr=SR):
    """Lautstaerke (RMS) nur der klingenden Teile - Stille zaehlt nicht."""
    x = trim_silence(np.asarray(x, dtype=np.float32), sr)
    if not len(x):
        return 0.0
    return float(np.sqrt(np.mean(x ** 2)))


def _estimate_reverb(x, sr=SR):
    """Schaetzt aus dem Original, wie hallig es klingt. Rueckgabe (wet, rt60):
    wet 0..~0.4 = Nassanteil, rt60 = Nachhallzeit in Sekunden. Grob, aber in
    die richtige Richtung: klingt eine Stimme lange aus, wird mehr Hall
    angenommen (leerer Raum), bei trockener Sprache fast keiner."""
    x = np.asarray(x, dtype=np.float32)
    hop = max(1, int(sr * 0.02))
    k = len(x) // hop
    if k < 6:
        return 0.0, 0.2
    env = np.sqrt((x[:k * hop].reshape(k, hop) ** 2).mean(axis=1))
    peak = float(env.max())
    if peak < 1e-4:
        return 0.0, 0.2
    env = env / peak
    decays = []
    i = 0
    while i < k:
        if env[i] > 0.5:                      # Silbenspitze
            j = i
            while j < k and env[j] > 0.1:     # bis -20 dB abgeklungen
                j += 1
            decays.append((j - i) * 0.02)
            i = max(j, i + 1)
        else:
            i += 1
    if not decays:
        return 0.0, 0.2
    d = float(np.median(decays))
    wet = float(np.clip((d - 0.22) / 0.5, 0.0, 0.4))
    rt60 = float(np.clip(d * 1.5, 0.15, 0.6))
    return wet, rt60


def _fftconv(x, h):
    """Schnelle Faltung ueber die FFT (ohne scipy)."""
    n = len(x) + len(h) - 1
    N = 1 << int(np.ceil(np.log2(max(1, n))))
    y = np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(h, N), N)[:n]
    return y.astype(np.float32)


def _reverb_ir(sr, rt60, predelay=0.008):
    """Kunst-Raumantwort: exponentiell abklingendes Rauschen."""
    n = max(1, int(rt60 * sr))
    t = np.arange(n) / float(sr)
    decay = np.exp(-6.9078 * t / max(0.05, rt60))       # -60 dB bei rt60
    ir = (np.random.RandomState(0).randn(n).astype(np.float32) * decay)
    pd = int(predelay * sr)
    if pd:
        ir = np.concatenate([np.zeros(pd, dtype=np.float32), ir])
    return ir


def _apply_reverb(take, sr, wet, rt60):
    """Legt Hall auf die Aufnahme. Rueckgabe ist laenger (Nachhall-Fahne)."""
    take = np.asarray(take, dtype=np.float32)
    if wet <= 0.01 or not len(take):
        return take
    wetsig = _fftconv(take, _reverb_ir(sr, rt60))
    out = np.zeros(len(wetsig), dtype=np.float32)
    out[:len(take)] = take * (1.0 - wet)
    rt = float(np.sqrt(np.mean(take ** 2)))
    rw = float(np.sqrt(np.mean(wetsig ** 2))) if len(wetsig) else 0.0
    scale = (rt / rw) if rw > 1e-6 else 0.0
    out += (wet * scale) * wetsig
    return out


def match_take(take, original, sr=SR):
    """Passt eine Aufnahme an das Original der Zeile an: gleiche Lautstaerke
    (leise/weit weg bleibt leise) und aehnlicher Hall/Echo."""
    take = np.asarray(take, dtype=np.float32)
    original = np.asarray(original, dtype=np.float32)
    if not len(take):
        return take
    ro = _active_rms(original, sr)
    rt = _active_rms(take, sr)
    if rt > 1e-5 and ro > 1e-6:
        take = take * float(np.clip(ro / rt, 0.1, 4.0))
    wet, rt60 = _estimate_reverb(original, sr)
    take = _apply_reverb(take, sr, wet, rt60)
    return np.clip(take, -1.0, 1.0)


def render_dub(pack, sr=SR, duck=0.0, match=False, log=None):
    """
    Legt alle Aufnahmen an ihre Zeitstempel ueber den Backing Track.

    Wo eine Aufnahme liegt, wird die Original-Stimme der Figur an dieser
    Stelle ersetzt, nicht nur leiser gezogen (duck=0.0 = vollstaendig
    entfernen; ein Wert > 0 laesst das Original entsprechend leise stehen).
    Ohne Backing Track fehlt dabei zwangslaeufig auch die Umgebung waehrend
    der Zeile - mit getrenntem Backing Track (Demucs) bleibt die Musik.
    Zeilen ohne Aufnahme behalten das Original.
    """
    total = max(pack.video_duration, 0.1)
    for line in pack.lines:
        total = max(total, line.end + 1.0)
    n = int(total * sr) + sr

    backing = getattr(pack, "backing_audio", None)
    if backing is not None:
        base = np.zeros(n, dtype=np.float32)
        base[:min(n, len(backing))] = backing[:n]
        have_backing = True
    else:
        original = read_wav_mono(pack.video, sr)
        base = np.zeros(n, dtype=np.float32)
        base[:min(n, len(original))] = original[:n]
        have_backing = False

    for line in pack.lines:
        a = int(line.start * sr)
        if line.take is not None and len(line.take):
            if match and line.audio is not None and len(line.audio):
                # An Original angleichen (Lautstaerke + Hall), NICHT normalisieren
                take = match_take(line.take, line.audio, sr)
            else:
                take = normalize(np.asarray(line.take, dtype=np.float32), 0.92)
            if not have_backing:
                # Original der Figur hier entfernen, damit die Aufnahme sie
                # ersetzt statt sich nur drueberzulegen.
                b = min(n, a + max(len(take), int(line.duration * sr)))
                if duck > 0.0:
                    base[a:b] *= duck
                else:
                    base[a:b] = 0.0
                    # kurze Ueberblendung an den Raendern gegen Knackser
                    f = min(int(0.008 * sr), (b - a) // 2)
                    if f > 0 and a - f >= 0:
                        base[a - f:a] *= np.linspace(1.0, 0.0, f,
                                                     dtype=np.float32)
                    if f > 0 and b + f <= n:
                        base[b:b + f] *= np.linspace(0.0, 1.0, f,
                                                     dtype=np.float32)
            b = min(n, a + len(take))
            base[a:b] += take[:b - a]
        elif line.audio is not None and have_backing:
            b = min(n, a + len(line.audio))
            base[a:b] += line.audio[:b - a] * 0.9

    return normalize(base, 0.97)


def _read_pcm_wav(path):
    """Liest ein 16-bit-PCM-WAV mit der Standardbibliothek (ohne ffmpeg).
    Rueckgabe (mono_float, samplerate) oder None, wenn nicht lesbar."""
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            return None
        ch = w.getnchannels()
        fsr = w.getframerate()
        raw = w.readframes(w.getnframes())
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, fsr


def read_take(path, sr=SR):
    """Laedt eine Aufnahme als Mono-Float. Erst die schnelle stdlib-Variante
    fuer unsere eigenen PCM-WAVs, sonst ffmpeg fuer alles andere."""
    try:
        got = _read_pcm_wav(path)
        if got is not None and got[1] == sr:
            return got[0]
    except Exception:
        pass
    return read_wav_mono(path, sr)


# ==========================================================================
#  Takes exportieren / importieren  (Mehrspieler-Grundlage)
# ==========================================================================

def export_takes(pack, out_dir, sr=SR):
    """Schreibt jede aufgenommene Zeile als WAV nach out_dir, benannt nach
    dem Clip-Dateinamen. Rueckgabe: Anzahl geschriebener Takes."""
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for line in pack.lines:
        take = getattr(line, "take", None)
        if take is not None and len(take):
            write_wav_mono(os.path.join(out_dir, line.file), take, sr)
            n += 1
    return n


def import_takes(pack, in_dir, sr=SR, override=False):
    """Laedt Takes aus in_dir in die passenden Zeilen (per Clip-Dateiname).
    Bereits vorhandene Takes bleiben, sofern override=False. Rueckgabe:
    Anzahl uebernommener Takes."""
    if not os.path.isdir(in_dir):
        return 0
    n = 0
    for line in pack.lines:
        if line.take is not None and len(line.take) and not override:
            continue
        path = os.path.join(in_dir, line.file)
        if os.path.isfile(path):
            try:
                line.take = read_take(path, sr)
                n += 1
            except Exception:
                pass
    return n


def merge_take_dirs(pack, take_dirs, sr=SR, override=False):
    """Fuehrt mehrere Take-Ordner (z. B. je Spieler) in den Pack zusammen.
    Frueher genannte Ordner gewinnen, solange override=False."""
    total = 0
    for d in take_dirs:
        total += import_takes(pack, d, sr=sr, override=override)
    return total


def assemble_from_take_dirs(pack, take_dirs, out_path, sr=SR, log=None,
                            progress=None):
    """Kompletter Zusammenbau aus mehreren Take-Ordnern: Audio laden, Takes
    zusammenfuehren, Dub rendern und als Video schreiben. Genau der Weg, den
    spaeter der Host mit den Takes vom Server geht."""
    load_pack_audio(pack, sr, progress)
    merge_take_dirs(pack, take_dirs, sr=sr)
    mixed = render_dub(pack, sr=sr, log=log)
    return export_dub_video(pack, mixed, out_path, sr=sr, log=log)


def export_dub_video(pack, mixed_audio, out_path, sr=SR, log=None):
    """Schreibt Video + eigener Tonspur als MP4 zum Weitergeben."""
    tmp_wav = tempfile.mktemp(suffix=".wav")
    write_wav_mono(tmp_wav, mixed_audio, sr)
    try:
        pc.run([pc.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                "-i", pack.video, "-i", tmp_wav,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "192k", "-shortest", out_path], log=log)
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)
    return out_path


# ==========================================================================
#  Mikrofon / microphone
# ==========================================================================

def audio_backend():
    """Gibt das sounddevice-Modul zurueck oder None."""
    try:
        import sounddevice as sd
        return sd
    except Exception:
        return None


ENV_MS = 20          # Aufloesung der laufenden Huellkurve / live envelope


class Mic(object):
    """Aufnahme, optional mit gleichzeitiger Wiedergabe des Backing Tracks."""

    def __init__(self, sr=SR):
        self.sr = sr
        self.sd = audio_backend()
        self._stream = None
        self._ostream = None            # eigener Ausgabestrom / output stream
        self._odata = None
        self._opos = 0
        self._chunks = []
        self._env = []                                  # Spitzenwerte je Fenster
        self._env_step = max(1, int(sr * ENV_MS / 1000.0))
        self._buf = np.zeros(0, dtype=np.float32)

    @property
    def available(self):
        return self.sd is not None

    def devices(self):
        if not self.sd:
            return []
        out = []
        for i, d in enumerate(self.sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append((i, d.get("name", "?")))
        return out

    def start(self, playback=None, device=None):
        """playback: Float-Array, das waehrend der Aufnahme laufen soll."""
        if not self.sd:
            raise RuntimeError("sounddevice fehlt / missing")
        self._chunks = []
        self._env = []
        self._env_step = max(1, int(self.sr * ENV_MS / 1000.0))
        self._buf = np.zeros(0, dtype=np.float32)

        def cb(indata, frames, time_info, status):
            data = np.asarray(indata[:, 0], dtype=np.float32).copy()
            self._chunks.append(data)
            # Spitzenwert je 20-ms-Fenster mitschreiben, damit die
            # Oberflaeche die Aufnahme live zeichnen kann.
            buf = data if not len(self._buf) else \
                np.concatenate([self._buf, data])
            step = self._env_step
            k = len(buf) // step
            if k:
                blocks = buf[:k * step].reshape(k, step)
                self._env.extend(np.abs(blocks).max(axis=1).tolist())
                buf = buf[k * step:]
            self._buf = buf

        kwargs = {"samplerate": self.sr, "channels": 1, "callback": cb,
                  "dtype": "float32"}
        if device is not None:
            kwargs["device"] = device
        self._stream = self.sd.InputStream(**kwargs)
        self._stream.start()
        if playback is not None and len(playback):
            self._start_output(playback)

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._stop_output()
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks).astype(np.float32)

    def envelope(self):
        """Bisher aufgenommene Huellkurve und ihre Schrittweite in Sekunden."""
        return list(self._env), self._env_step / float(self.sr)

    def level(self):
        """Aktueller Pegel 0..1 fuer die Aussteuerungsanzeige."""
        if not self._env:
            return 0.0
        return float(min(1.0, max(self._env[-5:]) * 1.4))

    def _start_output(self, data):
        """Spielt ein Float-Array ueber einen eigenen OutputStream ab. Wir
        umgehen damit sd.play()/sd.stop(): deren globaler Callback wirft beim
        Beenden ein 'ignored exception' (fehlendes .out) in die Konsole."""
        self._stop_output()
        if not self.sd or data is None:
            return
        arr = np.asarray(data, dtype=np.float32).reshape(-1, 1)
        if not len(arr):
            return
        self._odata = arr
        self._opos = 0

        def cb(outdata, frames, time_info, status):
            i = self._opos
            chunk = self._odata[i:i + frames]
            k = len(chunk)
            outdata[:k] = chunk
            if k < frames:
                outdata[k:] = 0
                self._opos += k
                raise self.sd.CallbackStop
            self._opos += frames

        try:
            self._ostream = self.sd.OutputStream(
                samplerate=self.sr, channels=1, dtype="float32", callback=cb)
            self._ostream.start()
        except Exception:
            self._ostream = None

    def _stop_output(self):
        st = self._ostream
        self._ostream = None
        if st is not None:
            try:
                st.stop()
                st.close()
            except Exception:
                pass

    def play(self, data):
        if not self.sd or data is None or not len(data):
            return
        self._start_output(data)

    def stop_play(self):
        self._stop_output()
