from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypedDict, cast


SUPPORTED_NODES = (
    "plan",
    "market",
    "search_web",
    "extract",
    "decide",
    "write_report",
)


class WarningPayload(TypedDict):
    code: str
    severity: Literal["warning"]
    message: str


class StepCompletedEvent(TypedDict):
    type: Literal["step_completed"]
    node: str
    summary: dict[str, Any]
    warnings: list[WarningPayload]
    state_patch: dict[str, Any]
    snapshot: dict[str, Any]
    timestamp: str


class RunStartedEvent(TypedDict):
    type: Literal["run_started"]
    query: str
    snapshot: dict[str, Any]
    timestamp: str


class RunCompletedEvent(TypedDict):
    type: Literal["run_completed"]
    final_report: str
    snapshot: dict[str, Any]
    timestamp: str


class RunFailedEvent(TypedDict, total=False):
    type: Literal["run_failed"]
    error: str
    node: str | None
    snapshot: dict[str, Any]
    timestamp: str


WorkbenchEvent = RunStartedEvent | StepCompletedEvent | RunCompletedEvent | RunFailedEvent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_supported_node(node: str) -> bool:
    return node in SUPPORTED_NODES


def _apply_state_patch(snapshot: dict[str, Any], state_patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(snapshot)
    merged.update(state_patch)
    return merged


def _count_tickers_with_market_data(
    market: dict[str, Any],
) -> tuple[int, int, int]:
    covered_ticker_count = 0
    tickers_with_price = 0
    tickers_with_market_cap = 0

    for provider_map in market.values():
        if not isinstance(provider_map, dict):
            continue

        has_any_data = False
        has_price = False
        has_market_cap = False
        for payload in provider_map.values():
            if not isinstance(payload, dict) or payload.get("error"):
                continue

            if any(
                key not in {"ticker", "source", "error"} and value not in (None, "", [], {})
                for key, value in payload.items()
            ):
                has_any_data = True
            if payload.get("price") is not None:
                has_price = True
            if payload.get("market_cap") is not None or payload.get("market_cap_cny") is not None:
                has_market_cap = True

        if has_any_data:
            covered_ticker_count += 1
        if has_price:
            tickers_with_price += 1
        if has_market_cap:
            tickers_with_market_cap += 1

    return covered_ticker_count, tickers_with_price, tickers_with_market_cap


def summarize_step(node: str, state_before: dict[str, Any], state_after: dict[str, Any]) -> dict[str, Any]:
    if node == "plan":
        plan = cast(dict[str, Any], state_after.get("plan") or {})
        tickers = plan.get("tickers") or []
        subqueries = state_after.get("subqueries") or plan.get("subqueries") or []
        return {
            "topic": str(plan.get("topic") or "").strip(),
            "ticker_count": len(tickers),
            "subquery_count": len(subqueries),
        }

    if node == "market":
        plan = cast(dict[str, Any], state_after.get("plan") or {})
        planned_tickers = [
            str(ticker).strip()
            for ticker in (plan.get("tickers") or [])
            if str(ticker).strip()
        ]
        market = cast(dict[str, Any], state_after.get("market") or {})
        covered_ticker_count, tickers_with_price, tickers_with_market_cap = _count_tickers_with_market_data(market)
        ticker_count = len(planned_tickers) if planned_tickers else len(market)
        return {
            "ticker_count": ticker_count,
            "covered_ticker_count": covered_ticker_count,
            "tickers_with_price": tickers_with_price,
            "tickers_with_market_cap": tickers_with_market_cap,
        }

    if node == "search_web":
        total_sources = len(state_after.get("sources") or [])
        previous_sources = len(state_before.get("sources") or [])
        return {
            "total_sources": total_sources,
            "new_sources": max(total_sources - previous_sources, 0),
        }

    if node == "extract":
        total_notes = len(state_after.get("notes") or [])
        previous_notes = len(state_before.get("notes") or [])
        return {
            "total_notes": total_notes,
            "new_notes": max(total_notes - previous_notes, 0),
        }

    if node == "decide":
        followups = state_after.get("followup_queries") or []
        return {
            "need_more": bool(state_after.get("need_more")),
            "followup_count": len(followups),
            "evidence_confidence": str(state_after.get("evidence_confidence") or ""),
        }

    if node == "write_report":
        report = str(state_after.get("final_report") or "")
        return {"report_length": len(report)}

    return {}


def collect_step_warnings(node: str, summary: dict[str, Any]) -> list[WarningPayload]:
    warnings: list[WarningPayload] = []

    if node == "market" and summary.get("ticker_count", 0) > 0 and summary.get("covered_ticker_count", 0) == 0:
        warnings.append(
            {
                "code": "empty_market_data",
                "severity": "warning",
                "message": "No usable market snapshot was returned for the planned tickers.",
            }
        )

    if node == "extract" and summary.get("new_notes", 0) == 0:
        warnings.append(
            {
                "code": "no_new_notes",
                "severity": "warning",
                "message": "No new evidence notes were extracted from the latest source batch.",
            }
        )

    if node == "decide" and summary.get("evidence_confidence") in {"low", "insufficient"}:
        warnings.append(
            {
                "code": "low_evidence_confidence",
                "severity": "warning",
                "message": "Evidence confidence is low, so the resulting memo should be treated cautiously.",
            }
        )

    return warnings


def build_step_event(
    node: str,
    state_before: dict[str, Any],
    state_patch: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> StepCompletedEvent | None:
    if not _is_supported_node(node):
        return None

    next_snapshot = _apply_state_patch(state_before, state_patch)
    summary = summarize_step(node, state_before, next_snapshot)
    return StepCompletedEvent(
        type="step_completed",
        node=node,
        summary=summary,
        warnings=collect_step_warnings(node, summary),
        state_patch=dict(state_patch),
        snapshot=next_snapshot,
        timestamp=timestamp or _utc_now_iso(),
    )


def build_run_started_event(
    query: str,
    snapshot: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> RunStartedEvent:
    return RunStartedEvent(
        type="run_started",
        query=query,
        snapshot=dict(snapshot),
        timestamp=timestamp or _utc_now_iso(),
    )


def build_run_completed_event(
    snapshot: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> RunCompletedEvent:
    return RunCompletedEvent(
        type="run_completed",
        final_report=str(snapshot.get("final_report") or ""),
        snapshot=dict(snapshot),
        timestamp=timestamp or _utc_now_iso(),
    )


def build_run_failed_event(
    error: str,
    snapshot: dict[str, Any],
    *,
    node: str | None = None,
    timestamp: str | None = None,
) -> RunFailedEvent:
    event: RunFailedEvent = {
        "type": "run_failed",
        "error": error,
        "snapshot": dict(snapshot),
        "timestamp": timestamp or _utc_now_iso(),
    }
    if node is not None:
        event["node"] = node
    return event


__all__ = [
    "SUPPORTED_NODES",
    "StepCompletedEvent",
    "WarningPayload",
    "WorkbenchEvent",
    "build_run_completed_event",
    "build_run_failed_event",
    "build_run_started_event",
    "build_step_event",
    "collect_step_warnings",
    "summarize_step",
]
