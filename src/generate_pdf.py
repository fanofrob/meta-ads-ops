"""
generate_pdf.py
Converts a dated markdown report to a styled PDF using WeasyPrint.

Usage:
    python src/generate_pdf.py                  # uses yesterday's date
    python src/generate_pdf.py --date 2026-03-08

macOS note: WeasyPrint requires GLib/Pango system libraries.
If you see a "cannot load library" error, install via Homebrew:
    brew install pango
Then run with:
    DYLD_LIBRARY_PATH=/opt/homebrew/lib python src/generate_pdf.py
"""

import os
import sys

# macOS: ensure Homebrew libs are on the dynamic library path before importing WeasyPrint
if sys.platform == "darwin" and "DYLD_LIBRARY_PATH" not in os.environ:
    homebrew_lib = "/opt/homebrew/lib"
    if os.path.isdir(homebrew_lib):
        os.environ["DYLD_LIBRARY_PATH"] = homebrew_lib
        # Re-exec with updated environment so cffi picks it up
        os.execv(sys.executable, [sys.executable] + sys.argv)

import argparse
from datetime import date, timedelta
from pathlib import Path

import markdown
import weasyprint

REPORTS_DIR = Path("outputs/reports")

CSS_STYLES = """
@page {
    size: A4;
    margin: 20mm 18mm 22mm 18mm;
    @top-center {
        content: "Meta Ads Daily Report";
        font-family: 'Helvetica Neue', Arial, sans-serif;
        font-size: 9pt;
        color: #888;
    }
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-family: 'Helvetica Neue', Arial, sans-serif;
        font-size: 9pt;
        color: #888;
    }
}

body {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.5;
    color: #1a1a1a;
}

h1 {
    font-size: 18pt;
    font-weight: 700;
    color: #1a1a1a;
    margin: 0 0 4px 0;
    border-bottom: 2px solid #1a73e8;
    padding-bottom: 6px;
}

h2 {
    font-size: 13pt;
    font-weight: 600;
    color: #1a1a1a;
    margin: 20px 0 6px 0;
    border-bottom: 1px solid #e0e0e0;
    padding-bottom: 4px;
    page-break-after: avoid;
}

h3 {
    font-size: 11pt;
    font-weight: 600;
    color: #333;
    margin: 14px 0 4px 0;
    page-break-after: avoid;
}

p {
    margin: 4px 0 8px 0;
}

em {
    color: #555;
    font-style: italic;
}

strong {
    font-weight: 600;
}

a {
    color: #1a73e8;
    text-decoration: none;
}

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 9pt;
    margin: 8px 0 14px 0;
    page-break-inside: avoid;
}

th {
    background-color: #f0f4ff;
    color: #1a1a1a;
    font-weight: 600;
    text-align: left;
    padding: 5px 8px;
    border: 1px solid #c8d0e0;
}

td {
    padding: 4px 8px;
    border: 1px solid #dde3ed;
    vertical-align: top;
}

tr:nth-child(even) td {
    background-color: #f8f9fc;
}

ul, ol {
    margin: 4px 0 8px 0;
    padding-left: 20px;
}

li {
    margin-bottom: 4px;
}

code {
    font-family: 'Courier New', monospace;
    font-size: 8.5pt;
    background-color: #f4f4f4;
    padding: 1px 4px;
    border-radius: 3px;
}

pre {
    background-color: #f4f4f4;
    padding: 10px;
    border-radius: 4px;
    font-size: 8pt;
    overflow-x: auto;
}

hr {
    border: none;
    border-top: 1px solid #e0e0e0;
    margin: 16px 0;
}

blockquote {
    border-left: 3px solid #1a73e8;
    margin: 8px 0;
    padding: 4px 12px;
    color: #444;
    background-color: #f0f4ff;
}
"""


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")

    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br"],
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Meta Ads Report</title>
</head>
<body>
{html_body}
</body>
</html>"""

    css = weasyprint.CSS(string=CSS_STYLES)
    weasyprint.HTML(string=html).write_pdf(str(pdf_path), stylesheets=[css])
    print(f"[OK] PDF written to {pdf_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a markdown report to PDF.")
    parser.add_argument(
        "--date",
        default=str(date.today() - timedelta(days=1)),
        help="Report date in YYYY-MM-DD format (default: yesterday)",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Convert the daily report variant (YYYY-MM-DD-daily.md).",
    )
    args = parser.parse_args()

    report_date = args.date
    suffix = "-daily" if args.daily else ""
    md_path = REPORTS_DIR / f"{report_date}{suffix}.md"
    pdf_path = REPORTS_DIR / f"{report_date}{suffix}.pdf"

    if not md_path.exists():
        print(f"[ERROR] Markdown report not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Converting {md_path} → {pdf_path} ...")
    md_to_pdf(md_path, pdf_path)


if __name__ == "__main__":
    main()
