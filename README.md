# screen-context-assistant

A floating, always-on-top **avatar companion** on `adraca-mini`: watches the
active window (KDE Activity + screenshot + OCR), and proactively helps
Abhishek recall what he was doing when he's lost track of a context or
returns from being away. Backed by a capture→OCR→redact→embed→Qdrant
pipeline and the LiteLLM gateway running on `adraca-aws-01`.

> **v4 re-scope (2026-09-09)**: this was originally a passive activity-logger
> + CLI/RAG query tool, deployed to desktop+laptop+aws-01. The Operator
> re-scoped it to a floating avatar, **mini-only** — desktop/laptop capture
> agents are retired. See `docs/screen-context-assistant-plan.md`'s v4
> RE-SCOPE block for the full rationale; the v3 material below it is the
> still-valid backend/pipeline reference.

## Hard constraints (do not re-open — see plan §0 / v4 block)

1. AI gateway is `adraca-azure-01:4000` only. Never `adraca-pve:4000` (zombie
   LiteLLM instance). Fallback: `adraca-oracle-01:4000`.
2. Backend (Qdrant/Postgres/OCR/RAG) runs on `adraca-aws-01` — own Postgres +
   own Qdrant, **not** azure-01's shared Qdrant. Chosen over collapsing onto
   mini because mini's headroom is genuinely tight (7.2GB free of 29GB RAM,
   65% root disk) — checked, not assumed.
3. Capture agent + avatar UI run on `adraca-mini` **only** — this is an
   explicit, app-specific exception to mini's usual Iron Rule (no heavy
   always-on Docker/agentic work), approved by the Operator. Don't extend
   that exception to anything else, and don't redeploy capture to
   desktop/laptop (retired).
4. Privacy is a **transmission** boundary: local OCR + secret-redaction runs
   on mini before anything leaves the machine, then again before anything
   leaves the fleet to a hosted model. Raw frames are never persisted or
   uploaded. Enforced in code (`app/ingest/redact.py`, `app/ingest/gateway.py`).
5. Services bind to the Tailscale IP only, never `0.0.0.0`.

## Status

- **Backend (Phase 0-3, on aws-01): done.** Embeddings gate confirmed live
  against the gateway (`text-embedding-004`, 768-dim); vision alias
  (`qwen-vl-ocr`) and synthesis alias (`glm-4.7`) confirmed reachable. Local
  Qdrant + Postgres in Docker, bound to the Tailscale IP. OCR+redact+embed
  pipeline verified end-to-end (fake AWS key/email confirmed stripped before
  landing in Qdrant).
- **Capture agent: done, mini-only.** Deployed as a `systemd --user` service
  on `adraca-mini` (desktop/laptop deployments retired 2026-09-09 — see the
  v4 re-scope). See `deploy/` + `scripts/install_capture_agent.sh` and the
  KWin authorization note below.
- **Avatar UI (folds in old Phase 4 + 5): MVP done, deployed on mini.**
  PySide6 + QtQuick + `org.kde.layershell` floating overlay
  (`app/avatar/`), verified live via screenshot on mini's real KWin 6.7.4
  session: renders correctly, always-on-top, doesn't steal window focus.
  Click-to-recall and idle-return proactive recall both call a new
  `GET /recall` endpoint on the aws-01 RAG service, which reconstructs "the
  last session" on the fly from recent Qdrant frames (see Known limitations
  — Postgres session-tracking was never wired up) and synthesizes a short
  recap. Verified live: a real screen capture flowed through OCR → redact →
  Qdrant → recall → LLM synthesis → speech bubble, all with real data.
  Vault-write (`app/avatar/vault_writer.py`) runs on a timer, appending
  recall summaries to a single journal file — see the note below on why
  it's not scattered into the vault's real per-project folders yet.
- **Phase 6 (permanence/Ansible): not started.**

## Architecture

```
mini: capture agent            mini: avatar UI                 aws-01: ingest (8088) + RAG (8089)
  DBus Activities + KWin  -->  (systemd --user, separate)         OCR -> redact -> embed -> Qdrant/Postgres
  screenshot + phash dedup      |         ^                              ^
  systemd --user                | click / idle-return                   | GET /recall
                                 v         |                             |
                          POST /ingest/frame (over Tailscale)  <---------+
                                 |
                                 v
                          speech bubble (QML)  +  vault_writer.py -> ~/ObsidianVault (local FS, mini only)
```

