"""Conservative, human-friendly presentation for text-backed artifacts.

The original bytes remain untouched and downloadable. Renderers escape source
content and only generate a small allowlisted set of semantic elements.
"""

import csv
import io
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from django.utils.html import escape
from django.utils.safestring import mark_safe


MAX_RENDER_CHARS = 500_000
MAX_TABLE_ROWS = 1_000
MAX_TABLE_COLUMNS = 40
EMBEDDED_HTML_CSP = (
    '<meta http-equiv="Content-Security-Policy" '
    'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; '
    'font-src data:; media-src data: blob:; form-action \'none\'; base-uri \'none\'">'
)

SAFE_HTML_TAGS = {
    "html", "head", "body", "title", "style", "article", "section", "header", "footer",
    "main", "nav", "aside", "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd", "table", "caption", "thead", "tbody", "tfoot",
    "tr", "th", "td", "colgroup", "col", "a", "img", "br", "hr", "pre", "code",
    "blockquote", "strong", "em", "b", "i", "small", "mark", "sup", "sub", "details",
    "summary", "figure", "figcaption",
}
VOID_HTML_TAGS = {"img", "br", "hr", "col"}
DROP_WITH_CONTENT = {"script", "iframe", "frame", "object", "embed", "form", "button", "input", "textarea", "select", "option", "video", "audio", "source"}
GLOBAL_HTML_ATTRS = {"class", "id", "title", "role", "aria-label", "aria-describedby"}
TAG_HTML_ATTRS = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "th": {"scope", "colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "col": {"span", "width"},
    "details": {"open"},
}


