# Project Brief: stock-agent

## Overview

`stock-agent` is a small LangGraph-based CLI agent for stock research. Its current scope is narrow and practical: take a user question about a company or ticker, turn it into a research plan, gather web and market context, extract evidence, and write a cited research memo.

The repo is positioned as a research assistant, not an execution or trading system. The README explicitly frames the output as research-only and not investment advice.

## Project Purpose and Current Scope

The project is built around a single workflow described in [README.md](../README.md):

- Convert a stock research question into a structured plan.
- Search the web for supporting material.
- Optionally fetch a market snapshot for the first detected ticker.
- Extract evidence notes from retrieved source snippets.
- Decide whether additional search passes are needed.
- Write a final memo with `[S#]` citations.

This is currently a v0-style agent rather than a full platform. It provides:

- One CLI entrypoint: `stock-agent`
- One graph-driven research workflow
- One LLM abstraction layer
- Two tool adapters: web search and market data
- Minimal automated test coverage

It does not currently provide:

- Persistent storage
- A web UI or service API
- Portfolio management or order execution
- Strong source-quality filtering or ranking beyond basic deduplication
- Broad test coverage across edge cases and failure paths

## Runtime Flow

The runtime starts in the package entrypoint defined in [pyproject.toml](../pyproject.toml):

- `stock-agent = "stock_agent.cli:main"`

From there, the flow is:

1. [`src/stock_agent/cli.py`](../src/stock_agent/cli.py) parses CLI arguments and decides whether to run in trace mode, plain mode, or JSON mode.
2. [`src/stock_agent/agent.py`](../src/stock_agent/agent.py) constructs `DeepSearchAgent`, loads environment variables with `dotenv`, and builds the graph.
3. [`src/stock_agent/graphs/deep_search_graph.py`](../src/stock_agent/graphs/deep_search_graph.py) defines and compiles the LangGraph state machine.
4. The agent either:
   - calls `run()` to execute the graph and return the final state, or
   - calls `stream()` to emit step-by-step graph updates for the CLI trace view.

### CLI Behavior

[`src/stock_agent/cli.py`](../src/stock_agent/cli.py) exposes three main runtime modes:

- Default trace mode: streams node-by-node progress panels and then prints the final report.
- `--no-trace`: executes the graph in one shot and prints only the final report.
- `--json`: executes the graph in one shot and prints the full final state as JSON.

The trace panels are lightweight summaries of each node:

- `plan`: topic and subquery count
- `market`: current market snapshot or a "no ticker detected" message
- `search_web`: current source count and delta
- `extract`: evidence note count and delta
- `decide`: whether more search is needed and how many follow-up queries were generated
- `write_report`: final report character count

## Core Architecture

The main architecture lives in [`src/stock_agent/graphs/deep_search_graph.py`](../src/stock_agent/graphs/deep_search_graph.py). The graph compiles a `DeepSearchState` workflow with this shape:

`plan -> market -> search_web -> extract -> decide -> (search_web | write_report)`

This gives the project a simple deep-search loop:

- plan once
- enrich with market context
- search and extract evidence
- decide whether the evidence is sufficient
- either search again or write the memo

### State Model

The shared state is defined as `DeepSearchState` and may include:

- `query`
- `iteration`
- `max_iterations`
- `plan`
- `subqueries`
- `sources`
- `notes`
- `market`
- `need_more`
- `followup_queries`
- `missing_angles`
- `final_report`

The file also defines the structured models used by the graph:

- `ResearchPlan`
- `EvidenceNote`
- `EvidenceNotes`
- `FollowupDecision`

## Graph Nodes and Responsibilities

### 1. `plan`

The `plan_node` turns the raw user query into a `ResearchPlan` with:

- a concise `topic`
- zero or more `tickers`
- a list of `subqueries`
- key assumptions or uncertainties to validate

The prompt explicitly asks for 6-10 English subqueries covering business, competition, financials, valuation, catalysts, risks, regulation, and macro. If structured generation fails, the code falls back to a hardcoded generic plan template.

This node also initializes:

- `iteration = 0`
- empty `sources`
- empty `notes`
- empty follow-up state

### 2. `market`

