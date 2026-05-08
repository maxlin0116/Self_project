from __future__ import annotations

import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "proposal"
MARKDOWN = OUT_DIR / "raspberry_pi_auto_trash_car_proposal.md"
HTML = OUT_DIR / "raspberry_pi_auto_trash_car_proposal.html"
PDF = OUT_DIR / "raspberry_pi_auto_trash_car_proposal.pdf"


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def render_table(lines: list[str]) -> str:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines
    ]
    head = rows[0]
    body = rows[2:] if len(rows) > 1 and all(set(cell) <= {"-", ":", " "} for cell in rows[1]) else rows[1:]
    out = ["<table>", "<thead><tr>"]
    out.extend(f"<th>{inline(cell)}</th>" for cell in head)
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>")
        out.extend(f"<td>{inline(cell)}</td>" for cell in row)
        out.append("</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def render_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    in_list = False
    list_tag = ""
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list, list_tag
        if in_list:
            out.append(f"</{list_tag}>")
            in_list = False
            list_tag = ""

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            close_list()
            i += 1
            continue

        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if image:
            flush_paragraph()
            close_list()
            alt = html.escape(image.group(1), quote=True)
            src = html.escape(image.group(2), quote=True)
            out.append(
                f'<figure><img src="{src}" alt="{alt}"><figcaption>{alt}</figcaption></figure>'
            )
            i += 1
            continue

        if stripped.startswith("|") and "|" in stripped[1:]:
            flush_paragraph()
            close_list()
            table_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            out.append(render_table(table_lines))
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        item = re.match(r"^(-|\d+\.)\s+(.+)$", stripped)
        if item:
            flush_paragraph()
            tag = "ul" if item.group(1) == "-" else "ol"
            if not in_list or list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                in_list = True
                list_tag = tag
            out.append(f"<li>{inline(item.group(2))}</li>")
            i += 1
            continue

        close_list()
        paragraph.append(stripped)
        i += 1

    flush_paragraph()
    close_list()
    return "\n".join(out)


def page(content: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>自選題企畫書</title>
<style>
@page {{ size: A4; margin: 12mm 12mm; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #fff;
  color: #202124;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif;
  font-size: 10.6px;
  line-height: 1.38;
}}
.markdown-body {{ max-width: 980px; margin: 0 auto; }}
h1, h2, h3 {{
  margin: 10px 0 6px;
  line-height: 1.2;
  page-break-after: avoid;
}}
h1 {{
  font-size: 20px;
  text-align: center;
  padding-bottom: 6px;
  border-bottom: 1px solid #d0d7de;
}}
h2 {{
  font-size: 13px;
  padding-bottom: 2px;
  border-bottom: 1px solid #d8dee4;
}}
p, ol, ul, table {{ margin: 0 0 7px; }}
ol, ul {{ padding-left: 18px; }}
li + li {{ margin-top: 1px; }}
code {{
  padding: .1em .3em;
  background: #f6f8fa;
  border-radius: 4px;
  font-family: Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 90%;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 9.4px;
  page-break-inside: auto;
}}
tr {{ page-break-inside: avoid; }}
th, td {{
  border: 1px solid #d0d7de;
  padding: 3px 4px;
  vertical-align: top;
  word-break: break-word;
}}
th {{
  background: #f6f8fa;
  font-weight: 600;
}}
tbody tr:nth-child(even) {{ background: #fbfbfc; }}
figure {{
  margin: 4px 0 9px;
  text-align: center;
  page-break-inside: avoid;
}}
img {{
  max-width: 78%;
  max-height: 82mm;
  height: auto;
}}
figcaption {{
  margin-top: 3px;
  color: #57606a;
  font-size: 9px;
}}
@media print {{
  body {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
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
    OUT_DIR.mkdir(exist_ok=True)
    HTML.write_text(page(render_markdown(MARKDOWN.read_text(encoding="utf-8"))), encoding="utf-8")

    browser = find_browser()
    if browser is None:
        print(f"Wrote {HTML}, but no Chrome or Edge executable was found for PDF export.")
        return 1

    with tempfile.TemporaryDirectory(prefix="proposal-pdf-browser-", ignore_cleanup_errors=True) as profile:
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
