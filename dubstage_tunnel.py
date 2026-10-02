# -*- coding: utf-8 -*-
"""
dubstage_tunnel.py -- den lokalen Relay OHNE Portfreigabe ins Internet bringen.

Idee: Statt einen Server zu mieten, startet DubStage den Relay direkt auf dem
Rechner des Hosts (dubstage_server.start_background) und legt davor einen
Cloudflare "Quick Tunnel". Der gibt eine oeffentliche https-Adresse
(https://....trycloudflare.com) zurueck, die ueberall erreichbar ist -- kein
Router-Portforwarding, kein Konto, keine Anmeldung noetig.

Gebraucht wird nur die kleine `cloudflared`-Binaerdatei. Finden wir sie nicht,
laden wir sie einmalig nach tools/ (genau wie ffmpeg). Alles Standardbibliothek.

Benutzung (aus einem Hintergrund-Thread):
    exe = ensure_cloudflared(progress=cb)       # laedt ggf. einmalig herunter
    httpd, port = srv.start_background(0)        # lokaler Relay auf freiem Port
    tun = Tunnel(exe, port); public = tun.start()# oeffentliche https-Adresse
    ...                                           # spaeter: tun.stop()
"""

import os
import re
import ssl
import sys
import time
import threading
import subprocess
import urllib.request

import dubforge_core as pc

# Offizielle Direktdownloads der jeweils neuesten Version.
_CF_BASE = ("https://github.com/cloudflare/cloudflared/releases/latest/"
            "download/")
_URL_RE = re.compile(rb"https://[-a-z0-9]+\.trycloudflare\.com")

# Kein schwarzes Konsolenfenster beim Start unter Windows.
_NO_WINDOW = 0
if sys.platform.startswith("win"):
    _NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class TunnelError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# cloudflared besorgen
# --------------------------------------------------------------------------
def _asset_name():
    """Passenden Release-Dateinamen fuer dieses System raten."""
    sixtyfour = sys.maxsize > 2 ** 32
    if sys.platform.startswith("win"):
        return "cloudflared-windows-amd64.exe" if sixtyfour \
            else "cloudflared-windows-386.exe"
    # macOS/Linux werden als Archiv ausgeliefert -> dort lieber eine vom
    # Nutzer installierte Binaerdatei verwenden (siehe ensure_cloudflared).
    return None


def ensure_cloudflared(progress=None):
    """Pfad zu cloudflared; laedt es unter Windows einmalig nach tools/.

    progress(pct) wird waehrend des Downloads mit 0..100 aufgerufen.
    """
    found = pc.find_tool("cloudflared")
    if found:
        return found

    asset = _asset_name()
    if asset is None:
        raise TunnelError(
            "cloudflared wurde nicht gefunden. Bitte einmalig installieren "
            "(z. B. per Paketmanager) und DubStage neu starten.\n"
            "cloudflared not found. Please install it once and restart.")

    os.makedirs(pc.TOOLS_DIR, exist_ok=True)
    dest = os.path.join(pc.TOOLS_DIR, "cloudflared.exe")
    tmp = dest + ".part"
    url = _CF_BASE + asset
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "DubStage"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            total = int(resp.headers.get("Content-Length", "0") or "0")
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        try:
                            progress(int(done * 100 / total))
                        except Exception:
                            pass
        os.replace(tmp, dest)
    except Exception as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise TunnelError("cloudflared konnte nicht geladen werden: %s" % exc)
    return dest


# --------------------------------------------------------------------------
# Quick Tunnel
# --------------------------------------------------------------------------
class Tunnel(object):
    """Ein Cloudflare Quick Tunnel vor http://localhost:<port>."""

    def __init__(self, exe, port):
        self.exe = exe
        self.port = int(port)
        self.proc = None
        self.url = None
        self._lines = []

    def start(self, timeout=45):
        """Startet cloudflared und wartet, bis die oeffentliche URL erscheint.
        Gibt die URL (str) zurueck oder wirft TunnelError."""
        args = [self.exe, "tunnel", "--no-autoupdate",
                "--url", "http://localhost:%d" % self.port]
        self.proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW)

        found = {"url": None}
        done = threading.Event()

        def reader():
            try:
                for raw in iter(self.proc.stdout.readline, b""):
                    self._lines.append(raw)
                    if len(self._lines) > 200:
                        self._lines = self._lines[-200:]
                    if found["url"] is None:
                        m = _URL_RE.search(raw)
                        if m:
                            found["url"] = m.group(0).decode("ascii")
                            done.set()
            except Exception:
                pass
            finally:
                done.set()

        threading.Thread(target=reader, daemon=True).start()
        done.wait(timeout)

        if found["url"] is None:
            tail = b"".join(self._lines[-8:]).decode("utf-8", "replace")
            self.stop()
            raise TunnelError(
                "Der Tunnel kam nicht zustande (Zeitueberschreitung).\n"
                "The tunnel did not come up in time.\n\n" + tail.strip())
        self.url = found["url"]
        return self.url

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        except Exception:
            pass
