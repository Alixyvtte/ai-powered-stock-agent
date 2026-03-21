# Project Brief: stock-agent

## Overview

`stock-agent` is a small LangGraph-based CLI agent for stock research. Its current responsibility is narrow and practical: accept a research question, generate a structured plan, gather web and market context, extract evidence, and write a cited English memo.

The project is a research assistant, not a trading system. The README positions it as research-only and explicitly excludes investment advice.

This document merges the earlier high-level project brief with the more detailed interface notes from `docs/interface.md`, and rewrites the combined content in English.

## What the Project Exposes

The repository does not currently expose an HTTP, REST, gRPC, or WebSocket service. The usable interfaces today are:

1. A CLI command: `stock-agent`
2. A Python API centered on `stock_agent.agent.DeepSearchAgent`
3. Tool-level helper functions for web search and market data

That means the project is best understood as a local library-plus-CLI application rather than a networked service.

## Runtime Architecture

The package entrypoint is defined in [pyproject.toml](../pyproject.toml):

- `stock-agent = "stock_agent.cli:main"`

The runtime path is:

1. [`src/stock_agent/cli.py`](../src/stock_agent/cli.py) parses arguments and decides how output should be rendered.
2. [`src/stock_agent/agent.py`](../src/stock_agent/agent.py) creates `DeepSearchAgent`, loads environment variables, and builds the graph.
3. [`src/stock_agent/graphs/deep_search_graph.py`](../src/stock_agent/graphs/deep_search_graph.py) defines and compiles the LangGraph workflow.
4. Supporting modules provide model access, web search, and market data lookup.

The compiled workflow is:

`plan -> market -> search_web -> extract -> decide -> (search_web | write_report)`

This gives the project one explicit deep-search loop:

- plan once
- optionally enrich with market data
- collect sources
- extract evidence
- decide whether evidence is sufficient
- either search again or write the final memo

## Public Interfaces

### CLI Interface

The installed command is:

```bash
stock-agent <query> [--json] [--no-trace]
```

Behavior by mode:

- Default mode calls `agent.stream(query)`, prints trace panels for each node, and then prints the final memo.
- `--no-trace` calls `agent.run(query)` and prints only the final memo.
- `--json` calls `agent.run(query)` and prints the final graph state as JSON.
- If both `--json` and `--no-trace` are supplied, the code path is effectively driven by `--json`.

Exit behavior:

- `0` on success
- `1` if execution raises an exception

The trace view summarizes these graph nodes:

- `plan`
- `market`
- `search_web`
- `extract`
- `decide`
- `write_report`

### Python Interface

The main Python entrypoint is `DeepSearchAgent` in [`src/stock_agent/agent.py`](../src/stock_agent/agent.py).

Constructor:

```python
DeepSearchAgent(config: Optional[AgentConfig] = None)
```

Behavior:

- calls `load_dotenv()`
- uses `AgentConfig.from_env()` when no config is passed
- builds the LangGraph workflow once during initialization

Primary methods:

```python
run(query: str) -> AgentResult
stream(query: str) -> Iterator[Tuple[str, Dict[str, Any]]]
```

`run()` returns:

```python
AgentResult(
    final_report: str,
    state: Dict[str, Any],
)
```

`stream()` yields:

```python
(node_name: str, update_dict: Dict[str, Any])
```

In normal operation, `node_name` is the graph node name. If LangGraph returns a non-standard event shape, the wrapper falls back to:

```python
("event", {"_raw": ...})
```

### Tool-Level Interfaces

The repo also exposes two direct helper modules:

- [`src/stock_agent/tools/web_search.py`](../src/stock_agent/tools/web_search.py)
- [`src/stock_agent/tools/market_data.py`](../src/stock_agent/tools/market_data.py)

These are not formal service APIs, but they are stable enough to be treated as internal extension points.

## Configuration and Environment Variables

### AgentConfig

