from __future__ import annotations

from dataclasses import dataclass
from html import escape
import re
from typing import Any


ALLOWED_HTML_TAGS = [
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "code",
    "pre",
    "blockquote",
    "a",
    "hr",
]
ALLOWED_HTML_ATTRIBUTES = {
    "a": ["href", "title"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
_NUMBER_FIELDS = (
    "price",
    "market_cap",
    "market_cap_cny",
    "trailing_pe",
    "forward_pe",
    "dividend_yield",
)


@dataclass(slots=True)
class MarketHighlight:
    ticker: str
    source: str | None
    currency: str | None
    price: float | int | None
    market_cap: float | int | None
    trailing_pe: float | int | None
    forward_pe: float | int | None
    dividend_yield: float | int | None


@dataclass(slots=True)
class RunPresentation:
    final_report_html: str | None
    evidence_confidence: str | None
    followup_history: list[str]
    market_highlights: list[MarketHighlight]


def render_final_report_html(report_text: str) -> str | None:
    normalized = report_text.strip()
    if not normalized:
        return None

    try:
        import bleach
        import markdown

        rendered = markdown.markdown(
            normalized,
            extensions=["extra", "sane_lists"],
        )
        cleaned = bleach.clean(
            rendered,
            tags=ALLOWED_HTML_TAGS,
            attributes=ALLOWED_HTML_ATTRIBUTES,
            protocols=ALLOWED_PROTOCOLS,
            strip=True,
        )
        return cleaned or f"<p>{escape(normalized)}</p>"
    except Exception:
        return _render_markdown_fallback(normalized)


def derive_evidence_confidence(
    snapshot: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
) -> str | None:
    decide_summary = summaries.get("decide") or {}
    confidence = str(decide_summary.get("evidence_confidence") or snapshot.get("evidence_confidence") or "").strip()
    return confidence or None


def collect_followup_history(events: list[dict[str, Any]]) -> list[str]:
    history: list[str] = []
    seen: set[str] = set()
    for event in events:
        if str(event.get("type") or "") != "step_completed":
            continue
        if str(event.get("node") or "") != "decide":
            continue
        snapshot = event.get("snapshot")
        if not isinstance(snapshot, dict):
            continue
        for query in snapshot.get("followup_queries") or []:
            normalized = str(query).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            history.append(normalized)
    return history


def build_market_highlights(snapshot: dict[str, Any]) -> list[MarketHighlight]:
    market = snapshot.get("market")
    if not isinstance(market, dict):
        return []

    highlights: list[MarketHighlight] = []
    for ticker, provider_map in market.items():
        if not isinstance(provider_map, dict):
            continue
        provider_name, payload = _pick_market_payload(provider_map)
        if payload is None:
            continue
        highlights.append(
            MarketHighlight(
                ticker=str(ticker).strip(),
                source=provider_name,
                currency=_string_or_none(payload.get("currency")),
                price=_number_or_none(payload.get("price")),
                market_cap=_number_or_none(payload.get("market_cap") or payload.get("market_cap_cny")),
                trailing_pe=_number_or_none(payload.get("trailing_pe")),
                forward_pe=_number_or_none(payload.get("forward_pe")),
                dividend_yield=_number_or_none(payload.get("dividend_yield")),
            )
        )
    return highlights


def build_run_presentation(
    *,
    status: Any,
    final_report: str | None,
    snapshot: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
) -> RunPresentation:
    status_value = getattr(status, "value", status)
    return RunPresentation(
        final_report_html=render_final_report_html(final_report or "") if status_value == "completed" else None,
        evidence_confidence=derive_evidence_confidence(snapshot, summaries),
        followup_history=collect_followup_history(events),
        market_highlights=build_market_highlights(snapshot),
    )


def _pick_market_payload(provider_map: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    for provider_name, payload in provider_map.items():
        if not isinstance(payload, dict) or payload.get("error"):
            continue
        if any(_number_or_none(payload.get(field)) is not None for field in _NUMBER_FIELDS):
            return str(provider_name).strip() or None, payload
        if _string_or_none(payload.get("currency")) is not None:
            return str(provider_name).strip() or None, payload
    return None, None


def _number_or_none(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _string_or_none(value: Any) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _render_markdown_fallback(markdown_text: str) -> str:
    lines = markdown_text.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            code = escape("\n".join(code_lines))
            blocks.append(f"<pre><code>{code}</code></pre>")
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            blocks.append(f"<h{level}>{_render_inline(heading_match.group(2).strip())}</h{level}>")
            index += 1
            continue

        if stripped == "---":
            blocks.append("<hr>")
            index += 1
            continue

        if re.match(r"^[-*]\s+.+$", stripped):
            items: list[str] = []
            while index < len(lines) and re.match(r"^\s*[-*]\s+.+$", lines[index]):
                content = re.sub(r"^\s*[-*]\s+", "", lines[index]).strip()
                items.append(f"<li>{_render_inline(content)}</li>")
                index += 1
            blocks.append(f"<ul>{''.join(items)}</ul>")
            continue

        if re.match(r"^\d+\.\s+.+$", stripped):
            items = []
            while index < len(lines) and re.match(r"^\s*\d+\.\s+.+$", lines[index]):
                content = re.sub(r"^\s*\d+\.\s+", "", lines[index]).strip()
                items.append(f"<li>{_render_inline(content)}</li>")
                index += 1
            blocks.append(f"<ol>{''.join(items)}</ol>")
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[index]).strip())
                index += 1
            content = " ".join(part for part in quote_lines if part)
            blocks.append(f"<blockquote><p>{_render_inline(content)}</p></blockquote>")
            continue

        paragraph_lines: list[str] = []
        while index < len(lines):
            current = lines[index].strip()
            if not current:
                break
            if (
                current.startswith("```")
                or current.startswith(">")
                or current == "---"
                or re.match(r"^(#{1,4})\s+", current)
                or re.match(r"^[-*]\s+.+$", current)
                or re.match(r"^\d+\.\s+.+$", current)
            ):
                break
            paragraph_lines.append(current)
            index += 1
        content = " ".join(paragraph_lines)
        blocks.append(f"<p>{_render_inline(content)}</p>")

    return "".join(blocks) or f"<p>{escape(markdown_text)}</p>"


def _render_inline(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+|mailto:[^)]+)\)",
        lambda match: f'<a href="{escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


__all__ = [
    "MarketHighlight",
    "RunPresentation",
    "build_market_highlights",
    "build_run_presentation",
    "collect_followup_history",
    "derive_evidence_confidence",
    "render_final_report_html",
]
