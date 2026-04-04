# Stock Agent

`stock-agent` is a LangGraph-based research assistant for stock analysis. It turns a free-form research question into a structured workflow that plans the task, gathers market and web context, extracts evidence, decides whether more research is needed, and produces a cited English memo.

This project is for research support only. It is not investment advice.

## What It Includes

- A CLI for running end-to-end research jobs locally
- A FastAPI-based Analyst Workbench web app with live step updates
- A LangGraph workflow with the pipeline:
  - `plan -> market -> search_web -> extract -> decide -> write_report`
- Concurrent web search across multiple providers with DuckDuckGo fallback
- Market snapshot enrichment via `yfinance`
- Shared step summaries for both CLI output and the web UI

## MVP Workbench Scope

The current web app follows the MVP Analyst Workbench plan:

- Single-process FastAPI app
- Server-rendered HTML with Jinja templates
- Vanilla JS frontend
- SSE streaming for step-level progress
- In-memory run store only
- One active run at a time

What is intentionally not included yet:

- Authentication
- Database persistence
- Background workers
- Multi-user concurrency
- Run history
- Cancel or retry controls

## Architecture

Core workflow:

1. `plan`: build a structured research plan from the user query
2. `market`: fetch market snapshots for detected tickers
3. `search_web`: collect web sources from configured search providers
4. `extract`: turn source content into evidence notes
5. `decide`: judge whether evidence is sufficient or more search is needed
6. `write_report`: generate the final English memo with `[S#]` citations

The web workbench adds a thin API layer on top of the agent:

- `GET /`: Analyst Workbench UI
- `POST /api/runs`: create a run
- `GET /api/runs/{run_id}`: fetch the latest run snapshot
- `GET /api/runs/{run_id}/events`: stream SSE events
- `GET /api/health`: health check

## Project Layout

```text
src/stock_agent/
  agent.py                 Agent entrypoint
  cli.py                   CLI command
  config.py                Runtime config from environment
  event_adapter.py         Shared step summaries and UI events
  llm.py                   OpenAI / DeepSeek model wiring
  graphs/deep_search_graph.py
                           LangGraph workflow
  tools/web_search.py      Web search providers and fallback logic
  tools/market_data.py     Market snapshot fetcher
  web/app.py               FastAPI app
  web/templates/index.html Analyst Workbench page
  web/static/              Frontend assets
tests/                     API, SSE, UI, and graph tests
docs/                      Project notes and brief
```

## Requirements

- Python 3.9+
- Recommended: Python 3.11 or 3.12 for the smoothest dependency compatibility
- An LLM API key:
  - `OPENAI_API_KEY`, or
  - `DEEPSEEK_API_KEY`
- Optional search API keys for better retrieval quality:
  - `TAVILY_API_KEY`
  - `SERPER_API_KEY`
  - `SERPAPI_KEY`

If no search API key is provided, the project falls back to DuckDuckGo.

## Setup

Examples below use PowerShell. On macOS or Linux, replace the activation step with `source .venv/bin/activate`.

1. Create a virtual environment.

```powershell
python -m venv .venv
```

2. Activate it.

```powershell
.\.venv\Scripts\Activate.ps1
```

3. Upgrade `pip`.

```powershell
python -m pip install --upgrade pip
```

4. Install the project in editable mode.

```powershell
python -m pip install -e ".[dev]"
```

## Environment Variables

Create a `.env` file in the project root or export these variables in your shell.

Minimal OpenAI setup:

```env
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini
```

Alternative DeepSeek setup:

```env
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

Optional search providers:

```env
TAVILY_API_KEY=your_key_here
SERPER_API_KEY=your_key_here
SERPAPI_KEY=your_key_here
```

Optional agent tuning:

```env
STOCK_AGENT_MAX_ITERATIONS=2
STOCK_AGENT_MAX_RESULTS=5
STOCK_AGENT_TIMEOUT_S=25
```

Notes:

- If `DEEPSEEK_API_KEY` is set, the code prefers DeepSeek over OpenAI.
- `.env` is loaded automatically by the agent at runtime.

## Run The CLI

1. Activate the virtual environment.
2. Make sure your `.env` file is present.
3. Run the CLI with a research question.

```powershell
stock-agent "Deep dive on NVDA: key catalysts and risks over the next 6-12 months"
```

Useful variants:

```powershell
stock-agent --no-trace "Analyze Microsoft cloud growth durability"
stock-agent --json "Compare AMD and NVDA AI positioning"
```

CLI modes:

- Default: prints step-by-step summaries and then the final memo
- `--no-trace`: prints only the final memo
- `--json`: prints the final graph state as JSON

## Run The Backend And Web UI

The backend API and the Analyst Workbench UI are served by the same FastAPI process.

1. Activate the virtual environment.
2. Confirm your `.env` file is configured.
3. Start the FastAPI app with Uvicorn.

```powershell
uvicorn stock_agent.web.app:app --reload
```

4. Open the workbench in your browser:

```text
http://127.0.0.1:8000/
```

5. Enter a research query and submit the form.
6. Watch the step timeline update live as the run progresses.
7. Read the rendered final memo once the run completes.

Useful URLs:

- UI: `http://127.0.0.1:8000/`
- Health check: `http://127.0.0.1:8000/api/health`

Important behavior:

- Only one active run is allowed at a time
- Run state is stored in memory only
- Restarting the server clears in-memory runs

## Run The API Directly

You can also use the backend without the browser UI.

1. Start the server:

```powershell
uvicorn stock_agent.web.app:app --reload
```

2. Create a run:

```http
POST /api/runs
Content-Type: application/json

{ "query": "Analyze Tesla margin durability" }
```

3. Stream step events from:

```text
GET /api/runs/{run_id}/events
```

4. Fetch the latest snapshot from:

```text
GET /api/runs/{run_id}
```

Event types emitted by the workbench API:

- `run_started`
- `step_completed`
- `run_completed`
- `run_failed`

## Output Shape

The final memo is written in English and is designed to include:

- Executive Summary
- Bull Case
- Bear Case
- Key Catalysts
- Key Risks
- Open Questions
- Sources

Important claims should include inline `[S#]` citations.

## Testing

Run the test suite with:

```powershell
pytest
```

Current tests cover:

- API run creation and validation
- SSE event streaming
- Snapshot behavior
- Workbench page rendering
- Event summarization
- Deep search graph behavior

## Known Limitations

- The app is an MVP and supports only one active run
- Run storage is in memory only
- Market enrichment is currently shallow compared with a full research terminal
- Source quality still depends heavily on search provider results
- The system is designed to degrade gracefully, so low-quality intermediate evidence can still produce a final memo

## Python API

If you want to use the agent directly in code:

```python
from stock_agent.agent import DeepSearchAgent

agent = DeepSearchAgent()
result = agent.run("Analyze Amazon AWS margin durability")
print(result.final_report)
```

## License

No license file is currently included in this repository.
