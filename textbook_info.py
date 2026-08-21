#!/usr/bin/env python3
"""
Export a CSV inventory of the National Smart Education Platform catalogue.

The catalogue part files list every textbook but carry an EMPTY `ti_items`,
so they say nothing about which files a book actually publishes. This script
therefore builds on the asset index from `pdf_book_download_from_zxxeducn`,
which probes each book's details JSON, and records per book:

  - whether a PDF is downloadable
  - whether a PowerPoint (.pptx) deck is downloadable
  - the size of each
  - whether the platform restricts the title entirely

The `Number` column is the same sequence number the downloader's `--sequence`
and `--range` options take, so the CSV doubles as a lookup table:

    python textbook_info.py
    # find a row you want, note its Number, then:
    python pdf_book_download_from_zxxeducn.py --sequence 1981 --format pptx
"""

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pdf_book_download_from_zxxeducn as downloader

DEFAULT_CSV = Path.home() / "Downloads" / "textbook_info" / "textbook_info.csv"

COLUMNS = [
    "Number",
    "Book ID",
    "Book Name",
    "Has PDF",
    "Has PPTX",
    "PDF Size (MB)",
    "PPTX Size (MB)",
    "Availability",
]

AVAILABILITY = {
    "ok": "public",
    "restricted": "no downloadable file",
    "error": "metadata unavailable",
}


def get_parts(return_type: str = "json"):
    """Return the catalog part URLs (kept for backwards compatibility)."""
    return downloader.get_parts(return_type)


def _megabytes(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f}" if num_bytes else ""


def build_rows(entries: Sequence[Dict[str, Any]]) -> List[List[Any]]:
    rows = []
    for entry in entries:
        formats = entry.get("formats") or {}
        pdf = formats.get("pdf")
        pptx = formats.get("pptx")
        rows.append([
            entry["seq"],
            entry["id"],
            entry["title"],
            "yes" if pdf else "no",
            "yes" if pptx else "no",
            _megabytes(pdf["size"]) if pdf else "",
            _megabytes(pptx["size"]) if pptx else "",
            AVAILABILITY.get(entry.get("status", "error"), entry.get("status", "")),
        ])
    return rows


def save_textbook_info(
    csv_path: Path = DEFAULT_CSV,
    refresh_index: bool = False,
    pptx_only: bool = False,
) -> Path:
    """Write the catalogue inventory to `csv_path` and return the path."""
    entries = downloader.build_asset_index(refresh=refresh_index)
    downloader.index_summary(entries)

    if pptx_only:
        entries = downloader.books_with_format(entries, "pptx")
        print(f"\n🔎 Filtered to {len(entries)} books that publish a PowerPoint deck")

    rows = build_rows(entries)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # utf-8-sig so Excel opens the Chinese titles correctly.
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    with_pdf = sum(1 for r in rows if r[3] == "yes")
    with_pptx = sum(1 for r in rows if r[4] == "yes")
    print(f"\n💾 Wrote {len(rows)} rows to {csv_path}")
    print(f"   • {with_pdf} with a PDF, {with_pptx} with a PowerPoint deck")
    print("   • 'Number' is the value to pass to --sequence / --range")
    return csv_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a CSV inventory of the textbook catalogue, including "
                    "which books publish a downloadable PDF and/or .pptx deck.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_CSV,
                        help=f"CSV destination (default: {DEFAULT_CSV})")
    parser.add_argument("--refresh-index", action="store_true",
                        help="Rebuild the cached format index (~3700 requests)")
    parser.add_argument("--pptx-only", action="store_true",
                        help="Only include books that publish a PowerPoint deck")
    args = parser.parse_args(argv)

    try:
        save_textbook_info(args.output, args.refresh_index, args.pptx_only)
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
