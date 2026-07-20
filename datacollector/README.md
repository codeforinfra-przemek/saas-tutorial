# Franchise AI research loop — Planner through Human Review and Importer

This is a standalone, local worker for auditable franchise research. It is kept
outside Django intentionally: long-running and paid agent work must not execute
inside a web request. Only a separately approved Human Review artifact may cross
the boundary into Django through the idempotent Importer.

The planned loop is:

```text
Planner → Searcher → Extractor → Checker ↔ Resolver → Executor → Checker
        → Normalizer → human review → Importer
```

Planner, Searcher, Extractor, Checker, Resolver, Executor, Normalizer, Human
Review and Importer are implemented.
Planner combines:

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
Checker consumes that exact lineage, applies deterministic source, grounding and
coverage rules, and optionally asks OpenAI for one bounded semantic review. It
produces quality decisions and follow-up work; it still does not normalize or
write production data.
Resolver consumes a successful paid Checker artifact and turns only its unresolved
fields into bounded execution batches for the next Searcher/Extractor round. It
plans retrieval and research; it never claims that planned work has already run.
Executor runs those exact batches through the existing workers, preserves
predecessor lineage, deduplicates retrieval, and materializes merged Searcher and
Extractor artifacts for a new Checker pass. Executor itself makes no model call.
Loop Orchestrator composes paid Checker, Resolver and Executor stages into bounded
cycles, expands into the next plan batch when the selected scope is ready, records
incremental cost, and stops on quality, budget, stagnation or round limits.
Normalizer consumes one successful paid Checker artifact, admits only accepted
and grounded claims, creates typed staging values with exact claim/citation/source
provenance, and always routes the result to human review. It cannot publish or
write production data.

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
Ordinary model calls use `OPENAI_TIMEOUT_SECONDS=60`; multi-step Searcher calls
use the separate `OPENAI_SEARCH_TIMEOUT_SECONDS=180` default. Increase the latter
for larger sequential web-search workloads without lengthening every agent call.
Searcher disables hidden SDK transport retries because a timed-out request may
already have incurred web-search cost; a retry must be an explicit, auditable run.
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

Legacy `--depth` remains available for old artifacts and scripts. New Polish
research should normally use the country-calibrated Research Profiles v2 instead:

```bash
.venv/bin/python -m datacollector profiles --country PL
.venv/bin/python -m datacollector questions --country PL --profile PL:L1
```

`--profile` and `--depth` are mutually exclusive. Profile aliases resolve to an
immutable versioned ID (`PL:L1` becomes `PL:L1:v1`), and Planner stores the full
materialized profile plus a self-verifying SHA-256 in plan schema `1.3.0`.

### Polish Research Profiles v2

| Profile | Questions | Fields | Completion gate | Intended use |
| --- | ---: | ---: | ---: | --- |
| `PL:L1:v1` | 13 | 61 | 30 | mass population of a useful public directory profile |
| `PL:L2:v1` | 26 | 179 | 77 | multi-source public verification, registries and manual risk checks |
| `PL:L3:v1` | 34 | 273 | 101 | public due diligence covering the FDD 1–23 benchmark and documenting private gaps |

The levels are cumulative: L1 questions and fields are retained in L2, and L2
is retained in L3. Common Planner task IDs are stable between levels. A higher
level adds scope and can strengthen evidence requirements; it cannot silently
weaken an inherited minimum-source or corroboration rule.

Availability is recorded per field, independently from task priority:

- `public_expected`: expected on an official or otherwise public page;
- `public_optional`: useful when published, but absence does not block completion;
- `registry_expected`: expected in an official public register;
- `manual_research_required`: public research requiring a person or a dedicated connector;
- `private_document_required`: requires an authorized agreement or disclosure document;
- `confidential_deal_room`: only for a separately authorized private-data profile;
- `system_derived`: calculated from the collected artifact rather than searched;
- `not_applicable`: excluded by the country policy.

The completion gate is also per field. In the current public PL profiles it
includes `public_expected`; L2/L3 additionally include `registry_expected` and
`manual_research_required`. `public_optional`, `private_document_required` and
`system_derived` fields do not masquerade as critical public facts in the profile
contract. Private document gaps remain visible in L3. Checker 1.5 consumes this
field-level policy: its completion score and critical-gap gate use only fields
required by the frozen profile, while total coverage continues to show every
optional and private gap.

