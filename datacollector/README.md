# Franchise AI research loop — Planner MVP

This is a standalone, local worker for auditable franchise research. It is kept
outside Django intentionally: long-running and paid agent work must not execute
inside a web request. No result is written to the production `Franchise` model.

The planned loop is:

```text
Planner → Searcher → Extractor → Checker ↔ Resolver
        → Normalizer → human review → Importer
```

Only Planner is implemented in this first milestone. It combines:

- a deterministic, versioned question catalog covering all 23 FTC FDD Items;
- additional commercial, risk, state-law and unit-level due-diligence questions;
- optional OpenAI planning guidance through the Responses API and Structured Outputs;
- a JSON artifact that later agents can consume.

The LLM cannot remove canonical coverage. It may only improve priorities,
queries, assumptions and warnings. Planner does not search or assert facts.

## Setup

From the repository root:

```bash
python -m venv .venv
.venv/bin/pip install -r datacollector/requirements.txt
cp -n datacollector/.env.example datacollector/.env
```

The `-n` flag preserves an existing `.env`. Put the API key in
`datacollector/.env` as `OPENAI_API_KEY`. The existing local
legacy name `openai_apikey` is accepted temporarily and is never printed.

The default model is `gpt-5.6-terra`, chosen as the balance between intelligence
and cost in the OpenAI model guidance checked on 2026-07-17. Override it through
`OPENAI_MODEL` without changing source code.

The adapter follows the official [Responses API text-generation guide](https://developers.openai.com/api/docs/guides/text)
and parses a Pydantic model with [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Responses are requested with `store=False` and an 8,000-token output cap by default.
Only the question fields needed for planning are sent to the model; deterministic
evidence criteria remain local, reducing the serialized canonical-question
payload by about 31% for the current Polish due-diligence catalog.

## Inspect the question bank (free, offline)

```bash
.venv/bin/python -m datacollector questions --country PL --depth due_diligence
```

Available depths are cumulative:

- `catalog`: identity, basic offer, evidence and stop rules;
- `due_diligence`: catalog plus the full FDD-inspired baseline;
- `risk`: adds unit economics, interviews, controversies, tax and resilience;
- `unit`: adds location-level inventory and reputation signals.

## Create a deterministic plan (free, offline)

```bash
.venv/bin/python -m datacollector plan \
  --brand "Żabka" \
  --country PL \
  --depth due_diligence \
  --offline
```

## Create an OpenAI-tailored plan

```bash
.venv/bin/python -m datacollector plan \
  --brand "Żabka" \
  --country PL \
  --region mazowieckie \
  --depth due_diligence
```

Each run writes only `plan.json` under:

```text
datacollector/data/runs/<brand>/<timestamp>_<run-id>/plan.json
```

Generated runs and `.env` are ignored by git. `plan.json` includes schema,
catalog and prompt versions, model, scope, tasks, evidence criteria, stopping
conditions, compliance rules and per-agent API usage. It contains no researched
facts yet.

## Token and cost accounting

Every successful OpenAI call records one `agent_usage` entry in `plan.json`:

- agent name and logical iteration;
- requested/resolved model, response ID and request ID when available;
- actual input, cached-input, cache-write, output, reasoning and total tokens from
  `response.usage`;
- a USD estimate calculated from a dated, versioned standard OpenAI rate card.

The CLI prints the same token totals and estimated cost after a paid run. The
token counts are provider-reported facts. The USD value is explicitly an
estimate: the OpenAI billing dashboard remains authoritative, and regional
processing, non-standard service tiers or separately billed tools may change
billed cost. Unknown models still record tokens but return a null cost.

For GPT-5.6, Planner disables the default implicit cache breakpoint. A one-off,
brand-specific planning payload would otherwise incur cache-write charges without
guaranteeing a later cache hit. Provider-reported cache-write tokens are still
recorded and priced if they occur.

For a later logical Planner pass, label the iteration:

```bash
.venv/bin/python -m datacollector plan \
  --brand "Żabka" \
  --country PL \
  --depth due_diligence \
  --iteration 2
```

Runs created before schema `1.1.0` do not contain provider usage because that
metadata was not retained and cannot be reconstructed exactly from `plan.json`.

## Tests

Tests never call OpenAI:

```bash
.venv/bin/python -m unittest discover -s datacollector/tests -p "test_*.py"
```

The existing Django suite is independent:

```bash
.venv/bin/python src/saashome/manage.py test
```

## Safety boundary

- FTC FDD requirements are a legal baseline for covered U.S. offers, not Polish law.
- Do not scrape private/login-protected pages or Google Reviews.
- Minimize personal data and keep opinions separate from verified facts.
- Every future fact must have retrievable evidence, dates and confidence metadata.
- No AI output may be published or imported without human review.

See [the U.S. standard](docs/us_franchise_research_standard.md) for the source
mapping and the exact information groups the Planner must cover.