class SafeEmbeddedHTMLParser(HTMLParser):
    """Allow readable report markup while removing active/navigation content."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.output = []
        self.drop_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_depth:
            if tag in DROP_WITH_CONTENT:
                self.drop_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.drop_depth = 1
            return
        if tag not in SAFE_HTML_TAGS:
            return
        rendered_attrs = []
        allowed = GLOBAL_HTML_ATTRS | TAG_HTML_ATTRS.get(tag, set())
        for name, value in attrs:
            name = name.lower()
            if name not in allowed or value is None:
                continue
            if tag == "a" and name == "href":
                parsed = urlsplit(value)
                if parsed.scheme and parsed.scheme.lower() not in {"http", "https", "mailto"}:
                    continue
            if tag == "img" and name == "src" and not value.lower().startswith("data:image/"):
                continue
            rendered_attrs.append(f' {name}="{escape(value)}"')
        if tag == "a" and any(name.lower() == "href" for name, _value in attrs):
            rendered_attrs.append(' target="_blank" rel="noopener noreferrer"')
        self.output.append(f"<{tag}{''.join(rendered_attrs)}>")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in DROP_WITH_CONTENT:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.drop_depth:
            if tag in DROP_WITH_CONTENT:
                self.drop_depth -= 1
            return
        if tag in SAFE_HTML_TAGS and tag not in VOID_HTML_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.drop_depth:
            self.output.append(str(escape(data)))

    def handle_entityref(self, name):
        if not self.drop_depth:
            self.output.append(f"&{name};")

    def handle_charref(self, name):
        if not self.drop_depth:
            self.output.append(f"&#{name};")


def sanitize_embedded_html(content):
    parser = SafeEmbeddedHTMLParser()
    parser.feed(content)
    parser.close()
    return "".join(parser.output)

FORMAT_BY_EXTENSION = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".log": "log",
    ".txt": "plain",
    ".rst": "plain",
}


def source_format(artifact):
    hint = (artifact.source_format or "").strip().lower()
    if hint:
        return hint
    return FORMAT_BY_EXTENSION.get(Path(artifact.file.name).suffix.lower(), "plain")


INLINE_TOKEN_RE = re.compile(r"(`[^`]+`|\[[^\]]+\]\(https?://[^\s)]+\))")
RESEARCH_REFERENCE_RE = re.compile(
    r"\b(?:(?:research\s+)?(?:entry|effort))\s+#(?P<id>\d+)\b",
    re.IGNORECASE,
)


def _plain_inline(value, reference_urls):
    value = str(escape(value))
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)

    def link_reference(match):
        url = reference_urls.get(int(match.group("id")))
        if not url:
            return match.group(0)
        return f'<a href="{escape(url)}">{match.group(0)}</a>'

    return RESEARCH_REFERENCE_RE.sub(link_reference, value)


def _inline(value, reference_urls=None):
    """Render safe inline markup, linking references only in ordinary text."""
    reference_urls = reference_urls or {}
    output = []
    cursor = 0
    for match in INLINE_TOKEN_RE.finditer(value):
        output.append(_plain_inline(value[cursor:match.start()], reference_urls))
        token = match.group(0)
        if token.startswith("`"):
            output.append(f"<code>{escape(token[1:-1])}</code>")
        else:
            link = re.match(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", token)
            output.append(
                f'<a href="{escape(link.group(2))}" target="_blank" rel="noopener">'
                f'{escape(link.group(1))}</a>'
            )
        cursor = match.end()
    output.append(_plain_inline(value[cursor:], reference_urls))
    return "".join(output)


def render_markdown(value, reference_urls=None):
    """Render a deliberately small, safe Markdown subset for reports."""
    lines = value.splitlines()
    output = []
    paragraph = []
    list_type = None

    def flush_paragraph():
        if paragraph:
            output.append(f"<p>{_inline(' '.join(paragraph), reference_urls)}</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_type
        if list_type:
            output.append(f"</{list_type}>")
            list_type = None

    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            close_list()
            index += 1
            continue

        # Pipe tables require a header, separator, and at least one data row.
        if index + 2 < len(lines) and "|" in stripped and re.match(
            r"^\s*\|?\s*:?-+", lines[index + 1]
        ):
            flush_paragraph()
            close_list()
            table_lines = [stripped]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index].strip())
                index += 1
            rows = [[cell.strip() for cell in row.strip("|").split("|")] for row in table_lines]
            width = min(max(len(row) for row in rows), MAX_TABLE_COLUMNS)
            output.append('<div class="artifact-table-wrap"><table class="artifact-table"><thead><tr>')
            for cell in rows[0][:width]:
                output.append(f"<th scope=\"col\">{_inline(cell, reference_urls)}</th>")
            output.append("</tr></thead><tbody>")
            for row in rows[1:MAX_TABLE_ROWS + 1]:
                output.append("<tr>" + "".join(f"<td>{_inline(cell, reference_urls)}</td>" for cell in row[:width]) + "</tr>")
            output.append("</tbody></table></div>")
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1)) + 1
            output.append(f"<h{level}>{_inline(heading.group(2), reference_urls)}</h{level}>")
        elif stripped.startswith("> "):
            flush_paragraph()
            close_list()
            output.append(f"<blockquote>{_inline(stripped[2:], reference_urls)}</blockquote>")
        elif re.match(r"^[-*]\s+", stripped):
            flush_paragraph()
            if list_type != "ul":
                close_list()
                list_type = "ul"
                output.append("<ul>")
            item_text = re.sub(r"^[-*]\s+", "", stripped)
            output.append(f"<li>{_inline(item_text, reference_urls)}</li>")
        elif re.match(r"^\d+\.\s+", stripped):
            flush_paragraph()
            if list_type != "ol":
                close_list()
                list_type = "ol"
                output.append("<ol>")
            item_text = re.sub(r"^\d+\.\s+", "", stripped)
            output.append(f"<li>{_inline(item_text, reference_urls)}</li>")
        else:
            close_list()
            paragraph.append(stripped)
        index += 1

    flush_paragraph()
    close_list()
    return mark_safe("".join(output))


def _tabular(content, delimiter):
    rows = list(csv.reader(io.StringIO(content), delimiter=delimiter))
    rows = [row[:MAX_TABLE_COLUMNS] for row in rows[: MAX_TABLE_ROWS + 1]]
    if not rows:
        return {"headers": [], "rows": [], "truncated": False}
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    return {
        "headers": normalized[0],
        "rows": normalized[1:],
        "truncated": len(content.splitlines()) > MAX_TABLE_ROWS + 1,
    }


def _flat_json_table(payload):
    if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) for item in payload):
        return None
    keys = []
    for item in payload[:MAX_TABLE_ROWS]:
        for key, value in item.items():
            if key not in keys:
                keys.append(key)
            if isinstance(value, (dict, list)):
                return None
    keys = keys[:MAX_TABLE_COLUMNS]
    return {
        "headers": keys,
        "rows": [[item.get(key, "") for key in keys] for item in payload[:MAX_TABLE_ROWS]],
        "truncated": len(payload) > MAX_TABLE_ROWS,
    }


def _json_within_depth(payload, limit=64):
    stack = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > limit:
            return False
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    return True


def present_content(
    content,
    *,
    source_format="plain",
    requested_view="",
    presentation_mode="auto",
    report=False,
    max_chars=MAX_RENDER_CHARS,
    source_truncated=False,
    reference_urls=None,
):
    """Present text without depending on an Artifact or ResearchEntry model."""
    truncated = source_truncated or (max_chars is not None and len(content) > max_chars)
    if max_chars is not None:
        content = content[:max_chars]
    fmt = source_format
    mode = presentation_mode
    if requested_view == "raw" or mode == "raw":
        return {"view": "raw", "format": fmt, "content": content, "truncated": truncated}
    if fmt == "html" and requested_view != "raw":
        return {
            "view": "html",
            "format": fmt,
            "content": f"{EMBEDDED_HTML_CSP}{sanitize_embedded_html(content)}",
            "truncated": truncated,
        }
    if fmt in {"csv", "tsv"} or mode == "table":
        try:
            table = _tabular(content, "\t" if fmt == "tsv" else ",")
            return {"view": "table", "format": fmt, "table": table, "truncated": truncated}
        except csv.Error:
            return {"view": "raw", "format": fmt, "content": content, "malformed": True, "truncated": truncated}
    if fmt == "json":
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, RecursionError, MemoryError, ValueError):
            return {"view": "raw", "format": fmt, "content": content, "malformed": True, "truncated": truncated}
        if not _json_within_depth(payload):
            return {"view": "raw", "format": fmt, "content": content, "malformed": True, "truncated": truncated}
        table = _flat_json_table(payload)
        if table and requested_view != "structured" and mode != "structured":
            return {"view": "table", "format": fmt, "table": table, "truncated": truncated}
        try:
            structured = json.dumps(payload, indent=2, ensure_ascii=False)
        except (RecursionError, MemoryError, ValueError):
            return {"view": "raw", "format": fmt, "content": content, "malformed": True, "truncated": truncated}
        return {"view": "structured", "format": fmt, "content": structured, "truncated": truncated}
    if fmt == "markdown" or report:
        return {
            "view": "report",
            "format": fmt,
            "html": render_markdown(content, reference_urls),
            "truncated": truncated,
        }
    return {"view": "raw", "format": fmt, "content": content, "truncated": truncated}


def present_artifact(artifact, content, requested_view="", source_truncated=False):
    return present_content(
        content,
        source_format=source_format(artifact),
        requested_view=requested_view,
        presentation_mode=artifact.presentation_mode,
        report=artifact.kind in {artifact.Kind.REPORT, artifact.Kind.SUMMARY},
        source_truncated=source_truncated,
    )


def present_research_context(content, requested_view="", reference_urls=None):
    """Bound formatted work while keeping the complete stored source available."""
    return present_content(
        content,
        source_format="markdown",
        requested_view=requested_view,
        report=True,
        max_chars=None if requested_view == "raw" else MAX_RENDER_CHARS,
        reference_urls=reference_urls,
    )
