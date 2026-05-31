"""Report export: build a Markdown document and render it to PDF.

The PDF path uses ``fpdf2`` (pure Python, no system libraries) and embeds a
CJK-capable TrueType font discovered on the host, so Chinese reports render
correctly. If no CJK font is found it degrades to a built-in Latin font.
"""
from __future__ import annotations

import os
import re
import warnings
from typing import Optional

# fpdf2 warns at import time when Pillow is absent; we never embed images.
warnings.filterwarnings("ignore", message="Pillow could not be imported")


# Candidate CJK fonts across macOS / Linux. First hit wins. ``.ttf`` is
# preferred over ``.ttc`` (collection files need a face index).
_CJK_FONT_CANDIDATES = (
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)


def _discover_cjk_font() -> Optional[str]:
    """Return a path to a CJK-capable font, or None.

    ``STOCK_AGENT_PDF_FONT`` overrides discovery when set.
    """
    override = os.getenv("STOCK_AGENT_PDF_FONT")
    if override and os.path.exists(override):
        return override
    for path in _CJK_FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def safe_filename(text: str, *, fallback: str = "stock-research", max_len: int = 48) -> str:
    """Turn an arbitrary query/topic into a filesystem-safe slug."""
    normalized = (text or "").strip().lower()
    # Keep word chars (incl. CJK via \w under re.UNICODE) and spaces/dashes.
    normalized = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    normalized = re.sub(r"[\s_-]+", "-", normalized).strip("-")
    if not normalized:
        return fallback
    return normalized[:max_len].strip("-") or fallback


def build_report_markdown(
    *,
    query: str,
    topic: Optional[str],
    final_report: str,
    evidence_confidence: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> str:
    """Assemble a standalone Markdown document around the final memo."""
    title = (topic or query or "Stock Research Memo").strip()
    header_lines = [f"# {title}", ""]
    meta: list[str] = []
    if query:
        meta.append(f"- **Query:** {query.strip()}")
    if generated_at:
        meta.append(f"- **Generated:** {generated_at}")
    if evidence_confidence:
        meta.append(f"- **Evidence confidence:** {evidence_confidence}")
    if meta:
        header_lines.extend(meta)
        header_lines.append("")
    header_lines.append("---")
    header_lines.append("")
    body = (final_report or "").strip() or "_No report content was produced._"
    return "\n".join(header_lines) + "\n" + body + "\n"


def _strip_inline_noise(text: str) -> str:
    """Drop markdown markers fpdf2's inline parser does not handle.

    Keeps ``**bold**`` (supported via markdown=True) and ``[S#]`` citations.
    """
    text = text.replace("`", "")
    # single-star italics -> plain (fpdf2 markdown uses __ for italics)
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", r"\1", text)
    return text


def markdown_to_pdf_bytes(markdown_text: str) -> bytes:
    """Render a Markdown report to PDF bytes.

    A lightweight block parser handles headings, bullet/numbered lists,
    blockquotes, horizontal rules and paragraphs; inline ``**bold**`` is
    rendered via fpdf2's markdown mode.
    """
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    font_path = _discover_cjk_font()

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    unicode_font = bool(font_path)
    if unicode_font:
        family = "report"
        # Register the same file for all styles so markdown bold/italic and
        # heading bold never raise "font not found" (single-weight fonts are
        # rendered at normal weight — acceptable).
        for style in ("", "B", "I", "BI"):
            try:
                pdf.add_font(family, style, font_path)
            except Exception:
                pass
    else:
        # No CJK font on host: fall back to a built-in Latin font. CJK glyphs
        # are unavailable, so non-latin-1 characters are replaced rather than
        # raising (English reports stay clean; Chinese degrades to placeholders).
        family = "Helvetica"

    epw = pdf.epw  # effective page width (inside margins)

    def _safe(text: str) -> str:
        if unicode_font:
            return text
        return text.encode("latin-1", "replace").decode("latin-1")

    def write_block(text: str, *, size: float, style: str = "", gap: float = 2.0,
                    leading: float = 1.45, markdown: bool = True) -> None:
        pdf.set_font(family, style, size)
        line_h = size * leading * 0.3528  # pt -> mm approximation
        pdf.multi_cell(
            epw, line_h, _safe(text), markdown=markdown,
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        if gap:
            pdf.ln(gap)

    lines = (markdown_text or "").replace("\r\n", "\n").split("\n")
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if stripped in {"---", "***", "___"}:
            y = pdf.get_y()
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.l_margin, y, pdf.l_margin + epw, y)
            pdf.ln(3)
            i += 1
            continue

        # Heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = _strip_inline_noise(m.group(2).strip())
            size = {1: 18, 2: 14, 3: 12.5}.get(level, 11.5)
            write_block(text, size=size, style="B", gap=2.2, markdown=False)
            i += 1
            continue

        # Bullet list
        if re.match(r"^[-*+]\s+\S", stripped):
            while i < n and re.match(r"^\s*[-*+]\s+\S", lines[i]):
                content = re.sub(r"^\s*[-*+]\s+", "", lines[i]).strip()
                write_block("•  " + _strip_inline_noise(content), size=11, gap=0.6)
                i += 1
            pdf.ln(1.4)
            continue

        # Numbered list
        if re.match(r"^\d+\.\s+\S", stripped):
            while i < n and re.match(r"^\s*\d+\.\s+\S", lines[i]):
                content = lines[i].strip()
                write_block(_strip_inline_noise(content), size=11, gap=0.6)
                i += 1
            pdf.ln(1.4)
            continue

        # Blockquote
        if stripped.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]).strip())
                i += 1
            write_block(_strip_inline_noise(" ".join(p for p in quote if p)),
                        size=10.5, style="I", gap=2.0)
            continue

        # Paragraph (gather consecutive plain lines)
        para: list[str] = []
        while i < n:
            cur = lines[i].strip()
            if (not cur or cur in {"---", "***", "___"}
                    or re.match(r"^#{1,6}\s+", cur)
                    or re.match(r"^[-*+]\s+\S", cur)
                    or re.match(r"^\d+\.\s+\S", cur)
                    or cur.startswith(">")):
                break
            para.append(cur)
            i += 1
        write_block(_strip_inline_noise(" ".join(para)), size=11, gap=2.4)

    return bytes(pdf.output())


__all__ = [
    "build_report_markdown",
    "markdown_to_pdf_bytes",
    "safe_filename",
]
