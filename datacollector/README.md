# Franchise AI research loop — Planner + Searcher + Extractor

This is a standalone, local worker for auditable franchise research. It is kept
outside Django intentionally: long-running and paid agent work must not execute
inside a web request. No result is written to the production `Franchise` model.

The planned loop is:

```text
Planner → Searcher → Extractor → Checker ↔ Resolver
        → Normalizer → human review → Importer
```

Planner, Searcher and Extractor are implemented. Planner combines:

- a deterministic, versioned question catalog covering all 23 FTC FDD Items;
- additional commercial, risk, state-law and unit-level due-diligence questions;
- optional OpenAI planning guidance through the Responses API and Structured Outputs;
- a JSON artifact that later agents can consume.

The LLM cannot remove canonical coverage. It may only improve priorities,
queries, assumptions and warnings. Planner does not search or assert facts.
Searcher consumes one explicit plan and discovers source candidates; it does not
extract or normalize facts. Extractor safely retrieves selected public documents,
creates exact evidence passages and, in paid mode, maps them to raw claims. It
does not verify, reconcile or normalize those claims.

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
It defaults to `search_context_size=low` for URL discovery and blocks domains
that produced high-volume, low-value results in the benchmark
(`arxiv.org`, `quora.com`, `reddit.com`, and `wikipedia.org`). Override these
without changing code through `OPENAI_WEB_SEARCH_CONTEXT_SIZE` and the
comma-separated `OPENAI_WEB_SEARCH_BLOCKED_DOMAINS`; an empty domain value
disables the block list. `medium` remains available for tasks that need more
search-result context, but the setting does not guarantee an exact token or
source count.
Every stored paid URL must also occur in provider-returned search sources or URL
citations; model-only URLs are discarded. Schema `1.1.0` additionally requires a
stored URL to be mapped to at least one selected task and to the provider action
that observed it. Only completed actions can establish query or URL provenance,
and an action's task scope must agree with the mapping. Unassigned provider
candidates remain visible in the action trace, but are not forwarded to
Extractor. See the official
[web search guide](https://developers.openai.com/api/docs/guides/tools-web-search).

Searcher output contains source candidates, query provenance, task mappings,
warnings and cost metadata. It deliberately contains no extracted franchise
facts; its mapped source candidates are the input to Extractor.

Searcher now measures minimum coverage deterministically instead of trusting the
model's status label. For each task it records planned and derived attempted
queries, query coverage, minimum source-candidate coverage, action IDs, missing
coverage and unresolved search targets. `sources_found` means only that the
Searcher minimums were met. It does not mean that the source contents or any
franchise fact have been verified. `partial` means useful candidates were found,
but at least one Searcher requirement is still open.
When independent corroboration is requested, Searcher also requires candidates
from at least two distinct registrable-domain approximations, so two subdomains
of one organization do not close the gap. This is only a diversity proxy;
Checker must later establish whether the publishers are genuinely independent.

`provider_observed` replaces the misleading `provider_verified` name. It means
only that the URL was present in provider provenance. Schema `1.0.0` artifacts
using the old field still load, but new artifacts serialize the new name. Exact
`discovered_via_queries` are stored only when one action/query association is
unambiguous; batched activity remains auditable through `observed_in_action_ids`.
An executed derived query may be assigned to a task only when the model reports
that exact provider-observed query for one task; ambiguous multi-task assignments
remain action-only. Third-party registry aggregators are classified as
`routing_lead`, draft legislation as `legislative_project`, and unrelated
contest/campaign URLs are kept out of Extractor inputs.

Paid quality retries are disabled by default, so a coverage gap cannot silently
create another API request. To permit at most two one-task retries while keeping
ten as the global built-in tool-call cap:

```bash
.venv/bin/python -m datacollector search \
  --plan datacollector/data/runs/zabka/<run>/plan.json \
  --iteration 2 \
  --limit-tasks 5 \
  --max-search-calls 10 \
  --max-retry-tasks 2 \
  --retry-search-calls 1
```

Each retry is a separate paid API request and is recorded with its own
`call_index`, task scope, token usage and cost estimate. A failed charged retry
does not discard the usable first response; it is retained in `failed_attempts`
and in the usage ledger. Retry selection uses deterministic coverage gaps, not
the model's advisory `partial` label alone. Use `--min-queries-per-task` to
change the default of one exact Planner query per task.

Artifacts are immutable. Re-running the same mode and iteration refuses to
overwrite the prior result. Later iterations use names such as
`sources-r002-free.json` and `sources-r002.json`.

The CLI reserves the target filename before a paid call so two cooperating
processes cannot accidentally buy the same iteration concurrently. If OpenAI
returns token usage but the response is incomplete or unusable, that charged
attempt is saved under the run's `attempts/` directory instead of disappearing
from the cost ledger. The same applies if local validation fails after one or
more successful paid responses: every known call usage is saved separately. If
the provider omits token usage after an observed search action, the attempt still
records the known tool-call cost and marks token usage as unknown.

## Run Extractor: local free baseline, then paid comparison

Point both Extractor variants at the exact same paid Searcher artifact. Do not
use `sources-r003-free.json` for this comparison: the free Searcher is a query
workload and contains no provider-discovered documents. Extractor records and
validates both the Searcher and Planner SHA-256 lineage.

Run the real no-OpenAI Extractor first:

```bash
.venv/bin/python -m datacollector extract \
  --sources datacollector/data/runs/zabka/<run>/sources-r003.json \
  --free \
  --iteration 3 \
  --limit-sources 5
```

This five-source command is a low-cost/resource smoke test, not a representative
quality run. For a quality run, use the full candidate set instead (the current
Żabka `r003` has 11):

```bash
.venv/bin/python -m datacollector extract \
  --sources datacollector/data/runs/zabka/<run>/sources-r003.json \
  --free \
  --iteration 3 \
  --limit-sources 11
```

Artifacts are immutable. If the five-source iteration already exists, use a new
iteration number for both the expanded free run and its paid comparison.

Unlike the free Searcher baseline, this is not a dry-run. It performs a real,
bounded network fetch of the selected public URLs, parses supported HTML and PDF
documents locally, and deterministically creates task-specific evidence
passages. URL and every redirect are checked against the public-network policy;
private/internal addresses, unsafe redirects, type mismatches and responses over
the configured byte, character, page, redirect or timeout limits are rejected or
recorded as incomplete. It neither executes page scripts nor bypasses access
controls. Default PDF parsing runs in a disposable worker with wall-clock, CPU
and memory limits. Extractor scans bounded PDF text, ranks task-relevant and
document-wide sample pages, and only then applies the smaller stored-text cap;
the artifact preserves actual total, parsed and selected page numbers.

The free artifact has `generated_by=deterministic`. It records retrieval and
parse status, final URL, content and text hashes, exact passage offsets and
coverage gaps, but contains no provider citations or claims. It makes no OpenAI
call, so its token usage and estimated API cost are exactly zero. Fetched raw
HTML/PDF bytes are stored immutably under `documents-rNNN-free/`; their byte count
and SHA-256 are revalidated whenever an extraction artifact is loaded.

Then run paid extraction against the same Searcher artifact:

```bash
.venv/bin/python -m datacollector extract \
  --sources datacollector/data/runs/zabka/<run>/sources-r003.json \
  --iteration 3 \
  --limit-sources 5
```

When a compatible `extractions-r003-free.json` exists beside the Searcher
artifact, paid mode automatically reuses its stored document text after
validating lineage, source mappings, raw snapshots and content hashes, then rebuilds the
passages deterministically with the requested limit. This prevents a second
fetch and makes the free/paid comparison use identical source text. Terminal
free results such as anti-bot, access-denied, not-found and unsupported content
are also reused so the immediately following paid comparison does not repeat a
request that is unlikely to succeed. Transient failures such as timeouts,
network errors and rate limits are retried. Use a new iteration when you want to
retry a terminal result.

Passage ranking folds diacritics, applies conservative Polish inflection
matching (for example `umowa`/`umowę`), downranks navigation and privacy/contact
boilerplate for unrelated tasks, weights document-specific terms above repeated
brand words, and limits repeated match patterns. Tasks with no positively
matched passage do not consume a semantic provider call for that source.

For each selected source, paid Extractor sends only the minimal mapped task
fields and locally grounded `EvidencePassage` objects through the Responses API
with Structured Outputs. It supplies no web-search or other model tool and does
not send the complete plan or unselected document text. Every accepted claim is
kept in its original wording and marked `raw`/`unverified`; Checker must later
assess reliability, corroboration and conflicts. A citation is accepted only
when its quote exactly matches the stored document at its recorded character
offsets and its text SHA-256 matches the document. Model-only or incorrectly
anchored claims are discarded. Sources classified as `routing_lead` may be
retained for routing context but cannot produce claims.

Legacy ISAP document-detail URLs receive special official handling. Extractor
derives and validates the act identity, retrieves metadata from the official
`https://api.sejm.gov.pl/eli/acts/...` ELI endpoint, verifies that the returned
publisher/year/position match the requested act, and obtains the official HTML
or PDF text advertised by that API. It does not rely on the anti-bot ISAP page as
the legal text.
The connector follows the official
[Polish ELI API documentation](https://api.sejm.gov.pl/eli_pl.html).

`--limit-sources` is the first cost-control boundary. The default limits also cap
each download at 40 MiB, local PDF scan text at 2,000,000 characters, stored
selected text at 250,000 characters, provider evidence at 100,000 characters per
source call, passages per task at 6, and paid calls at 5. Extractor disables
hidden OpenAI SDK retries, so `--max-api-calls` is also the hard HTTP-request
ceiling for this agent. Sources beyond the selected limit remain explicit in
`unselected_source_ids`; unreadable, unsupported, truncated or unprocessed
content remains a coverage gap rather than being silently treated as absence of
a fact. Increase limits only after reviewing document coverage, token use and
cost from a smaller comparison.

Extractor artifacts are immutable. Iteration 3 creates
`extractions-r003-free.json` and `extractions-r003.json` beside the supplied
Searcher output. Re-running the same mode and iteration refuses to overwrite the
existing artifact, and the paid path reserves its filename before any provider
call. JSON publication is atomic and immutable. If a paid result cannot be
published after provider calls, every known usage entry and every unknown-usage
attempt is written best-effort to the `attempts/` ledger.

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

The CLI prints the same token totals and estimated cost after a paid run. Its
`usage_totals` object sums all provider calls for that agent iteration, including
recorded API attempts, tokens, tool calls and tool cost.
The token counts are provider-reported facts. The USD value is explicitly an
estimate: the OpenAI billing dashboard remains authoritative, and regional
processing, non-standard service tiers or separately billed tools may change
billed cost. Unknown models still record tokens but return a null cost.

For Searcher, the estimate adds observed `web_search_call` items at the current
published rate of $10 per 1,000 calls; search-content tokens are already charged
at the selected model's token rates. See official
[API pricing](https://developers.openai.com/api/docs/pricing#built-in-tools).

Extractor records one `agent_usage` entry for every completed per-source OpenAI
response, with its `call_index`, exact `scope_source_ids` and mapped task scope.
This makes both per-source and whole-iteration token cost auditable. It has no separately
billed web-search tool calls, so its estimate consists only of model input,
cache and output tokens. Incomplete or unusable charged responses retain their
known usage in the failure ledger; if token usage is unavailable, the attempt is
marked unknown instead of being reported as free. Deterministic extraction has
an empty provider ledger and zero token cost.

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

## From offline baseline to a real no-OpenAI Searcher

The current Searcher `--free` flag is deliberately an offline dry-run. A local
language model alone would not change that: a model is not a current web index.
The program in this repository is already the agent/orchestrator; a real free
variant primarily needs a search backend, not necessarily another LLM.

The proposed next milestone is:

```text
Planner queries
    -> self-hosted SearXNG and direct official-source connectors
    -> deterministic URL validation, ranking and task mapping
    -> optional local model for reranking only
    -> sources-free.json
```

SearXNG exposes an HTTP search API and can be self-hosted in a container, but it
is a metasearch service: it still needs internet access and upstream engines may
rate-limit it. This avoids an OpenAI API bill, not the costs of hardware,
electricity, bandwidth and maintenance. A truly offline Searcher requires a
previously downloaded and indexed document corpus.

See [the free Searcher architecture note](docs/free_searcher_architecture.md) for
the recommended backend boundary, deployment stages, security requirements and
the local-model fit for this workstation.

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
- Every extracted claim must have retrievable evidence and exact citation lineage.
- No AI output may be published or imported without human review.

See [the U.S. standard](docs/us_franchise_research_standard.md) for the source
mapping and the exact information groups the Planner must cover.
