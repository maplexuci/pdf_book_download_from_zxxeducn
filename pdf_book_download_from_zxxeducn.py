#!/usr/bin/env python3
"""
Textbook Downloader for the National Smart Education Platform (国家中小学智慧教育平台)

Downloads the freely published PDF textbooks from basic.smartedu.cn.

Version: 3.1.0

How it works
------------
1. `data_version.json` lists 4 catalog "part" files (~3700 books total).
2. Each catalog entry has an `id` but an EMPTY `ti_items`, so the per-book
   details JSON must be fetched separately.
3. The details JSON lists `ti_items`; the printable book is the item whose
   `ti_format` is `pdf` (usually flag `source`, but for some materials
   `source` is a .pptx and the PDF lives under flag `pdf`).
4. `ti_storages` point at `*-ndr-private` hosts which return 401; rewriting
   the host to `*-ndr-oversea` makes them publicly fetchable.

Some books (`download_policy: 2`, e.g. parts of the 体育与健康 series) have no
public details JSON at all - the platform returns 403. Those are reported as
restricted rather than silently failing; only page preview images are exposed
for them and this script does not assemble those into a PDF.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_VERSION_URL = (
    "https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/resources/"
    "tch_material/version/data_version.json"
)

# The details JSON is mirrored across these hosts; try each in order.
DETAILS_HOSTS = ("s-file-1", "s-file-2", "s-file-3")
DETAILS_PATH = "/zxx/ndrv2/resources/tch_material/details/{book_id}.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://basic.smartedu.cn/",
    "Origin": "https://basic.smartedu.cn",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# A real textbook PDF is never this small; anything smaller is an error page.
MIN_PDF_BYTES = 20 * 1024
CHUNK_SIZE = 1 << 18  # 256 KiB

OUTPUT_DIR = Path.home() / "Downloads" / "textbook_download"

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Populated on first use by get_catalog_parts().
_CATALOG_PARTS: Optional[List[List[Dict[str, Any]]]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Make a book title safe to use as a filename."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:180] or "untitled"


def encode_url(url: str) -> str:
    """Percent-encode a URL path (titles contain spaces and CJK characters)."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=quote(parts.path, safe="/")))


def book_display_name(book: Dict[str, Any]) -> str:
    """Build 'publisher + title' the way the platform labels a textbook."""
    title = book.get("title") or book.get("global_title") or book.get("id", "")
    publisher = next(
        (
            tag["tag_name"]
            for tag in book.get("tag_list") or []
            if "版" in (tag.get("tag_name") or "")
        ),
        "",
    )
    return sanitize_filename(f"{publisher}{title}")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def get_parts(return_type: str = "json"):
    """Return the list of catalog part URLs (4 of them)."""
    response = SESSION.get(DATA_VERSION_URL, timeout=30)
    response.raise_for_status()
    if return_type != "json":
        return response.text
    return response.json()["urls"].split(",")


def get_catalog_parts(force: bool = False) -> List[List[Dict[str, Any]]]:
    """
    Fetch every catalog part once and cache it for the rest of the run.

    Each part is ~10 MB, so re-fetching per book (as earlier versions did)
    made range downloads unusably slow.
    """
    global _CATALOG_PARTS
    if _CATALOG_PARTS is not None and not force:
        return _CATALOG_PARTS

    print("📚 Fetching textbook catalog...")
    parts: List[List[Dict[str, Any]]] = []
    for index, url in enumerate(get_parts(), 1):
        try:
            response = SESSION.get(url, timeout=120)
            response.raise_for_status()
            books = response.json()
            print(f"   • catalog {index}: {len(books)} books")
            parts.append(books)
        except (requests.RequestException, json.JSONDecodeError) as exc:
            print(f"   ❌ catalog {index} unavailable ({exc}); continuing")
            parts.append([])

    _CATALOG_PARTS = parts
    print(f"✅ Catalog ready: {sum(len(p) for p in parts)} books total")
    return parts


def flat_catalog() -> List[Dict[str, Any]]:
    """All books from all catalogs in sequence-number order."""
    return [book for part in get_catalog_parts() for book in part]


def get_book_by_sequence_number(
    catalog_urls: Any, sequence_number: int
) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[int]]:
    """
    Locate a book by its 1-based global sequence number.

    `catalog_urls` is accepted for backwards compatibility and ignored - the
    cached catalog is used instead.
    """
    if sequence_number < 1:
        print(f"❌ Invalid sequence number: {sequence_number} (must be >= 1)")
        return None, None, None

    offset = 0
    for catalog_index, part in enumerate(get_catalog_parts()):
        if offset < sequence_number <= offset + len(part):
            position = sequence_number - offset - 1
            return part[position], catalog_index, position
        offset += len(part)

    print(f"❌ Sequence number {sequence_number} not found (catalog holds {offset} books)")
    return None, None, None


