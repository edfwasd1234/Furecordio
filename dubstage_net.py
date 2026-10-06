# -*- coding: utf-8 -*-
"""
dubstage_net.py -- Client fuer den DubStage-Mehrspieler-Server.

Nur Standardbibliothek (urllib + ssl), genau wie updater.py. Die GUI ruft
diese Funktionen ueber ihren Hintergrund-Thread auf (_run_bg), damit das
Fenster nicht blockiert.

Ablauf:
  Host:    c = create_room(base, pack)      -> Raumcode, laedt Pack hoch
  Spieler: c = join_room(base, code, name)  -> laedt Pack herunter
  beide:   c.state()                        -> Lobby-Fortschritt (pollen)
  Spieler: c.upload_take(clip, wav_bytes)
  Host:    c.pull_takes(dest_dir)           -> alle Takes fuer den Zusammenbau
"""

import base64
import io
import json
import os
import ssl
import zipfile
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

TIMEOUT = 30
UA = "DubStage-Client"

# Einladungscode: ein einziger String, der Serveradresse UND Raumcode traegt.
# So muss ein Mitspieler nur eine Sache einfuegen -- keine Serveradresse tippen.
INVITE_PREFIX = "DUB1"


def make_invite(base_url, code):
    """Baut einen Einladungscode aus oeffentlicher Adresse + Raumcode."""
    raw = ((base_url or "").rstrip("/") + "|" + (code or "")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return INVITE_PREFIX + body


def web_link(base_url, code):
    """Link fuer den Web-Spieler (Handy): https://host/r/CODE."""
    return "%s/r/%s" % ((base_url or "").rstrip("/"), (code or "").upper())


def _parse_web_link(t):
    """https://host/r/CODE -> (https://host, CODE) oder None."""
    if not t.lower().startswith(("http://", "https://")):
        return None
    u = urlparse(t)
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 2 and parts[-2] == "r" and parts[-1].isalnum():
        prefix = "/".join(parts[:-2])
        base = "%s://%s%s" % (u.scheme, u.netloc,
                              ("/" + prefix) if prefix else "")
        return base, parts[-1].upper()
    return None


def parse_invite(text):
    """Zerlegt einen Einladungscode oder Web-Link -> (base_url, code) oder
    None (dann ist es wohl ein blanker Raumcode)."""
    t = (text or "").strip()
    web = _parse_web_link(t)
    if web:
        return web
    if len(t) <= len(INVITE_PREFIX) or t[:len(INVITE_PREFIX)].upper() != INVITE_PREFIX:
        return None
    body = t[len(INVITE_PREFIX):]
    body += "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8")
        url, code = raw.rsplit("|", 1)
        if not url or not code:
            return None
        return url, code.upper()
    except Exception:
        return None

_SSL = ssl.create_default_context()


# --------------------------------------------------------------------------
# Pack <-> Zip  und  Manifest
# --------------------------------------------------------------------------
def manifest_from_pack(pack):
    """Baut das Manifest (ohne Medien), das der Server fuer Lobby und
    Zuordnung braucht."""
    lines = []
    for ln in pack.lines:
        lines.append({
            "file": ln.file,
            "start": float(ln.start),
            "caption": getattr(ln, "caption", "") or "",
            "character": getattr(ln, "character", "") or "",
        })
    chars = list(getattr(pack, "characters", []) or [])
    for ln in pack.lines:                       # noch fehlende ergaenzen
        c = getattr(ln, "character", "")
        if c and c not in chars:
            chars.append(c)
    return {"name": pack.name, "characters": chars, "lines": lines}


