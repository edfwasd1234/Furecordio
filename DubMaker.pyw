# -*- coding: utf-8 -*-
"""
DubMaker  --  visual pack maker for DubStage.

Drop in a video, see it right above its full soundtrack waveform, mark each
character's speech as coloured regions (auto-detected or drawn by hand), give
every region a subtitle, and build a pack that DubStage opens and dubs.

All heavy lifting is reused from dubforge_core / dubstage_core - this file is
just the editor on top. Same pack format as DubForge, so nothing downstream
changes.
"""

import os
import sys
import json
import time
import queue
import shutil
import tempfile
import threading
import traceback

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dubforge_core as pc
import dubstage_core as ds
import updater as upd

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

APP_DIR = pc.APP_DIR          # frozen-aware (neben der .exe bzw. dieser Datei)
CFG_PATH = os.path.join(APP_DIR, "dubmaker_settings.json")
PACKS_DIR = os.path.join(APP_DIR, "packs")

# ------------------------------------------------------------------ Palette
BG_TOP = "#171a2e"
BG_BOT = "#0a0b12"
PANEL = "#1a1e30"
PANEL_HI = "#242a42"
EDGE = "#333a5c"
TXT = "#eef1ff"
DIM = "#8a92b4"
ACC = "#7c5cff"
ACC_HI = "#9b83ff"
TEAL = "#25d3a4"
TEAL_HI = "#4ee5bb"
RED = "#ff4f6d"
GOLD = "#ffc861"
WAVE = "#3c4470"
WAVE_HI = "#5866a0"

PREVIEW_FPS = 12
PREVIEW_W = 640

CHAR_COLORS = ["#7c5cff", "#25d3a4", "#ff9f43", "#ff4f6d", "#4e8cff",
               "#f368e0", "#ffd166", "#2ec4b6", "#e56b6f", "#9b5de5",
               "#54a0ff", "#00d2d3"]

SR8 = 8000                 # Wellenform-/Analyse-Rate
PSR = 44100                # Wiedergabe-Rate


# ==========================================================================
LANG = "en"
T = {
    "title":     ("DubMaker", "DubMaker"),
    "tagline":   ("Pack-Werkstatt fuer DubStage", "Pack maker for DubStage"),
    "open":      ("Video oeffnen", "Open video"),
    "drop":      ("Video hierher ziehen oder klicken zum Oeffnen",
                  "Drop a video here, or click to open"),
    "drop_2":    ("mp4, mkv, mov, webm - oder eine Tonspur",
                  "mp4, mkv, mov, webm - or an audio file"),
    "loading":   ("Video wird vorbereitet ...", "Preparing the video ..."),
    "play":      ("Abspielen", "Play"),
    "pause":     ("Pause", "Pause"),
    "characters": ("Figuren", "Characters"),
    "add_char":  ("Figur +", "Character +"),
    "no_chars":  ("Noch keine Figur. Lege eine an.",
                  "No character yet. Add one."),
    "active":    ("aktiv", "active"),
    "auto":      ("Automatisch finden", "Auto-detect"),
    "clear":     ("Alle Bereiche loeschen", "Clear all regions"),
    "regions_n": ("%d Bereiche", "%d regions"),
    "subtitle":  ("Untertitel", "Subtitle"),
    "sub_hint":  ("Enter = speichern und zum naechsten Bereich",
                  "Enter = save and go to the next region"),
    "pick_char": ("Erst eine Figur waehlen (links).",
                  "Pick a character first (left)."),
    "region_of": ("Bereich %d / %d  -  %s", "Region %d / %d  -  %s"),
    "no_region": ("Bereich ziehen: mit gewaehlter Figur ueber die Wellenform.",
                  "Drag on the waveform (with a character active) to make a region."),
    "pack_name": ("Pack-Name", "Pack name"),
    "backing":   ("Backing-Spur (Demucs)", "Backing track (Demucs)"),
    "sep_run":   ("Trenne Stimme und Hintergrund (Demucs) ...",
                  "Separating voice and background (Demucs) ..."),
    "voice":     ("Stimme", "Voice"),
    "background": ("Hintergrund", "Background"),
    "build":     ("Pack bauen", "Build pack"),
    "building":  ("Pack wird gebaut ...", "Building the pack ..."),
    "built":     ("Gebaut:\n%s", "Built:\n%s"),
    "need_regions": ("Erst Bereiche anlegen.", "Add some regions first."),
    "need_name":  ("Bitte einen Pack-Namen eingeben.",
                   "Please enter a pack name."),
    "err":       ("Fehler", "Error"),
    "del_char_q": ("Figur '%s' loeschen? Ihre Bereiche werden frei.",
                   "Delete character '%s'? Its regions become unassigned."),
    "rename_t":  ("Figur umbenennen", "Rename character"),
    "rename":    ("Neuer Name:", "New name:"),
    "unassigned": ("ohne Figur", "unassigned"),
    "no_pil":    ("Videoanzeige braucht 'Pillow'. Bitte Setup.bat ausfuehren.",
                  "Video display needs 'Pillow'. Please run Setup.bat."),
    # --- Update
    "upd_head":  ("Version %s ist da", "Version %s is out"),
    "upd_ask":   ("Jetzt auf %s aktualisieren?\n\nDubMaker und DubStage werden "
                  "getauscht und die App startet neu. Packs, Aufnahmen und "
                  "Einstellungen bleiben erhalten.",
                  "Update to %s now?\n\nDubMaker and DubStage are replaced and "
                  "the app restarts. Your packs, recordings and settings are "
                  "kept."),
    "upd_ask_page": ("Eine neue Version ist da. Download-Seite oeffnen?",
                     "A new version is available. Open the download page?"),
    "upd_dl":    ("Lade Update ... %d%%", "Downloading update ... %d%%"),
    "upd_swap":  ("Tausche Dateien - gleich geht es weiter ...",
                  "Replacing files - back in a moment ..."),
    "upd_fail_t": ("Update fehlgeschlagen", "Update failed"),
}