PL profiles use Polish search-query templates and freeze the appropriate Polish
authorities in the snapshot (CEIDG/Biznes.gov.pl, KRS/PRS/RDF, UOKiK, UPRP,
ELI and EUR-Lex). The FTC/FDD material remains a comparative coverage framework,
not a claim that US disclosure law applies in Poland.

Create a no-API profile plan:

```bash
.venv/bin/python -m datacollector plan \
  --brand "Żabka" \
  --country PL \
  --profile PL:L1 \
  --offline
```

Remove `--offline` for an OpenAI-guided Planner run. The profile still controls
coverage and completion; the model may enrich guidance but cannot remove fields.
Resolver 1.4 routes public/registry fields to automated evidence work, manual and
authorized private fields to Human Review, and system-derived fields to local
audit. Searcher and Extractor independently enforce the same boundary and send
only public/registry field views to their models for mixed tasks. Country-level
reuse and cross-plan L1→L2→L3 reuse remain a separate future stage because they
require an explicit reuse manifest rather than weakening exact artifact lineage.

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
remain action-only. Searcher schema `1.5.0` records the deterministic
`query_task_ids` mapping on each action and asks the provider to use one task and
one query per search action. Provider output can still omit or batch queries, so
the local validator never invents missing URL-to-query provenance. Citation
titles are merged by canonical URL and sentence punctuation is removed before
URL identity is calculated.

If the provider returns more tool actions than the requested `max_tool_calls`,
the artifact retains the complete billed trace, records
`provider_tool_call_overrun`, and emits a warning. The requested ceiling remains
visible in `limits`; observed provider usage is never rewritten to look cheaper.

`candidate_routes` records provider-observed URLs that the model did not map.
Candidate Router promotes a URL only when its completed action has one task, its
complete query attribution resolves to one task, a distinctive registry/legal
authority path resolves to one task, or URL/title terms produce a unique,
thresholded match with a minimum margin. Ambiguous candidates stay in the action
trace. The default promotion ceiling is five per Searcher run and can be changed
with `--max-candidate-routes`; `0` disables promotion while keeping the audit
decisions. Third-party registry aggregators are classified as `routing_lead`,
draft legislation as `legislative_project`, and unrelated contest/campaign URLs
are kept out of Extractor inputs.

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

## Run Checker: paid semantic review with optional local smoke test

Point both Checker variants at the exact same paid Extractor artifact. The
Checker loads the referenced Planner and Searcher artifacts automatically and
validates their IDs, exact resolved references and SHA-256 lineage. Optional
`--plan` and `--sources` let automation supply those same explicit input paths;
they do not permit artifact relocation or bypass lineage validation.

For normal research runs, execute the paid Checker directly. Use the free
deterministic Checker only as an optional smoke/regression test:

```bash
.venv/bin/python -m datacollector check \
  --extractions datacollector/data/runs/zabka/<run>/extractions-r003.json \
  --free \
  --iteration 3 \
  --max-claims 500 \
  --max-evidence-chars 100000
```

This is a real local quality pass, not a dry-run. It checks immutable lineage,
claim/citation grounding, source roles, task and field coverage, inaccessible or
unprocessed inputs, critical gaps and the configured plan threshold. It creates
source assessments, field/task results and deterministic Resolver follow-ups.
Semantic verdicts remain explicitly `not_reviewed`, so the free result cannot
pass the complete quality gate and normally recommends `run_paid_checker`. It
makes no OpenAI call and has zero provider token cost.

Run the paid Checker against the Extractor path:

```bash
.venv/bin/python -m datacollector check \
  --extractions datacollector/data/runs/zabka/<run>/extractions-r003.json \
  --iteration 3 \
  --max-claims 500 \
  --max-evidence-chars 100000
```

Use `--model <model-name>` when Checker should use a different model from the
global `OPENAI_MODEL`; the selected model and its usage remain recorded in the
artifact.

