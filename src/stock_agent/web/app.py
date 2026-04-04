from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .runs import ActiveRunConflictError, InMemoryRunStore, RunStatus


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


class CreateRunRequest(BaseModel):
    query: str


class CreateRunResponse(BaseModel):
    run_id: str
    status: RunStatus


def create_app(run_store: InMemoryRunStore | None = None) -> FastAPI:
    app = FastAPI(title="Stock Agent Analyst Workbench")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    store = run_store or InMemoryRunStore()

    app.state.run_store = store

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
            run = store.create_run(payload.query)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ActiveRunConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        return CreateRunResponse(run_id=run.run_id, status=run.status)

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
                    "extract",
                    "decide",
                    "write_report",
                ],
            },
        )

    return app


app = create_app()
