from __future__ import annotations

import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MARKDOWN = ROOT / "self_project_documentation.md"
HTML = ROOT / "self_project_documentation.html"
PDF = ROOT / "self_project_documentation.pdf"


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: (
            f'<img class="table-image" src="{html.escape(m.group(2), quote=True)}" '
            f'alt="{html.escape(m.group(1), quote=True)}"/>'
        ),
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(
        r"(https?://[^\s<]+)",
        lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>',
        escaped,
    )
    return escaped


def render_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)

    head = rows[0]
    body = rows[2:] if len(rows) > 1 and all(set(c) <= {"-", ":", " "} for c in rows[1]) else rows[1:]
    out = ["<table>", "<thead><tr>"]
    out.extend(f"<th>{inline(cell)}</th>" for cell in head)
    out.append("</tr></thead>")
    out.append("<tbody>")
    for row in body:
        out.append("<tr>")
        out.extend(f"<td>{inline(cell)}</td>" for cell in row)
        out.append("</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def render_image(alt: str, src: str) -> str:
    safe_alt = html.escape(alt)
    safe_src = html.escape(src, quote=True)
    caption = f"<figcaption>{safe_alt}</figcaption>" if safe_alt else ""
    return (
        '<figure class="photo-figure">'
        f'<img src="{safe_src}" alt="{safe_alt}"/>'
        f"{caption}"
        "</figure>"
    )


def system_architecture_image() -> str:
    return """
<figure class="diagram">
<img src="assets/system_architecture.png" alt="系統架構圖"/>
</figure>
"""


def render_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    list_stack: list[tuple[int, str]] = []
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_lists(to_indent: int = -1) -> None:
        while list_stack and list_stack[-1][0] > to_indent:
            out.append(f"</{list_stack[-1][1]}>")
            list_stack.pop()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            close_lists()
            i += 1
            continue

        fence = re.match(r"^```([\w-]*)", stripped)
        if fence:
            flush_paragraph()
            close_lists()
            lang = fence.group(1)
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            if lang == "mermaid" and any("flowchart LR" in line for line in code):
                out.append(system_architecture_image())
                continue
            out.append(
                f'<div class="code-block"><pre><code>{html.escape(chr(10).join(code))}</code></pre></div>'
            )
            continue

        if stripped.startswith("|") and "|" in stripped[1:]:
            flush_paragraph()
            close_lists()
            table_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            out.append(render_table(table_lines))
            continue

        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if image:
            flush_paragraph()
            close_lists()
            out.append(render_image(image.group(1), image.group(2)))
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        item = re.match(r"^(\s*)(-|\d+\.)\s+(.+)$", line)
        if item:
            flush_paragraph()
            indent = len(item.group(1))
            tag = "ul" if item.group(2) == "-" else "ol"
            if not list_stack or indent > list_stack[-1][0]:
                out.append(f"<{tag}>")
                list_stack.append((indent, tag))
            else:
                close_lists(indent)
                if not list_stack or list_stack[-1] != (indent, tag):
                    if list_stack and list_stack[-1][0] == indent:
                        out.append(f"</{list_stack[-1][1]}>")
                        list_stack.pop()
                    out.append(f"<{tag}>")
                    list_stack.append((indent, tag))
            out.append(f"<li>{inline(item.group(3))}</li>")
            i += 1
            continue

        close_lists()
        paragraph.append(stripped)
        i += 1

    flush_paragraph()
    close_lists()
    return "\n".join(out)


def page(content: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>Self Project Documentation</title>
<style>
@page {{ size: A4; margin: 14mm 13mm; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #fff;
  color: #24292f;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif;
  font-size: 13px;
  line-height: 1.55;
}}
.markdown-body {{
  max-width: 980px;
  margin: 0 auto;
}}
h1, h2, h3, h4, h5, h6 {{
  margin: 24px 0 16px;
  font-weight: 600;
  line-height: 1.25;
  page-break-after: avoid;
}}
h1 {{
  padding-bottom: .3em;
  font-size: 2em;
  border-bottom: 1px solid #d8dee4;
}}
h2 {{
  padding-bottom: .3em;
  font-size: 1.5em;
  border-bottom: 1px solid #d8dee4;
}}
h3 {{ font-size: 1.25em; }}
p, ul, table, .code-block {{ margin-top: 0; margin-bottom: 16px; }}
ul {{ padding-left: 2em; }}
li + li {{ margin-top: .25em; }}
a {{ color: #0969da; text-decoration: none; }}
code {{
  padding: .2em .4em;
  margin: 0;
  font-size: 85%;
  white-space: break-spaces;
  background-color: rgba(175, 184, 193, .2);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
}}
.code-block {{
  position: relative;
  page-break-inside: avoid;
}}
pre {{
  padding: 16px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  background-color: #f6f8fa;
  border-radius: 6px;
  border: 1px solid #d8dee4;
  font-size: 12px;
  line-height: 1.45;
}}
pre code {{
  padding: 0;
  background: transparent;
  border-radius: 0;
  font-size: 100%;
}}
table {{
  display: table;
  width: 100%;
  border-spacing: 0;
  border-collapse: collapse;
  page-break-inside: auto;
  font-size: 11.5px;
}}
tr {{ page-break-inside: avoid; }}
th, td {{
  padding: 6px 8px;
  border: 1px solid #d0d7de;
  vertical-align: top;
  word-break: break-word;
}}
th {{
  font-weight: 600;
  background-color: #f6f8fa;
}}
tbody tr:nth-child(even) {{ background-color: #f6f8fa; }}
.diagram {{
  margin: 0 0 16px;
  page-break-inside: avoid;
}}
.diagram svg {{
  width: 100%;
  height: auto;
  display: block;
}}
.diagram img {{
  width: 100%;
  height: auto;
  display: block;
}}
.photo-figure {{
  margin: 0 0 18px;
  page-break-inside: avoid;
}}
.photo-figure img {{
  display: block;
  max-width: 100%;
  max-height: 170mm;
  margin: 0 auto;
  border: 1px solid #d8dee4;
  border-radius: 6px;
}}
.photo-figure figcaption {{
  margin-top: 6px;
  color: #57606a;
  font-size: 12px;
  text-align: center;
}}
.table-image {{
  display: block;
  max-width: 128px;
  max-height: 88px;
  width: auto;
  height: auto;
  object-fit: contain;
  margin: 0 auto;
  border: 1px solid #d8dee4;
  border-radius: 4px;
}}
.node-box {{
  fill: #f6f8fa;
  stroke: #d8dee4;
  stroke-width: 1;
}}
.node-text {{
  fill: #24292f;
  font-size: 22px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif;
}}
    .edge {{
  stroke: #8c959f;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}}
.edge-label-bg {{
  fill: #ffffff;
}}
.edge-label {{
  fill: #57606a;
  font-size: 19px;
  paint-order: stroke;
  stroke: #fff;
  stroke-width: 5px;
  stroke-linejoin: round;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif;
}}
@media print {{
  body {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
  h2 {{ break-before: auto; }}
}}
</style>
</head>
<body>
<article class="markdown-body">
{content}
</article>
</body>
</html>
"""


def find_browser() -> Path | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]
    return next((path for path in candidates if path.exists()), None)


def main() -> int:
    HTML.write_text(page(render_markdown(MARKDOWN.read_text(encoding="utf-8"))), encoding="utf-8")

    browser = find_browser()
    if browser is None:
        print(f"Wrote {HTML}, but no Chrome/Edge executable was found for PDF export.")
        return 1

    with tempfile.TemporaryDirectory(prefix="self-project-doc-browser-", ignore_cleanup_errors=True) as profile:
        subprocess.run(
            [
                str(browser),
                "--headless",
                "--disable-gpu",
                "--disable-crash-reporter",
                "--no-pdf-header-footer",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={PDF}",
                HTML.resolve().as_uri(),
            ],
            check=True,
        )
    print(f"Wrote {HTML}")
    print(f"Wrote {PDF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
