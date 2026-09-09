# On-screen context assistant — MVP build plan (v3, infra-reviewed)

Reviewer note: this doc is written for the **Antigravity (AGY) session doing the build on adraca-aws-01**. It has been reviewed by the Claude CLI infra session; the resolutions of that review are folded in below (see §0 and §7). Build to §4/§5, and treat §0 + §7 as hard constraints, not suggestions.

## 0. Infra-review resolutions (READ FIRST — these are decided, do not re-open)

1. **AI gateway is `adraca-azure-01:4000`.** NOT adraca-pve. There is a *superseded/zombie* LiteLLM still running on `pve:4000` — **do not route any model calls there.** Fallback gateway is `adraca-oracle-01:4000`. All vision/embedding/RAG calls go to `http://100.125.158.117:4000/v1`.
2. **Build + run on adraca-aws-01** (idle: 32 vCPU, ~59 GiB free, 184 GB disk, already on the mesh). No fresh cloud VM. Do **not** put load on azure-01 (disk-tight) or mini (SOT + Iron Rule).
3. **Qdrant + Postgres for this project run locally on aws-01** — a *separate* Qdrant instance, NOT azure-01's shared Qdrant (which holds `india_open_data` and is on the disk-tight box). This isolation is mandatory.
4. **Privacy is a TRANSMISSION boundary, not just a retention one** (see §5 — this is the biggest change from earlier drafts).
5. **Phase-0 gate:** confirm the gateway actually exposes an **embeddings** model alias before building Phase 2 on it. If none, run a small local embedder on aws-01 (keeps text on-fleet too). Do not assume the embedding endpoint exists.
6. **Register this project** in the Master Project Registry (`~/adraca-registry/registry.py` on mini) at inception, and **create its private GitHub repo under `abhiavi` on day one** (no-unbacked-program rule — nothing lives only on aws-01's disk).

## 1. What this is

An always-on desktop assistant (KDE/Arch) that watches screen + window activity across Abhishek's work tracks (Adraca AI, PhD research, bug bounty/kernel security, article writing, contract work, home lab, job/opportunity hunting), keeps a structured record of what he's doing and when, syncs distilled summaries into his Obsidian vault, and answers questions like "what was I doing on X at 3pm yesterday" or "help me pick this back up" via RAG.

**Inference policy (revised):** hosted models via the LiteLLM gateway do the heavy synthesis (RAG answers, activity summaries). BUT a **local OCR/redaction pass runs on aws-01** as the first stage of ingest, so raw screen content is reduced to text and stripped of obvious secrets *before* anything is transmitted off-fleet. This is the one deliberate exception to "no self-hosted inference" — it exists because the alternative (shipping every raw screenshot of credentials/client data to Anthropic/Google) is unacceptable for a sovereign fleet, and because it also bounds cost on an always-on loop. No large local VLM to maintain — just OCR (paddleOCR/tesseract) + a redaction filter.

## 2. Relevant existing infra (do not duplicate or collide with these)

Fleet runs on Tailscale mesh only (no public exposure), operated solo by Abhishek. Key nodes:

- **adraca-aws-01** — **build + run target for this project.** 32 vCPU, ~59 GiB free RAM, 184 GB free disk, idle, on the mesh. Hosts this project's ingest/OCR/Postgres/Qdrant/RAG services.
- **adraca-azure-01** — AI Hub: **LiteLLM gateway (port 4000)** = the ONLY gateway to use; Qdrant (`india_open_data` — do not touch), Grafana/Prometheus, Gitea, n8n, Kuma, Portainer, Grist. Root disk tight (~82% used) + separate high-token Claude account — **add no storage load here.**
- **adraca-mini** — always-on home server, Master Project Registry SOT, media stack. Under the Iron Rule (no heavy always-on agentic/Docker work) — **not a deploy target for this project** (Phase 6 revised, see below).
- **adraca-pve** — cameras/media + PQC archive. Runs a **superseded/zombie LiteLLM on :4000 — ignore it.**
- **Master Project Registry** (`~/adraca-registry/registry.db` on mini) — link this project's KDE-Activity "tracks" to registry project ids via a small config map (define fresh; ids are stable).

## 3. Build sequencing

1. **Build + validate on adraca-aws-01** (already on the mesh — no VM to provision).
2. **Do NOT auto-migrate to mini.** aws-01 is the intended permanent home given its idle capacity and mini's Iron Rule. Only revisit a mini move if aws-01 is ever reclaimed (see §7).

## 3a. Development workflow

Antigravity (AGY) does the implementation on aws-01. **Claude CLI's role is a final audit pass** against §0, §4, §5, §7 once the build is complete — not incremental pairing. Before starting, AGY must run `bash ~/ObsidianVault/10_Agent_Embassies/handshake-check.sh` and add an aws-01 lease row to the swarm board.

## 4. Architecture (6 stages)

1. **Capture agent** (on each active desktop/laptop — deploy to both) — reads Plasma Activities via DBus (`org.kde.ActivityManager`) as the track signal (map each track to a KDE Activity — no separate classifier). Screenshots via `org.kde.KWin.ScreenShot2` (Wayland-native) on window-focus-change or timer, perceptual-hash diff to skip near-duplicates. Sends `{frame, window title, app name, activity id, timestamp}` to ingest over Tailscale.
2. **Ingest & processing service** (aws-01) — **(a) local OCR + redaction FIRST** (extract text on-node, strip secret-shaped strings); **(b)** then call the LiteLLM vision alias only if visual context beyond OCR is needed, on the **redacted** frame; **(c)** call the embedding endpoint on the resulting text. Raw frames are not transmitted un-redacted and not retained long-term (§5).
3. **Storage layer** (aws-01) — separate Qdrant `screen-context` collection (payload indexed on `track`, `app`, `timestamp`); own Postgres DB with session-level rows (track start/end, app-switch events) for cheap "what at 3pm" SQL. Retention: frame-level vectors ~30 days → daily per-track rollups.
4. **Obsidian sync writer** (alongside the capture agent, needs vault FS access) — polls the backend, asks LiteLLM to synthesize a short per-track summary, appends to the relevant note (e.g. `Adraca/2026-09-09.md`). Batch on track-switch/interval, not per-frame (the vault auto-syncs every 15 min; avoid commit spam). Distilled summaries only.
5. **RAG query API** (aws-01) — question → top-k from Qdrant + Postgres rows + vault search → strong LiteLLM alias for the answer.
6. **Client** — MVP: CLI or bare local webpage hitting the RAG API. Native KDE app (Kirigami/QML or Tauri) after MVP.

## 5. Privacy/security boundary (TRANSMISSION boundary — encode as config, enforce in code)

Screens show credentials, client data, private messages. **The boundary is what LEAVES the fleet, not just what is stored.** Enforce, in code (not just this doc):
- **Local OCR + secret-redaction on aws-01 runs before any off-fleet call.** Regex/entropy strip of tokens, keys, passwords, emails/phones where flagged.
- **Raw frames never leave aws-01** and are deleted after processing (config flag; default = do-not-upload-raw). Only redacted text/captions/embeddings persist and only redacted text is sent to hosted models.
- A per-Activity "sensitive" flag can force fully-local handling (no hosted call at all) for tracks like bug-bounty/credentials work.

## 6. Phased plan

**Phase 0 — Infra & guardrails (~½ day):** on aws-01 (already meshed) install Docker/Podman + Postgres + Qdrant + local OCR. Scoped LiteLLM key with its own rate-limit + spend budget + alert. **Verify an embeddings alias exists on the gateway** (gate). Pick vision + RAG aliases. Encode the §5 transmission/redaction boundary as config. Register the project + create the abhiavi repo.

**Phase 1 — Capture agent (~2–3 days):** DBus daemon (Plasma Activities + KWin focus), screenshot+phash dedup, POST to ingest. Deploy to desktop AND laptop.

**Phase 2 — Ingest & processing (~3–4 days):** OCR+redact → (optional) vision alias on redacted frame → embedding → Qdrant + Postgres.

**Phase 3 — Storage schema (parallel, ~1 day):** `screen-context` collection + Postgres session-log + retention/rollup from day one.

**Phase 4 — Obsidian sync writer (~2 days):** poll-and-summarize into the vault on track-switch/interval.

**Phase 5 — RAG API + thin client (~2–3 days):** retrieval + synthesis; CLI/webpage for validation.

**MVP done when:** "what was I doing on the CRS doc around 2pm yesterday" is answered correctly from real captured activity, and the vault shows accurate readable session notes without manual input — **and** a spot-check confirms no un-redacted secret was transmitted off-fleet.

**Phase 6 — permanence (revised):** aws-01 is the intended permanent home. Containerize + Ansible-ify following the `fleet-sync-context.sh` pattern for reproducibility/backup, but do NOT migrate to mini by default. Register final state; keep the repo synced.

## 7. Open questions — RESOLVED by infra review
- **Scoped LiteLLM key budget:** Yes — own rate-limit + spend cap + alert (an always-on vision loop must not starve dpp/rgm/upwork-notify on the shared gateway).
- **aws-01 reclaim risk:** none current (old nodejs-fuzzing/Buttercup use is retired). Add an aws-01 lease row on the board so it stays coordinated.
- **aws-01 Tailscale reachability:** already reachable from desktop/laptop (full mesh). Bind ingest to the Tailscale IP only, never 0.0.0.0.
- **mini day-one vs aws-01:** aws-01. Don't burden mini.
- **Registry track↔Activity mapping:** define a small config map (KDE Activity id → registry project id); ids are stable.
- **Qdrant on azure-01 risk:** avoided by running a *separate* Qdrant on aws-01 (do not use azure-01:6333).
- **Privacy enforced in code (was "not retained" — reframed):** the audit will verify the build **redacts + keeps raw frames on-fleet before any off-fleet transmission**, in code — not merely that raw frames aren't retained. Retention-only compliance is insufficient.
