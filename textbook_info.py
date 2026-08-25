#!/usr/bin/env python3
"""
Export a CSV inventory of the National Smart Education Platform catalogue.

Nothing here is read straight from the catalogue JSON: those part files list
every textbook but carry an EMPTY `ti_items`, so they say nothing about which
files a book publishes. This script therefore exports `catalog_index`, the same
index the web UI shows, which joins the catalogue's tags with the probed asset
index and the course-bundle listings. Per row:

  - whether a PDF / PowerPoint deck is downloadable, and how big
  - 学段/学科/版本/年级/册次, and whether the row declared them or inherited
    them from its course (many titles repeat, and these columns are what tell
    two rows of the same name apart)
  - what the row sits under, so a child titled just "第2课 使用数字设备" or a
    bare deck says which book or course it came from
  - what a row with no file of its own actually is: a course bundle whose
    books are listed separately, a second entry for a book downloadable under
    another Number, or a genuine gap

The `Number` column is the same sequence number the downloader's `--sequence`
and `--range` options take, so the CSV doubles as a lookup table:

    python textbook_info.py
    # find a row you want, note its Number, then:
    python pdf_book_download_from_zxxeducn.py --sequence 1981 --format pptx
"""

import argparse
import collections
import csv
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import catalog_index
import pdf_book_download_from_zxxeducn as downloader

DEFAULT_CSV = Path.home() / "Downloads" / "textbook_info" / "textbook_info.csv"

COLUMNS = [
    "Number",
    "Book ID",
    "Book Name",
    "Stage 学段",
    "Subject 学科",
    "Version 版本",
    "Grade 年级",
    "Volume 册次",
    "Tags From",
    "Row Type",
    "Belongs To",
    "Contains",
    "Same Book As",
    "Has PDF",
    "Has PPTX",
    "PDF Size (MB)",
    "PPTX Size (MB)",
    "Availability",
]

ROW_TYPE = {
    "downloadable": "book",
    "course": "course bundle",
    "duplicate": "duplicate entry",
    "missing": "book",
}


def get_parts(return_type: str = "json"):
    """Return the catalog part URLs (kept for backwards compatibility)."""
    return downloader.get_parts(return_type)


def _megabytes(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f}" if num_bytes else ""


def _availability(entry: Dict[str, Any]) -> str:
    """
    Say what a row without a file of its own actually is.

    Reporting a course bundle as "no downloadable file" reads as a gap in the
    catalogue when its books are simply listed under their own Numbers, and a
    duplicate entry reads as an unobtainable book when the file is one row away.
    """
    kind = entry.get("kind")
    if entry.get("formats"):
        return "public"
    if kind == "course":
        held = len(entry.get("children") or [])
        return f"course bundle - {held} book(s) listed separately" if held else \
               "course bundle - holds nothing downloadable"
    if kind == "duplicate":
        other = entry["duplicate_of"]
        sizes = ", ".join(f"{k.upper()} {v / (1024 * 1024):.1f}MB"
                          for k, v in (other.get("formats") or {}).items())
        return f"this row has no file - #{other['seq']} has it ({sizes})"
    if entry.get("status") == "error":
        return "metadata unavailable"
    return "no downloadable file"


def _belongs_to(entry: Dict[str, Any]) -> str:
    parent = entry.get("parent")
    if not parent:
        return ""
    label = parent.get("title") or ""
    if parent.get("seq"):
        return f"#{parent['seq']} {label}".strip()
    # a parent the catalogue never lists: the id is all there is to name it
    return label or f"(uncatalogued course {parent['id'][:8]})"


# weakest first, so a row is described by the least reliable value it relies on
_ORIGIN_ORDER = [
    ("namesakes", "GUESSED (namesakes agree)"),
    ("title", "read from title"),
    ("derived", "derived from 年级+学制"),
    ("course", "course"),
    ("confirmed", "CONFIRMED by hand"),
    ("declared", "declared"),
]


def _tag_origin(entry: Dict[str, Any]) -> str:
    sources = set((entry.get("tag_sources") or {}).values())
    for key, label in _ORIGIN_ORDER:
        if key in sources:
            return label
    return ""


def _same_book_as(entry: Dict[str, Any]) -> str:
    """
    Other rows that are the same book: identical title AND identical 分类, both
    with files of different sizes - a re-scan, not a different edition. Worth
    knowing before a bulk download fetches the book twice.
    """
    parts = []
    for alt in entry.get("alternates") or []:
        pdf = (alt.get("formats") or {}).get("pdf")
        size = f" ({pdf / (1024 * 1024):.1f}MB" if pdf else " ("
        note = "same file)" if alt.get("same_file") else "other scan)"
        parts.append(f"#{alt['seq']}{size}, {note}" if pdf else f"#{alt['seq']} ({note}")
    return " ".join(parts)


def build_rows(entries: Sequence[Dict[str, Any]]) -> List[List[Any]]:
    rows = []
    for entry in entries:
        formats = entry.get("formats") or {}
        pdf, pptx = formats.get("pdf"), formats.get("pptx")
        children = entry.get("children") or []
        first = lambda key: (entry.get(key) or [""])[0]  # noqa: E731
        rows.append([
            entry["seq"],
            entry["id"],
            entry["title"],
            first("stage"), first("subject"), first("version"),
            first("grade"), first("volume"),
            _tag_origin(entry),
            ROW_TYPE.get(entry.get("kind"), "book"),
            _belongs_to(entry),
            " ".join(f"#{c['seq']}" for c in children if c.get("seq")),
            _same_book_as(entry),
            "yes" if pdf else "no",
            "yes" if pptx else "no",
            _megabytes(pdf) if pdf else "",
            _megabytes(pptx) if pptx else "",
            _availability(entry),
        ])
    return rows


def save_textbook_info(
    csv_path: Path = DEFAULT_CSV,
    refresh_index: bool = False,
    pptx_only: bool = False,
) -> Path:
    """Write the catalogue inventory to `csv_path` and return the path."""
    entries = catalog_index.build_index(refresh=refresh_index)

    if pptx_only:
        entries = [e for e in entries if "pptx" in (e.get("formats") or {})]
        print(f"\n🔎 Filtered to {len(entries)} books that publish a PowerPoint deck")

    rows = build_rows(entries)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # utf-8-sig so Excel opens the Chinese titles correctly.
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    counted = collections.Counter(e.get("kind") for e in entries)
    with_pdf = sum(1 for r in rows if r[COLUMNS.index("Has PDF")] == "yes")
    with_pptx = sum(1 for r in rows if r[COLUMNS.index("Has PPTX")] == "yes")
    origins = collections.Counter(r[COLUMNS.index("Tags From")] or "(none)" for r in rows)
    print(f"\n💾 Wrote {len(rows)} rows to {csv_path}")
    print(f"   • {with_pdf} with a PDF, {with_pptx} with a PowerPoint deck")
    print(f"   • {counted['course']} course bundles (no file of their own; their "
          f"books are separate rows)")
    alts = sum(1 for e in entries if e.get("alternates"))
    print(f"   • {counted['duplicate']} rows are a fileless second entry, "
          f"{counted['missing']} genuinely without a file")
    same = sum(1 for e in entries
               if any(a.get("same_file") for a in (e.get("alternates") or [])))
    print(f"   • {alts} rows are the same book as another row ({same} of them "
          f"byte-identical, the rest a separate scan); see 'Same Book As'")
    print("   • 分类 origin: " + ", ".join(f"{v} {k}" for k, v in origins.most_common()))
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