[`src/stock_agent/config.py`](../src/stock_agent/config.py) defines:

```python
AgentConfig(
    openai_model: str = "gpt-4o-mini",
    max_iterations: int = 2,
    max_results_per_query: int = 5,
    timeout_s: int = 25,
)
```

These values can be passed directly or loaded from environment variables with `AgentConfig.from_env()`.

### Model Configuration

Supported environment variables:

- `DEEPSEEK_API_KEY`
  - If present, DeepSeek is preferred over OpenAI.
- `DEEPSEEK_MODEL`
  - DeepSeek model name.
  - Falls back to `OPENAI_MODEL`, which itself defaults to `gpt-4o-mini`.
- `DEEPSEEK_BASE_URL`
  - Defaults to `https://api.deepseek.com/v1`.
- `OPENAI_API_KEY`
  - Required only when `DEEPSEEK_API_KEY` is absent.
- `OPENAI_MODEL`
  - Defaults to `gpt-4o-mini`.

The model client is built in [`src/stock_agent/llm.py`](../src/stock_agent/llm.py) using `ChatOpenAI`, including the DeepSeek path via an OpenAI-compatible base URL.

### Search and Execution Configuration

Additional environment variables:

- `SERPAPI_KEY`
  - If present, web search prioritizes Google SerpApi.
- `TAVILY_API_KEY`
  - If present, web search includes Tavily.
- `SERPER_API_KEY`
  - If present, web search includes Google Serper (as a fallback for SerpApi).
- Fallback:
  - If no API keys are provided, the code falls back to DuckDuckGo.
- `STOCK_AGENT_MAX_ITERATIONS`
  - Maximum allowed `search_web -> extract -> decide` loop count.
  - Default: `2`
- `STOCK_AGENT_MAX_RESULTS`
  - Maximum raw results fetched per query.
  - Default: `5`
- `STOCK_AGENT_TIMEOUT_S`
  - Search timeout setting passed through the graph.
  - Default: `25`

One implementation detail matters here: `timeout_s` is forwarded into `web_search(...)`, but the current search backends do not enforce a strong explicit timeout contract themselves.

## Data Models and State Contract

### Dataclasses

The main frozen dataclasses in the repo are:

| Name | Location | Purpose |
| --- | --- | --- |
| `AgentConfig` | `src/stock_agent/config.py` | top-level runtime configuration |
| `AgentResult` | `src/stock_agent/agent.py` | return type of `DeepSearchAgent.run()` |
| `WebDocument` | `src/stock_agent/tools/web_search.py` | normalized web search result |
| `MarketSnapshot` | `src/stock_agent/tools/market_data.py` | normalized market data snapshot |

There is also one test-only dataclass:

| Name | Location | Purpose |
| --- | --- | --- |
| `_Msg` | `tests/test_deep_search_graph.py` | fake LLM message container for tests |

### Pydantic and TypedDict Models

The graph logic also relies on non-dataclass schema objects:

| Name | Type | Purpose |
| --- | --- | --- |
| `ResearchPlan` | `Pydantic BaseModel` | output schema for planning |
| `EvidenceNote` | `Pydantic BaseModel` | one evidence item |
| `EvidenceNotes` | `Pydantic BaseModel` | batch container for extraction |
| `FollowupDecision` | `Pydantic BaseModel` | decision output after evidence review |
| `DeepSearchState` | `TypedDict` | logical shape of graph state |

### DeepSearchState

The graph state may contain:

```python
{
    "query": str,
    "iteration": int,
    "max_iterations": int,
    "plan": dict,
    "subqueries": list[str],
    "sources": list[dict],
    "notes": list[dict],
    "market": dict,
    "need_more": bool,
    "followup_queries": list[str],
    "missing_angles": list[str],
    "final_report": str,
}
```

Important distinction: `DeepSearchState` is only the type definition. The actual runtime state is a regular Python `dict` managed by LangGraph.

There are effectively three views of state in the project:

