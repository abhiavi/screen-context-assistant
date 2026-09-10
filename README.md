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

**Maintenance jobs run via cron on aws-01** (`crontab -l` to inspect; both
need `PYTHONPATH=/home/ubuntu/screen-context-assistant` and to run from the
repo root — neither script adds the repo root to `sys.path` itself):
- Retention, daily at 03:00: `scripts/retention_rollup.py` — purges
  frame-level Qdrant vectors older than `FRAME_VECTOR_RETENTION_DAYS`. This
  was documented as "run daily via cron" since the v3 build but had no
  actual crontab entry until 2026-09-10 — was silently never running.
- Session segmentation, every 15min: `scripts/segment_sessions.py`
  (upgrade-roadmap "Now" item 5, 2026-09-10) — gap-clusters the trailing
  24h of Qdrant frames per track (same `SESSION_GAP_SECONDS` = 5min as
  `/recall`'s on-the-fly clustering) and upserts into the Postgres
  `sessions` table via `postgres_store.upsert_session` (idempotent — a
  unique `(track_id, started_at)` index lets re-clustering an overlapping
  window extend an existing row instead of duplicating it). That table had
  existed in `schema.sql` since the v3 build with nothing ever writing to
  it. `/recall`'s on-the-fly Qdrant clustering (`qdrant_store.recent_points`)
  is kept as-is and still runs on every request — deliberately not made to
  depend on this cron job, so recall never has a multi-minute blind spot for
  whatever's happened since the last run. Logs: `data/segment_sessions.log`.

**RAG evaluation harness** (upgrade-roadmap "Now" item 6, 2026-09-10):
`scripts/run_ragas_eval.py` runs the gold-set questions in
`app/eval/gold_set.json` through the live `/query` endpoint and scores each
with `app/eval/metrics.py` — faithfulness, answer relevancy, context
precision (all reference-free) plus context recall for the gold-set entries
that carry a hand-written `reference`. Manual/regression tool, not
scheduled — run it after touching retrieval, the prompt template, or the
embedding/synthesis models:
```bash
PYTHONPATH=/home/ubuntu/screen-context-assistant .venv/bin/python scripts/run_ragas_eval.py
```
Writes a full per-question JSON report to `data/ragas_eval_<timestamp>.json`
(each judged statement kept, not just the aggregate score) and prints a
summary. **Reimplemented natively rather than using the `ragas` package** —
`ragas==0.4.3`'s own dependencies are mutually incompatible on PyPI right
now (an unconditional dead-code import of `ChatVertexAI` drags in an old
`langchain-community` that conflicts with the `langchain-openai` version
ragas itself also requires; not resolvable by version-pinning within
reasonable effort). The four metrics are well-documented algorithms
(atomic-statement extraction + LLM-judge verdicts for faithfulness/context
recall, generated-question embedding similarity for answer relevancy,
ranked relevance judgments for context precision) and reimplementing them
directly against `app/ingest/gateway.py` also keeps every eval LLM call
inside the same `assert_clean()` redaction gate production calls use, which
a generic library wouldn't know to do. First real run against live data
(6 gold questions): faithfulness 0.84, answer_relevancy 0.57,
context_precision 0.90, context_recall 0.75 (n=2, only entries with a
reference) — plausible and discriminating (verified by hand: it correctly
flagged speculative/advisory statements as unsupported while confirming
concrete factual ones, not just returning uniform scores).

**Thematic project clustering** (upgrade-roadmap "Next" phase, 2026-09-10):
`scripts/cluster_sessions.py`, cron daily at 03:20 on aws-01, groups
`sessions` rows into `activity_clusters` by embedding similarity per track
(`sklearn.cluster.HDBSCAN` over mean-pooled per-session vectors) rather than
time adjacency — so a project worked on in two separate sittings, with
unrelated work or a multi-day gap in between, gets recognized as the same
thing instead of reading as disconnected sessions. Idempotent across reruns
via session-membership matching (HDBSCAN's own cluster numbering isn't
stable between fits). `/recall` surfaces the most recent multi-session
cluster for a track in both its synthesized text and structured
`project_label`/`project_summary` response fields. Needs `numpy` +
`scikit-learn` (aws-01 only — mini's avatar/capture venv doesn't run this).

**LiteLLM key is scoped** (`LITELLM_API_KEY` in `.env`) — `$20/30d` budget,
60 rpm / 100k tpm, restricted to exactly the three models this app uses
(`text-embedding-004`, `qwen-vl-ocr`, `glm-4.7`). This was flagged as a
deviation early on (the gateway was believed not to support scoped keys) —
turned out `POST /key/generate` on the gateway works fine and is
Postgres-backed, so this is resolved, not an accepted exception anymore. A
copy of the key also lives at `~/.screen-context-litellm-scoped.key` on
aws-01. Regenerate via the same `/key/generate` call (needs the gateway's
master key) if it's ever lost or needs rotating.

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

**Per-app exclusion**: `excluded_apps` in `capture.json` — a match against
`kdotool getwindowclassname` skips the screenshot entirely for that app
(never taken, not just never sent). Defaults to KeePassXC/Bitwarden/
1Password/KWallet as illustrative examples; add your own (banking, private
chat apps, etc.) freely.

**Pause/incognito**: `bash scripts/toggle_capture_pause.sh` flips a flag
file the capture agent checks before every poll. Bind it to a KDE global
shortcut: *System Settings → Shortcuts → Custom Shortcuts → new → Global
Shortcut → Command/URL*, point it at the script's full path. The avatar
shows a small red pause badge next to the character whenever paused (both
collapsed and expanded) — pausing is never silent.

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

Once expanded: **left-click** triggers an on-demand recall. **Ctrl+click**
cycles it to the next screen corner (not right-click — the embedded browser
view intercepts right-click for its own native context menu before it ever
reaches our handler, confirmed live: right-click silently did nothing;
that menu is now suppressed outright via `onContextMenuRequested`, and
corner-cycling moved to Ctrl+click, which reuses the already-working
left-click path). **Middle-click** cycles to the next available Live2D
character. There is no free-drag: Wayland gives
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

**Which monitor it appears on**: `follow_active_screen` in `avatar.json`
defaults to **off** — final call (2026-09-10) after trying both a
continuous 4s poll and an idle-return-only version; the Operator doesn't
want the avatar relocating to another monitor at all. It now just stays
wherever the compositor places it at launch. Ctrl+click still cycles it
between corners on its current monitor. The idle-return-triggered follow
code (`_check_active_screen`, called from `_check_idle_return`) still
exists and is harmless to re-enable via `"follow_active_screen": true` if
ever wanted again, and `preferred_screen_index` remains available for a
one-time pin to a specific monitor — but don't flip either on without being
asked; this has gone back and forth twice already.

**Scroll wheel** over the character browses conversation history (past
recalls/answers, newest first, lazily fetched from `GET /history` on the
RAG service on first scroll) — useful since you may have different
discussions with it across different tracks/work. Any new recall or typed
answer exits history-browsing mode and returns to showing the live result.

**Answers are markdown-rendered** (`Text.MarkdownText`) and the panel
scrolls internally (capped at 420px tall) instead of clipping long expert
answers — this was broken until 2026-09-10 (see Known limitations below on
why the panel had to be redesigned).

**Real compositor blur-behind** (2026-09-10, upgrade-roadmap "Now" item 4):
the panel is genuinely blurred by KWin, not just tinted. `app/avatar/wayland_blur/`
is a small hand-built Qt6 QML plugin (`BackgroundBlur`, C++, CMake) binding
the `ext_background_effect_v1` Wayland protocol — the newer replacement for
`org_kde_kwin_blur_manager`, which Plasma 6.7 removed outright (confirmed via
`wayland-info`: the old global is absent, the new one is present). Qt's own
`QWaylandClientExtensionTemplate` handles the registry bind; the wl_surface
comes from `QPlatformNativeInterface::nativeResourceForWindow("surface", ...)`
and the wl_compositor from the public `QNativeInterface::QWaylandApplication`
— both public/stable Qt API, no private headers for those two; `Qt6::GuiPrivate`
is linked only for the `qpa/qplatformnativeinterface.h` header location itself.
Built against the system Qt6 (not a bundled wheel copy) because `pyside6` here
is the Arch package linked against system Qt — confirmed matching versions
(6.11.2) before relying on that. Wired into `avatar.qml` via `RealBlur.qml`
loaded through a `Loader` (gated on a `hasWaylandBlur` context property set
by `main.py` if `wayland_blur/build/WaylandBlur/qmldir` exists) so a
missing/unbuilt plugin only fails that Loader, never the whole engine — falls
back cleanly to the old near-opaque simulated-glass tint. The panel's own
alpha is bound to `BackgroundBlur.supported` (`glass.blurActive` in
avatar.qml): ~0.6 (glassy) when real blur actually attached at runtime, ~0.92
(the old safe tint) if it didn't. **Requires KWin's Blur effect plugin to be
enabled** (`kwriteconfig6 --file kwinrc --group Plugins --key blurEnabled
true && qdbus6 org.kde.KWin /KWin reconfigure`) — the protocol can be bound
and `set_blur_region` calls succeed with zero errors even when the effect is
off, they just silently no-op; this cost real debugging time (found via
`qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.isEffectLoaded blur` →
`false`) and is exactly the kind of failure the `supported`-gated alpha
fallback above is protecting against. Build: `cd
app/avatar/wayland_blur && cmake -S . -B build -DCMAKE_PREFIX_PATH=/usr/lib/qt6
&& cmake --build build`; rebuild after any Qt6 point-release bump (it links
`Qt6::GuiPrivate`, so it's tied to that exact Qt build per Qt's own CMake
warning).

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

## Auditing outbound calls

`data/audit_log.jsonl` on aws-01 (gitignored, append-only) records every
single call `app/ingest/gateway.py` makes off-fleet: model, token usage,
success/failure, and a SHA-256 of the exact text sent — never the text
itself. `tail -f data/audit_log.jsonl | jq` to watch it live, or `jq -s`
to aggregate spend/volume by model. This exists so the transmission
boundary is auditable after the fact, not just trusted at call time.

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

- **A real credential leaked through redaction on 2026-09-10** — `sshpass
  -p 'PASSWORD' ssh root@...` (space-separated CLI flag, not `key=value`)
  wasn't covered by any existing pattern and reached a hosted LLM call
  before being caught by manual testing. Fixed (`sshpass_flag`,
  `long_password_flag`, `basic_auth_url` patterns added to
  `app/ingest/redact.py`; the 37 affected Qdrant points and 1 Postgres row
  were scrubbed), but recorded here as a reminder that the pattern set is
  necessarily incomplete — it's been extended twice now (this, plus the
  original set) by discovering gaps after the fact, not by exhaustive
  design. Treat the per-Activity `sensitive` flag as the real backstop for
  high-risk tracks, not redaction alone — see the next bullet.
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
