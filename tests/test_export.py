from __future__ import annotations

from fastapi.testclient import TestClient

from stock_agent.web.app import create_app
from stock_agent.web.export import (
    build_report_markdown,
    markdown_to_pdf_bytes,
    safe_filename,
)
from stock_agent.web.runs import InMemoryRunStore


SAMPLE_REPORT = (
    "## Executive Summary\n"
    "Research as of 2026-05-31.\n"
    "NVDA benefits from **AI demand**, with data-center revenue up sharply. [S1]\n\n"
    "## Key Risks\n"
    "- Supply constraints could cap shipments [S2]\n"
    "- Valuation is sensitive to earnings misses\n\n"
    "## Sources\n"
    "- [S1] Reuters\n\n"
    "For research purposes only. Not investment advice."
)


def _completed_client():
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))
    run = store.create_run("Deep dive on NVDA: catalysts & risks")
    store.complete_run(
        run.run_id,
        final_report=SAMPLE_REPORT,
        snapshot={
            "query": "Deep dive on NVDA: catalysts & risks",
            "plan": {"topic": "NVDA deep dive"},
            "evidence_confidence": "medium",
            "final_report": SAMPLE_REPORT,
        },
    )
    return client, store, run.run_id


# ── export module units ──────────────────────────────────────


def test_build_report_markdown_includes_metadata_and_body():
    md = build_report_markdown(
        query="question about NVDA",
        topic="NVDA",
        final_report="## Body\nSome text.",
        evidence_confidence="high",
        generated_at="2026-05-31T00:00:00Z",
    )
    assert md.startswith("# NVDA")
    assert "**Query:** question about NVDA" in md
    assert "**Evidence confidence:** high" in md
    assert "**Generated:** 2026-05-31T00:00:00Z" in md
    assert "## Body" in md


def test_build_report_markdown_handles_empty_report():
    md = build_report_markdown(query="q", topic=None, final_report="")
    assert "# q" in md
    assert "No report content" in md


def test_safe_filename_variants():
    assert safe_filename("Deep dive on NVDA: catalysts & risks!") == "deep-dive-on-nvda-catalysts-risks"
    assert safe_filename("") == "stock-research"
    assert safe_filename("   ") == "stock-research"
    # CJK characters are preserved (downloaded with RFC 5987 encoding)
    assert safe_filename("英伟达研究") == "英伟达研究"


def test_markdown_to_pdf_bytes_returns_valid_pdf():
    pdf = markdown_to_pdf_bytes(
        "# Title\n\nHello world and 世界 [S1]\n\n- bullet one\n- bullet two\n\n1. first\n2. second"
    )
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 800


# ── download endpoints ───────────────────────────────────────


def test_download_markdown_ok():
    client, _store, run_id = _completed_client()
    resp = client.get(f"/api/runs/{run_id}/report.md")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert ".md" in disposition
    assert "Executive Summary" in resp.text
    assert "NVDA deep dive" in resp.text  # topic surfaces in the title
    assert "**Evidence confidence:** medium" in resp.text


def test_download_pdf_ok():
    client, _store, run_id = _completed_client()
    resp = client.get(f"/api/runs/{run_id}/report.pdf")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:5] == b"%PDF-"
    assert len(resp.content) > 800


def test_download_returns_404_for_unknown_run():
    client = TestClient(create_app(run_store=InMemoryRunStore()))
    assert client.get("/api/runs/not-real/report.md").status_code == 404
    assert client.get("/api/runs/not-real/report.pdf").status_code == 404


def test_download_returns_409_when_report_not_ready():
    store = InMemoryRunStore()
    client = TestClient(create_app(run_store=store))
    run = store.create_run("a pending run with no report yet")

    assert client.get(f"/api/runs/{run.run_id}/report.md").status_code == 409
    assert client.get(f"/api/runs/{run.run_id}/report.pdf").status_code == 409
