import html
import re

from bs4 import BeautifulSoup, NavigableString, Tag


ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "em", "h1", "h2", "h3",
    "hr", "img", "li", "ol", "p", "pre", "strong", "ul",
}
ALLOWED_ATTRS = {
    "a": {"href"},
    "img": {"src", "alt"},
}

SAFE_LINK_SCHEMES = ("http://", "https://")


def render_mod_description(raw_description: str) -> str:
    text = (raw_description or "").strip()
    if not text:
        body = "<p>Sin descripci&#243;n.</p>"
    elif looks_like_html(text):
        body = sanitize_html_fragment(text)
    else:
        body = sanitize_html_fragment(markdown_to_html(text))
    return build_document_html(body)


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<(?:p|br|div|span|ul|ol|li|strong|em|img|a|h[1-6]|hr|pre|code|blockquote)\b", text, re.I))


def markdown_to_html(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts = []
    paragraph_lines = []
    list_type = None
    code_lines = []
    in_code = False
    blockquote_lines = []

    def flush_paragraph():
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        content = " ".join(line.strip() for line in paragraph_lines if line.strip())
        if content:
            parts.append(f"<p>{render_inline_markdown(content)}</p>")
        paragraph_lines = []

    def flush_list():
        nonlocal list_type
        if list_type:
            parts.append(f"</{list_type}>")
            list_type = None

    def flush_blockquote():
        nonlocal blockquote_lines
        if not blockquote_lines:
            return
        content = " ".join(line.strip() for line in blockquote_lines if line.strip())
        if content:
            parts.append(f"<blockquote>{render_inline_markdown(content)}</blockquote>")
        blockquote_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            flush_blockquote()
            if in_code:
                code_html = html.escape("\n".join(code_lines))
                parts.append(f"<pre><code>{code_html}</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(raw_line)
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            flush_blockquote()
            continue

        if re.fullmatch(r"[-*_]\s*[-*_]\s*[-*_][-_*\s]*", stripped):
            flush_paragraph()
            flush_list()
            flush_blockquote()
            parts.append("<hr>")
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            flush_blockquote()
            level = len(heading_match.group(1))
            content = render_inline_markdown(heading_match.group(2).strip())
            parts.append(f"<h{level}>{content}</h{level}>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            blockquote_lines.append(stripped[1:].strip())
            continue
        flush_blockquote()

        ordered_match = re.match(r"^\d+\.\s+(.*)$", stripped)
        unordered_match = re.match(r"^[-*]\s+(.*)$", stripped)
        if ordered_match or unordered_match:
            flush_paragraph()
            desired_type = "ol" if ordered_match else "ul"
            if list_type != desired_type:
                flush_list()
                list_type = desired_type
                parts.append(f"<{list_type}>")
            content = ordered_match.group(1) if ordered_match else unordered_match.group(1)
            parts.append(f"<li>{render_inline_markdown(content.strip())}</li>")
            continue

        flush_list()
        paragraph_lines.append(stripped)

    if in_code:
        code_html = html.escape("\n".join(code_lines))
        parts.append(f"<pre><code>{code_html}</code></pre>")
    flush_paragraph()
    flush_list()
    flush_blockquote()
    return "\n".join(parts)


def render_inline_markdown(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: build_image_tag(m.group(1), m.group(2)),
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: build_link_tag(m.group(1), m.group(2)),
        escaped,
    )
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)", r"<em>\1</em>", escaped)
    return escaped.replace("\n", "<br>")


def build_link_tag(label: str, href: str) -> str:
    safe_href = sanitize_url(html.unescape(href))
    safe_label = label.strip() or href
    if not safe_href:
        return html.escape(safe_label, quote=False)
    return f'<a href="{html.escape(safe_href, quote=True)}">{safe_label}</a>'


def build_image_tag(alt: str, src: str) -> str:
    safe_src = sanitize_url(html.unescape(src))
    safe_alt = html.escape(alt.strip(), quote=True)
    if not safe_src:
        return html.escape(alt.strip(), quote=False)
    return f'<img src="{html.escape(safe_src, quote=True)}" alt="{safe_alt}">'


def sanitize_url(url: str) -> str:
    candidate = (url or "").strip()
    if not candidate:
        return ""
    if candidate.startswith(SAFE_LINK_SCHEMES):
        return candidate
    return ""


def sanitize_html_fragment(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    root = soup.body or soup
    for node in list(root.contents):
        sanitize_node(node)
    return "".join(str(child) for child in root.contents).strip()


def sanitize_node(node):
    if isinstance(node, NavigableString):
        return
    if not isinstance(node, Tag):
        return
    if node.name in {"script", "style"}:
        node.decompose()
        return
    for child in list(node.contents):
        sanitize_node(child)
    if node.name not in ALLOWED_TAGS:
        node.unwrap()
        return
    allowed_attrs = ALLOWED_ATTRS.get(node.name, set())
    for attr in list(node.attrs):
        if attr not in allowed_attrs:
            del node.attrs[attr]
    if node.name == "a":
        href = sanitize_url(node.get("href", ""))
        if href:
            node["href"] = href
        else:
            node.unwrap()
    elif node.name == "img":
        src = sanitize_url(node.get("src", ""))
        if src:
            node["src"] = src
            node["alt"] = node.get("alt", "")
        else:
            fallback = node.get("alt", "")
            node.replace_with(NavigableString(fallback))


def build_document_html(body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    color: #202124;
    margin: 0;
}}
h1, h2, h3 {{
    color: #111827;
    margin: 14px 0 8px 0;
}}
h1 {{
    font-size: 16pt;
}}
h2 {{
    font-size: 13pt;
}}
h3 {{
    font-size: 11pt;
}}
p, ul, ol, blockquote, pre {{
    margin: 8px 0;
}}
ul, ol {{
    padding-left: 22px;
}}
li {{
    margin: 3px 0;
}}
a {{
    color: #0a66c2;
    text-decoration: none;
}}
blockquote {{
    border-left: 3px solid #cbd5e1;
    color: #475569;
    margin-left: 0;
    padding-left: 10px;
}}
pre {{
    background: #f4f4f5;
    border: 1px solid #e4e4e7;
    padding: 10px;
    white-space: pre-wrap;
}}
code {{
    font-family: Consolas, "Courier New", monospace;
}}
hr {{
    border: 0;
    border-top: 1px solid #d4d4d8;
    margin: 12px 0;
}}
img {{
    display: block;
    margin: 10px 0;
    max-width: 100%;
}}
</style>
</head>
<body>{body_html}</body>
</html>"""
