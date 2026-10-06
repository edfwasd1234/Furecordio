# -*- coding: utf-8 -*-
"""
dubstage_server.py -- Relay-/Lobby-Server fuer DubStage-Mehrspieler.

Gartic-Phone-Prinzip: ein Host oeffnet einen Raum (Raumcode), Spieler treten
uebers Internet bei, jede Figur wird einem Spieler zugeordnet, alle sprechen
ihre Zeilen parallel ein, ein Lobby-Bildschirm zeigt den Fortschritt, am Ende
holt der Host alle Takes und baut das fertige Video zusammen.

Nur Standardbibliothek - keine Fremd-Pakete. Damit laeuft der Server ohne
Build-Schritt ueberall, wo Python liegt (z. B. ein kostenloser Render-Dienst).
Start:   python dubstage_server.py        (Port aus $PORT oder 8000)

Der Server haelt nur: Raeume, das hochgeladene Pack-Zip, die Figurenzuordnung
und die kleinen Take-WAVs. Das schwere Rendern passiert beim Host, nie hier.
"""

import io
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

VERSION = "1.1.0"

# Web-Spieler (HTML/JS fuer Handys). Im Exe-Build liegt er im entpackten
# Bundle (_MEIPASS), sonst neben dieser Datei.
WEB_DIR = os.path.join(getattr(sys, "_MEIPASS",
                               os.path.dirname(os.path.abspath(__file__))),
                       "web")

CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".ogv": "video/ogg",
    ".avi": "video/x-msvideo",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}
VIDEO_NAMES = ("dub_video.mp4", "dub_video.webm", "dub_video.mov",
               "dub_video.mkv", "dub_video.ogv", "dub_video.avi")


def _ctype(name):
    return CTYPES.get(os.path.splitext(name)[1].lower(),
                      "application/octet-stream")


def _wav_dur(path):
    """Laenge eines PCM-WAVs in Sekunden (ohne ffmpeg) oder None."""
    import wave
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return None

# --------------------------------------------------------------------------
# Konfiguration / configuration (per Umgebungsvariablen ueberschreibbar)
# --------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = os.environ.get("DUBSTAGE_DATA",
                          os.path.join(tempfile.gettempdir(), "dubstage_server"))
ROOM_TTL = int(os.environ.get("DUBSTAGE_ROOM_TTL", str(6 * 3600)))   # 6 h
# Spieler, die sich so lange nicht gemeldet haben, gelten als weg und werden
# aus der Lobby entfernt (Figuren wieder frei). AWAY = noch "da, aber still".
# 90 s: Handys pausieren das Pollen, wenn der Bildschirm kurz gesperrt ist.
PLAYER_TTL = int(os.environ.get("DUBSTAGE_PLAYER_TTL", "90"))
PLAYER_AWAY = int(os.environ.get("DUBSTAGE_PLAYER_AWAY", "12"))
MAX_PLAYERS = int(os.environ.get("DUBSTAGE_MAX_PLAYERS", "10"))      # inkl. Host
MAX_PACK = int(os.environ.get("DUBSTAGE_MAX_PACK", str(300 * 1024 * 1024)))
MAX_TAKE = int(os.environ.get("DUBSTAGE_MAX_TAKE", str(25 * 1024 * 1024)))
MAX_JSON = 2 * 1024 * 1024

# Raumcode: gut vorlesbar, ohne leicht verwechselbare Zeichen.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 4