# ---------------------------------------------------------------------------
# PDF resolution
# ---------------------------------------------------------------------------

def get_book_details(book_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Fetch a book's details JSON.

    Returns (details, status) where status is one of:
      'ok'         - details retrieved
      'restricted' - platform refuses public access (HTTP 401/403)
      'error'      - network / parse failure
    """
    last_status = "error"
    for host in DETAILS_HOSTS:
        url = f"https://{host}.ykt.cbern.com.cn" + DETAILS_PATH.format(book_id=book_id)
        try:
            response = SESSION.get(url, timeout=30)
        except requests.RequestException as exc:
            print(f"    ❌ {host}: network error ({exc})")
            continue

        if response.ok:
            try:
                return response.json(), "ok"
            except json.JSONDecodeError:
                print(f"    ❌ {host}: details response was not valid JSON")
                continue

        if response.status_code in (401, 403):
            last_status = "restricted"
        else:
            print(f"    ❌ {host}: details returned HTTP {response.status_code}")

    return None, last_status


def pick_pdf_item(details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Choose the PDF among a book's `ti_items`.

    Selection is driven by `ti_format == 'pdf'`, NOT by `ti_file_flag`:
      - most textbooks expose the book under flag 'source'
      - some materials have a .pptx under 'source' and the PDF under 'pdf'
      - flag 'image'/'thumbnail' are folders of page JPEGs, never a PDF
    """
    pdf_items = [
        item
        for item in details.get("ti_items") or []
        if (item.get("ti_format") or "").lower() == "pdf" and item.get("ti_storages")
    ]
    if not pdf_items:
        return None

    priority = {"source": 0, "pdf": 1}
    pdf_items.sort(key=lambda i: (priority.get(i.get("ti_file_flag"), 9),
                                  -(i.get("ti_size") or 0)))
    return pdf_items[0]


def storages_to_public_urls(storages: List[str]) -> List[str]:
    """
    Rewrite CDN hosts into publicly reachable ones.

    `*-ndr-private` hosts answer 401; the `*-ndr-oversea` mirrors serve the
    same object anonymously.
    """
    urls: List[str] = []
    for storage in storages:
        for candidate in (storage.replace("-ndr-private", "-ndr-oversea"), storage):
            candidate = encode_url(candidate)
            if candidate not in urls:
                urls.append(candidate)
    return urls


def get_pdf_url(book_id: str) -> Optional[List[str]]:
    """Return candidate PDF URLs for a book id, or None if it has no PDF."""
    details, status = get_book_details(book_id)
    if details is None:
        if status == "restricted":
            print(f"🔒 {book_id}: platform does not allow public download of this title")
        else:
            print(f"❌ {book_id}: could not fetch details metadata")
        return None

    item = pick_pdf_item(details)
    if item is None:
        formats = sorted(
            {(i.get("ti_file_flag"), i.get("ti_format")) for i in details.get("ti_items") or []}
        )
        print(f"⚠️ {book_id}: no PDF among ti_items {formats or '[]'}")
        return None

    return storages_to_public_urls(item["ti_storages"])


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_pdf_with_cdn_fallback(
    pdf_urls: List[str],
    book_name: str,
    headers: Optional[Dict[str, str]] = None,
    work_path: Optional[str] = None,
    overwrite: bool = False,
) -> bool:
    """
    Download a PDF, trying each CDN mirror in turn.

    Streams to a `.part` file and only renames it into place once the whole
    body has arrived, so an interrupted run never leaves a truncated PDF that
    looks complete. Validity is decided by the `%PDF` magic bytes and a size
    floor - not by Content-Type alone, and not by a 1 MB minimum (real
    textbook PDFs can be a few hundred KB).
    """
    if not pdf_urls:
        print(f"❌ No PDF URLs available for {book_name}")
        return False

    work_dir = Path(work_path) if work_path else OUTPUT_DIR
    work_dir.mkdir(parents=True, exist_ok=True)
    final_path = work_dir / f"{sanitize_filename(book_name)}.pdf"

    if final_path.exists() and not overwrite:
        size_mb = final_path.stat().st_size / (1024 * 1024)
        print(f"    ⏭️  Already downloaded ({size_mb:.1f} MB): {final_path.name}")
        return True

    for url in pdf_urls:
        mirror = urlsplit(url).netloc.split(".")[0]
        part_path = final_path.with_suffix(".pdf.part")
        try:
            with SESSION.get(url, headers=headers, timeout=(15, 120), stream=True) as response:
                if response.status_code != 200:
                    print(f"    ❌ {mirror}: HTTP {response.status_code}")
                    continue

                expected = int(response.headers.get("content-length") or 0)
                written = 0
                first = b""
                with open(part_path, "wb") as handle:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if not chunk:
                            continue
                        if not written:
                            first = chunk[:5]
                            if not first.startswith(b"%PDF"):
                                print(f"    ⚠️ {mirror}: not a PDF "
                                      f"(content-type {response.headers.get('content-type')})")
                                break
                        handle.write(chunk)
                        written += len(chunk)

                if not first.startswith(b"%PDF"):
                    part_path.unlink(missing_ok=True)
                    continue
                if written < MIN_PDF_BYTES:
                    print(f"    ⚠️ {mirror}: implausibly small response ({written} bytes)")
                    part_path.unlink(missing_ok=True)
                    continue
                if expected and written < expected:
                    print(f"    ⚠️ {mirror}: truncated ({written}/{expected} bytes)")
                    part_path.unlink(missing_ok=True)
                    continue

            part_path.replace(final_path)
            print(f"    💾 Downloaded: {final_path.name}  {written / (1024 * 1024):.1f} MB")
            return True

        except requests.exceptions.Timeout:
            print(f"    ⏰ {mirror}: timed out")
            part_path.unlink(missing_ok=True)
        except requests.exceptions.RequestException as exc:
            print(f"    ❌ {mirror}: network error ({exc})")
            part_path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"    ❌ Failed writing {part_path.name}: {exc}")
            part_path.unlink(missing_ok=True)
            return False

    print(f"❌ All CDN mirrors failed for {book_name}")
    return False