Paid mode sends only the selected tasks, bounded source/document status metadata,
raw claims, exact citation quotes and upstream coverage summaries through one
Responses API Structured Outputs request. It does not browse, retrieve raw
documents, use tools, normalize values or accept instructions embedded in source
text. The
local agent remains authoritative for lineage, scope, exact grounding, source
classification, completeness, deductions and the final score/pass gate; the
model supplies bounded semantic-fit, support, contradiction and safety
judgments.

Only accepted, semantically eligible claims may create a scored contradiction.
A rejected claim (for example, a legislative proposal rejected as evidence of
current law) cannot conflict with an accepted current-law claim or deduct score.
Checker also canonicalizes common currency labels before accepting a currency
contradiction. For example, `PLN`, `zł`, `złoty` and `złotych` describe the same
currency; different cited investment amounts remain separate values on their
amount field but no longer create a false `investment.currency` conflict.

Checker contract `1.5.0` keeps semantic acceptance separate from source
corroboration. A directly supported claim can therefore be `accepted` while its
field remains `needs_corroboration`. Multi-valued fields with both supported and
unresolved values use `partial`, so accepted evidence keeps partial quality
credit. A local field-contract guard prevents physical store descriptions such
as "furnished and equipped" from satisfying `offer.unit_formats`; that field
requires evidence of single-unit, multi-unit, area/master/subfranchise, renewal,
transfer, or resale structure. For document inventory, a page that only names a
document can establish the stated title or existence, but receives
`mentioned_not_obtained` until the actual current document is fetched and parsed.

Every unresolved field produces one explicitly routed follow-up containing an
action, known candidate/retry/re-extraction source IDs, unresolved and supporting
claim IDs, suggested plan queries, the minimum additional source count,
independence requirement and an explicit completion criterion. This lets the next
agent act on existing Searcher results before paying for another broad search.
Profile follow-ups also retain field availability, completion significance and
reuse scope. Authorized private or manual work is handed to Human Review, while
`system_derived` work requests a bounded local audit. A `not_applicable` field is
excluded from coverage denominators and does not create a follow-up.

The defaults impose a hard preflight ceiling of 500 claims and 100,000 quoted
evidence characters. Checker refuses an artifact above either ceiling instead of
silently truncating its semantic-review scope; raise the limits deliberately
after inspecting the extraction. `unevaluated_task_ids` and
`unevaluated_source_ids` expose scope that the upstream Searcher or Extractor did
not select. For legacy plans either list makes `scope_complete=false`. For a
profile plan every task must still be attempted, while an unused candidate URL
remains an explicit evidence backlog and does not by itself block completion.
Checker also requires all critical fields, no unresolved contradiction or high/critical
unsafe item, and a score at or above the Planner threshold before it can
recommend `human_review`. A pass means ready for human review, never safe for
automatic production import.

`selected_scope_ready` applies the quality, critical-field, contradiction and
safety gates to the tasks already evaluated without pretending the whole plan is
complete. When that gate passes while plan tasks remain, Checker returns
`recommended_next_action=research_next_batch`. This prevents an already
satisfactory selected batch from being sent through repeated gap repair.

Iteration 3 writes `check-r003-free.json` and `check-r003.json` beside the exact
Extractor artifact. Results are immutable and the target is reserved before a
paid call. The OpenAI client disables SDK retries and Checker allows at most one
provider request per run. If that call is unusable, or its final artifact cannot
be used, known usage (or an explicit unknown-token attempt) is retained in the
Checker artifact. If that final artifact cannot be published, the same attempt
facts are written best-effort to the run's `attempts/` ledger.

After Executor, an incremental pass can reuse only successful paid judgments
from the exact predecessor Checker/Extractor/Searcher lineage:

```bash
.venv/bin/python -m datacollector check \
  --extractions datacollector/data/runs/zabka/<run>/extractions-r018.json \
  --iteration 18 \
  --incremental \
  --max-claims 500 \
  --max-evidence-chars 500000
```

The task is the invalidation boundary: one added, removed or changed claim,
citation, cited-source semantic metadata or document parse metadata causes every
claim in that task to be reviewed again. Unchanged task judgments,
contradictions and safely scoped unsafe items retain their original decisions.
The artifact records `reviewed_*` and `inherited_claim_ids`; provider usage scope
must match only `reviewed_task_ids` and `reviewed_source_ids`. Incremental mode
refuses free or failed/unreviewed predecessors and verifies exact SHA-256 lineage
before inheriting anything.

