# CLAUDE.md — Rambler Agent project notes

Project-specific guidance for working in this repo. (User-level preferences live
in the global `~/CLAUDE.md`.)

## What this is
An agentic assistant for planning countryside walks as day trips from London by
public transport, for a family with young kids. See `README.md` for the feature
overview and `planning/problem_statement.md` for the full why.

## Planning documents (read before implementing anything)
- `planning/plan.md` — master plan: architecture, principles, phase gates,
  decision log. **Canonical record of project state; update it as work lands.**
- `planning/plans/NN-*.md` — feature plans, designed to be executed one at a
  time (including by subagents). Follow the working agreement in
  `planning/plan.md` §4: stay in scope, surface un-planned decisions instead of
  improvising, tick checkboxes and record actual outcomes in the plan files.
- `planning/research.md` — data source & tooling research with feasibility
  verdicts; check here before proposing a new API/library.
- The `planning/` directory is git-ignored (internal, unpolished).

## Tech stack (decided — see plan.md §5 decision log before changing)
- Python ≥3.12, uv, ruff, pytest, pydantic v2. `src/` layout, package `rambler`.
- Agent: **deepagents ≥0.6** (pinned) on LangGraph; Claude via API
  (Sonnet-class orchestrator, Haiku-class extraction subagents); Anthropic
  native web search for runtime colour.
- Data: SQLite (`data/walks.db`) + GPX/artifact files under `data/` (all
  git-ignored). Geometry: gpxpy + shapely + pyproj in **EPSG:27700**.
- Serving: LangGraph Server with custom FastAPI routes mounted — monolith now,
  structured so a future front-end/back-end split is straightforward.
  Front-end: deep-agents-ui fork + MapLibre GL JS (added in Phase 4).

## Engineering rules
1. **Deterministic core, agentic shell.** Real logic lives in the plain Python
   library (typed, unit-tested, framework-free); agent tools are thin wrappers
   in `src/rambler/agent/`. Nothing outside `agent/` may import langchain/
   deepagents.
2. **Grounding invariants** (the product's whole point — never regress):
   recommendations come from the walk DB; distances/times computed from GPX
   geometry, never quoted from model text; POIs pass the route-corridor check
   or are labelled detours; journeys come from the transport adapter;
   web-search findings are labelled unverified and cited.
3. **Network etiquette**: all HTTP through `src/rambler/http.py` (on-disk
   cache, per-host rate limits, contactable User-Agent). Ingestion is
   idempotent over cached raw responses. Be polite to walkingclub.org.uk and
   Overpass.
4. **Tests are offline**: recorded fixtures under `tests/fixtures/`; marker
   tiers `unit` (default) / `golden` (needs local DB) / `evals` (spends tokens,
   manual). Fixtures must not embed bulk third-party content (SWC licence is
   personal-use — keep fixtures minimal or synthetic).
5. **Personalisation is config** (`user_profile.yaml`, git-ignored; example
   committed). No hardcoded home stations or preferences.
6. **Never commit**: `data/`, `.env`, `user_profile.yaml`, `planning/`,
   `AUTHOR.local.md`.

## Project structure and working style
The `planning` subdirectory is git-ignored: internally-facing development
documents (research, plans), particularly transient or unpolished ones.

## Author-specific context
Author-specific context such as experience level and how much to explain
components of the tech stack lives in the following local, git-ignored file.
It may be absent on a fresh clone; ignore this in such cases.

<!-- Local, git-ignored author context. Absent on fresh clones (import is
     skipped silently); present on the author's machine. -->
@AUTHOR.local.md