1. The `TypedDict` schema in the graph file
2. The real runtime `dict` LangGraph mutates across nodes
3. A CLI-side shadow `state` rebuilt from streamed updates to print before/after summaries

Only the second one is authoritative.

## Workflow and Node Contracts

### `plan`

Responsibility:

- convert a raw user question into a `ResearchPlan`

Primary input:

```python
{"query": str}
```

Typical output:

```python
{
    "plan": {
        "topic": str,
        "tickers": list[str],
        "subqueries": list[str],
        "assumptions": list[str],
    },
    "subqueries": list[str],
    "iteration": 0,
    "need_more": False,
    "followup_queries": [],
    "missing_angles": [],
    "sources": [],
    "notes": [],
}
```

Current behavior:

- asks for 6-10 English subqueries
- truncates `subqueries` to at most 10
- falls back to a generic hardcoded plan if structured generation fails

### `market`

Responsibility:

- fetch a market snapshot for the first detected ticker

Primary input:

```python
{
    "plan": {
        "tickers": list[str],
    }
}
```

Output:

```python
{
    "market": {
        "ticker": str,
        "currency": str | None,
        "price": float | None,
        "market_cap": float | None,
        "trailing_pe": float | None,
        "forward_pe": float | None,
        "dividend_yield": float | None,
    }
}
```

or:

```python
{"market": {}}
```

Current behavior:

- uses only `tickers[0]`
- does not fail if no ticker is found

### `search_web`

Responsibility:

- execute search queries and append normalized sources to state

Primary input:

```python
{
    "iteration": int,
    "subqueries": list[str],
    "followup_queries": list[str],
    "sources": list[dict],
}
```

Query selection rules:

- if `iteration == 0`, use `subqueries`
- if `iteration > 0` and `followup_queries` is non-empty, prefer `followup_queries`

Output:

```python
{
    "sources": [
        {
            "id": int,
            "title": str,
            "url": str,
            "content": str,
        }
    ]
}
```

Current behavior:

- aggregates raw search results across all active queries
- keeps at most 12 selected docs per pass
- deduplicates new sources by URL against existing state
- truncates stored source content to roughly 2400 characters

### `extract`

Responsibility:

- convert source snippets into evidence notes

Primary input:

```python
{
    "sources": [
        {
            "id": int,
            "title": str,
            "url": str,
            "content": str,
        }
    ],
    "notes": [
        {
            "source_id": int,
            "claim": str,
            "why_it_matters": str,
        }
    ],
}
```

Output when work is available:

```python
{
    "notes": [
        {
            "source_id": int,
            "claim": str,
            "why_it_matters": str,
        }
    ]
}
```

Output when there is no eligible batch:

```python
{}
```

Current behavior:

- processes at most 8 previously unseen sources per pass
- merges newly generated notes with existing notes
- asks the model for no more than 2 items per source, but that limit is prompt-level rather than code-enforced
- falls back to an empty extraction result on failure

### `decide`

Responsibility:

- judge whether the evidence is sufficient for a memo
- generate follow-up search queries if not

Primary input:

```python
{
    "iteration": int,
    "plan": {...},
    "notes": [...],
    "sources": [...],
    "market": {...},
}
```

Output:

```python
{
    "need_more": bool,
    "followup_queries": list[str],
    "missing_angles": list[str],
    "iteration": int,
}
```

Current behavior:

- increments `iteration` by 1
- generates 3-6 follow-up queries when evidence is insufficient
- falls back to:

```python
{
    "need_more": False,
    "followup_queries": [],
    "missing_angles": ["structured parsing failed"],
    "iteration": current_iteration + 1,
}
```

### `write_report`

Responsibility:

- produce the final English memo

Primary input:

```python
{
    "plan": {...},
    "notes": [...],
    "sources": [...],
    "market": {...},
}
```

Output:

```python
{"final_report": str}
```

Current behavior:

- requires an English memo
- requires the following sections:
  - Executive Summary
  - Bull Case
  - Bear Case
  - Key Catalysts
  - Key Risks
  - Open Questions
  - Sources
- requires `[S#]` citations after key claims
- ends with the research-only disclaimer

### Routing Logic

The routing function is:

```python
route_after_decide(state: DeepSearchState) -> str
```

Input:

```python
{
    "need_more": bool,
    "iteration": int,
    "max_iterations": int,
}
```

Output:

- `"search_web"` when more evidence is needed and the loop limit has not been reached
- `"write_report"` otherwise

## State Lifecycle and Merge Semantics

The project does not use a special append-aware state reducer for `sources` or `notes`. In practice, the semantics are close to:

```python
current_state = {...}
update = node(current_state)
current_state = {**current_state, **update}
```

That means simple scalar fields are overwritten directly by node output. For list-like fields such as `sources` and `notes`, the append logic is implemented manually inside the node itself.

Current ownership looks like this:

| Field | Merge Behavior | Owned By |
| --- | --- | --- |
| `plan` | overwrite/write once | `plan_node` |
| `subqueries` | overwrite/write once | `plan_node` |
| `iteration` | overwrite | `plan_node`, `decide_node` |
| `need_more` | overwrite | `plan_node`, `decide_node` |
| `followup_queries` | overwrite | `plan_node`, `decide_node` |
| `missing_angles` | overwrite | `plan_node`, `decide_node` |
| `market` | overwrite | `market_node` |
| `final_report` | overwrite/write once | `write_node` |
| `sources` | manual merge, then overwrite full list | `search_node` |
| `notes` | manual merge, then overwrite full list | `extract_node` |

This is important when extending the graph. For example:

- `search_node` currently returns `{"sources": existing + new_sources}`
- `extract_node` currently returns `{"notes": merged}`

If a future change returned only incremental results, the old values would be replaced rather than automatically appended.

## Supporting Modules

### Model Layer

[`src/stock_agent/llm.py`](../src/stock_agent/llm.py) creates the chat model. The implementation uses:

- OpenAI directly when `OPENAI_API_KEY` is available and DeepSeek is not configured
- DeepSeek through an OpenAI-compatible base URL when `DEEPSEEK_API_KEY` is present

One key implementation detail is in the graph builder:

- OpenAI path uses `with_structured_output(...)`
- DeepSeek path uses manual JSON prompting and `_extract_json_object()`

So structured-output reliability is provider-dependent.

### Web Search Layer

[`src/stock_agent/tools/web_search.py`](../src/stock_agent/tools/web_search.py) defines:

```python
web_search(query: str, max_results: int = 5, timeout_s: int = 25) -> List[WebDocument]
```

Behavior:

- Executes search concurrently prioritizing fast providers (SerpApi, Tavily, Serper).
- Utilizes an aggressive fast-timeout truncation mechanism (cutting off at ~10s) to guarantee high-speed agent execution over exhaustive retrieval.
- Interleaves results to maintain original provider relevance ranking.
- Falls back to DuckDuckGo if no search API keys are available.
- Normalizes all backend results into `WebDocument`.

The helper:

```python
pick_best_docs(
    docs: List[WebDocument],
    *,
    limit: int = 6,
    require_url: bool = True,
) -> List[WebDocument]
```

works by:

- preserving input order
- deduplicating by URL or title
- optionally requiring a URL
- stopping at `limit`

There is no authority or freshness scoring beyond backend order.

### Market Data Layer

[`src/stock_agent/tools/market_data.py`](../src/stock_agent/tools/market_data.py) defines:

```python
fetch_market_snapshot(ticker: str) -> MarketSnapshot
```

Behavior:

- reads from `yfinance.Ticker(ticker).info`
- attempts to normalize:
  - `currency`
  - `currentPrice` or `regularMarketPrice`
  - `marketCap`
  - `trailingPE`
  - `forwardPE`
  - `dividendYield`