## Run Resolver: paid prioritization with deterministic local guards

Point both Resolver variants at the same successful paid Checker artifact. A
free Checker is intentionally rejected because its claims remain semantically
`not_reviewed`, so it cannot provide reliable repair targets.

For normal research runs, execute the paid Resolver directly. The deterministic
variant remains available as an optional strategy smoke test:

```bash
.venv/bin/python -m datacollector resolve \
  --check datacollector/data/runs/zabka/<run>/check-r005.json \
  --free \
  --iteration 5 \
  --max-follow-ups 30 \
  --max-source-actions 10 \
  --max-search-tasks 5
```

This makes no network or OpenAI request. It validates the complete
Planner→Searcher→Extractor→Checker lineage and produces a real repair strategy.
Known unprocessed sources are selected before blocked-source retries or new
searches. A `mentioned_not_obtained` document cannot be resolved by merely
re-extracting the page that mentioned it. Work is grouped into
`extract_known_source`, `retry_retrieval`, `reextract_existing`,
`search_new_source`, `local_audit`, or `human_review` execution batches.

For profile plans, Resolver also carries field availability, completion and
reuse policy into every work item. `manual_research_required`,
`private_document_required` and `confidential_deal_room` are locked to
`human_review` with no source IDs or queries. `system_derived` is locked to
`local_audit`. Required public work is ordered ahead of optional enrichment so a
bounded round cannot spend its whole budget on non-gating fields. Resolver skips
its paid model request when all selected work is local or human, and does not run
a no-op Executor cycle for field-level local work.

The final `data_quality` catalog tasks describe the pipeline's own evidence,
status, scoring, stopping and approval contracts. They are not franchise facts
and must not be researched on the web. Resolver therefore materializes them as
deterministic `local_audit` work, skips its paid model call when the selected
batch contains only such work, and supplies no queries or source actions.

Resolver does not schedule `reextract_existing` for a routing-only lead, an
unparsed/inaccessible document, or a source already recorded as semantically
processed for the same plan task. Gaps requiring another publisher,
independent corroboration, a preferred authority, or alternative evidence
prefer `search_new_source` after any genuinely unevaluated known source. If a
paid strategy is rejected locally, the fallback artifact records the specific
validation code and reason instead of hiding it behind a generic failure.

Run paid prioritization against the Checker input:

```bash
.venv/bin/python -m datacollector resolve \
  --check datacollector/data/runs/zabka/<run>/check-r005.json \
  --iteration 5 \
  --max-follow-ups 30 \
  --max-source-actions 10 \
  --max-search-tasks 5
```

Paid Resolver makes one bounded Responses API Structured Outputs request without
web-search, retrieval, or extraction tools. The model may reorder work, select
from locally allowed actions and source IDs, and add narrow queries. Local code
rejects invented IDs, incompatible actions, incomplete coverage, or budget
overruns and retains the deterministic executable strategy as an explicit
fallback. Iteration 5 writes `resolution-r005-free.json` and
`resolution-r005.json` immutably beside the Checker artifact.

Resolver also accepts `research_next_batch`. Legacy mode selects known
`unevaluated_source_ids` before the next plan task. Profile mode treats those URLs
as a non-blocking evidence backlog and advances through the next plan-ordered
`unevaluated_task_ids`, bounded by both `--max-follow-ups` and
`--max-search-tasks`. A mixed task schedules public automation only; a human-only
task stops at Human Review. Remaining plan tasks are recorded as deferred scope
work. Executor then uses its immutable merge path to add the new task, source,
document, claim and usage state to the exact predecessor artifacts.

Executor completes `local_audit` batches without Searcher, network retrieval or
Extractor calls and adds the task scope to the merged artifacts. The following
paid Checker then derives each `quality.*` result from validated immutable local
artifacts, stores a human-readable `audit_basis`, and creates neither fake web
citations nor follow-up searches. An incremental Checker with no other changed
claims also skips its model call.

