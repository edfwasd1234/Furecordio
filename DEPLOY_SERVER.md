# Deploying the DubStage relay server (Render)

**One click:**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/edfwasd1234/Furecordio)

This opens Render, reads `render.yaml` from the repo, and sets the whole thing
up for you — no local steps. When it finishes you get a
`https://…onrender.com` URL; paste it into DubStage → **Play online →
Server**. (You'll need a free Render account; the button asks you to sign in.)

The rest of this file explains what it does and the manual path.

---

The relay server (`dubstage_server.py`) is what lets people **host and join
online rooms over the internet**. It holds the rooms in memory and stores the
uploaded pack + take files on disk while a session is running. That means it
has to be a **normal always-on web service**, not a serverless function.

## Why Render, not Vercel

Vercel runs *serverless functions*: each request spins up a short-lived,
stateless handler with no persistent process, no shared memory between
requests, no writable local disk, and a hard time limit. Our server keeps rooms
in memory and writes uploaded files to disk for the length of a session — none
of that survives on Vercel without rewriting it around an external database and
object store. **Render** runs the actual `python dubstage_server.py` process,
keeps it alive, gives it a disk and an HTTPS URL, and sets `$PORT` (which the
server already reads). So Render is the right home; the included `render.yaml`
is written for it.

## One-time deploy

1. **Put this code in a GitHub repo of your own.** The server only needs
   `dubstage_server.py`, `render.yaml`, and `requirements.txt`, but pushing the
   whole folder is fine.

2. Go to **https://render.com**, sign in with GitHub, and click
   **New +  ->  Blueprint**.

3. Pick your repo. Render reads `render.yaml` and proposes a free web service
   called **dubstage-relay**. Click **Apply / Create**.

4. Wait ~1 minute for the first deploy. Render gives you a URL like
   **`https://dubstage-relay.onrender.com`**. Open it in a browser — you should
   see `{"ok": true, "service": "dubstage", ...}`.

That URL is your server address.

## Using it from DubStage

In DubStage: **Online  ->** put your Render URL (e.g.
`https://dubstage-relay.onrender.com`) in the **Server** field, then Host or
Join as normal. The address is remembered.

## Good to know

- **Free tier sleeps.** After ~15 minutes idle, Render's free service spins
  down; the next request wakes it, which takes a few seconds. The first
  "Connecting…" of a session may pause briefly — that's the wake-up, not a
  failure. Upgrading to a paid instance removes the sleep.
- **Rooms are temporary.** Room state and uploaded files live only while the
  service runs; a restart (including waking from sleep after a long idle)
  clears old rooms. That's fine for a dubbing session — finish and assemble in
  one sitting.
- **Player cap.** `DUBSTAGE_MAX_PLAYERS` is set to 10 in `render.yaml`. Change
  it there (or in the Render dashboard under Environment) and redeploy.
- **Manual (non-Blueprint) alternative:** New +  ->  Web Service  ->  your repo,
  Runtime **Python 3**, Build `pip install -r requirements.txt`, Start
  `python dubstage_server.py`, Health check path `/health`.