def t(key, *a):
    pair = T.get(key)
    if not pair:
        return key
    val = pair[1] if LANG == "en" else pair[0]
    return val % a if a else val


def load_cfg():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ==========================================================================
#  Zeichen-Helfer / drawing helpers (aus DubStage uebernommen)
# ==========================================================================
def lerp_color(c1, c2, f):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(
        int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def round_rect(cv, x0, y0, x1, y1, r=14, **kw):
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class Button(object):
    STYLES = {
        "primary": (ACC, ACC_HI, "#ffffff"),
        "go":      (TEAL, TEAL_HI, "#062a20"),
        "ghost":   (PANEL_HI, EDGE, TXT),
        "flat":    ("", "", DIM),
        "danger":  (RED, "#ff7b92", "#2b0710"),
    }

    def __init__(self, cv, x, y, w, h, text, command, kind="ghost",
                 font=("Segoe UI Semibold", 11), radius=None):
        self.cv = cv
        self.box = (x, y, x + w, y + h)
        self.command = command
        self.kind = kind
        self.enabled = True
        self.hover = False
        r = radius if radius is not None else min(16, h / 2)
        self.rect = None if kind == "flat" else round_rect(
            cv, x, y, x + w, y + h, r=r, fill=self.STYLES[kind][0], outline="")
        self.label = cv.create_text(x + w / 2, y + h / 2, text=text,
                                    fill=self.STYLES[kind][2], font=font)
        self.refresh()

    def refresh(self):
        base, hi, fg = self.STYLES[self.kind]
        if not self.enabled:
            fill, txt = PANEL, "#565d7d"
        elif self.hover:
            fill, txt = (hi if self.kind != "flat" else ""), TXT
        else:
            fill, txt = base, fg
        if self.rect is not None:
            self.cv.itemconfigure(self.rect, fill=fill)
        self.cv.itemconfigure(self.label, fill=txt)

    def set_enabled(self, v):
        v = bool(v)
        if v != self.enabled:
            self.enabled = v
            self.refresh()

    def hit(self, x, y):
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1

    def set_hover(self, v):
        if v != self.hover:
            self.hover = v
            self.refresh()


class Shim(object):
    """Minimalobjekt fuer ds.extract_frames / ds.frame_at."""
    def __init__(self, video, folder):
        self.video = video
        self.folder = folder
        self.frames = []
        self.fps = PREVIEW_FPS


# ==========================================================================
class DubMaker(tk.Tk):

    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.title(t("title"))
        sh = self.winfo_screenheight()
        self.geometry("1280x%d" % min(900, max(780, sh - 130)))
        self.minsize(1080, 760)
        self.configure(bg=BG_BOT)

        self.msgq = queue.Queue()
        self.mic = ds.Mic(PSR)

        self.work = None
        self.video_path = None
        self.audio_path = None
        self.cut_source = None        # Quelle fuer Clips (Vocals oder Ton)
        self.backing_path = None
        self.data8 = None             # Wellenform-Daten (mono, SR8)
        self.play_audio = None        # Wiedergabe (mono, PSR)
        self.duration = 0.0
        self.frames = []
        self.fps = PREVIEW_FPS
        self._peaks = (None, None, None)   # (cols, view, peaks)

        self.characters = []          # [{name, color}]
        self.active = None            # Index aktive Figur
        self.regions = []             # [{start,end,character,caption}]
        self.sel = None               # gewaehlter Bereich (dict) oder None

        self.view_a = 0.0
        self.view_b = 1.0
        self.playing = False
        self._play = None
        self.playhead = 0.0
        self._imgcache = {}
        self._busy = False

        self.buttons = []
        self.char_hits = []           # [(x0,y0,x1,y1,index,kind)]
        self._embedded = []
        self._drag = None
        self.wave_box = None
        self.video_box = None
        self.phead_item = None
        self.sub_entry = None
        self.sub_var = tk.StringVar(value="")
        self._sub_for = None

        self.pack_name = tk.StringVar(value=self.cfg.get("last_name", "MyScene"))
        self.backing_on = tk.BooleanVar(value=bool(self.cfg.get("backing", False)))

        # Demucs-Trennung / vocal separation
        self.blend_var = tk.DoubleVar(value=0.0)   # 0 = Stimme, 1 = Hintergrund
        self._sep_done = False
        self._sep_voc = None          # Pfad Vocals-WAV
        self._sep_nov = None          # Pfad No-Vocals-WAV
        self.voc_audio = None         # Vocals als Float (PSR)
        self.nov_audio = None         # Hintergrund als Float (PSR)

        self.cv = tk.Canvas(self, bg=BG_BOT, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", self._on_resize)
        self.cv.bind("<Motion>", self._on_motion)
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag_move)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Double-Button-1>", self._double)
        self.cv.bind("<MouseWheel>", self._on_wheel)
        self.bind("<space>", lambda e: self._space())
        self.bind("<Delete>", lambda e: self.delete_region())

        self._style()
        self._resize_job = None
        self._upd_busy = False
        self.after(50, self.build_ui)
        self.after(60, self._pump)
        self.after(1700, self._check_update)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass

    # ------------------------------------------------------------- basics
    def size(self):
        w = self.cv.winfo_width()
        h = self.cv.winfo_height()
        return (w if w > 300 else 1280), (h if h > 300 else 900)

    def _clear_canvas(self):
        for w in self._embedded:
            try:
                w.destroy()
            except Exception:
                pass
        self._embedded = []
        self.buttons = []
        self.char_hits = []
        self.cv.delete("all")

    def _backdrop(self):
        w, h = self.size()
        steps = 46
        for i in range(steps):
            self.cv.create_rectangle(
                0, h * i / steps, w, h * (i + 1) / steps + 1, width=0,
                fill=lerp_color(BG_TOP, BG_BOT, i / float(steps - 1)))

    def _btn(self, *a, **kw):
        b = Button(self.cv, *a, **kw)
        self.buttons.append(b)
        return b

    def _on_resize(self, _e=None):
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(150, self._rebuild)

    def _rebuild(self):
        self._resize_job = None
        self.build_ui()

    # ==================================================================
    #  HAUPTBILD
    # ==================================================================
    def build_ui(self):
        self._clear_canvas()
        self._backdrop()
        cv = self.cv
        w, h = self.size()
        pad = 28

        cv.create_text(pad, 34, anchor="w", text="DUBMAKER", fill=TXT,
                       font=("Segoe UI Black", 22))
        cv.create_text(pad + 168, 38, anchor="w", text=t("tagline"), fill=DIM,
                       font=("Segoe UI", 11))

        if not self.frames or not HAVE_PIL:
            self._build_empty(w, h)
            return

        # ---- Kopf: Build-Zeile rechts
        self._btn(pad, 58, 150, 36, "＋ " + t("open"), self.open_video, "ghost",
                  font=("Segoe UI Semibold", 11))
        # Pack-Name + backing + build (rechts)
        name_e = tk.Entry(self, textvariable=self.pack_name, bg=PANEL_HI,
                          fg=TXT, insertbackground=TXT, relief="flat",
                          font=("Segoe UI", 12), highlightthickness=1,
                          highlightbackground=EDGE, highlightcolor=ACC)
        self._embedded.append(name_e)
        cv.create_text(w - 520, 76, anchor="e", text=t("pack_name"), fill=DIM,
                       font=("Segoe UI Semibold", 10))
        cv.create_window(w - 512, 76, window=name_e, anchor="w", width=170,
                         height=30)
        chk = ttk.Checkbutton(self, text=t("backing"), variable=self.backing_on,
                              command=self._on_backing_toggle)
        self._embedded.append(chk)
        cv.create_window(w - 330, 76, window=chk, anchor="w")
        self._btn(w - pad - 150, 60, 150, 40, t("build"), self.build_pack, "go",
                  font=("Segoe UI Semibold", 12))

        # ---- linke Spalte: Figuren
        col_w = 250
        lx0 = pad
        lx1 = pad + col_w
        ctop = 120
        cv.create_text(lx0, ctop, anchor="w", text=t("characters"), fill=TXT,
                       font=("Segoe UI Semibold", 13))
        self._btn(lx1 - 108, ctop - 16, 108, 30, t("add_char"),
                  self.add_character, "primary", font=("Segoe UI Semibold", 10))
        self._draw_characters(lx0, lx1, ctop + 22, h - 150)

        # ---- rechter Bereich: Video + Wellenform
        rx0 = lx1 + 26
        rx1 = w - pad
        rw = rx1 - rx0

        # Videoflaeche
        vtop = 118
        vh = min(int(rw * 9 / 16.0), h - vtop - 320)
        vh = max(180, vh)
        fw, fh = self._frame_dims()
        aspect = fw / float(fh) if fh else 16 / 9.0
        vw = min(rw, int(vh * aspect))
        vx = rx0 + (rw - vw) / 2
        round_rect(cv, vx - 6, vtop - 6, vx + vw + 6, vtop + vh + 6, r=14,
                   fill="#05060a", outline=EDGE)
        self.video_box = (vx, vtop, vw, vh)
        self.video_item = cv.create_image(vx + vw / 2, vtop + vh / 2,
                                          anchor="center")

        # Transportzeile
        ty = vtop + vh + 14
        self._btn(rx0, ty, 120, 36,
                  ("❚❚ " + t("pause")) if self.playing else ("▶ " + t("play")),
                  self.toggle_play, "ghost", font=("Segoe UI Semibold", 11))
        self._btn(rx0 + 132, ty, 150, 36, t("auto"), self.auto_detect, "ghost",
                  font=("Segoe UI Semibold", 11))
        self._btn(rx0 + 292, ty, 170, 36, t("clear"), self.clear_regions,
                  "ghost", font=("Segoe UI Semibold", 11))
        cv.create_text(rx1, ty + 18, anchor="e",
                       text=t("regions_n", len(self.regions)), fill=DIM,
                       font=("Segoe UI", 10))

        # Stimme <-> Hintergrund Slider (nur wenn getrennt)
        if self._sep_done:
            sly = ty + 46
            cv.create_text(rx0, sly, anchor="w", text="🔊 " + t("voice"),
                           fill=TEAL, font=("Segoe UI Semibold", 10))
            sc = ttk.Scale(self, from_=0.0, to=1.0, variable=self.blend_var,
                           orient="horizontal")
            self._embedded.append(sc)
            cv.create_window(rx0 + 76, sly, window=sc, anchor="w",
                             width=rw - 200, height=22)
            cv.create_text(rx1, sly, anchor="e", text=t("background") + " 🎵",
                           fill=ACC_HI, font=("Segoe UI Semibold", 10))
            wtop = ty + 80
        else:
            wtop = ty + 52
        wh = 150
        self.wave_box = (rx0, wtop, rw, wh)
        round_rect(cv, rx0, wtop, rx1, wtop + wh, r=10, fill="#11141f",
                   outline=EDGE)

        # Untertitelzeile
        sy = wtop + wh + 22
        cv.create_text(rx0, sy, anchor="w", text=t("subtitle") + ":", fill=DIM,
                       font=("Segoe UI Semibold", 11))
        self.sub_entry = tk.Entry(self, textvariable=self.sub_var, bg=PANEL_HI,
                                  fg=TXT, insertbackground=TXT, relief="flat",
                                  font=("Segoe UI", 12), highlightthickness=1,
                                  highlightbackground=EDGE, highlightcolor=ACC)
        self._embedded.append(self.sub_entry)
        cv.create_window(rx0 + 90, sy, window=self.sub_entry, anchor="w",
                         width=rw - 90, height=30)
        self.sub_entry.bind("<Return>", self._sub_next)
        self.sub_entry.bind("<FocusOut>", lambda e: self._sub_save())
        self.sel_info = cv.create_text(rx0, sy + 30, anchor="w",
                                       text=t("no_region"), fill=DIM,
                                       font=("Segoe UI", 10))
        self.status_item = cv.create_text(rx1, sy + 30, anchor="e", text="",
                                          fill=GOLD, font=("Segoe UI", 10))

        self._load_sub()
        self.draw_wave()
        self.show_frame(self.playhead)

    def _build_empty(self, w, h):
        cv = self.cv
        bx0, by0 = w / 2 - 300, h / 2 - 110
        bx1, by1 = w / 2 + 300, h / 2 + 110
        round_rect(cv, bx0, by0, bx1, by1, r=22, fill=PANEL, outline=ACC)
        if not HAVE_PIL:
            cv.create_text(w / 2, h / 2, text=t("no_pil"), fill=RED,
                           font=("Segoe UI Semibold", 13), width=520,
                           justify="center")
            return
        cv.create_text(w / 2, h / 2 - 24, text=t("drop"), fill=TXT,
                       font=("Segoe UI Semibold", 16))
        cv.create_text(w / 2, h / 2 + 12, text=t("drop_2"), fill=DIM,
                       font=("Segoe UI", 11))
        self._btn(w / 2 - 90, h / 2 + 44, 180, 46, "＋ " + t("open"),
                  self.open_video, "go", font=("Segoe UI Semibold", 13))
        # ganze Karte klickbar
        self._drop_zone = (bx0, by0, bx1, by1)

    # ------------------------------------------------- Figuren zeichnen
    def _draw_characters(self, x0, x1, y0, ybot):
        cv = self.cv
        if not self.characters:
            cv.create_text(x0, y0 + 10, anchor="nw", text=t("no_chars"),
                           fill=DIM, font=("Segoe UI", 10), width=x1 - x0)
            return
        rowh = 42
        y = y0
        for i, ch in enumerate(self.characters):
            if y + rowh > ybot:
                break
            active = (i == self.active)
            round_rect(cv, x0, y, x1, y + rowh - 6, r=10,
                       fill=PANEL_HI if active else PANEL,
                       outline=ch["color"] if active else EDGE)
            cv.create_oval(x0 + 12, y + (rowh - 6) / 2 - 7, x0 + 26,
                           y + (rowh - 6) / 2 + 7, fill=ch["color"], outline="")
            cv.create_text(x0 + 38, y + (rowh - 6) / 2, anchor="w",
                           text=ch["name"], fill=TXT,
                           font=("Segoe UI Semibold", 12))
            cnt = sum(1 for r in self.regions if r["character"] == ch["name"])
            cv.create_text(x1 - 40, y + (rowh - 6) / 2, anchor="e",
                           text=str(cnt), fill=DIM, font=("Consolas", 10))
            # Loeschen-Kreuz
            cv.create_text(x1 - 16, y + (rowh - 6) / 2, text="✕", fill=DIM,
                           font=("Segoe UI", 11))
            self.char_hits.append((x1 - 28, y, x1, y + rowh - 6, i, "del"))
            self.char_hits.append((x0, y, x1 - 28, y + rowh - 6, i, "pick"))
            if active:
                cv.create_text(x0 + 38, y + rowh - 12, anchor="w",
                               text=t("active"), fill=ch["color"],
                               font=("Segoe UI", 8))
            y += rowh

    def _frame_dims(self):
        if not self.frames:
            return 16, 9
        try:
            im = Image.open(self.frames[0])
            return im.size
        except Exception:
            return 16, 9

    # ==================================================================
    #  VIDEO LADEN
    # ==================================================================
    def open_video(self):
        if not HAVE_PIL:
            messagebox.showerror(t("err"), t("no_pil"))
            return
        path = filedialog.askopenfilename(
            title=t("open"),
            filetypes=[("Video / Audio",
                        "*.mp4 *.mkv *.mov *.webm *.avi *.m4a *.mp3 *.wav *.ogg"),
                       ("All files", "*.*")])
        if not path:
            return
        self._load_video(path)

    def _load_video(self, path):
        if self._busy:
            return
        self._busy = True
        self.cfg["last_dir"] = os.path.dirname(path)
        save_cfg(self.cfg)
        if self.work:
            shutil.rmtree(self.work, ignore_errors=True)
        self.work = tempfile.mkdtemp(prefix="dubmaker_")
        self._set_status(t("loading"))

        def work():
            self.video_path = path
            self.audio_path = os.path.join(self.work, "audio.wav")
            pc.extract_audio(path, self.audio_path)
            self.cut_source = self.audio_path
            self.backing_path = None
            self.duration = max(0.1, pc.probe_duration(self.audio_path))
            self.data8, _ = pc.load_mono(self.audio_path, SR8)
            self.play_audio = ds.read_wav_mono(self.audio_path, PSR)
            shim = Shim(path, self.work)
            ds.extract_frames(shim, fps=PREVIEW_FPS, width=PREVIEW_W)
            self._new_frames = shim.frames
            self._new_fps = shim.fps

        def done():
            self._busy = False
            self.frames = self._new_frames
            self.fps = self._new_fps or PREVIEW_FPS
            self.regions = []
            self.sel = None
            self.playhead = 0.0
            self.view_a = 0.0
            self.view_b = self.duration
            self._imgcache = {}
            # neue Quelle -> alte Trennung verwerfen
            self._sep_done = False
            self._sep_voc = self._sep_nov = None
            self.voc_audio = self.nov_audio = None
            self.build_ui()
            # War die Backing-Spur schon angehakt, gleich neu trennen.
            if self.backing_on.get():
                self.after(60, self._on_backing_toggle)
        self._run_bg(work, done)

    # ==================================================================
    #  WELLENFORM
    # ==================================================================
    def _wave_geom(self):
        x0, y0, w, h = self.wave_box
        return x0 + 8, y0 + 8, w - 16, h - 16

    def _t2x(self, tsec):
        gx, gy, gw, gh = self._wave_geom()
        span = max(1e-6, self.view_b - self.view_a)
        return gx + (tsec - self.view_a) / span * gw

    def _x2t(self, x):
        gx, gy, gw, gh = self._wave_geom()
        span = max(1e-6, self.view_b - self.view_a)
        return self.view_a + (x - gx) / max(1.0, gw) * span

    def draw_wave(self):
        if not self.wave_box or self.data8 is None:
            return
        cv = self.cv
        cv.delete("wave")
        cv.delete("region")
        cv.delete("phead")
        gx, gy, gw, gh = self._wave_geom()
        mid = gy + gh / 2.0
        cols = max(60, int(gw))
        a = int(self.view_a / self.duration * len(self.data8)) if self.duration else 0
        b = int(self.view_b / self.duration * len(self.data8)) if self.duration else len(self.data8)
        a = max(0, min(a, len(self.data8) - 1))
        b = max(a + 1, min(b, len(self.data8)))
        peaks = pc.waveform_peaks(self.data8[a:b], cols)
        m = max(1e-4, max((max(abs(lo), abs(hi)) for lo, hi in peaks),
                          default=1e-4))
        for i, (lo, hi) in enumerate(peaks):
            x = gx + i
            cv.create_line(x, mid - hi / m * (gh / 2 - 2),
                           x, mid - lo / m * (gh / 2 - 2),
                           fill=WAVE, tags="wave")

        # Bereiche
        for r in self.regions:
            if r["end"] < self.view_a or r["start"] > self.view_b:
                continue
            x0 = max(gx, self._t2x(r["start"]))
            x1 = min(gx + gw, self._t2x(r["end"]))
            col = self._char_color(r["character"])
            sel = (r is self.sel)
            cv.create_rectangle(x0, gy, x1, gy + gh, fill=col, outline="",
                                stipple="gray50", tags="region")
            cv.create_rectangle(x0, gy, x1, gy + gh,
                                outline="#ffffff" if sel else col,
                                width=2 if sel else 1, tags="region")
            if x1 - x0 > 26:
                cv.create_text((x0 + x1) / 2, gy + 10,
                               text=r["character"] or t("unassigned"),
                               fill="#ffffff", font=("Segoe UI Semibold", 8),
                               tags="region")

        self.phead_item = cv.create_line(self._t2x(self.playhead), gy,
                                         self._t2x(self.playhead), gy + gh,
                                         fill=GOLD, width=2, tags="phead")

    def _char_color(self, name):
        for ch in self.characters:
            if ch["name"] == name:
                return ch["color"]
        return "#5b6488"

    # ------------------------------------------------- Maus / Interaktion
    def _in_wave(self, x, y):
        if not self.wave_box:
            return False
        gx, gy, gw, gh = self._wave_geom()
        return gx <= x <= gx + gw and gy <= y <= gy + gh

    def _region_at(self, x, y):
        for r in self.regions:
            x0, x1 = self._t2x(r["start"]), self._t2x(r["end"])
            if x0 - 6 <= x <= x1 + 6:
                if abs(x - x0) <= 6:
                    return r, "left"
                if abs(x - x1) <= 6:
                    return r, "right"
                if x0 <= x <= x1:
                    return r, "body"
        return None, None

    def _press(self, e):
        for b in self.buttons:
            if b.enabled and b.hit(e.x, e.y):
                b.command()
                return
        # Startbild-Karte
        dz = getattr(self, "_drop_zone", None)
        if dz and not self.frames and dz[0] <= e.x <= dz[2] and dz[1] <= e.y <= dz[3]:
            self.open_video()
            return
        # Figuren-Klick
        for (x0, y0, x1, y1, idx, kind) in self.char_hits:
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                if kind == "del":
                    self.delete_character(idx)
                else:
                    self.active = idx
                    self.build_ui()
                return
        # Wellenform
        if self._in_wave(e.x, e.y):
            r, part = self._region_at(e.x, e.y)
            if r is not None and part in ("left", "right"):
                self._drag = {"mode": "resize", "r": r, "edge": part}
            elif r is not None and part == "body":
                self.sel = r
                self._drag = {"mode": "maybe_move", "r": r,
                              "x": e.x, "t0": self._x2t(e.x)}
                self._load_sub()
                self.draw_wave()
                self._update_sel_info()
            else:
                self._drag = {"mode": "pending", "x": e.x, "t": self._x2t(e.x)}

    def _drag_move(self, e):
        d = self._drag
        if not d:
            return
        tnow = self._x2t(e.x)
        if d["mode"] == "resize":
            r = d["r"]
            if d["edge"] == "left":
                r["start"] = max(0.0, min(tnow, r["end"] - 0.05))
            else:
                r["end"] = min(self.duration, max(tnow, r["start"] + 0.05))
            self.draw_wave()
        elif d["mode"] == "maybe_move":
            if abs(e.x - d["x"]) > 4:
                d["mode"] = "move"
                d["grab"] = self._x2t(e.x) - d["r"]["start"]
        if d["mode"] == "move":
            r = d["r"]
            dur = r["end"] - r["start"]
            ns = max(0.0, min(self.duration - dur, tnow - d["grab"]))
            r["start"], r["end"] = ns, ns + dur
            self.draw_wave()
        elif d["mode"] == "pending":
            if abs(e.x - d["x"]) > 4:
                d["mode"] = "create"
            if d["mode"] == "create":
                self._temp_region(d["t"], tnow)

    def _temp_region(self, t0, t1):
        self.cv.delete("tmpreg")
        gx, gy, gw, gh = self._wave_geom()
        x0, x1 = sorted((self._t2x(t0), self._t2x(t1)))
        col = self._char_color(self._active_name())
        self.cv.create_rectangle(max(gx, x0), gy, min(gx + gw, x1), gy + gh,
                                 outline=col, width=2, tags="tmpreg")

    def _release(self, e):
        d = self._drag
        self._drag = None
        self.cv.delete("tmpreg")
        if not d:
            return
        if d["mode"] == "pending":
            # reiner Klick -> Playhead setzen
            self.seek(self._x2t(e.x))
            return
        if d["mode"] == "create":
            a, b = sorted((d["t"], self._x2t(e.x)))
            if b - a >= 0.12:
                self.add_region(a, b)
            return
        if d["mode"] in ("resize", "move"):
            self._normalize_regions()
            self.draw_wave()
            self._update_sel_info()

    def _double(self, e):
        if self._in_wave(e.x, e.y):
            r, _p = self._region_at(e.x, e.y)
            if r is not None:
                self.play_region(r)

    def _on_wheel(self, e):
        if not self._in_wave(e.x, e.y) or not self.duration:
            return
        anchor = self._x2t(e.x)
        span = self.view_b - self.view_a
        factor = 0.8 if e.delta > 0 else 1.25
        nspan = max(0.5, min(self.duration, span * factor))
        f = (anchor - self.view_a) / max(1e-6, span)
        self.view_a = max(0.0, anchor - f * nspan)
        self.view_b = min(self.duration, self.view_a + nspan)
        self.view_a = max(0.0, self.view_b - nspan)
        self.draw_wave()

    def _on_motion(self, e):
        for b in self.buttons:
            b.set_hover(b.enabled and b.hit(e.x, e.y))

    # ==================================================================
    #  BEREICHE
    # ==================================================================
    def _active_name(self):
        if self.active is not None and 0 <= self.active < len(self.characters):
            return self.characters[self.active]["name"]
        return ""

    def add_region(self, a, b):
        name = self._active_name()
        if not name:
            self._set_status(t("pick_char"))
            return
        r = {"start": float(a), "end": float(b), "character": name,
             "caption": ""}
        self.regions.append(r)
        self._normalize_regions()
        self.sel = r
        self._load_sub()
        self.draw_wave()
        self.build_ui()

    def _normalize_regions(self):
        self.regions.sort(key=lambda r: r["start"])

    def delete_region(self):
        if self.sel is None:
            return
        try:
            self.regions.remove(self.sel)
        except ValueError:
            pass
        self.sel = None
        self.build_ui()

    def clear_regions(self):
        if not self.regions:
            return
        if messagebox.askyesno(t("title"), t("clear")):
            self.regions = []
            self.sel = None
            self.build_ui()

    def auto_detect(self):
        if self.data8 is None:
            return
        found = pc.detect_clips(self.data8, SR8, sensitivity=1.0, max_clip=6.0)
        name = self._active_name()
        for (a, b) in found:
            self.regions.append({"start": float(a), "end": float(b),
                                 "character": name, "caption": ""})
        self._normalize_regions()
        self.build_ui()
        self._set_status(t("regions_n", len(self.regions)))

    def _update_sel_info(self):
        try:
            if self.sel is None:
                self.cv.itemconfigure(self.sel_info, text=t("no_region"))
            else:
                i = self.regions.index(self.sel) + 1
                self.cv.itemconfigure(
                    self.sel_info,
                    text=t("region_of", i, len(self.regions),
                           self.sel["character"] or t("unassigned")))
        except Exception:
            pass

    # ------------------------------------------------------ Untertitel
    def _load_sub(self):
        self._sub_for = self.sel
        self.sub_var.set(self.sel["caption"] if self.sel else "")
        self._update_sel_info()

    def _sub_save(self):
        if self._sub_for is None or self._sub_for not in self.regions:
            return
        self._sub_for["caption"] = self.sub_var.get().strip()

    def _sub_next(self, _e=None):
        self._sub_save()
        if self.sel in self.regions:
            i = self.regions.index(self.sel)
            if i + 1 < len(self.regions):
                self.sel = self.regions[i + 1]
                self._load_sub()
                self.draw_wave()
                if self.sub_entry:
                    self.sub_entry.focus_set()
                    self.sub_entry.selection_range(0, "end")
        return "break"

    # ==================================================================
    #  FIGUREN
    # ==================================================================
    def add_character(self):
        name = simpledialog.askstring(t("add_char"), t("rename"), parent=self)
        if not name:
            return
        name = name.strip()
        if not name or any(c["name"] == name for c in self.characters):
            return
        color = CHAR_COLORS[len(self.characters) % len(CHAR_COLORS)]
        self.characters.append({"name": name, "color": color})
        self.active = len(self.characters) - 1
        self.build_ui()

    def delete_character(self, idx):
        if not (0 <= idx < len(self.characters)):
            return
        name = self.characters[idx]["name"]
        if not messagebox.askyesno(t("title"), t("del_char_q", name)):
            return
        for r in self.regions:
            if r["character"] == name:
                r["character"] = ""
        del self.characters[idx]
        if self.active is not None:
            if self.active == idx:
                self.active = None
            elif self.active > idx:
                self.active -= 1
        self.build_ui()

    # ==================================================================
    #  WIEDERGABE
    # ==================================================================
    def _space(self):
        if self.frames:
            self.toggle_play()

    def toggle_play(self):
        if self.playing:
            self.stop_play()
        else:
            self.play_from(self.playhead)

    # ------------------------------------------------ Demucs / Trennung
    def _on_backing_toggle(self):
        """Beim Anhaken sofort trennen (Demucs). Ergebnis wird gecacht, damit
        Wiederanhaken und der Bau ohne erneute Trennung auskommen."""
        if not self.backing_on.get():
            self.build_ui()               # Slider ausblenden, Stems bleiben
            return
        if self.audio_path is None or self._busy:
            return
        if self._sep_done:
            self.build_ui()               # schon getrennt -> nur Slider zeigen
            return
        self._busy = True
        self._set_status(t("sep_run"))

        def work():
            voc, nov = pc.separate_vocals(self.audio_path, self.work)
            self._n_voc, self._n_nov = voc, nov
            self._n_voca = ds.read_wav_mono(voc, PSR)
            self._n_nova = ds.read_wav_mono(nov, PSR)

        def done():
            self._busy = False
            self._sep_voc, self._sep_nov = self._n_voc, self._n_nov
            self.voc_audio, self.nov_audio = self._n_voca, self._n_nova
            self._sep_done = True
            self.build_ui()
        self._run_bg(work, done)

    def _preview_audio(self):
        """Wiedergabe-Quelle: bei getrennter Spur eine Mischung aus Stimme und
        Hintergrund je nach Slider (0 = Stimme, 1 = Hintergrund)."""
        if self._sep_done and self.voc_audio is not None \
                and self.nov_audio is not None:
            s = float(self.blend_var.get())
            n = min(len(self.voc_audio), len(self.nov_audio))
            return self.voc_audio[:n] * (1.0 - s) + self.nov_audio[:n] * s
        return self.play_audio

    def play_from(self, start):
        src = self._preview_audio()
        if src is None:
            return
        start = max(0.0, min(self.duration - 0.02, start))
        a = int(start * PSR)
        seg = src[a:]
        if not len(seg):
            return
        try:
            self.mic.play(seg)
        except Exception:
            return
        self.playing = True
        self._play = {"start": start, "t0": time.perf_counter(),
                      "dur": self.duration - start, "region": None}
        self.build_ui()
        self._tick()

    def play_region(self, r):
        self.stop_play()
        self.sel = r
        self.playhead = r["start"]
        a = int(r["start"] * PSR)
        b = int(r["end"] * PSR)
        seg = self._preview_audio()[a:b]
        if not len(seg):
            return
        try:
            self.mic.play(seg)
        except Exception:
            return
        self.playing = True
        self._play = {"start": r["start"], "t0": time.perf_counter(),
                      "dur": r["end"] - r["start"], "region": r}
        self.build_ui()
        self._tick()

    def _tick(self):
        job = self._play
        if not job or not self.playing:
            return
        elapsed = time.perf_counter() - job["t0"]
        if elapsed >= job["dur"]:
            self.stop_play()
            return
        self.playhead = job["start"] + elapsed
        try:
            self.show_frame(self.playhead)
            if self.phead_item:
                gx, gy, gw, gh = self._wave_geom()
                self.cv.coords(self.phead_item, self._t2x(self.playhead), gy,
                               self._t2x(self.playhead), gy + gh)
        except Exception:
            traceback.print_exc()
        self.after(int(1000 / max(1.0, self.fps)), self._tick)

    def stop_play(self):
        self.playing = False
        self._play = None
        try:
            self.mic.stop_play()
        except Exception:
            pass
        # Knopfbeschriftung zuruecksetzen
        if self.wave_box:
            self.build_ui()

    def seek(self, tsec):
        self.stop_play()
        self.playhead = max(0.0, min(self.duration, tsec))
        self.show_frame(self.playhead)
        if self.phead_item:
            gx, gy, gw, gh = self._wave_geom()
            self.cv.coords(self.phead_item, self._t2x(self.playhead), gy,
                           self._t2x(self.playhead), gy + gh)

    def show_frame(self, seconds):
        if not self.frames or not HAVE_PIL or not self.video_box:
            return
        idx = int(round(seconds * self.fps))
        idx = max(0, min(len(self.frames) - 1, idx))
        vx, vy, vw, vh = self.video_box
        key = (idx, int(vw), int(vh))
        photo = self._imgcache.get(key)
        if photo is None:
            try:
                im = Image.open(self.frames[idx]).convert("RGB")
                im = im.resize((max(1, int(vw)), max(1, int(vh))))
                photo = ImageTk.PhotoImage(im)
            except Exception:
                return
            if len(self._imgcache) > 240:
                self._imgcache.clear()
            self._imgcache[key] = photo
        self.cv.itemconfigure(self.video_item, image=photo)
        self._photo = photo

    # ==================================================================
    #  PACK BAUEN
    # ==================================================================
    def build_pack(self):
        self._sub_save()
        if not self.regions:
            self._set_status(t("need_regions"))
            return
        name = pc.safe_name(self.pack_name.get(), "MyScene")
        if not name:
            self._set_status(t("need_name"))
            return
        self.pack_name.set(name)
        self.cfg["last_name"] = name
        self.cfg["backing"] = bool(self.backing_on.get())
        save_cfg(self.cfg)
        regions = sorted([dict(r) for r in self.regions],
                         key=lambda r: r["start"])
        want_back = bool(self.backing_on.get())
        self._set_status(t("building"))
        self._run_bg(lambda: self._do_build(name, regions, want_back),
                     self._after_build)

    def _do_build(self, name, regions, want_back):
        os.makedirs(PACKS_DIR, exist_ok=True)
        dest = os.path.join(PACKS_DIR, name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)

        cut_source = self.audio_path
        backing = None
        if want_back:
            if self._sep_done and self._sep_voc and self._sep_nov:
                cut_source, backing = self._sep_voc, self._sep_nov   # gecacht
            else:
                try:
                    voc, nov = pc.separate_vocals(self.audio_path, self.work)
                    cut_source = voc
                    backing = nov
                except Exception:
                    cut_source = self.audio_path
                    backing = None

        captions, characters = {}, {}
        lines = ["# %s" % name]
        total = len(regions)
        for i, r in enumerate(regions, 1):
            label = r["character"] or "line"
            fn = pc.clip_filename(i, label, r["start"], dub=True)
            self._set_status("%d / %d" % (i, total))
            pc.export_clip(cut_source, r["start"], r["end"],
                           os.path.join(dest, fn))
            cap = (r.get("caption") or "").strip()
            if cap:
                captions[fn] = cap
            if r["character"]:
                characters[fn] = r["character"]
            lines.append("%-44s %10.3f   %5.2fs%s"
                         % (fn, r["start"], r["end"] - r["start"],
                            "   | " + cap if cap else ""))

        if captions:
            pc.write_captions(dest, captions)
        if characters:
            pc.write_characters(dest, characters,
                                [c["name"] for c in self.characters])
        if backing and os.path.isfile(backing):
            pc.export_backing_track(backing,
                                    os.path.join(dest, "_backing_track.wav"))
        pc.convert_video(self.video_path,
                         os.path.join(dest, "dub_video.mp4"), max_height=720)
        with open(os.path.join(dest, "_TIMESTAMPS.txt"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(dest, "_README.txt"), "w",
                  encoding="utf-8") as f:
            f.write("Dub pack '%s' - %d lines. Built with DubMaker.\n"
                    % (name, total))
        self._built_path = dest

    def _after_build(self):
        self._set_status("")
        messagebox.showinfo(t("title"), t("built", self._built_path))

    # ------------------------------------------------------- Infra
    def _set_status(self, text):
        try:
            self.cv.itemconfigure(self.status_item, text=text)
        except Exception:
            pass

    def _run_bg(self, fn, on_done):
        def wrapper():
            try:
                fn()
                self.msgq.put(("done", on_done))
            except Exception as ex:
                traceback.print_exc()
                self.msgq.put(("error", str(ex)))
        threading.Thread(target=wrapper, daemon=True).start()

    def _pump(self):
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "done":
                    payload()
                elif kind == "error":
                    self._busy = False
                    messagebox.showerror(t("err"), payload)
                    self._set_status("")
                elif kind == "update":
                    self._prompt_update(payload)
                elif kind == "upd_status":
                    self._set_status(payload)
                elif kind == "upd_quit":
                    self.after(500, self._on_close)
                elif kind == "upd_error":
                    self._upd_busy = False
                    messagebox.showerror(t("upd_fail_t"), payload)
        except queue.Empty:
            pass
        self.after(60, self._pump)

    # ------------------------------------------------------------ Update
    def _check_update(self):
        if not upd.due(self.cfg):
            return
        upd.note_checked(self.cfg)
        save_cfg(self.cfg)

        def work():
            try:
                info = upd.check_latest()
            except Exception:
                return
            if info.get("newer"):
                self.msgq.put(("update", info))
        threading.Thread(target=work, daemon=True).start()

    def _prompt_update(self, info):
        if self._upd_busy:
            return
        frozen = getattr(sys, "frozen", False)
        ver = info.get("version", "?")
        if frozen and not info.get("asset"):
            if messagebox.askyesno(t("upd_head", ver), t("upd_ask_page")):
                try:
                    import webbrowser
                    webbrowser.open(info.get("page") or upd.RELEASES_PAGE)
                except Exception:
                    pass
            return
        if not frozen and not info.get("zip"):
            return
        if not messagebox.askyesno(t("upd_head", ver), t("upd_ask", ver)):
            return
        self._upd_busy = True
        self._set_status(t("upd_dl", 0))

        def work():
            wd = tempfile.mkdtemp(prefix="dubmaker_upd_")
            try:
                def prog(done, total):
                    pct = int(done * 100 / total) if total else 0
                    self.msgq.put(("upd_status", t("upd_dl", pct)))
                if frozen:
                    zp = os.path.join(wd, "build.zip")
                    upd.download_zip(info["asset"], zp, progress=prog,
                                     max_bytes=upd.MAX_ASSET)
                    self.msgq.put(("upd_status", t("upd_swap")))
                    root = upd.stage_packaged(zp, os.path.join(wd, "neu"))
                    upd.apply_packaged(root, APP_DIR,
                                       tag=info.get("tag", ""), stage_dir=wd)
                else:
                    zp = os.path.join(wd, "release.zip")
                    upd.download_zip(info["zip"], zp, progress=prog)
                    self.msgq.put(("upd_status", t("upd_swap")))
                    root = upd.stage(zp, os.path.join(wd, "neu"))
                    upd.apply(root, APP_DIR, which="DubMaker",
                              tag=info.get("tag", ""))
                self.msgq.put(("upd_quit", None))
            except Exception as e:
                self.msgq.put(("upd_error", "%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_close(self):
        try:
            self.mic.stop_play()
        except Exception:
            pass
        if self.work:
            shutil.rmtree(self.work, ignore_errors=True)
        save_cfg(self.cfg)
        self.destroy()


def _selftest_demucs():
    """Prueft im gebauten Exe, ob Demucs wirklich laeuft. Aktiviert ueber
    die Umgebungsvariable DUBMAKER_SELFTEST=demucs; oeffnet keine GUI."""
    import tempfile
    result = os.path.join(APP_DIR, "selftest_demucs.txt")
    try:
        w = tempfile.mkdtemp(prefix="selftest_")
        wav = os.path.join(w, "t.wav")
        pc.run([pc.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
                "-ac", "2", "-ar", "44100", wav])
        voc, nov = pc.separate_vocals(wav, w)
        ok = os.path.isfile(voc) and os.path.isfile(nov)
        msg = "OK" if ok else "FAIL: output missing"
    except Exception as e:
        msg = "ERROR: %r" % e
    try:
        with open(result, "w", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    if os.environ.get("DUBMAKER_SELFTEST") == "demucs":
        _selftest_demucs()
        raise SystemExit(0)
    DubMaker().mainloop()
