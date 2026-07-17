# Franchise AI research loop — Planner + Searcher MVP

This is a standalone, local worker for auditable franchise research. It is kept
outside Django intentionally: long-running and paid agent work must not execute
inside a web request. No result is written to the production `Franchise` model.

The planned loop is:

```text
Planner → Searcher → Extractor → Checker ↔ Resolver
        → Normalizer → human review → Importer
```

Planner and the first Searcher milestone are implemented. Planner combines:

- a deterministic, versioned question catalog covering all 23 FTC FDD Items;
- additional commercial, risk, state-law and unit-level due-diligence questions;
- optional OpenAI planning guidance through the Responses API and Structured Outputs;
- a JSON artifact that later agents can consume.

The LLM cannot remove canonical coverage. It may only improve priorities,
queries, assumptions and warnings. Planner does not search or assert facts.
Searcher consumes one explicit plan and discovers source candidates; it does not
extract or normalize facts.

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

Each Planner run writes one plan artifact under:

```text
datacollector/data/runs/<brand>/<timestamp>_<run-id>/plan.json
```

Free Planner runs use `plan-free.json`; paid Planner runs use `plan.json`.

Generated runs and `.env` are ignored by git. The plan artifact includes schema,
catalog and prompt versions, model, scope, tasks, evidence criteria, stopping
conditions, compliance rules and per-agent API usage. It contains no researched
facts yet.

## Run Searcher: free baseline, then paid comparison

Always point both Searcher variants at the exact same plan. Searcher records the
plan's SHA-256, so a comparison cannot silently mix different inputs.

The free variant makes no network or OpenAI call. It writes the selected query
workload and only brand-specific seed URLs supplied as a known official website
or direct URL source hint, clearly marked as unverified. General FTC/SBA framework
references are not counted as discovered brand sources:

```bash
.venv/bin/python -m datacollector search \
  --plan datacollector/data/runs/zabka/<run>/plan.json \
  --free \
  --limit-tasks 5 \
  --max-search-calls 10
```

It creates `sources-free.json` beside the plan. This is a deterministic baseline,
not a claim that the web was searched.

Then run the paid variant against that same path:

```bash
.venv/bin/python -m datacollector search \
  --plan datacollector/data/runs/zabka/<run>/plan.json \
  --limit-tasks 5 \
  --max-search-calls 10
```

It creates `sources.json`. The safe default selects the first five tasks. Use
repeatable `--task <task-id-or-catalog-question-id>` for an exact subset, or
increase `--limit-tasks` deliberately after comparing quality and cost.

Paid Searcher uses the Responses API hosted `web_search` tool with live external
access, required search, provider source inclusion, and a hard tool-call cap.
Every stored paid URL must also occur in provider-returned search sources or URL
citations; model-only URLs are discarded. See the official
[web search guide](https://developers.openai.com/api/docs/guides/tools-web-search).

Searcher output contains source candidates, query provenance, task mappings,
warnings and cost metadata. It deliberately contains no extracted franchise
facts; that will be the responsibility of the next Extractor agent.

Artifacts are immutable. Re-running the same mode and iteration refuses to
overwrite the prior result. Later iterations use names such as
`sources-r002-free.json` and `sources-r002.json`.

The CLI reserves the target filename before a paid call so two cooperating
processes cannot accidentally buy the same iteration concurrently. If OpenAI
returns token usage but the response is incomplete or unusable, that charged
attempt is saved under the run's `attempts/` directory instead of disappearing
from the cost ledger.

## Token and cost accounting

Every successful OpenAI call records one `agent_usage` entry in the artifact
created by that agent:

- agent name and logical iteration;
- requested/resolved model, response ID and request ID when available;
- actual input, cached-input, cache-write, output, reasoning and total tokens from
  `response.usage`;
- observed separately billed tool calls;
- a USD estimate calculated from a dated, versioned standard OpenAI rate card,
  including supported tool-call prices.

The CLI prints the same token totals and estimated cost after a paid run. The
token counts are provider-reported facts. The USD value is explicitly an
estimate: the OpenAI billing dashboard remains authoritative, and regional
processing, non-standard service tiers or separately billed tools may change
billed cost. Unknown models still record tokens but return a null cost.

For Searcher, the estimate adds observed `web_search_call` items at the current
published rate of $10 per 1,000 calls; search-content tokens are already charged
at the selected model's token rates. See official
[API pricing](https://developers.openai.com/api/docs/pricing#built-in-tools).

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
