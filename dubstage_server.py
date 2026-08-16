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
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Konfiguration / configuration (per Umgebungsvariablen ueberschreibbar)
# --------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = os.environ.get("DUBSTAGE_DATA",
                          os.path.join(tempfile.gettempdir(), "dubstage_server"))
ROOM_TTL = int(os.environ.get("DUBSTAGE_ROOM_TTL", str(6 * 3600)))   # 6 h
MAX_PLAYERS = int(os.environ.get("DUBSTAGE_MAX_PLAYERS", "10"))      # inkl. Host
MAX_PACK = int(os.environ.get("DUBSTAGE_MAX_PACK", str(300 * 1024 * 1024)))
MAX_TAKE = int(os.environ.get("DUBSTAGE_MAX_TAKE", str(25 * 1024 * 1024)))
MAX_JSON = 2 * 1024 * 1024
MAX_RESULT = 200 * 1024 * 1024
PLAY_BUFFER = 5.0            # Vorlauf in s, damit alle synchron starten

# Raumcode: gut vorlesbar, ohne leicht verwechselbare Zeichen.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 4


# --------------------------------------------------------------------------
# Zustand / state
# --------------------------------------------------------------------------
class Room(object):
    def __init__(self, code, manifest, host_name):
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
        self.dir = os.path.join(DATA_DIR, code)
        self.takes_dir = os.path.join(self.dir, "takes")
        self.pack_path = os.path.join(self.dir, "pack.zip")
        self.result_path = os.path.join(self.dir, "result.wav")
        self.play_start = None          # Server-Epoch, wann die Wiedergabe laeuft
        self.play_seq = 0               # zaehlt jede neue "gemeinsam ansehen"-Runde
        os.makedirs(self.takes_dir, exist_ok=True)
        self.host_pid = self.add_player(host_name)[0]

    def add_player(self, name):
        pid = _token(6)
        self.players[pid] = {"name": (name or "Player").strip()[:40] or "Player",
                             "token": _token(), "chars": set()}
        self.touch()
        return pid, self.players[pid]

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
            "server_time": time.time(),
            "play_start": self.play_start,
            "play_seq": self.play_seq,
            "has_result": os.path.isfile(self.result_path),
        }

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


ROOMS = {}
LOCK = threading.RLock()


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
            parts = [p for p in path.split("/") if p]
            if not parts:
                return self._send(200, {"ok": True, "service": "dubstage",
                                        "version": VERSION,
                                        "rooms": len(ROOMS)})
            if parts[0] == "health":
                return self._send(200, {"ok": True, "rooms": len(ROOMS)})
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

            if sub == "" and method == "GET":
                return self._send(200, room.public_state())
            if sub == "" and method == "DELETE":
                return self._delete_room(room)
            if sub == "state" and method == "GET":
                return self._send(200, room.public_state())
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
            if sub == "takes" and method == "GET":
                return self._download_takes(room)
            if sub == "takes" and len(parts) == 4 and method == "POST":
                return self._upload_take(room, parts[3])
            if sub == "result" and method == "POST":
                return self._upload_result(room)
            if sub == "result" and method == "GET":
                return self._download_result(room)
            return self._err(404, "not found")
        except BrokenPipeError:
            pass
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
        with LOCK:
            code = _new_code()
            room = Room(code, manifest, host_name)
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
        with LOCK:
            if len(room.players) >= MAX_PLAYERS:
                return self._err(403, "room is full (%d players)" % MAX_PLAYERS)
            pid, p = room.add_player(data.get("name", "Player"))
        return self._send(200, {
            "player_id": pid,
            "token": p["token"],
            "manifest": room.manifest,
            "state": room.public_state(),
        })

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
        return self._send(200, {"ok": True, "size": len(raw)})

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

    def _upload_result(self, room):
        """Host laedt den fertigen Mix hoch und startet die gemeinsame
        Wiedergabe. Alle Clients starten dann zum selben Zeitpunkt."""
        if self._token_hdr() != room.host_token:
            return self._err(403, "host only")
        raw = self._body(MAX_RESULT)
        if raw is None:
            return self._err(413, "result too large")
        if not raw[:4] == b"RIFF":
            return self._err(400, "expected a wav")
        with open(room.result_path, "wb") as f:
            f.write(raw)
        with LOCK:
            room.play_seq += 1
            room.play_start = time.time() + PLAY_BUFFER
            room.phase = "playing"
            room.touch()
        return self._send(200, {"ok": True, "play_seq": room.play_seq,
                                "play_start": room.play_start,
                                "server_time": time.time()})

    def _download_result(self, room):
        who = self._requester_pid(room)
        if who is None:
            return self._err(403, "join the room first")
        if not os.path.isfile(room.result_path):
            return self._err(404, "no result yet")
        with open(room.result_path, "rb") as f:
            raw = f.read()
        return self._send(200, raw=raw, ctype="audio/wav")

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
def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    threading.Thread(target=_prune_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("DubStage server %s on port %d  (data: %s)"
          % (VERSION, PORT, DATA_DIR))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