# --------------------------------------------------------------------------
# Zustand / state
# --------------------------------------------------------------------------
class Room(object):
    def __init__(self, code, manifest, host_name, host_version=""):
        now = time.time()
        self.code = code
        self.created = now
        self.touched = now
        self.phase = "lobby"                 # lobby | recording | done
        self.manifest = manifest             # {name, characters:[], lines:[]}
        self.host_token = _token()
        self.players = {}                    # pid -> {name, token, chars:set}
        self.assign = {}                     # character -> pid
        self.takes = {}                      # clip -> {pid, path, size, ts}
        self.versions = set()                # App-Versionen im Raum
        self.dir = os.path.join(DATA_DIR, code)
        self.takes_dir = os.path.join(self.dir, "takes")
        self.pack_path = os.path.join(self.dir, "pack.zip")
        self.files_dir = os.path.join(self.dir, "pack")   # entpackt (Web)
        self.info = None                     # {video, backing, lines[dur]}
        os.makedirs(self.takes_dir, exist_ok=True)
        self.host_pid = self.add_player(host_name, host_version)[0]

    def add_player(self, name, version="", key=""):
        pid = _token(6)
        self.players[pid] = {"name": (name or "Player").strip()[:40] or "Player",
                             "token": _token(), "chars": set(),
                             "key": str(key or "")[:64],
                             "last_seen": time.time()}
        v = str(version or "").strip()[:20]
        if v:
            self.versions.add(v)
        self.touch()
        return pid, self.players[pid]

    def find_by_key(self, key):
        """pid eines bereits bekannten Spielers mit diesem Wiederverbindungs-
        Schluessel - fuer die Rueckkehr nach einem Absturz/Neustart."""
        key = str(key or "")
        if not key:
            return None
        for pid, p in self.players.items():
            if p.get("key") and p.get("key") == key:
                return pid
        return None

    def seen(self, pid):
        p = self.players.get(pid)
        if p:
            p["last_seen"] = time.time()

    def remove_player(self, pid):
        """Spieler entfernen und seine Figuren wieder freigeben. Der Host wird
        so nie entfernt (der Host beendet den Raum ueber DELETE)."""
        if pid == self.host_pid or pid not in self.players:
            return False
        self.players.pop(pid, None)
        for ch in [c for c, q in list(self.assign.items()) if q == pid]:
            self.assign.pop(ch, None)
        self.touch()
        return True

    def prune_players(self, ttl):
        """Spieler entfernen, die sich seit ttl Sekunden nicht mehr gemeldet
        haben (App zu/abgestuerzt), damit sie nicht als Geister haengen und
        ihre Figuren wieder frei werden. Host bleibt immer."""
        now = time.time()
        dead = [pid for pid, p in self.players.items()
                if pid != self.host_pid
                and (now - p.get("last_seen", now)) > ttl]
        for pid in dead:
            self.remove_player(pid)
        return dead

    def touch(self):
        self.touched = time.time()

    def line_files(self):
        return [ln["file"] for ln in self.manifest.get("lines", [])]

    def char_of(self, clip):
        for ln in self.manifest.get("lines", []):
            if ln["file"] == clip:
                return ln.get("character", "")
        return None

    def lines_for_pid(self, pid):
        """Clip-Dateinamen, deren Figur diesem Spieler zugeordnet ist."""
        mine = {c for c, p in self.assign.items() if p == pid}
        return [ln["file"] for ln in self.manifest.get("lines", [])
                if ln.get("character", "") in mine]

    def public_state(self):
        now = time.time()
        recorded = set(self.takes.keys())
        lines = self.manifest.get("lines", [])
        total = len(lines)
        unassigned = [ln["file"] for ln in lines
                      if self.assign.get(ln.get("character", "")) is None]
        players = []
        for pid, p in self.players.items():
            mine = self.lines_for_pid(pid)
            players.append({
                "id": pid,
                "name": p["name"],
                "is_host": pid == self.host_pid,
                "online": (now - p.get("last_seen", now)) < PLAYER_AWAY,
                "characters": sorted(c for c, q in self.assign.items()
                                     if q == pid),
                "assigned": len(mine),
                "recorded": sum(1 for f in mine if f in recorded),
            })
        players.sort(key=lambda x: (not x["is_host"], x["name"].lower()))
        return {
            "code": self.code,
            "phase": self.phase,
            "version": VERSION,
            "pack_name": self.manifest.get("name", ""),
            "characters": self.manifest.get("characters", []),
            "assignments": dict(self.assign),
            "players": players,
            "player_count": len(self.players),
            "max_players": MAX_PLAYERS,
            "total_lines": total,
            "recorded_lines": len(recorded & set(self.line_files())),
            "unassigned_lines": len(unassigned),
            "has_pack": os.path.isfile(self.pack_path),
            "recorded_clips": sorted(recorded & set(self.line_files())),
            "versions": sorted(self.versions),
        }

    def extract_pack(self):
        """Pack-Zip flach entpacken, damit der Web-Spieler einzelne Dateien
        (Video, Clips) streamen kann, und Zeilenlaengen ermitteln."""
        shutil.rmtree(self.files_dir, ignore_errors=True)
        os.makedirs(self.files_dir, exist_ok=True)
        with zipfile.ZipFile(self.pack_path) as z:
            for zi in z.infolist():
                name = os.path.basename(zi.filename)   # kein Pfad-Ausbruch
                if not name or zi.is_dir():
                    continue
                with z.open(zi) as src, \
                        open(os.path.join(self.files_dir, name), "wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
        files = set(os.listdir(self.files_dir))
        video = next((v for v in VIDEO_NAMES if v in files), None)
        backing = next((f for f in sorted(files)
                        if f.lower().startswith("_backing_track")
                        and f.lower().endswith(".wav")), None)
        lines = []
        for ln in self.manifest.get("lines", []):
            d = _wav_dur(os.path.join(self.files_dir, ln["file"]))
            item = dict(ln)
            item["dur"] = round(d, 3) if d else 3.0
            lines.append(item)
        self.info = {"video": video, "backing": backing, "lines": lines,
                     "characters": self.manifest.get("characters", []),
                     "name": self.manifest.get("name", "")}

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


ROOMS = {}
LOCK = threading.RLock()
_PRUNE_STARTED = False


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Wie ThreadingHTTPServer, aber harmlose Verbindungsabbrueche (ein
    Tunnel/Browser schliesst die Verbindung) landen nicht als Traceback auf
    der Konsole."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, ConnectionResetError,
                            BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def _token(n=16):
    return secrets.token_hex(n)


def _new_code():
    for _ in range(500):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        if code not in ROOMS:
            return code
    # Extrem unwahrscheinlich: laenger werden.
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN + 2))