`retry_retrieval` is a plan for the next Extractor round, not proof of a completed
download. Terminal `anti_bot_page` and `access_denied` results are not retry
candidates; Resolver must search for an alternative public route instead.
Likewise, `extract_known_source` means the source is already known to
Searcher but has not yet been processed in the selected extraction scope. A new
Checker pass is required after executing these batches; unresolved data cannot
advance to Normalizer.

Before any paid Resolver request, the CLI walks the exact immutable Executor
predecessor chain and counts consecutive rounds created from `resolve_gaps`
checks. Once that count reaches the Planner's `max_rounds`, Resolver stops
locally and routes the scope to human review instead of starting another costly
loop. `--allow-round-limit` is an explicit emergency override for a deliberately
approved extra round; the resulting artifact records that override in its
warnings. When the gaps are documented but public research is exhausted,
`--advance-with-documented-gaps` schedules the next unevaluated plan batch
instead. It preserves every unresolved field as a final approval blocker and is
mutually exclusive with spending another gap-repair round.

## Run Executor: paid execution with optional local smoke test

Executor consumes one exact Resolver artifact. It uses Resolver queries for
`search_new_source`, never uses a stale cache entry for `retry_retrieval`, and
reuses an eligible predecessor document for `reextract_existing` or
`extract_known_source`. Every source is processed at most once even when several
follow-ups reference it. The exact, already validated predecessor cache is not
tied to the newest Searcher UUID; URL, task mapping, status, size and content
integrity checks still apply. When a paid result has the same raw-content hash,
Executor preserves checked predecessor claims and merges newly grounded claims
additively instead of silently replacing the evidence set.

For normal research runs, execute paid mode directly. Use the free comparison
only for smoke tests, retrieval diagnostics or regression work:

```bash
.venv/bin/python -m datacollector execute \
  --resolution datacollector/data/runs/zabka/<run>/resolution-r005.json \
  --free \
  --iteration 6 \
  --max-search-calls 10 \
  --max-extractor-api-calls 20
```

Free execution performs bounded local retrieval and parsing, but no OpenAI web
search or semantic extraction. Resolver search queries remain an explicit
workload. Existing paid claims are not discarded merely because a free retry is
inaccessible or cannot semantically replace them. It writes:

```text
sources-r006-free.json
extractions-r006-free.json
execution-r006-free.json
```

Then run the paid execution against the same immutable Resolver artifact:

```bash
.venv/bin/python -m datacollector execute \
  --resolution datacollector/data/runs/zabka/<run>/resolution-r005.json \
  --iteration 6 \
  --max-search-calls 10 \
  --max-extractor-api-calls 20
```

The paid variant invokes Searcher only for Resolver's `search_new_source` tasks
and invokes Extractor once per eligible, deduplicated source up to the explicit
cap. It writes the corresponding artifacts without `-free` and prints the exact
next Checker command. `execution-r006.json` is the audit manifest: it records
batch outcomes, retry/cache decisions, preserved predecessor states, pending
human work, child-agent token usage, and exact hashes for every input and output.
Cache warnings distinguish a same-iteration free retrieval artifact from the
exact predecessor artifact reused by Executor.
Rediscovered URLs whose predecessor documents already cover the same task
mappings are merged into Searcher provenance but are not sent through another
paid Extractor call. Only a genuinely new source, a newly added task mapping or
an explicit Resolver source action can schedule extraction.

If an older Executor artifact was created before additive same-content merging,
repair it locally without repeating paid calls:

```bash
.venv/bin/python -m datacollector reconcile \
  --extractions datacollector/data/runs/zabka/<run>/extractions-r010.json
```

The command verifies the exact plan, merged Searcher, Resolver and predecessor
hashes recorded in lineage. It writes a separate immutable
`extractions-r010-reconciled.json`, records both predecessor and repaired-current
lineage, and reports zero reconciliation API/network calls and zero additional
cost. Original provider usage remains in the repaired artifact because it
describes the paid evidence being rematerialized; it is not a new charge.

Run Checker on the paid merged extraction:

```bash
.venv/bin/python -m datacollector check \
  --extractions datacollector/data/runs/zabka/<run>/extractions-r006.json \
  --iteration 6 \
  --incremental
```

