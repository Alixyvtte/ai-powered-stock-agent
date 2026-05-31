from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, Callable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from stock_agent.agent import DeepSearchAgent
from stock_agent.config import AgentConfig, normalize_mode
from stock_agent.event_adapter import build_run_failed_event

from .export import build_report_markdown, markdown_to_pdf_bytes, safe_filename
from .presentation import build_run_presentation, render_final_report_html
from .runs import InMemoryRunStore, RunStatus, ActiveRunConflictError


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
TERMINAL_EVENT_TYPES = {"run_completed", "run_failed"}


def _content_disposition(slug: str, ext: str) -> str:
    """Build a Content-Disposition header that survives non-ASCII filenames.

    Provides an ASCII ``filename=`` fallback plus an RFC 5987 ``filename*=``
    UTF-8 variant so Chinese report names download cleanly across browsers.
    """
    ascii_slug = slug.encode("ascii", "ignore").decode("ascii").strip("-") or "stock-research"
    utf8_name = quote(f"{slug}.{ext}")
    return f'attachment; filename="{ascii_slug}.{ext}"; filename*=UTF-8\'\'{utf8_name}'


def _report_slug(run: Any) -> str:
    plan = run.snapshot.get("plan") if isinstance(run.snapshot, dict) else None
    topic = plan.get("topic") if isinstance(plan, dict) else None
    return safe_filename(run.query or topic or "")


def _build_markdown_for_run(run: Any) -> str:
    snapshot = run.snapshot if isinstance(run.snapshot, dict) else {}
    plan = snapshot.get("plan") if isinstance(snapshot.get("plan"), dict) else {}
    return build_report_markdown(
        query=run.query,
        topic=(plan or {}).get("topic"),
        final_report=run.final_report or "",
        evidence_confidence=snapshot.get("evidence_confidence"),
        generated_at=run.finished_at.isoformat() if run.finished_at else None,
    )


class CreateRunRequest(BaseModel):
    query: str
    mode: str | None = None  # fast | standard | deep (speed/quality preset)


class CreateRunResponse(BaseModel):
    run_id: str
    status: RunStatus


class MarketHighlightResponse(BaseModel):
    ticker: str
    source: str | None = None
    currency: str | None = None
    price: float | int | None = None
    market_cap: float | int | None = None
    trailing_pe: float | int | None = None
    forward_pe: float | int | None = None
    dividend_yield: float | int | None = None


class RunSnapshotResponse(BaseModel):
    run_id: str
    query: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latest_node: str | None = None
    duration_s: float | None = None
    snapshot: dict[str, Any]
    summaries: dict[str, dict[str, Any]]
    final_report: str | None = None
    final_report_html: str | None = None
    evidence_confidence: str | None = None
    followup_history: list[str] = Field(default_factory=list)
    market_highlights: list[MarketHighlightResponse] = Field(default_factory=list)
    error: str | None = None