## Running the backend (aws-01)

```bash
cp .env.example .env   # fill in LITELLM_API_KEY, POSTGRES_PASSWORD
bash scripts/init_storage.sh          # docker compose up postgres+qdrant, apply schema, ensure collection
source .venv/bin/activate
python -m uvicorn app.api.ingest_service:app --host $BIND_HOST --port 8088
python -m uvicorn app.api.rag_service:app --host $BIND_HOST --port 8089
```

Retention (cron daily): `python scripts/retention_rollup.py`

### Capture agent (mini only)

```bash
rsync -az --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
  --exclude='qdrant_storage' --exclude='pgdata' --exclude='.env' \
  ./ mini:~/screen-context-assistant/
ssh mini
cd ~/screen-context-assistant && bash scripts/install_capture_agent.sh
```

**KWin screenshot authorization**: `org.kde.KWin.ScreenShot2.CaptureActiveWindow`
refuses unauthorized callers (`Error.NoAuthorized`). Verified live against
KWin 6.7.4: the check matches the calling process's *resolved* executable
(`/proc/pid/exe`, i.e. `readlink -f .venv/bin/python3` → the real system
interpreter, not the venv symlink) against the `Exec=` of a `.desktop` file
under `~/.local/share/applications/` that declares
`X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`. The install
script generates this from `deploy/screen-context-capture.desktop.template`.
Note this is a different path than what the systemd unit's `ExecStart` uses
(the venv symlink, so Python's own venv detection via `pyvenv.cfg` still
works) — see the comment in `scripts/install_capture_agent.sh`.

**Window title / app name** come from `kdotool` (AUR), the KWin-Wayland
equivalent of `xdotool`.

**IMPORTANT — review before relying on this**: `scripts/generate_capture_config.py`
writes `~/.config/screen-context-assistant/capture.json` with every
discovered KDE Activity defaulted to *non-sensitive*. Nothing in this repo
knows which of your Activities is the bug-bounty/credentials one — **you
must edit `sensitive_tracks` in that file yourself** before trusting it with
high-risk tracks; plan §5's guarantee that sensitive tracks never leave the
machine only holds for tracks you've actually flagged.

### Avatar UI (mini only)

```bash
ssh mini
cd ~/screen-context-assistant && bash scripts/install_avatar.sh
```

`pyside6` and `layer-shell-qt` were already installed system-wide on mini
when this was built (2026-09-09) — the install script pulls them via pacman
for reproducibility on a fresh machine. The avatar reuses the capture
agent's `--system-site-packages` venv (no separate one).

**Starts small, collapsed** (72px, no ask bar) so it doesn't take up screen
space upfront — the **first click** on it just expands it to full size
(240px) and reveals the ask bar; a proactive idle-return recall or a
scroll-wheel history browse also auto-expands it. It quietly re-collapses
after ~45s of no engagement (no panel showing, ask bar not focused).

Once expanded: **left-click** triggers an on-demand recall. **Right-click**
cycles it to the next screen corner. **Middle-click** cycles to the next
available Live2D character. There is no free-drag: Wayland gives
layer-shell surfaces edge-relative `anchors`/`margins` positioning, not
arbitrary x/y, and `QMargins` isn't a QML-constructible value type — so
per the plan's own suggested fallback, this degrades to **edge-docked with
corner-cycling** rather than pixel-level dragging.

