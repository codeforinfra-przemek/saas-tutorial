# Real no-OpenAI Searcher architecture

## The important distinction

The current `search --free` path is an offline workload generator. It proves
that Planner tasks can be selected and serialized without spending money, but
it does not search the web.

Running a language model locally does not by itself create a web Searcher. The
two capabilities are separate:

1. retrieval finds current URLs and records which query produced them;
2. ranking/mapping decides which task a candidate may help.

The existing Python application already orchestrates these steps. A first real
no-OpenAI implementation can do retrieval with a search service and perform
ranking/mapping with deterministic rules. A local LLM is optional.

## Recommended target

```text
ResearchPlan
    |
    v
SearchBackend
    +-- OfflineBackend       (no network; present behaviour)
    +-- SearxngBackend       (real web search; no OpenAI API)
    +-- OpenAIWebBackend     (hosted web_search)
    +-- DirectSourceBackend  (official sitemaps/APIs/registries)
    |
    v
deterministic provenance + public-URL checks + task mapping
    |
    +-- optional LocalRanker (later, only if measurements justify it)
    |
    v
SearchResults
```

SearXNG is a practical first retrieval backend because it exposes a simple HTTP
API returning JSON and has an official container deployment. JSON output must
be enabled in its `settings.yml`; public instances often disable it. Refer to
the official [Search API](https://docs.searxng.org/dev/search_api.html) and
[container installation guide](https://docs.searxng.org/admin/installation-docker.html).

SearXNG is a metasearch engine, not a local copy of the web. Queries are passed
to external search services, so this mode still requires internet access and is
subject to upstream availability, limits and terms. Self-hosting removes the
OpenAI charge but not operating costs. Fully offline retrieval would require us
to download, refresh and index an approved corpus ourselves.

## Proposed command semantics

Do not overload one flag with two meanings. When the SearXNG backend is built,
move toward an explicit backend option:

```text
--backend offline   prepare workload only, no network
--backend searxng   perform real no-OpenAI web retrieval
--backend openai    use Responses API web_search
```

During migration, `--offline` can keep the present dry-run behaviour and
`--free` can become an alias for `--backend searxng`. Real no-OpenAI artifacts
should keep the requested `-free` filename marker, while the backend must also
be stored explicitly in the JSON. Offline and SearXNG outputs from the same
logical iteration need distinct filenames or iterations so they cannot collide.

## Minimum implementation contract

For every selected task, `SearxngBackend` should:

1. execute at least `min_queries_per_task` exact Planner queries;
2. record one stable action per request with query, task scope and returned URLs;
3. canonicalize and deduplicate URLs;
4. map only task-relevant candidates into `sources`;
5. retain unassigned candidates only in the action trace;
6. report deterministic query/source coverage and unresolved targets;
7. record zero OpenAI token cost while still recording backend request counts and
   elapsed time.

Start with deterministic ranking: favour exact official domains, government and
regulator domains, registries, legal-document repositories and task source
hints. Add direct connectors for important sources such as official franchise
sites, sitemaps, government portals and registries. These narrow connectors can
be more reliable for due diligence than a general web search.

## Fetching and security boundary

Search results should initially be treated as URL candidates. If this component
later fetches pages, it needs a dedicated hardened fetcher rather than a generic
unrestricted HTTP client. At minimum:

- allow only HTTP/HTTPS public destinations;
- resolve DNS and block loopback, private, link-local and metadata addresses;
- revalidate every redirect target to prevent SSRF and DNS-rebinding bypasses;
- cap redirects, response bytes and content types;
- apply connect/read timeouts, concurrency limits and per-domain rate limits;
- do not send browser cookies or access login-protected/paywall-bypassed content;
- treat page content as untrusted data and never follow embedded instructions.

## Do we need a local model on this workstation?

Not for the first implementation. The machine inspected on 2026-07-17 has an
Intel Core i7-1260P, about 15 GiB RAM, and no visible NVIDIA runtime/device.
There is no Ollama executable currently installed.

OpenAI's official local-run guide says `gpt-oss-20b` needs at least 16 GB of
VRAM, so that model is not a sensible default for this host. See the official
[gpt-oss LM Studio guide](https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-lmstudio).
A smaller quantized model could be tested on CPU, but that is an inference about
performance, not a requirement for real retrieval. It would add latency and
model-specific license/maintenance work. Build and measure deterministic
SearXNG mapping first; add a local ranker only if an evaluation set shows a real
quality gain.

This would not be a local installation of ChatGPT. ChatGPT is a hosted product;
the optional component would be a separate open-weight model served through a
local HTTP endpoint and bound to localhost.

## Delivery stages

1. Introduce a provider-neutral `SearchBackend` contract and explicit backend
   metadata without changing the OpenAI path.
2. Add the SearXNG JSON client, configuration, timeouts and fake-client tests.
3. Add deterministic ranking/mapping and comparison fixtures against paid runs.
4. Add direct official-source connectors.
5. Evaluate whether a small local ranker improves precision enough to justify
   its operational cost.

No SearXNG service or local model is installed by the current milestone. That is
intentional: installing services changes the host and should be a separate,
explicit deployment task.