- degrades to `None` values when parsing fails
- degrades to an empty `info` dict when `t.info` raises

## Testing Status

Automated test coverage is minimal.

[`tests/test_deep_search_graph.py`](../tests/test_deep_search_graph.py) provides an offline smoke test that:

- injects a fake LLM
- stubs `web_search()`
- builds the graph with a small config
- runs the graph without network access
- asserts that a report, one source, and one note are produced

The test is useful, but it does not cover:

- CLI rendering behavior
- real OpenAI or DeepSeek integration
- Tavily vs DuckDuckGo behavior differences
- market data edge cases
- multi-iteration loop behavior in realistic failure scenarios
- parsing failures across all schema types
- report quality or citation consistency

## Current Limitations and Design Notes

Several limitations are structural, not accidental.

### No web service layer

The repo does not currently include:

- a REST API
- a FastAPI or Flask wrapper
- OpenAPI or Swagger docs
- a WebSocket streaming interface

If the project later needs networked access, a service layer should be added on top of `DeepSearchAgent` rather than mixed directly into graph logic.

### Output language is intentionally English

Even though some local docs and CLI strings include Chinese, the prompts explicitly require English output for:

- planning
- extraction
- decision-making
- final memo writing

### The system prefers robustness over strict quality control

The graph is designed to degrade rather than fail hard:

- planning can fall back to a generic plan
- extraction can fall back to empty notes
- decision-making can fall back to a terminal decision

This keeps the workflow runnable, but it also means low-quality intermediate results can still produce a final memo.

### Source handling is shallow

Search results are deduplicated and truncated, but not strongly filtered by:

- primary vs. secondary sources
- domain quality
- recency
- conflict resolution

That makes output quality sensitive to backend result ordering.

### Market enrichment is single-ticker only

Only the first ticker in `plan.tickers` is used for market data.

### State is in-memory only

There is no persistence, resume support, or checkpointing. During `run()` and `stream()`, the state exists only in memory unless the caller saves the returned result explicitly.

### Package exports are small

The package root exports very little, so the stable import style is:

```python
from stock_agent.agent import DeepSearchAgent
from stock_agent.config import AgentConfig
```

rather than relying on root-level re-exports.

## Dependency Snapshot

The main runtime dependencies from [pyproject.toml](../pyproject.toml) are:

- `langgraph`
- `langchain-core`
- `langchain-openai`
- `pydantic`
- `python-dotenv`
- `tavily-python`
- `duckduckgo-search`
- `yfinance`
- `rich`

Development dependencies currently include:

- `pytest`

This matches the project’s shape: a lightweight graph-driven research agent with a CLI-first surface.

**Note:** The search module leverages the built-in `concurrent.futures` and `requests` for fast, parallel search with timeout truncation across providers like SerpApi, Tavily, and Serper.

## Practical Reading Order

For a fast understanding of the repo, read these files in order:

1. [README.md](../README.md)
2. [`src/stock_agent/cli.py`](../src/stock_agent/cli.py)
3. [`src/stock_agent/agent.py`](../src/stock_agent/agent.py)
4. [`src/stock_agent/graphs/deep_search_graph.py`](../src/stock_agent/graphs/deep_search_graph.py)
5. [`src/stock_agent/tools/web_search.py`](../src/stock_agent/tools/web_search.py)
6. [`src/stock_agent/tools/market_data.py`](../src/stock_agent/tools/market_data.py)
7. [`tests/test_deep_search_graph.py`](../tests/test_deep_search_graph.py)

## Summary

`stock-agent` is a compact research-agent prototype with a clear graph-driven architecture and a small implementation surface. Its strengths are simplicity, readability, and explicit workflow design. Its current weak points are shallow source selection, provider-dependent structured output behavior, limited evidence processing depth, lack of persistence, and minimal tests.

It is a solid base for iteration, but it is not yet a production-grade investment research platform or service API.
