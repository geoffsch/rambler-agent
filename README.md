# Rambler Agent

An agentic assistant for planning countryside walks (rambles, in British English)
in the UK, typically as day trips from London by public transport.

**Status: scaffolding.** Project skeleton, configuration and the shared HTTP
layer exist; no walk data, connectors or agent yet. This README describes the
intended system.

## What it does (planned)

Given a request like *"find us a walk under 8 miles with a good pub, within
75 minutes of home, somewhere we haven't been"*, the agent produces a **trip
pack**:

- a real, published walk (grounded in a local database of verified routes with
  GPX geometry — distances computed, never guessed), including documented
  shorter variations and escape-route options;
- concrete rail plans out and back, scored against the household's home
  stations and preferred lines;
- a pub or cafe lunch stop verified to be *on the route* (corridor-checked
  against the GPX track), plus kid-friendly diversions (playgrounds, farm
  shops, ice cream) and terrain notes (stile counts);
- a weather-informed suitability note (recent rainfall as a mud proxy, and
  exposure) used to rank *which* walk — not day-of forecasts;
- GPX files ready to import into the OS Maps app for on-the-day navigation.

The design goal is **grounding**: every factual claim traces to a data source,
which is what distinguishes this from asking a general chatbot with web search.

Out of scope by design: live train running and day-of weather. A phone does
those better once the route is loaded.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python ≥3.12 (uv will fetch one).

```
uv sync
cp .env.example .env                                # optional for now; keys arrive per phase
cp user_profile.example.yaml user_profile.yaml      # personalise (git-ignored)
uv run pytest
uv run ruff check .
uv run rambler --help
uv run rambler profile show
```

Set `RAMBLER_CONTACT` in `.env` to your e-mail before running anything that
fetches from third-party sites: it goes into the HTTP `User-Agent` so site
operators can reach you.

## Tech stack

| Layer | Choice |
|---|---|
| Language / tooling | Python ≥3.12, uv, ruff, pytest, pydantic |
| Agent harness | [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) (subagents + skills) on LangGraph |
| Models | Claude via API (Sonnet-class orchestrator, Haiku-class extraction) |
| Core data | SQLite + GPX file store; gpxpy / shapely / pyproj (EPSG:27700) |
| Route source | Saturday Walkers Club catalogue (ingested for personal use) |
| Transport | TransportAPI free tier (adapter; Transitous as a candidate), NaPTAN station data |
| Places | OpenStreetMap Overpass + FSA hygiene API |
| Weather | Open-Meteo |
| Serving | LangGraph Server with custom FastAPI routes (monolith, split-ready) |
| Front-end | deep-agents-ui (Next.js) fork + MapLibre GL JS with OS Maps API tiles |

Dependency policy: libraries still on 0.x are pinned to their current minor
(the minor bump is what breaks 0.x APIs). `uv.lock` is committed; upgrades are
manual and tested.

## Repository structure

```
rambler-agent/
├── src/rambler/
│   ├── cli.py            # typer CLI
│   ├── config.py         # Settings (.env) + UserProfile (yaml)
│   ├── models.py         # shared pydantic models
│   ├── http.py           # cached, rate-limited HTTP client (the only HTTP path)
│   ├── geo/              # GPX parsing, geometry, corridor, timing
│   ├── db/               # SQLite walk database
│   ├── sources/          # walk ingestion connectors (SWC first)
│   ├── connectors/       # transport, places, weather clients
│   ├── agent/            # deepagents assembly, tools, subagents, skills/
│   ├── artifacts/        # trip-pack rendering
│   └── server/           # FastAPI custom routes for LangGraph server
├── tests/                # unit tests + fixtures (offline)
├── scripts/              # one-off probes and utilities
├── data/                 # git-ignored: walks.db, GPX cache, HTTP cache, trip packs
├── user_profile.example.yaml
└── .env.example
```

Tests are offline. Tiers: `unit` (default, runs in CI), `golden` (needs the
local walk database), `evals` (spends model tokens; manual).

## Deployment

Local-first by design (personal tool). The server binds to localhost only; no
authentication or multi-tenancy. The server/front-end boundary is kept clean so
a small cloud deployment remains straightforward later.

## Licence, data and etiquette

- **The MIT licence covers the code only.** See [LICENSE](LICENSE).
- **Saturday Walkers Club content is personal-use, non-commercial.** Walks are
  ingested from the excellent [Saturday Walkers Club](https://www.walkingclub.org.uk/)
  for personal use only. Their content is never redistributed and never
  committed: `data/` is git-ignored by design, and test fixtures are minimal
  or synthetic. If you use their walks, please
  [donate](https://www.walkingclub.org.uk/donate/).
- **Polite by default.** All HTTP goes through one client with an on-disk
  cache, per-host minimum intervals (1 s for SWC, 2 s for Overpass) and a
  contactable `User-Agent`. Ingestion is cache-first: a second run makes no
  requests.
- **Attribution.** Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors, ODbL. Base maps contain OS data © Crown copyright and database
  rights, Ordnance Survey. Rail data via National Rail / DfT open data. Hygiene
  ratings from the Food Standards Agency. Weather by
  [Open-Meteo](https://open-meteo.com/) (CC BY 4.0).