def create_app(
    run_store: InMemoryRunStore | None = None,
    agent_factory: Callable[[], DeepSearchAgent] | None = None,
) -> FastAPI:
    app = FastAPI(title="Stock Agent Analyst Workbench")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    store = run_store or InMemoryRunStore()
    build_agent = agent_factory or DeepSearchAgent

    app.state.run_store = store
    app.state.agent_factory = build_agent

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def encode_sse_event(event: dict[str, Any]) -> bytes:
        event_type = str(event.get("type") or "message")
        payload = json.dumps(event, ensure_ascii=False)
        return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")

    def run_in_background(run_id: str) -> None:
        run = store.get_run(run_id)
        if run is None:
            return

        try:
            # The default factory honours the run's speed/quality preset; an
            # injected factory (e.g. in tests) is used as-is.
            if build_agent is DeepSearchAgent:
                agent = DeepSearchAgent(AgentConfig.from_env(mode=run.mode))
            else:
                agent = build_agent()
            for event in agent.stream_events(run.query):
                if str(event.get("type") or "") == "run_completed":
                    event = {
                        **event,
                        "final_report_html": render_final_report_html(str(event.get("final_report") or "")),
                    }
                store.append_event(run_id, event)
        except Exception as exc:
            latest_run = store.get_run(run_id)
            if latest_run is None or latest_run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                return
            store.append_event(
                run_id,
                build_run_failed_event(
                    str(exc),
                    latest_run.snapshot,
                    node=latest_run.latest_node,
                ),
            )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/api/runs",
        response_model=CreateRunResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_run(payload: CreateRunRequest) -> CreateRunResponse:
        try:
            run = store.create_run(payload.query, mode=normalize_mode(payload.mode))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ActiveRunConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        return CreateRunResponse(run_id=run.run_id, status=run.status)

    @app.get("/api/runs/{run_id}", response_model=RunSnapshotResponse)
    def get_run_snapshot(run_id: str) -> RunSnapshotResponse:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")

        presentation = build_run_presentation(
            status=run.status,
            final_report=run.final_report,
            snapshot=run.snapshot,
            summaries=run.summaries,
            events=run.events,
        )

        duration_s: float | None = None
        if run.started_at is not None:
            end = run.finished_at or run.updated_at
            if end is not None:
                duration_s = round(max((end - run.started_at).total_seconds(), 0.0), 2)

        return RunSnapshotResponse(
            run_id=run.run_id,
            query=run.query,
            status=run.status,
            created_at=run.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            latest_node=run.latest_node,
            duration_s=duration_s,
            snapshot=run.snapshot,
            summaries=run.summaries,
            final_report=run.final_report,
            final_report_html=presentation.final_report_html,
            evidence_confidence=presentation.evidence_confidence,
            followup_history=presentation.followup_history,
            market_highlights=[
                MarketHighlightResponse(
                    ticker=highlight.ticker,
                    source=highlight.source,
                    currency=highlight.currency,
                    price=highlight.price,
                    market_cap=highlight.market_cap,
                    trailing_pe=highlight.trailing_pe,
                    forward_pe=highlight.forward_pe,
                    dividend_yield=highlight.dividend_yield,
                )
                for highlight in presentation.market_highlights
            ],
            error=run.error,
        )

    def _require_downloadable_run(run_id: str):
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
        if run.status is not RunStatus.COMPLETED or not (run.final_report or "").strip():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The report is not ready for download yet.",
            )
        return run

    @app.get("/api/runs/{run_id}/report.md")
    def download_report_markdown(run_id: str) -> Response:
        run = _require_downloadable_run(run_id)
        markdown_doc = _build_markdown_for_run(run)
        return Response(
            content=markdown_doc,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": _content_disposition(_report_slug(run), "md")},
        )

    @app.get("/api/runs/{run_id}/report.pdf")
    def download_report_pdf(run_id: str) -> Response:
        run = _require_downloadable_run(run_id)
        markdown_doc = _build_markdown_for_run(run)
        try:
            pdf_bytes = markdown_to_pdf_bytes(markdown_doc)
        except Exception as exc:  # pragma: no cover - depends on fpdf2/runtime fonts
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"PDF generation failed: {exc}",
            ) from exc
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": _content_disposition(_report_slug(run), "pdf")},
        )

    @app.get("/api/runs/{run_id}/events")
    def stream_run_events(run_id: str) -> StreamingResponse:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")

        _, should_start = store.start_run(run_id)
        if should_start:
            worker = Thread(target=run_in_background, args=(run_id,), daemon=True)
            worker.start()

        def event_stream():
            next_index = 0
            while True:
                try:
                    events = store.wait_for_events(run_id, next_index)
                except KeyError as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.") from exc

                if not events:
                    return

                for event in events:
                    next_index += 1
                    yield encode_sse_event(event)
                    if str(event.get("type") or "") in TERMINAL_EVENT_TYPES:
                        return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "page_title": "Stock Agent Analyst Workbench",
                "steps": [
                    "plan",
                    "market",
                    "search_web",
                    "fetch_content",
                    "extract",
                    "decide",
                    "synthesize",
                    "write_report",
                    "verify",
                ],
            },
        )

    return app


app = create_app()
