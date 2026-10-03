# -*- coding: utf-8 -*-
"""
dubstage_stt.py -- automatische Untertitel (Speech-to-Text) fuer DubMaker.

Nutzt faster-whisper. Das Sprachmodell wird NICHT mitgeliefert, sondern beim
ersten Gebrauch einmalig neben die App geladen (nach models/). So bleibt der
Download der App klein und offline-Nutzung ist nach dem ersten Mal moeglich.

Die eigentliche GUI ruft nur `available()` und `transcribe_clip(...)`.
"""

import os

import numpy as np

import dubforge_core as pc

STT_SR = 16000                               # Whisper erwartet 16 kHz mono
MODELS_DIR = os.path.join(pc.APP_DIR, "models")
DEFAULT_SIZE = "base"

_MODEL = None
_MODEL_SIZE = None


def available():
    """True, wenn die STT-Bibliothek vorhanden ist (sonst Hinweis anzeigen)."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def model_present(size=DEFAULT_SIZE):
    """True, wenn das Modell schon heruntergeladen wurde (kein Netz noetig)."""
    tag = "models--Systran--faster-whisper-%s" % size
    return os.path.isdir(os.path.join(MODELS_DIR, tag))


def load_model(size=DEFAULT_SIZE, progress=None):
    """Modell laden (beim ersten Mal herunterladen). Gecached pro Groesse."""
    global _MODEL, _MODEL_SIZE
    if _MODEL is not None and _MODEL_SIZE == size:
        return _MODEL
    from faster_whisper import WhisperModel
    os.makedirs(MODELS_DIR, exist_ok=True)
    if progress:
        progress("model")
    _MODEL = WhisperModel(size, device="cpu", compute_type="int8",
                          download_root=MODELS_DIR)
    _MODEL_SIZE = size
    return _MODEL


def _to_16k(audio, sr):
    """Mono-Float auf 16 kHz bringen (einfache lineare Resampling-Stufe)."""
    audio = np.asarray(audio, dtype=np.float32)
    if sr == STT_SR or len(audio) == 0:
        return audio
    n = int(round(len(audio) * STT_SR / float(sr)))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    x = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    xi = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(xi, x, audio).astype(np.float32)


def transcribe(audio, sr, lang=None, size=DEFAULT_SIZE, progress=None):
    """Mono-Float-Audio -> Text. lang=None laesst die Sprache erkennen."""
    model = load_model(size, progress)
    a16 = _to_16k(audio, sr)
    if len(a16) < STT_SR // 20:              # < 50 ms -> nichts zu erkennen
        return ""
    segments, _info = model.transcribe(a16, language=lang, beam_size=1,
                                        vad_filter=False)
    return " ".join(s.text.strip() for s in segments).strip()