**Choosing an avatar**: three characters are bundled today — `haru`
(default), `mao`, and `natori` (the most dynamic: 8 action motions vs. the
others' 1-2), all Live2D's own official free sample models (see
`app/avatar/live2d_assets/NOTICE.md` for licensing). Middle-click cycles
live for the current session only; to change the *default* on startup, set
`"avatar_id"` in `~/.config/screen-context-assistant/avatar.json` to one of
the ids in `app/avatar/live2d_assets/avatars.json` and restart the service.
To add another character: vendor its Cubism 4 model files under
`live2d_assets/<id>/`, add an entry to `avatars.json` (model path + which
motion group/expression indices to use for the idle/attentive/speaking
states — inspect the model's own `.model3.json` for what it actually has,
they're not consistent between models), and it shows up in the middle-click
cycle automatically — no code changes needed.

**Scroll wheel** over the character browses conversation history (past
recalls/answers, newest first, lazily fetched from `GET /history` on the
RAG service on first scroll) — useful since you may have different
discussions with it across different tracks/work. Any new recall or typed
answer exits history-browsing mode and returns to showing the live result.

**Answers are markdown-rendered** (`Text.MarkdownText`) and the panel
scrolls internally (capped at 420px tall) instead of clipping long expert
answers — this was broken until 2026-09-10 (see Known limitations below on
why the panel had to be redesigned).

**Answers adopt an expert persona**: both `/recall` and `/query` prompts on
the RAG service ask the model to infer the relevant domain from the
captured context (software engineering, security research, business
strategy, etc.) and answer in that expert's voice, rather than generically.

**More idle liveliness**: the character plays a brief "flavor" motion from
its extra motion group every 12-27s while genuinely idle (not mid-recall),
in addition to the Live2D library's own built-in idle-motion autoplay —
addresses feedback that it read as too static. Models with no extra motion
group configured (check `avatars.json`) skip this; nothing to break.

**Idle-return detection** uses systemd-logind's `IdleHint` (DE-agnostic),
not KWin's own screensaver interface — `org.freedesktop.ScreenSaver.
GetSessionIdleTime` returned `NotSupported` on this KWin/platform when
tested live. Resolving "our own" session also needed care: a
`systemd --user` service runs in a different cgroup than the interactive
login session, so `GetSessionByPID` raises `NoSessionForPID` (this crashed
the first real deployment — an interactive SSH test had misleadingly
worked). `IdleWatcher` enumerates sessions instead and picks the actual
seat0 graphical one.

**Vault-write is NOT yet routed to the vault's real per-project folders**
(`01_Adraca_Enterprise`, `02_Lambda_Consulting`, etc.) — the KDE Activity
track_ids (`adraca`, `career`, `darkside`, `home-server`, `lambda`,
`personal`, `research`) don't map onto that taxonomy unambiguously, and
guessing risked scattering notes into the wrong place in an already-organized
vault. Everything currently lands in one journal file:
`~/ObsidianVault/10_Agent_Embassies/Screen_Context_Journal/<date>.md`.
Ask the Operator for the track→folder mapping before changing this.

**Recall reconstructs sessions from Qdrant, not Postgres**: nothing in the
ingest pipeline currently calls `postgres_store.start_session`/`end_session`
— only Qdrant gets written per frame. `GET /recall` on the RAG service
works around this by clustering recent Qdrant frames on the fly (a >5min
gap between consecutive frames = session boundary) rather than depending on
the (empty) `sessions` table. Fine for MVP; wiring real session tracking
into the ingest pipeline would make this more precise later.

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

`tests/test_redact.py` is the code-level evidence for the MVP privacy
acceptance criterion (plan §6): fake AWS keys, GitHub tokens, private key
blocks, emails, phone numbers, JWTs, and high-entropy bare tokens are all
stripped before `assert_clean()` (the hard gate in front of every gateway
call) would allow them through.

## Known limitations

- Redaction operates on OCR output, not the raw pixels — an OCR
  misread can occasionally fragment or garble a secret enough to dodge a
  regex (verified: crisp, normally-rendered screenshot text OCRs cleanly;
  a degenerate test with a tiny bitmap font did not). The entropy-based
  fallback catches most unstructured high-entropy tokens, but the
  per-Activity `sensitive` flag (fully local, no hosted call at all) is the
  real backstop for high-risk tracks (bug bounty / credentials work) — set
  it, don't rely on redaction alone for those.
- Vision-on-redacted-frame path (`gateway.describe_redacted_frame`) is wired
  but not used by the MVP pipeline; OCR text is enough for text-heavy
  screens, and doing image-level redaction (blackout boxes) before any
  vision call is unbuilt.
- See the Avatar UI section above for the drag/corner-cycling, idle-detection,
  vault-routing, and Postgres-vs-Qdrant-session caveats.
- The panel used to be positioned above the character and sized purely to
  its own content, with no scrolling — fine for short recalls, but longer
  markdown-formatted expert answers got silently clipped (Operator report:
  "response is not fully visible since scrolling is not enabled"). Fixed
  2026-09-10 by decoupling the panel from the character's position (now
  fixed to the window's top area) and adding a `Flickable` with a capped
  max height (420px) and a scroll indicator.