The `market_node` validates the plan, reads the ticker list, and fetches market data only for the first detected ticker. It uses the `fetch_market_snapshot()` helper from [`src/stock_agent/tools/market_data.py`](../src/stock_agent/tools/market_data.py).

If no ticker is present, the node returns an empty market snapshot object instead of failing.

### 3. `search_web`

The `search_node` chooses which queries to run:

- first pass: `subqueries`
- later passes: `followup_queries` if they exist

For each active query, it calls `web_search()` from [`src/stock_agent/tools/web_search.py`](../src/stock_agent/tools/web_search.py). It then:

- aggregates all returned documents
- picks up to 12 documents using `pick_best_docs()`
- deduplicates against previously stored source URLs
- stores each source with an incremental `id`
- truncates stored content to reduce state size

### 4. `extract`

The `extract_node` converts source snippets into structured evidence notes. It selects up to 8 unseen sources that have content, then prompts the model to produce:

- `claim`
- `why_it_matters`
- `source_id`

The prompt explicitly asks for verifiable evidence and limits output to at most 2 items per source.

If extraction fails, the current fallback is simply an empty note list rather than a hard failure.

### 5. `decide`

The `decide_node` asks the model whether the current evidence is sufficient for a high-quality memo. If not, it asks for 3-6 follow-up search queries and a list of missing angles.

This node increments `iteration` and stores:

- `need_more`
- `followup_queries`
- `missing_angles`

The routing decision is handled by `route_after_decide()`:

- if `need_more` is true and `iteration < max_iterations`, return to `search_web`
- otherwise continue to `write_report`

### 6. `write_report`

The `write_node` asks the model to write a structured English research memo using only the accumulated notes and sources. The prompt requires:

- Executive Summary
- Bull Case
- Bear Case
- Key Catalysts
- Key Risks
- Open Questions
- Sources

It also requires `[S#]` citations after key statements and ends with the disclaimer:

`For research purposes only. Not investment advice.`

## Supporting Modules

### Agent Wrapper

[`src/stock_agent/agent.py`](../src/stock_agent/agent.py) provides the main wrapper class, `DeepSearchAgent`.

Its responsibilities are:

- load `.env` variables with `load_dotenv()`
- construct `AgentConfig`
- build the compiled graph
- provide `run(query)` for full execution
- provide `stream(query)` for update-based execution

The returned `AgentResult` includes:

- `final_report`
- full final `state`

### Configuration

[`src/stock_agent/config.py`](../src/stock_agent/config.py) keeps configuration intentionally small. `AgentConfig` currently exposes:

- `openai_model`
- `max_iterations`
- `max_results_per_query`
- `timeout_s`

These are loaded from environment variables:

- `OPENAI_MODEL`
- `STOCK_AGENT_MAX_ITERATIONS`
- `STOCK_AGENT_MAX_RESULTS`
- `STOCK_AGENT_TIMEOUT_S`

### Model Selection

[`src/stock_agent/llm.py`](../src/stock_agent/llm.py) centralizes model construction with `ChatOpenAI`.

Behavior:

- If `DEEPSEEK_API_KEY` is set, the code uses DeepSeek through the OpenAI-compatible client.
- It reads:
  - `DEEPSEEK_API_KEY`
  - `DEEPSEEK_BASE_URL`
  - `DEEPSEEK_MODEL`
- Otherwise it requires `OPENAI_API_KEY` and uses `OPENAI_MODEL` from config.

This means the project presents a single chat-model abstraction even though it can target two providers.

### Web Search

[`src/stock_agent/tools/web_search.py`](../src/stock_agent/tools/web_search.py) defines a simple `WebDocument` dataclass and two search backends:

- Tavily via `_search_tavily()`
- DuckDuckGo via `_search_duckduckgo()`

The public `web_search()` helper prefers Tavily when `TAVILY_API_KEY` is present and falls back to DuckDuckGo otherwise.

`pick_best_docs()` is intentionally simple. It:

- requires a URL by default
- deduplicates by URL or title
- keeps the first `limit` documents

There is no scoring by credibility, freshness, domain quality, or relevance beyond backend ordering.

### Market Data

[`src/stock_agent/tools/market_data.py`](../src/stock_agent/tools/market_data.py) wraps `yfinance` in a `MarketSnapshot` dataclass with:

- `ticker`
- `currency`
- `price`
- `market_cap`
- `trailing_pe`
- `forward_pe`
- `dividend_yield`

`fetch_market_snapshot()` reads `Ticker.info`, coerces numeric values where possible, and degrades to `None` values when fields are missing or parsing fails.

## Current Limitations and Notable Implementation Quirks

Several details are worth knowing before extending the project.

### 1. Structured output depends on provider path

In the graph builder, `use_structured` is enabled only when `DEEPSEEK_API_KEY` is absent. In practice:

- OpenAI path: uses `with_structured_output(...)`
- DeepSeek path: uses manual JSON prompting plus `_extract_json_object()` and retry logic

That makes schema reliability dependent on which provider is active.

### 2. Extraction coverage is capped per pass

`extract_node` only processes up to 8 unseen sources at a time, while `search_node` may add up to 12 new sources per search pass. This means some newly collected sources may remain unprocessed if the loop stops early.

### 3. Source selection is intentionally shallow

Search results are deduplicated and truncated, but not meaningfully ranked for:

- authority
- primary-vs-secondary source quality
- recency
- conflicting claims

As a result, memo quality depends heavily on search backend output ordering and model judgment.

### 4. Report generation depends on extracted notes

The report prompt is driven primarily by `notes`, not full raw source content. If extraction produces weak or empty notes, the final memo can still be generated, but its evidence base may be thin.

### 5. Only the first ticker gets market data

Even if the plan identifies multiple tickers, the market snapshot is fetched only for the first one.

### 6. Error handling is designed to degrade rather than fail hard

This is pragmatic for a prototype, but it also hides quality issues:

- planning falls back to a generic template
- extraction falls back to empty notes
- decision generation falls back to `need_more = False`

This improves robustness while making silent quality regressions more likely.

### 7. Encoding in existing docs and strings is inconsistent

Some existing Chinese text in `README.md`, CLI help strings, and error messages appears garbled in the current environment. That does not change the core architecture, but it is a documentation and UX quality issue worth fixing separately.

## Testing Status

Automated test coverage is minimal.

[`tests/test_deep_search_graph.py`](../tests/test_deep_search_graph.py) contains one main offline graph test that:

- injects a fake LLM
- stubs `web_search()`
- builds the graph with a small config
- verifies the graph can run without network access
- asserts that a report, one source, and one note are produced

This test is useful as a smoke test, but it does not cover:

- CLI behavior
- real provider integration
- Tavily vs DuckDuckGo fallback differences
- market data edge cases
- multi-iteration loop behavior under realistic evidence gaps
- structured-output parsing failure scenarios
- final report quality or citation consistency

## Dependency Snapshot

From [`pyproject.toml`](../pyproject.toml), the main runtime dependencies are:

- `langgraph`
- `langchain-core`
- `langchain-openai`
- `pydantic`
- `python-dotenv`
- `tavily-python`
- `duckduckgo-search`
- `yfinance`
- `rich`

Development dependencies currently include only:

- `pytest`

This aligns with the overall design: a lightweight CLI research agent with a small implementation surface.

## Practical Read of the Repo

If you want to understand the project quickly, these are the highest-value files to read in order:

1. [README.md](../README.md)
2. [`src/stock_agent/cli.py`](../src/stock_agent/cli.py)
3. [`src/stock_agent/agent.py`](../src/stock_agent/agent.py)
4. [`src/stock_agent/graphs/deep_search_graph.py`](../src/stock_agent/graphs/deep_search_graph.py)
5. [`src/stock_agent/tools/web_search.py`](../src/stock_agent/tools/web_search.py)
6. [`src/stock_agent/tools/market_data.py`](../src/stock_agent/tools/market_data.py)
7. [`tests/test_deep_search_graph.py`](../tests/test_deep_search_graph.py)

## Summary

`stock-agent` is a focused research-agent prototype with a clear graph-driven design and a small codebase. Its main strengths are simplicity, readability, and an explicit research workflow. Its main weaknesses are shallow evidence handling, provider-dependent structured output behavior, limited source-quality control, and minimal testing.

That makes it a good foundation for experimentation and iteration, but not yet a production-grade investment research system.
