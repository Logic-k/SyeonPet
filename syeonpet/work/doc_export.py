"""SyeonPet Document Exporter.

Exports assistant deliverables to TXT, Markdown, CSV (with Excel UTF-8 BOM),
and HWP/Word-friendly HTML formats.
"""
from __future__ import annotations

import csv
import html
import io
from pathlib import Path
import re


def export_to_txt(content: str, filepath: str | Path) -> Path:
    """Save content as plain text file in UTF-8."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def export_to_markdown(content: str, filepath: str | Path) -> Path:
    """Save content as markdown document in UTF-8."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def export_to_csv(content: str, filepath: str | Path) -> Path:
    """Extract tables or lines and export to CSV with UTF-8 BOM for Microsoft Excel."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[list[str]] = []
    # Check if content has markdown table lines
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("|") and trimmed.endswith("|"):
            parts = [p.strip() for p in trimmed.strip("|").split("|")]
            # Skip markdown table separator lines like :---
            if all(re.match(r"^:?-+:?$", p) for p in parts if p):
                continue
            rows.append(parts)
        elif trimmed and not rows:
            # Fallback line-by-line if no table found yet
            continue

    if not rows:
        # Fallback to single column lines
        rows = [[line] for line in content.splitlines() if line.strip()]

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for r in rows:
        writer.writerow(r)

    # Use utf-8-sig (with BOM) for seamless Korean support in Excel
    path.write_text(output.getvalue(), encoding="utf-8-sig")
    return path


def export_to_html(content: str, filepath: str | Path, title: str = "션 업무 산출물") -> Path:
    """Export to an HTML document optimized for Korean HWP and Word copy-paste."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    escaped_title = html.escape(title)
    # Simple line-by-line conversion with table preservation
    body_parts = []
    in_table = False

    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("|") and trimmed.endswith("|"):
            cells = [c.strip() for c in trimmed.strip("|").split("|")]
            if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                continue
            if not in_table:
                body_parts.append("<table border='1' cellspacing='0' cellpadding='6' style='border-collapse: collapse; width: 100%; margin: 12px 0;'>")
                in_table = True
            row_html = "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>"
            body_parts.append(row_html)
        else:
            if in_table:
                body_parts.append("</table>")
                in_table = False
            if trimmed:
                body_parts.append(f"<p style='margin: 4px 0; line-height: 1.6;'>{html.escape(line)}</p>")
            else:
                body_parts.append("<br/>")

    if in_table:
        body_parts.append("</table>")

    body_html = "\n".join(body_parts)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>{escaped_title}</title>
  <style>
    body {{
      font-family: 'Malgun Gothic', '맑은 고딕', 'Apple SD Gothic Neo', sans-serif;
      line-height: 1.6;
      color: #222;
      padding: 24px;
      max-width: 800px;
      margin: 0 auto;
    }}
    table {{ border: 1px solid #ddd; border-collapse: collapse; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; }}
    th {{ background-color: #f5f5f5; font-weight: bold; }}
  </style>
</head>
<body>
  <h2 style="border-bottom: 2px solid #3498DB; padding-bottom: 8px;">{escaped_title}</h2>
  {body_html}
</body>
</html>"""

    path.write_text(html_content, encoding="utf-8")
    return path


def export_deliverable(
    content: str,
    filepath: str | Path,
    title: str = "션 업무 산출물",
) -> Path:
    """Export deliverable content dispatched by file extension."""
    path = Path(filepath)
    ext = path.suffix.lower()
    if ext == ".md":
        return export_to_markdown(content, path)
    if ext in (".html", ".htm"):
        return export_to_html(content, path, title=title)
    if ext == ".csv":
        return export_to_csv(content, path)
    return export_to_txt(content, path)