def zip_folder(folder):
    """Packt einen Pack-Ordner (Video, Clips, _captions/_characters ...) in
    ein Zip im Speicher. Take-/Cache-Reste bleiben draussen."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                z.write(path, arcname=name)
    return buf.getvalue()


def unzip_to(raw, dest_dir):
    """Entpackt ein heruntergeladenes Pack-Zip flach nach dest_dir."""
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for info in z.infolist():
            name = os.path.basename(info.filename)      # kein Pfad-Ausbruch
            if not name or info.is_dir():
                continue
            with z.open(info) as src, \
                    open(os.path.join(dest_dir, name), "wb") as out:
                out.write(src.read())
    return dest_dir


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class NetError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _request(method, url, data=None, token=None, ctype=None,
             expect_json=True, timeout=TIMEOUT):
    headers = {"User-Agent": UA}
    if token:
        headers["X-DS-Token"] = token
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    kw = {"timeout": timeout}
    if url.lower().startswith("https"):
        kw["context"] = _SSL
    try:
        with urllib.request.urlopen(req, **kw) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        msg = _extract_error(body) or ("HTTP %s" % exc.code)
        raise NetError(msg, status=exc.code)
    except urllib.error.URLError as exc:
        raise NetError("cannot reach server: %s" % exc.reason)
    if not expect_json:
        return raw
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise NetError("bad response from server")


def _extract_error(body):
    try:
        return json.loads(body.decode("utf-8")).get("error")
    except Exception:
        return None


def _jbytes(obj):
    return json.dumps(obj).encode("utf-8")


def ping(base_url, timeout=8):
    """True, wenn der Server erreichbar ist (weckt auch Free-Tier-Dienste)."""
    try:
        _request("GET", base_url.rstrip("/") + "/health", timeout=timeout)
        return True
    except NetError:
        return False


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------
class Session(object):
    """Haelt Serveradresse, Raumcode und die eigenen Tokens beisammen."""

    def __init__(self, base_url, code, player_id, token,
                 host_token=None, manifest=None):
        self.base = base_url.rstrip("/")
        self.code = code
        self.player_id = player_id
        self.token = token
        self.host_token = host_token
        self.manifest = manifest or {}

    @property
    def is_host(self):
        return bool(self.host_token)

    def _room(self, *parts):
        return "/".join([self.base, "rooms", self.code]
                        + [quote(p, safe="") for p in parts])

    # -- Zustand ----------------------------------------------------------
    def state(self):
        # Token mitsenden, damit der Server "ich bin noch da" merkt
        # (Anwesenheit/Geister-Entfernung).
        return _request("GET", self._room("state"),
                        token=self.host_token or self.token)

    def leave(self):
        """Ausdruecklich aus dem Raum austreten (Figuren werden frei)."""
        return _request("POST", self._room("leave"),
                        token=self.token or self.host_token)

    # -- Zuordnung / Phase (Host bzw. Selbstwahl) -------------------------
    def set_assignments(self, mapping):
        """Host: komplette Figur->Spieler-Zuordnung setzen."""
        return _request("POST", self._room("assign"),
                        data=_jbytes({"assignments": mapping}),
                        token=self.host_token, ctype="application/json")

    def claim(self, character, player_id=None):
        """Eine Figur (sich selbst oder als Host beliebig) zuordnen; mit
        player_id=None wird sie wieder freigegeben."""
        pid = self.player_id if player_id is None else player_id
        body = {"character": character, "player_id": pid}
        return _request("POST", self._room("assign"), data=_jbytes(body),
                        token=self.host_token or self.token,
                        ctype="application/json")

    def release(self, character):
        return _request("POST", self._room("assign"),
                        data=_jbytes({"character": character,
                                      "player_id": None}),
                        token=self.host_token or self.token,
                        ctype="application/json")

    def set_phase(self, phase):
        return _request("POST", self._room("phase"),
                        data=_jbytes({"phase": phase}),
                        token=self.host_token, ctype="application/json")

    # -- Pack -------------------------------------------------------------
    def upload_pack_folder(self, folder):
        raw = zip_folder(folder)
        return _request("PUT", self._room("pack"), data=raw,
                        token=self.host_token, ctype="application/zip")

    def download_pack_to(self, dest_dir):
        raw = _request("GET", self._room("pack"), expect_json=False,
                       timeout=120)
        return unzip_to(raw, dest_dir)

    # -- Takes ------------------------------------------------------------
    def upload_take(self, clip, wav_bytes):
        return _request("POST", self._room("takes", clip), data=wav_bytes,
                        token=self.token, ctype="audio/wav")

    def pull_takes(self, dest_dir):
        """Host: alle Takes als Zip holen und flach nach dest_dir entpacken.
        Rueckgabe: Liste der Clip-Dateinamen."""
        raw = _request("GET", self._room("takes"), token=self.host_token,
                       expect_json=False, timeout=120)
        os.makedirs(dest_dir, exist_ok=True)
        names = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for info in z.infolist():
                name = os.path.basename(info.filename)
                if not name or info.is_dir():
                    continue
                with z.open(info) as src, \
                        open(os.path.join(dest_dir, name), "wb") as out:
                    out.write(src.read())
                names.append(name)
        return names

    def close_room(self):
        return _request("DELETE", self._room(), token=self.host_token)


# --------------------------------------------------------------------------
# Einstiegspunkte
# --------------------------------------------------------------------------
def create_room(base_url, pack, host_name="Host", upload=True, app_version=""):
    """Host: Raum anlegen und (optional) das Pack hochladen."""
    base = base_url.rstrip("/")
    manifest = manifest_from_pack(pack)
    manifest["host_name"] = host_name
    if app_version:
        manifest["app_version"] = app_version
    res = _request("POST", base + "/rooms", data=_jbytes(manifest),
                   ctype="application/json")
    sess = Session(base, res["code"], res["player_id"], res["token"],
                   host_token=res["host_token"], manifest=manifest)
    if upload:
        sess.upload_pack_folder(pack.folder)
    return sess


def join_room(base_url, code, name="Player", app_version="", key=""):
    """Spieler: Raum betreten (oder mit key wiederverbinden). Danach
    download_pack_to(...) aufrufen. Das Ergebnis traegt `reconnected`."""
    base = base_url.rstrip("/")
    body = {"name": name}
    if app_version:
        body["app_version"] = app_version
    if key:
        body["key"] = key
    res = _request("POST", "/".join([base, "rooms", code.upper(), "join"]),
                   data=_jbytes(body), ctype="application/json")
    sess = Session(base, code.upper(), res["player_id"], res["token"],
                   manifest=res.get("manifest", {}))
    sess.reconnected = bool(res.get("reconnected"))
    return sess
