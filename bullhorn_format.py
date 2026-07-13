"""Convert the ECHO call-analysis markdown into formats that paste cleanly
into Bullhorn's rich-text note editor.

The analysis stored on a call (`summary_text`) is markdown: an h1 header,
`**bold**` label lines, `##` section headings, `-` bullet lists, and `1.`
numbered lists (the Technical Screening Questions). Copying that markdown to
the clipboard verbatim meant recruiters pasted raw `#`, `**`, and `-`
characters into Bullhorn, which looked terrible.

This module produces the two representations we write to the clipboard
together (see the "Copy for Bullhorn" button in app.py):

- ``to_html`` — semantic HTML (h1/h2/strong/ul/ol) with light inline spacing.
  Bullhorn's note editor is rich text and renders this with real headings,
  bold, and bullets. Inline styles are used (not a stylesheet) because paste
  targets strip <style> blocks and class-based CSS.
- ``to_plaintext`` — the same content with markdown tokens stripped and tidy
  spacing, for any target that only accepts plain text.

Deliberately hand-rolled instead of pulling in a markdown library: the input
is a narrow, known subset (headings, bold/italic, bullets, ordered lists), we
want full control over the emitted HTML, and we avoid adding a dependency +
redeploy. Candidate answers are HTML-escaped before any formatting is applied,
so transcript content containing <, >, & or quotes can't break the markup.
"""

import html
import re

# Inline markers. Bold is handled before italic so ``**x**`` doesn't get
# mistaken for two italic ``*`` runs. The italic patterns require the marker to
# sit at a word boundary with non-space content, so underscores inside
# identifiers/emails/code in verbatim quotes (e.g. ``gpt_4o``) are left alone.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_US_RE = re.compile(r"(?<![\w*])_(?=\S)(.+?)(?<=\S)_(?![\w])")
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")

# Inline spacing for the emitted HTML. Kept modest so it reads well whether
# Bullhorn honors the inline styles or falls back to its own note styling.
_H1_STYLE = "font-size:1.4em; font-weight:700; margin:0 0 8px;"
_H2_STYLE = "font-size:1.15em; font-weight:700; margin:14px 0 6px;"
_H3_STYLE = "font-size:1.05em; font-weight:700; margin:12px 0 4px;"
_P_STYLE = "margin:0 0 8px;"
_LIST_STYLE = "margin:4px 0 10px; padding-left:22px;"
_LI_STYLE = "margin:2px 0;"


def _inline_html(text: str) -> str:
    """HTML-escape ``text`` then apply inline bold/italic markdown."""
    out = html.escape(text, quote=False)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _ITALIC_US_RE.sub(r"<em>\1</em>", out)
    out = _ITALIC_STAR_RE.sub(r"<em>\1</em>", out)
    return out


def _strip_inline(text: str) -> str:
    """Remove inline bold/italic markers, leaving clean plain text."""
    out = _BOLD_RE.sub(r"\1", text)
    out = _ITALIC_US_RE.sub(r"\1", out)
    out = _ITALIC_STAR_RE.sub(r"\1", out)
    return out


def to_html(md: str) -> str:
    """Render ECHO analysis markdown as paste-ready HTML."""
    if not md:
        return ""

    blocks: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None  # "ul" or "ol"

    def flush_list() -> None:
        nonlocal list_items, list_tag
        if list_items:
            items = "".join(
                f'<li style="{_LI_STYLE}">{item}</li>' for item in list_items
            )
            blocks.append(f'<{list_tag} style="{_LIST_STYLE}">{items}</{list_tag}>')
        list_items = []
        list_tag = None

    for raw in md.split("\n"):
        line = raw.rstrip()

        if not line.strip():
            flush_list()
            continue

        if _HR_RE.match(line):
            flush_list()
            blocks.append('<hr style="border:none; border-top:1px solid #ccc; margin:12px 0;">')
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_list()
            level = len(heading.group(1))
            style = _H1_STYLE if level == 1 else _H2_STYLE if level == 2 else _H3_STYLE
            tag = f"h{min(level, 3)}"
            blocks.append(f'<{tag} style="{style}">{_inline_html(heading.group(2))}</{tag}>')
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            if list_tag == "ol":
                flush_list()
            list_tag = "ul"
            list_items.append(_inline_html(bullet.group(1)))
            continue

        ordered = _ORDERED_RE.match(line)
        if ordered:
            if list_tag == "ul":
                flush_list()
            list_tag = "ol"
            list_items.append(_inline_html(ordered.group(2)))
            continue

        # Plain text line -> paragraph.
        flush_list()
        blocks.append(f'<p style="{_P_STYLE}">{_inline_html(line)}</p>')

    flush_list()
    return "\n".join(blocks)


def to_plaintext(md: str) -> str:
    """Render ECHO analysis markdown as clean, symbol-free plain text."""
    if not md:
        return ""

    out: list[str] = []

    for raw in md.split("\n"):
        line = raw.rstrip()

        if not line.strip():
            out.append("")
            continue

        if _HR_RE.match(line):
            out.append("")
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            out.append(_strip_inline(heading.group(2)))
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            out.append("• " + _strip_inline(bullet.group(1)))
            continue

        ordered = _ORDERED_RE.match(line)
        if ordered:
            out.append(f"{ordered.group(1)}. " + _strip_inline(ordered.group(2)))
            continue

        out.append(_strip_inline(line))

    text = "\n".join(out)
    # Collapse runs of 3+ blank lines down to a single blank line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