If Checker returns `resolve_gaps`, create another Resolver/Executor repair round.
If it returns `research_next_batch`, run Resolver against that Checker artifact;
Resolver schedules and Executor merges the next bounded plan batch. Normalizer
is allowed only after the required plan scope has passed or a human explicitly
accepts documented gaps.

## Run Loop Orchestrator: bounded paid automation

The Orchestrator replaces manual Resolver → Executor → Checker repetition. It
starts from one exact Checker artifact and runs only paid agent variants. A cycle
either repairs the selected scope or, after `selected_scope_ready=true`, adds the
next plan-ordered task batch. It never approves or imports data.

Start with conservative limits:

```bash
.venv/bin/python -m datacollector loop \
  --check datacollector/data/runs/zabka/<run>/check-r012.json \
  --max-rounds 2 \
  --max-cost-usd 1.00 \
  --max-stagnant-rounds 2 \
  --max-search-tasks 5 \
  --max-search-calls 10 \
  --min-queries-per-task 2 \
  --max-extractor-api-calls 20
```

`--max-search-calls` is a ceiling, not a target. Use
`--min-queries-per-task 2` when a five-task batch should attempt two distinct
Resolver queries per task (up to ten search calls). The setting is passed to the
Executor/Searcher child and recorded in the immutable Executor limits. Keep the
product of selected search tasks and this minimum within the search-call ceiling
when full minimum-query coverage is required.

Each invocation writes a separate immutable `loop-<id>.json` manifest. It
records every newly executed stage, before/after quality and scope counts,
provider attempts, tokens, tool calls, incremental estimated USD cost, the exact
stop reason and the final Checker reference. Pre-existing artifact usage is not
charged again in the loop total.

Stops include `checker_passed`, `max_rounds`, `plan_repair_limit`, `no_progress`,
`budget_exhausted`, `cost_unknown` and `human_review_required`. The cost ceiling
is evaluated between complete cycles so the final already-started cycle may
produce a bounded overshoot; the manifest states this explicitly. Unknown usage
stops subsequent automatic spend.

The Planner's gap-repair limit remains a separate safety gate. If public research
for the selected scope is exhausted, advance to the next batch while retaining
all current gaps:

```bash
.venv/bin/python -m datacollector loop \
  --check datacollector/data/runs/zabka/<run>/check-r012.json \
  --advance-with-documented-gaps \
  --max-rounds 1 \
  --max-cost-usd 0.75
```

This is a scope-progression override, not acceptance: missing documents and
unverified critical fields remain visible and prevent Checker pass. Use
`--allow-plan-repair-limit` instead only when another attempt at the same gaps is
deliberately justified. The two options are mutually exclusive and neither
bypasses budget or stagnation stops. Loop progress now counts quality-gate
improvements and newly evaluated tasks; merely collecting more URLs or raw claims
does not hide stagnation. Regressions in critical gaps, contradictions, verified
fields or quality are recorded separately in every round.
Once documented-gap progression reaches complete plan scope, the Orchestrator
returns `inspect_gaps` instead of suggesting another empty progression loop.
Further paid work must then be an explicit gap-repair decision or a deliberate
incomplete finalization for Human Review.

When Checker passes, Orchestrator runs the paid Normalizer automatically unless
`--skip-normalize` is supplied. Loop uses Incremental Checker after each Executor
cycle. Before any complete or explicitly incomplete Normalizer run it performs a
mandatory full paid Checker pass against the same extraction; standalone
Normalizer rejects an incremental Checker artifact. This prevents inherited
history from becoming the final publication/import gate.
For a deliberately incomplete review draft, `--normalize-incomplete` is required;
it is not run after a budget or unknown-cost stop. Human Review remains mandatory
in both cases.

## Run Normalizer: typed staging data, never publication

Normalizer consumes an exact successful paid Checker artifact and validates the
complete Plan→Searcher→Extractor→Checker hashes and IDs. It never searches,
fetches, invokes tools, changes Checker verdicts, fills missing facts, or writes
to Django. Rejected and `needs_review` claims are excluded. Accepted claims tied
to a Checker safety finding are also excluded. Every retained value records its
raw claim IDs, citation IDs and source IDs.
The input Checker must have `checker_mode=full`; an incremental artifact is valid
for loop routing and cost control, but never as the final Normalizer gate.

