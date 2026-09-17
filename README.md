# Rambler Agent

An agentic assistant for planning countryside walks (rambles, in British English)
in the UK, typically as day trips from London by public transport.

**Status: planning.** No code yet — architecture and feature plans are drafted
and under review. This README describes the intended system.

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
- a weather summary for the day;
- GPX files ready to import into the OS Maps app for on-the-day navigation.

The design goal is **grounding**: every factual claim traces to a data source,
which is what distinguishes this from asking a general chatbot with web search.

## Tech stack

| Layer | Choice |
|---|---|
| Language / tooling | Python ≥3.12, uv, ruff, pytest, pydantic |
| Agent harness | [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) (subagents + skills) on LangGraph |
| Models | Claude via API (Sonnet-class orchestrator, Haiku-class extraction) |
| Core data | SQLite + GPX file store; gpxpy / shapely / pyproj (EPSG:27700) |
| Route source | Saturday Walkers Club catalogue (ingested for personal use) |
| Transport | TransportAPI or Transitous (adapter), NaPTAN station data |
| Places | OpenStreetMap Overpass + FSA hygiene API (Google Places optional) |
| Weather | Open-Meteo |
| Serving | LangGraph Server with custom FastAPI routes (monolith, split-ready) |
| Front-end | deep-agents-ui (Next.js) fork + MapLibre GL JS with OS Maps API tiles |

## Planned repository structure

```
rambler-agent/
├── src/rambler/
│   ├── cli.py            # typer CLI (ingest, matrix, chat, evals)
│   ├── config.py         # settings (.env) + user profile (yaml)
│   ├── models.py         # shared pydantic models
│   ├── http.py           # cached, rate-limited HTTP client
│   ├── geo/              # GPX parsing, geometry, corridor, timing
│   ├── db/               # SQLite walk database
│   ├── sources/          # walk ingestion connectors (SWC first)
│   ├── connectors/       # transport, places, weather clients
│   ├── agent/            # deepagents assembly, tools, subagents, skills/
│   ├── artifacts/        # trip-pack rendering
│   └── server/           # FastAPI custom routes for LangGraph server
├── frontend/             # deep-agents-ui fork (added in Phase 4)
├── tests/                # unit tests + fixtures (offline)
├── evals/                # agent scenario evals
├── data/                 # git-ignored: walks.db, GPX cache, trip packs
├── planning/             # git-ignored: internal research & plans
├── user_profile.example.yaml
├── .env.example
└── langgraph.json
```

## Environment (planned)

```
uv sync
copy .env.example .env               # add ANTHROPIC_API_KEY etc.
copy user_profile.example.yaml user_profile.yaml   # personalise
uv run pytest
uv run rambler ingest swc            # build the local walk database
uv run langgraph dev                 # agent server (UI: see frontend/)
```

Secrets, the personal profile, and all ingested data stay local and
git-ignored.

## Deployment

Local-first by design (personal tool). The server/front-end boundary is kept
clean so a small cloud deployment remains straightforward later.

## Credits & data etiquette

Walk content is ingested for personal, non-commercial use from the excellent
[Saturday Walkers Club](https://www.walkingclub.org.uk/) — if you use their
walks, donate. Map data © OpenStreetMap contributors; base maps © Ordnance
Survey; rail data via National Rail / DfT open data; hygiene ratings from the
Food Standards Agency; weather by Open-Meteo. Ingested third-party content is
never committed to this repository.