def _prune_loop():
    while True:
        time.sleep(300)
        cutoff = time.time() - ROOM_TTL
        with LOCK:
            dead = [c for c, r in ROOMS.items() if r.touched < cutoff]
            for c in dead:
                ROOMS.pop(c).cleanup()


# --------------------------------------------------------------------------
# HTTP-Handler
# --------------------------------------------------------------------------
SAFE_CLIP = re.compile(r"^[\w.\- ]+\.(wav|mp3|ogg|flac)$", re.IGNORECASE)


class Handler(BaseHTTPRequestHandler):
    server_version = "DubStage/" + VERSION
    protocol_version = "HTTP/1.1"

    # -- kleine Helfer ----------------------------------------------------
    def _body(self, limit):
        n = int(self.headers.get("Content-Length", "0") or "0")
        if n < 0 or n > limit:
            return None
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        raw = self._body(MAX_JSON)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return None

    def _send(self, code, payload=None, ctype="application/json", raw=None):
        if raw is None:
            raw = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                   if payload is not None else b"")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _send_file(self, path, ctype, cache="no-cache"):
        """Datei streamen, mit HTTP-Range (206). Ohne Range spielt iOS-Safari
        kein Video ab; ausserdem laedt so niemand das ganze Video vorab."""
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = (self.headers.get("Range") or "").strip()
        m = re.match(r"^bytes=(\d*)-(\d*)$", rng)
        if m and size > 0:
            s, e = m.group(1), m.group(2)
            if s == "" and e:                         # letzte n Bytes
                start = max(0, size - int(e))
            else:
                start = int(s or 0)
                if e:
                    end = min(int(e), size - 1)
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range",
                             "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command == "HEAD" or not length:
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _token_hdr(self):
        return self.headers.get("X-DS-Token", "")

    def log_message(self, fmt, *args):        # ruhiger Log
        pass

    # -- Routing ----------------------------------------------------------
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_HEAD(self):
        self._route("GET")

    def _route(self, method):
        try:
            path = self.path.split("?", 1)[0].rstrip("/")
            parts = [unquote(p) for p in path.split("/") if p]
            if not parts:
                return self._send(200, {"ok": True, "service": "dubstage",
                                        "version": VERSION,
                                        "rooms": len(ROOMS)})
            if parts[0] == "health":
                return self._send(200, {"ok": True, "rooms": len(ROOMS)})
            # Web-Spieler: /r/CODE oeffnet die Handy-Seite, /web/... Dateien
            if parts[0] == "r" and method == "GET":
                return self._web("index.html")
            if parts[0] == "web" and len(parts) == 2 and method == "GET":
                return self._web(parts[1])
            if parts[0] != "rooms":
                return self._err(404, "not found")

            # POST /rooms  -> Raum anlegen
            if len(parts) == 1:
                if method != "POST":
                    return self._err(405, "method not allowed")
                return self._create_room()

            code = parts[1].upper()
            with LOCK:
                room = ROOMS.get(code)
            if room is None:
                return self._err(404, "room not found")
            room.touch()
            sub = parts[2] if len(parts) > 2 else ""

            if sub in ("", "state") and method == "GET":
                # Anwesenheit pflegen: wer gerade pollt, ist "da"; wer lange
                # weg ist, wird entfernt (kein Geist, Figur wird frei).
                with LOCK:
                    who = self._requester_pid(room)
                    if who == "*host*":
                        who = room.host_pid
                    if who:
                        room.seen(who)
                    room.prune_players(PLAYER_TTL)
                return self._send(200, room.public_state())
            if sub == "" and method == "DELETE":
                return self._delete_room(room)
            if sub == "leave" and method == "POST":
                return self._leave(room)
            if sub == "join" and method == "POST":
                return self._join(room)
            if sub == "assign" and method == "POST":
                return self._assign(room)
            if sub == "phase" and method == "POST":
                return self._phase(room)
            if sub == "pack" and method == "PUT":
                return self._upload_pack(room)
            if sub == "pack" and method == "GET":
                return self._download_pack(room)
            if sub == "info" and method == "GET":
                return self._room_info(room)
            if sub == "files" and len(parts) == 4 and method == "GET":
                return self._room_file(room, parts[3])
            if sub == "takes" and method == "GET":
                return self._download_takes(room)
            if sub == "takes" and len(parts) == 4 and method == "POST":
                return self._upload_take(room, parts[3])
            return self._err(404, "not found")
        except (BrokenPipeError, ConnectionError):
            pass                              # Client hat abgebrochen (normal)
        except Exception as exc:              # nie den Server umbringen
            try:
                self._err(500, "server error: %s" % exc)
            except Exception:
                pass

    # -- Endpunkte --------------------------------------------------------
    def _create_room(self):
        data = self._json_body()
        if data is None:
            return self._err(400, "bad json")
        lines = data.get("lines")
        if not isinstance(lines, list) or not lines:
            return self._err(400, "manifest needs a non-empty 'lines' list")
        clean_lines = []
        for ln in lines:
            if not isinstance(ln, dict) or "file" not in ln:
                return self._err(400, "each line needs a 'file'")
            clean_lines.append({
                "file": str(ln["file"]),
                "start": float(ln.get("start", 0.0)),
                "caption": str(ln.get("caption", "")),
                "character": str(ln.get("character", "")),
            })
        manifest = {
            "name": str(data.get("name", "Scene"))[:80],
            "characters": [str(c) for c in data.get("characters", []) if c],
            "lines": clean_lines,
        }
        host_name = data.get("host_name", "Host")
        host_version = data.get("app_version", "")
        with LOCK:
            code = _new_code()
            room = Room(code, manifest, host_name, host_version)
            ROOMS[code] = room
            host = room.players[room.host_pid]
        return self._send(200, {
            "code": code,
            "host_token": room.host_token,
            "player_id": room.host_pid,
            "token": host["token"],
        })

    def _delete_room(self, room):
        if self._token_hdr() != room.host_token:
            return self._err(403, "host only")
        with LOCK:
            ROOMS.pop(room.code, None)
        room.cleanup()
        return self._send(200, {"ok": True})

    def _join(self, room):
        data = self._json_body()
        if data is None:
            return self._err(400, "bad json")
        key = data.get("key", "")
        version = str(data.get("app_version", "")).strip()[:20]
        with LOCK:
            existing = room.find_by_key(key)
            if existing:
                # Wiederverbindung: gleiche Identitaet + Figuren behalten.
                pid = existing
                p = room.players[pid]
                p["last_seen"] = time.time()
                if data.get("name"):
                    p["name"] = str(data["name"]).strip()[:40] or p["name"]
                reconnected = True
            else:
                if len(room.players) >= MAX_PLAYERS:
                    return self._err(403,
                                     "room is full (%d players)" % MAX_PLAYERS)
                pid, p = room.add_player(data.get("name", "Player"),
                                         version, key)
                reconnected = False
            if version:
                room.versions.add(version)
        return self._send(200, {
            "player_id": pid,
            "token": p["token"],
            "reconnected": reconnected,
            "manifest": room.manifest,
            "state": room.public_state(),
        })

    def _leave(self, room):
        """Spieler verlaesst den Raum ausdruecklich -> sofort entfernen und
        seine Figuren freigeben. (Host-Token: der Host beendet ueber DELETE.)"""
        who = self._requester_pid(room)
        if who not in (None, "*host*"):
            with LOCK:
                room.remove_player(who)
        return self._send(200, {"ok": True})

    def _requester_pid(self, room):
        tok = self._token_hdr()
        if tok == room.host_token:
            return "*host*"
        for pid, p in room.players.items():
            if p["token"] == tok:
                return pid
        return None

    def _assign(self, room):
        who = self._requester_pid(room)
        if who is None:
            return self._err(403, "unknown token")
        data = self._json_body()
        if data is None:
            return self._err(400, "bad json")
        is_host = who == "*host*"
        with LOCK:
            if isinstance(data.get("assignments"), dict):
                if not is_host:
                    return self._err(403, "host sets full assignments")
                new = {}
                for ch, pid in data["assignments"].items():
                    if pid in room.players and ch:
                        new[str(ch)] = pid
                room.assign = new
            elif "character" in data:
                ch = str(data["character"])
                pid = data.get("player_id")
                if pid in (None, "", "null"):
                    room.assign.pop(ch, None)          # freigeben
                elif pid in room.players and (is_host or pid == who):
                    room.assign[ch] = pid
                else:
                    return self._err(403, "cannot assign that player")
            else:
                return self._err(400, "need 'assignments' or 'character'")
            room.touch()
        return self._send(200, room.public_state())

    def _phase(self, room):
        if self._token_hdr() != room.host_token:
            return self._err(403, "host only")
        data = self._json_body() or {}
        phase = data.get("phase")
        if phase not in ("lobby", "recording", "done"):
            return self._err(400, "bad phase")
        room.phase = phase
        return self._send(200, room.public_state())

    def _upload_pack(self, room):
        if self._token_hdr() != room.host_token:
            return self._err(403, "host only")
        raw = self._body(MAX_PACK)
        if raw is None:
            return self._err(413, "pack too large")
        if not raw[:2] == b"PK":
            return self._err(400, "expected a zip")
        with open(room.pack_path, "wb") as f:
            f.write(raw)
        try:
            room.extract_pack()
        except Exception as exc:
            return self._err(400, "bad pack zip: %s" % exc)
        return self._send(200, {"ok": True, "size": len(raw)})

    def _room_info(self, room):
        if room.info is None:
            return self._err(404, "no pack uploaded yet")
        return self._send(200, room.info)

    def _room_file(self, room, name):
        """Einzelne Pack-Datei fuer den Web-Spieler (mit Range-Support)."""
        if (not name or name != os.path.basename(name)
                or name.startswith(".")):
            return self._err(400, "bad name")
        path = os.path.join(room.files_dir, name)
        if not os.path.isfile(path):
            return self._err(404, "no such file")
        return self._send_file(path, _ctype(name), cache="public, max-age=3600")

    def _web(self, name):
        """Statische Dateien des Web-Spielers."""
        if (not name or name != os.path.basename(name)
                or name.startswith(".")):
            return self._err(400, "bad name")
        path = os.path.join(WEB_DIR, name)
        if not os.path.isfile(path):
            return self._err(404, "web player missing (%s)" % name)
        return self._send_file(path, _ctype(name), cache="no-cache")

    def _download_pack(self, room):
        if not os.path.isfile(room.pack_path):
            return self._err(404, "no pack uploaded yet")
        with open(room.pack_path, "rb") as f:
            raw = f.read()
        return self._send(200, raw=raw, ctype="application/zip")

    def _upload_take(self, room, clip):
        who = self._requester_pid(room)
        if who is None or who == "*host*":
            return self._err(403, "join as a player to upload takes")
        if not SAFE_CLIP.match(clip) or clip not in room.line_files():
            return self._err(400, "unknown clip")
        # Optional: nur die eigene Figur einsprechen.
        ch = room.char_of(clip)
        owner = room.assign.get(ch)
        if owner is not None and owner != who:
            return self._err(403, "that line belongs to another player")
        raw = self._body(MAX_TAKE)
        if raw is None:
            return self._err(413, "take too large")
        if not raw[:4] == b"RIFF":
            return self._err(400, "expected a wav")
        path = os.path.join(room.takes_dir, clip)
        with open(path, "wb") as f:
            f.write(raw)
        with LOCK:
            room.takes[clip] = {"pid": who, "path": path,
                                "size": len(raw), "ts": time.time()}
            room.touch()
        return self._send(200, {"ok": True, "clip": clip,
                                "recorded_lines": len(room.takes)})

    def _download_takes(self, room):
        if self._token_hdr() != room.host_token:
            return self._err(403, "host only")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for clip, info in list(room.takes.items()):
                if os.path.isfile(info["path"]):
                    z.write(info["path"], arcname=clip)
        return self._send(200, raw=buf.getvalue(), ctype="application/zip")


# --------------------------------------------------------------------------
def _ensure_prune():
    """Startet den Aufraeum-Thread genau einmal pro Prozess."""
    global _PRUNE_STARTED
    if not _PRUNE_STARTED:
        _PRUNE_STARTED = True
        threading.Thread(target=_prune_loop, daemon=True).start()


def start_background(port=0, host="0.0.0.0"):
    """Startet den Relay im selben Prozess in einem Daemon-Thread und gibt
    (httpd, port) zurueck. port=0 -> freier Port wird gewaehlt.

    Damit kann DubStage selbst einen Raum auf diesem Rechner anbieten, ohne
    einen separaten Python-Prozess oder einen gemieteten Server zu brauchen.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    _ensure_prune()
    httpd = QuietThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    _ensure_prune()
    httpd = QuietThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("DubStage server %s on port %d  (data: %s)"
          % (VERSION, PORT, DATA_DIR))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