For a Checker that passed, run paid Normalizer directly:

```bash
.venv/bin/python -m datacollector normalize \
  --check datacollector/data/runs/zabka/<run>/check-r012.json \
  --iteration 12
```

When the repair-round budget has been exhausted and the Checker still did not
pass, inspect its gaps first and explicitly request an incomplete review draft:

```bash
.venv/bin/python -m datacollector normalize \
  --check datacollector/data/runs/zabka/<run>/check-r012.json \
  --iteration 12 \
  --allow-incomplete
```

Without `--allow-incomplete`, a failed Checker is rejected before any Normalizer
API call. The override never makes the data publishable: the artifact records the
failed score, incomplete scope, critical gaps and override, and keeps
`publishable=false`.

The paid pass makes at most one Structured Outputs request. It may group truly
equivalent accepted claims and normalize explicit values as text, integer,
decimal, boolean, date, URL, money or percentage. Numeric ranges, approximate
values, units and ISO currencies remain explicit. Local code rejects invented,
missing, duplicated or cross-field claim IDs and falls back to one conservative
text value per accepted claim while retaining usage/cost metadata. When claim
coverage and grouping are sound but individual typed groups violate a local
semantic rule, Normalizer schema `1.2.0` preserves valid provider groups and replaces only
the invalid groups with deterministic text values. `repair_summary` records
counts and non-sensitive rule codes; it never stores rejected model prose.

The optional free smoke test uses that conservative representation directly:

```bash
.venv/bin/python -m datacollector normalize \
  --check datacollector/data/runs/zabka/<run>/check-r012.json \
  --free \
  --iteration 12 \
  --allow-incomplete
```

It writes `normalized-r012-free.json`; paid mode writes
`normalized-r012.json`. Both are immutable, review-only staging artifacts.
Field results preserve the Checker's verification status, rejected and
needs-review partitions, unresolved contradictions, missing fields and
corroboration requirements. Locally audited `quality.*` fields use the explicit
`derived` status and retain their audit basis as notes; they do not fabricate a
normalized value, claim, citation or source. The only next action is
`human_review`. A later
Importer must consume a separately approved review artifact, never raw
Normalizer output.

## Human Review report and decision

### Human Research Workbench (recommended editorial flow)

Apply Django migrations and materialize a mutable, staff-only workspace from the
exact Normalizer lineage:

```bash
.venv/bin/python src/saashome/manage.py migrate
.venv/bin/python src/saashome/manage.py open_research_workbench \
  --normalized datacollector/data/runs/zabka/<run>/normalized-r014.json \
  --franchise-slug zabka
```

The command prints a URL under `/internal/research/<workspace-id>/`. The
Workbench shows all planned fields (including unevaluated ones), evidence,
pipeline stages, token/cost totals and warnings. Staff can accept or reject a
proposal with one icon, fill a missing value and accept it in one submit, mark a
verified absence, or undo a decision. Every change records its actor and time.

Contracts and other private documents are uploaded to a storage root outside
public `MEDIA_ROOT` and can only be downloaded through a staff-protected view.
They remain queued for a later extraction run; uploading a document does not
silently turn it into evidence or publish it. A Workbench approval closes the
editorial staging step but intentionally does not bypass the immutable signed
review artifact and Importer described below.

The command is idempotent by `normalization_id` and exact Normalizer bytes.

Run one database-backed worker as a separate process to execute jobs queued from
the Workbench UI:

```bash
.venv/bin/python src/saashome/manage.py process_research_jobs
```

Use `--once` for an operational smoke test or a scheduler that starts one job at
a time. The web request never performs a paid model call. It stores only typed
limits and a selected strategy; the worker reconstructs a closed argument list,
revalidates the exact Checker SHA-256 and plan lineage, and prevents two active
jobs for the same workspace. While it runs, the UI polls a staff-only status
endpoint and shows the current Resolver/Executor/Extractor/Checker/Normalizer
stage. Exact usage and cost are attached after completion. A new Normalizer
artifact creates a new Workbench instead of overwriting the reviewed draft.