def download_book(
    book: Dict[str, Any],
    work_path: Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Tuple[bool, str]:
    """Resolve and download one catalog entry. Returns (ok, reason)."""
    name = book_display_name(book)
    book_id = book.get("id", "")
    print(f"📖 {name}")

    details, status = get_book_details(book_id)
    if details is None:
        if status == "restricted":
            print("    🔒 Not publicly downloadable (platform restricts this title)")
            return False, "restricted by platform"
        print("    ❌ Could not fetch details metadata")
        return False, "details unavailable"

    item = pick_pdf_item(details)
    if item is None:
        print("    ⚠️ No PDF file attached to this entry")
        return False, "no PDF in metadata"

    urls = storages_to_public_urls(item["ti_storages"])
    if dry_run:
        size_mb = (item.get("ti_size") or 0) / (1024 * 1024)
        print(f"    🔗 {urls[0]}")
        print(f"    ℹ️  flag={item.get('ti_file_flag')} size={size_mb:.1f} MB (dry run)")
        return True, "resolved"

    if download_pdf_with_cdn_fallback(urls, name, work_path=str(work_path), overwrite=overwrite):
        return True, "ok"
    return False, "download failed"


# ---------------------------------------------------------------------------
# Download modes
# ---------------------------------------------------------------------------

def _report(results: List[Tuple[str, bool, str]]) -> None:
    ok = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]
    print(f"\n🎉 Done: {len(ok)} succeeded, {len(failed)} failed")
    if failed:
        print("⚠️ Not downloaded:")
        for name, _, reason in failed:
            print(f"   • {name} - {reason}")


def pdf_download(
    table: int = 0,
    item: int = 0,
    single_book: Optional[int] = None,
    download_limit: Optional[int] = None,
    sequence_number: Optional[int] = None,
    book_range: Optional[str] = None,
    book_id: Optional[str] = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> None:
    """Entry point dispatching to the requested download mode."""
    print("🚀 Starting textbook download...")

    work_path = OUTPUT_DIR
    try:
        work_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"❌ Failed to create output directory {work_path}: {exc}")
        return

    if book_id:
        _download_by_book_id(book_id, work_path, overwrite, dry_run)
    elif sequence_number is not None:
        _download_by_sequence_number(sequence_number, work_path, overwrite, dry_run)
    elif book_range:
        _download_by_book_range(book_range, work_path, overwrite, dry_run)
    elif single_book is not None or download_limit is not None or table > 0 or item > 0:
        _download_legacy_mode(table, item, single_book, download_limit,
                              work_path, overwrite, dry_run)
    else:
        print("❌ No download mode specified. Use --help to see available options.")
        return

    print(f"📁 Files are in {work_path}")


def _download_by_book_id(book_id: str, work_path: Path,
                         overwrite: bool, dry_run: bool) -> None:
    print(f"🔍 Book ID: {book_id}")
    details, status = get_book_details(book_id)
    if details is None:
        if status == "restricted":
            print("🔒 Not publicly downloadable (platform restricts this title)")
        else:
            print("❌ Could not fetch details metadata for this ID")
        return

    # Details JSON carries the same title/tag fields as the catalog entry.
    details.setdefault("id", book_id)
    ok, reason = download_book(details, work_path, overwrite, dry_run)
    _report([(book_display_name(details), ok, reason)])


