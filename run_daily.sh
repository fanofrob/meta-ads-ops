#!/usr/bin/env bash
# run_daily.sh — Full daily Meta Ads pipeline
# Usage: ./run_daily.sh
# Fetches fresh data, generates the markdown report, and converts it to PDF.

set -euo pipefail

PYTHON=".venv/bin/python"

echo "========================================"
echo " Meta Ads Daily Pipeline — $(date '+%Y-%m-%d %H:%M')"
echo "========================================"
echo ""

echo "[1/6] Fetching campaigns..."
$PYTHON src/fetch_campaigns.py

echo ""
echo "[2/6] Fetching ad sets..."
$PYTHON src/fetch_adsets.py

echo ""
echo "[3/6] Fetching ads..."
$PYTHON src/fetch_ads.py

echo ""
echo "[4/6] Fetching insights (yesterday snapshot + 7-day primary + 30-day history)..."
$PYTHON src/fetch_insights.py
TODAY=$(date '+%Y-%m-%d')
if ls data/raw/insights_7d_${TODAY}.json &>/dev/null; then
  echo "  7-day file for today already exists — skipping."
else
  $PYTHON src/fetch_insights.py --last7d
fi
if ls data/raw/insights_30d_${TODAY}.json &>/dev/null; then
  echo "  30-day file for today already exists — skipping historical fetch."
else
  $PYTHON src/fetch_insights.py --historical
fi

echo ""
echo "[5/5] Checking landing pages..."
$PYTHON src/check_landing_pages.py

echo ""
echo "[6/6] Generating reports and PDFs..."
echo "  [5a] 7-day report..."
$PYTHON src/report.py
$PYTHON src/generate_pdf.py
echo "  [5b] Daily report (yesterday + trend signals)..."
$PYTHON src/report.py --daily
$PYTHON src/generate_pdf.py --daily

echo ""
echo "========================================"
echo " Done. Reports saved to outputs/reports/"
echo "========================================"