Uploaded private, confidential and deal-room documents remain human-only under
the research-profile policy. Researchers can attach them to a corrected field as
supporting evidence, but the public research worker never sends their bytes to
OpenAI. Automated private-document analysis would require a separate opt-in
policy and consent boundary.

Create a portable HTML report without making another model or network call:

```bash
.venv/bin/python -m datacollector review \
  --normalized datacollector/data/runs/zabka/<run>/normalized-r014.json
```

This writes immutable `review-r014-pending.json` and
`review-r014-pending.html`. The report shows the whole planned task/field scope,
accepted values, missing and unevaluated fields, Checker status, raw claims,
exact citation quotes and the complete source register. A pending decision does
not authorize import.

Complete research uses `approved`. Incomplete research cannot use that label;
an explicit gaps decision and acknowledgement are required:

```bash
.venv/bin/python -m datacollector review \
  --normalized datacollector/data/runs/zabka/<run>/normalized-r014.json \
  --decision approved_with_gaps \
  --reviewer "Reviewer name" \
  --notes "Why this incomplete dataset may enter the review database." \
  --acknowledge-incomplete
```

`changes_requested` and `rejected` are also final, signed decisions but never
authorize import. Import approval requires a successful paid Normalizer; free
smoke-test artifacts may be inspected but cannot cross the database boundary.

## Import approved research into Django

Apply migrations once, then import the signed review artifact:

```bash
.venv/bin/python src/saashome/manage.py migrate
.venv/bin/python src/saashome/manage.py import_franchise_research \
  --review datacollector/data/runs/zabka/<run>/review-r014-approved-with-gaps.json \
  --allow-approved-with-gaps
```

Use `--franchise-slug <slug>` to target an existing profile and
`--category-slug <slug>` when a new profile needs a specific category. Import is
transactional and idempotent by Normalizer/review identity. It stores all six
source artifacts losslessly and indexes every plan task, target field, source,
claim, citation and normalized value in relational models. Repeating the exact
command does not duplicate data. A newer approved import becomes current while
older imports remain immutable history.

The detailed Django view is available at:

```text
/franchises/<slug>/research/
```

It displays the review decision, coverage and quality, all acquired values,
missing/unevaluated fields, evidence quotes and source links. Only safe,
single-valued mappings update the compact `Franchise` profile; the research
models remain the lossless source of truth.

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

Checker records at most one `agent_usage` entry with the exact reviewed task and
source scope. It has no separately billed web-search tool call, so the estimate
contains model tokens only. The CLI summary reports the claim verdicts, field and
task statuses, scope completeness, deductions, final score, pass decision,
recommended next action and the same per-call and total token/cost ledger stored
in the artifact. `accepted_claim_source_quality_score` describes only sources
behind accepted claims; it is not a score for every selected source.
Deterministic checking keeps that provider ledger empty and its estimated cost at
zero.

Resolver records at most one `agent_usage` entry and has no tool calls. Its free
variant has zero provider cost. The paid variant pays only for strategy and uses
Structured Outputs; if the response is unusable, usage is retained while the
deterministic plan remains available as `deterministic_fallback`.

Executor has no independent provider usage or model charge. Its manifest contains
only the current child Searcher and Extractor usage, so `usage_totals` is the real
incremental cost of that execution rather than a cumulative re-count of inherited
claims. The free manifest has zero tokens and tool cost.

Normalizer records at most one `agent_usage` entry and has no tools. Paid mode
charges only model input/output tokens for accepted claims and their bounded
evidence context. Structurally invalid or failed responses retain known usage
(or mark it unknown) while producing a deterministic text fallback. A
structurally complete response with invalid typed groups uses
`openai_repaired`: valid groups survive and only invalid groups are downgraded.
Free mode has no API usage and zero provider cost.

For GPT-5.6, Planner, Searcher, Extractor, Checker, Resolver and Normalizer disable the default
implicit cache breakpoint for their one-off, brand-specific calls. Those payloads
would otherwise incur cache-write charges without guaranteeing a later cache hit.
Provider-reported cache-write tokens are still recorded and priced if they occur.

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