def _download_by_sequence_number(sequence_number: int, work_path: Path,
                                 overwrite: bool, dry_run: bool) -> None:
    print(f"🔍 Sequence number: {sequence_number}")
    book, catalog_index, position = get_book_by_sequence_number(None, sequence_number)
    if not book:
        return
    print(f"📍 Catalog {catalog_index + 1}, position {position + 1}")
    ok, reason = download_book(book, work_path, overwrite, dry_run)
    _report([(book_display_name(book), ok, reason)])


def _download_by_book_range(book_range: str, work_path: Path,
                            overwrite: bool, dry_run: bool) -> None:
    try:
        if "-" in book_range:
            start, end = (int(x) for x in book_range.split("-", 1))
        else:
            start = end = int(book_range)
    except ValueError:
        print(f"❌ Invalid range format: {book_range!r}. Use '200-250' or '200'")
        return

    if start > end:
        start, end = end, start
    start = max(1, start)

    books = flat_catalog()
    if start > len(books):
        print(f"❌ Range starts past the end of the catalog ({len(books)} books)")
        return
    end = min(end, len(books))
    print(f"📚 Downloading books {start}-{end} of {len(books)}")

    results: List[Tuple[str, bool, str]] = []
    for offset, book in enumerate(books[start - 1:end], start):
        print(f"\n[{offset}/{end}]", end=" ")
        ok, reason = download_book(book, work_path, overwrite, dry_run)
        results.append((f"#{offset} {book_display_name(book)}", ok, reason))
        if offset != end:
            time.sleep(1)  # be polite to the CDN
    _report(results)


def _download_legacy_mode(table: int, item: int, single_book: Optional[int],
                          download_limit: Optional[int], work_path: Path,
                          overwrite: bool, dry_run: bool) -> None:
    print("📚 Legacy catalog mode...")
    parts = get_catalog_parts()
    results: List[Tuple[str, bool, str]] = []
    counter = 0
    start_item = item

    for catalog_index in range(table, len(parts)):
        books = parts[catalog_index]
        print(f"\n正在下载目录 {catalog_index + 1}/{len(parts)} 中的电子教材")

        for book in books[start_item:]:
            counter += 1
            if single_book is not None and counter != single_book:
                continue
            if download_limit is not None and len(results) >= download_limit:
                print(f"已达到下载限制 ({download_limit} 本教材)")
                _report(results)
                return

            ok, reason = download_book(book, work_path, overwrite, dry_run)
            results.append((book_display_name(book), ok, reason))

            if single_book is not None:
                _report(results)
                return
            time.sleep(1)

        start_item = 0

    _report(results)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download PDF textbooks from the National Smart Education Platform "
            "(国家中小学智慧教育平台). Files are saved to ~/Downloads/textbook_download/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
DOWNLOAD MODES
  --sequence N     the Nth book across all catalogs (1-based)
  --range A-B      books A through B (also accepts a single number)
  --book-id UUID   one book by its platform id
  --single N       legacy: only the Nth book encountered
  --limit N        legacy: stop after N successful downloads
  --table N        legacy: start at catalog N (0-based)
  --item N         legacy: start at item N within the first catalog (0-based)

OPTIONS
  --dry-run        resolve and print the PDF URL without downloading
  --overwrite      re-download books that already exist locally

EXAMPLES
  python pdf_book_download_from_zxxeducn.py --sequence 1
  python pdf_book_download_from_zxxeducn.py --range 1-5
  python pdf_book_download_from_zxxeducn.py --book-id bdc00134-465d-454b-a541-dcd0cec4d86e
  python pdf_book_download_from_zxxeducn.py --range 1-20 --dry-run

NOTES
  A minority of titles (e.g. parts of the 体育与健康 series) are marked
  download-restricted by the platform: their details metadata returns HTTP 403
  and no PDF is published. Those are reported as restricted and skipped.
""",
    )
    parser.add_argument("--sequence", type=int, help="Download by global sequence number")
    parser.add_argument("--range", type=str, help='Download a range, e.g. "200-250"')
    parser.add_argument("--book-id", type=str, help="Download by platform book id (UUID)")
    parser.add_argument("--single", type=int, help="Legacy: download only book number N")
    parser.add_argument("--limit", type=int, help="Legacy: stop after N downloads")
    parser.add_argument("--table", type=int, default=0, help="Legacy: starting catalog index")
    parser.add_argument("--item", type=int, default=0, help="Legacy: starting item index")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-download books that already exist locally")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve the PDF URL without downloading")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pdf_download(
            table=args.table,
            item=args.item,
            single_book=args.single,
            download_limit=args.limit,
            sequence_number=args.sequence,
            book_range=args.range,
            book_id=args.book_id,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
