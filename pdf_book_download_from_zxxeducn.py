#!/usr/bin/env python3
"""
Textbook Downloader for the National Smart Education Platform (国家中小学智慧教育平台)

Downloads the freely published textbook files from basic.smartedu.cn.

Version: 3.2.0

How it works
------------
1. `data_version.json` lists 4 catalog "part" files (~3700 books total).
2. Each catalog entry has an `id` but an EMPTY `ti_items`, so the per-book
   details JSON must be fetched separately.
3. The details JSON lists `ti_items`. The downloadable file is picked by
   `ti_format`, not by `ti_file_flag`:
     - 3132 books ship the book as a PDF under flag `source`
     - 252 books (mostly 信息科技 lesson materials) ship a .pptx deck under
       flag `source` and the same lesson as a PDF under flag `pdf`
     - flags `image` / `thumbnail` are folders of page JPEGs, never a file
4. `ti_storages` point at `*-ndr-private` hosts which return 401; rewriting
   the host to `*-ndr-oversea` makes them publicly fetchable.

Special-education titles are published as `thematic_course` bundles rather
than standalone documents. The child document's own id returns 403 on
DETAILS_PATH; its real record - ti_items and all - lives in the parent
course listing at SPECIAL_EDU_PATH. The resolver falls back to that listing
automatically.

That leaves 359 of the 3743 catalogued entries with no file of their own:
302 `thematic_course` container nodes (course bundles, not documents - their
children are catalogued separately and are downloadable) and 57
special-education `sub` entries whose parent course lists no file for them.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
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

# Special-education titles are published as `thematic_course` bundles instead
# of standalone documents: the course id resolves to a list of child
# resources, and it is the CHILD that carries ti_items / a source PDF. The
# child's own id 403s on DETAILS_PATH, which is why these looked unavailable.
SPECIAL_EDU_PATH = "/zxx/ndrs/special_edu/thematic_course/{course_id}/resources/list.json"

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

# Downloadable asset kinds. `aliases` are the ti_format values that count as
# this kind; `magic` are the leading bytes a valid file must start with.
ASSET_KINDS: Dict[str, Dict[str, Any]] = {
    "pdf": {
        "aliases": {"pdf"},
        "magic": (b"%PDF",),
        "label": "PDF",
    },
    "pptx": {
        # .pptx is a zip container, hence the PK signature.
        "aliases": {"pptx", "ppt"},
        "magic": (b"PK\x03\x04", b"PK\x05\x06", b"\xd0\xcf\x11\xe0"),
        "label": "PowerPoint",
    },
}

# When several items share a format, prefer the one filed under `source`.
FLAG_PRIORITY = {"source": 0, "pdf": 1, "pptx": 1}

# A real textbook file is never this small; anything smaller is an error page.
MIN_ASSET_BYTES = 20 * 1024
CHUNK_SIZE = 1 << 18  # 256 KiB

OUTPUT_DIR = Path.home() / "Downloads" / "textbook_download"


def cache_dir() -> Path:
    """
    Where derived state lives.

    Deliberately NOT inside OUTPUT_DIR: the index is app state, not a
    download, and clearing out downloaded books should not cost a full
    re-scan of the catalogue.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "textbook-downloader"


CACHE_DIR = cache_dir()
INDEX_PATH = CACHE_DIR / "asset_index.json"
SPECIAL_EDU_INDEX_PATH = CACHE_DIR / "special_edu_index.json"
LEGACY_INDEX_PATH = OUTPUT_DIR / ".asset_index.json"
INDEX_MAX_AGE = 7 * 24 * 3600  # refetch the asset index after a week

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Populated on first use by get_catalog_parts().
_CATALOG_PARTS: Optional[List[List[Dict[str, Any]]]] = None

# One requests.Session per worker thread, for the concurrent index scan.
_THREAD_LOCAL = threading.local()

# child resource id -> resource record, built from the thematic_course lists
_SPECIAL_EDU: Optional[Dict[str, Dict[str, Any]]] = None
_SPECIAL_EDU_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def thread_session() -> requests.Session:
    """A per-thread session, so the index scan can run concurrently."""
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=4, max_retries=2
        )
        session.mount("https://", adapter)
        _THREAD_LOCAL.session = session
    return session


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


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def resolve_formats(spec: str) -> List[str]:
    """Turn a --format value into an ordered list of asset kinds."""
    if spec == "all":
        return list(ASSET_KINDS)
    return [spec]


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
# Asset resolution
# ---------------------------------------------------------------------------

def build_special_edu_index(refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Map every special-education child resource id to its full record.

    Each `thematic_course` entry in the catalogue exposes its children at
    SPECIAL_EDU_PATH, and those children carry the ti_items (including the
    source PDF) that the per-document details endpoint refuses to serve.
    """
    global _SPECIAL_EDU
    if _SPECIAL_EDU is not None and not refresh:
        return _SPECIAL_EDU

    with _SPECIAL_EDU_LOCK:
        if _SPECIAL_EDU is not None and not refresh:
            return _SPECIAL_EDU

        if not refresh and SPECIAL_EDU_INDEX_PATH.exists():
            try:
                cached = json.loads(SPECIAL_EDU_INDEX_PATH.read_text(encoding="utf-8"))
                if time.time() - cached.get("generated", 0) < INDEX_MAX_AGE:
                    _SPECIAL_EDU = cached.get("children") or {}
                    return _SPECIAL_EDU
            except (json.JSONDecodeError, OSError):
                pass

        courses = [b["id"] for b in flat_catalog()
                   if b.get("resource_type_code") == "thematic_course"]
        print(f"🔎 Resolving {len(courses)} special-education course bundles...")

        children: Dict[str, Dict[str, Any]] = {}
        lock = threading.Lock()

        def fetch(course_id: str) -> None:
            for host in DETAILS_HOSTS:
                url = f"https://{host}.ykt.cbern.com.cn" + SPECIAL_EDU_PATH.format(
                    course_id=course_id)
                try:
                    response = thread_session().get(url, timeout=30)
                except requests.RequestException:
                    continue
                if not response.ok:
                    continue
                try:
                    items = response.json()
                except json.JSONDecodeError:
                    continue
                with lock:
                    for item in items or []:
                        if item.get("id"):
                            children[item["id"]] = item
                return

        with ThreadPoolExecutor(10) as pool:
            list(pool.map(fetch, courses))

        _SPECIAL_EDU = children
        try:
            SPECIAL_EDU_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            SPECIAL_EDU_INDEX_PATH.write_text(
                json.dumps({"generated": time.time(), "children": children},
                           ensure_ascii=False),
                encoding="utf-8")
        except OSError as exc:
            print(f"⚠️ Could not cache special-education index: {exc}")
        print(f"✅ Special-education index: {len(children)} child resources")
        return _SPECIAL_EDU


def thread_session() -> requests.Session:
    """A per-thread session, so index scans can run concurrently."""
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.mount("https://", requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=4, max_retries=2))
        _THREAD_LOCAL.session = session
    return session


def get_book_details(
    book_id: str, session: Optional[requests.Session] = None, quiet: bool = False
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Fetch a book's details JSON.

    Returns (details, status) where status is one of:
      'ok'         - details retrieved
      'restricted' - platform refuses public access (HTTP 401/403)
      'error'      - network / parse failure
    """
    session = session or SESSION
    last_status = "error"
    for host in DETAILS_HOSTS:
        url = f"https://{host}.ykt.cbern.com.cn" + DETAILS_PATH.format(book_id=book_id)
        try:
            response = session.get(url, timeout=30)
        except requests.RequestException as exc:
            if not quiet:
                print(f"    ❌ {host}: network error ({exc})")
            continue

        if response.ok:
            try:
                return response.json(), "ok"
            except json.JSONDecodeError:
                if not quiet:
                    print(f"    ❌ {host}: details response was not valid JSON")
                continue

        if response.status_code in (401, 403):
            last_status = "restricted"
        elif not quiet:
            print(f"    ❌ {host}: details returned HTTP {response.status_code}")

    if last_status == "restricted":
        # Not actually restricted - it may be a special-education child
        # resource, whose record lives in its parent course listing.
        record = build_special_edu_index().get(book_id)
        if record:
            return record, "ok"

    return None, last_status


def pick_asset(details: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    """
    Choose the best `ti_items` entry of the given kind ('pdf' or 'pptx').

    Selection is driven by `ti_format`, NOT by `ti_file_flag`:
      - most textbooks expose the book as a PDF under flag 'source'
      - the 信息科技 lesson materials have a .pptx under 'source' and the
        matching PDF under flag 'pdf'
      - flags 'image'/'thumbnail' are folders of page JPEGs, never a file
    """
    aliases = ASSET_KINDS[kind]["aliases"]
    candidates = [
        item
        for item in details.get("ti_items") or []
        if (item.get("ti_format") or "").lower() in aliases and item.get("ti_storages")
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda i: (FLAG_PRIORITY.get(i.get("ti_file_flag"), 9), -(i.get("ti_size") or 0))
    )
    return candidates[0]


def available_kinds(details: Dict[str, Any]) -> List[str]:
    """Which asset kinds this book actually publishes."""
    return [kind for kind in ASSET_KINDS if pick_asset(details, kind)]


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


def get_asset_urls(book_id: str, kind: str = "pdf") -> Optional[List[str]]:
    """Return candidate download URLs of `kind` for a book id."""
    details, status = get_book_details(book_id)
    if details is None:
        if status == "restricted":
            print(f"🔒 {book_id}: no downloadable file published (online preview only)")
        else:
            print(f"❌ {book_id}: could not fetch details metadata")
        return None

    item = pick_asset(details, kind)
    if item is None:
        formats = sorted(
            {(i.get("ti_file_flag"), i.get("ti_format")) for i in details.get("ti_items") or []}
        )
        print(f"⚠️ {book_id}: no {kind} among ti_items {formats or '[]'}")
        return None

    return storages_to_public_urls(item["ti_storages"])


def get_pdf_url(book_id: str) -> Optional[List[str]]:
    """Backwards-compatible alias for `get_asset_urls(book_id, 'pdf')`."""
    return get_asset_urls(book_id, "pdf")


# ---------------------------------------------------------------------------
# Asset index (which books publish which formats)
# ---------------------------------------------------------------------------

def build_asset_index(workers: int = 12, refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Probe every catalogued book's details JSON and record which formats it
    publishes. Cached on disk because it costs ~3700 requests.
    """
    source = INDEX_PATH if INDEX_PATH.exists() else LEGACY_INDEX_PATH
    if not refresh and source.exists():
        try:
            cached = json.loads(source.read_text(encoding="utf-8"))
            age = time.time() - cached.get("generated", 0)
            if age < INDEX_MAX_AGE and cached.get("books"):
                print(f"🗂️  Using cached asset index ({len(cached['books'])} books, "
                      f"{age / 3600:.0f}h old; --refresh-index to rebuild)")
                return cached["books"]
        except (json.JSONDecodeError, OSError):
            pass

    books = flat_catalog()
    print(f"🔎 Scanning {len(books)} books for available formats "
          f"({workers} workers, this takes a few minutes)...")

    entries: List[Dict[str, Any]] = []
    lock = threading.Lock()
    counter = {"n": 0}

    def probe(indexed: Tuple[int, Dict[str, Any]]) -> None:
        seq, book = indexed
        book_id = book.get("id", "")
        details, status = get_book_details(book_id, session=thread_session(), quiet=True)
        entry = {
            "seq": seq,
            "id": book_id,
            "title": book_display_name(book),
            "status": status,
            "formats": {},
        }
        if details is not None:
            for kind in ASSET_KINDS:
                item = pick_asset(details, kind)
                if item:
                    entry["formats"][kind] = {
                        "flag": item.get("ti_file_flag"),
                        "size": item.get("ti_size") or 0,
                    }
        with lock:
            entries.append(entry)
            counter["n"] += 1
            if counter["n"] % 500 == 0:
                print(f"   • scanned {counter['n']}/{len(books)}")

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(probe, enumerate(books, 1)))

    entries.sort(key=lambda e: e["seq"])
    try:
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDEX_PATH.write_text(
            json.dumps({"generated": time.time(), "books": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"💾 Asset index cached at {INDEX_PATH}")
    except OSError as exc:
        print(f"⚠️ Could not cache asset index: {exc}")

    return entries


def index_summary(entries: Sequence[Dict[str, Any]]) -> None:
    total = len(entries)
    restricted = sum(1 for e in entries if e["status"] == "restricted")
    errored = sum(1 for e in entries if e["status"] == "error")
    with_pdf = sum(1 for e in entries if "pdf" in e["formats"])
    with_pptx = sum(1 for e in entries if "pptx" in e["formats"])
    both = sum(1 for e in entries if {"pdf", "pptx"} <= set(e["formats"]))
    print(f"\n📊 Catalog: {total} books")
    print(f"   • PDF available        : {with_pdf}")
    print(f"   • PowerPoint available : {with_pptx}  (of which {both} also have a PDF)")
    print(f"   • no downloadable file : {restricted}")
    if errored:
        print(f"   • metadata unreachable  : {errored}")


def books_with_format(entries: Sequence[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    return [e for e in entries if kind in e["formats"]]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_asset(
    urls: List[str],
    book_name: str,
    kind: str = "pdf",
    work_path: Optional[str] = None,
    overwrite: bool = False,
    extension: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    quiet: bool = False,
) -> bool:
    """
    Download one asset, trying each CDN mirror in turn.

    Streams to a `.part` file and only renames it into place once the whole
    body has arrived, so an interrupted run never leaves a truncated file
    that looks complete. Validity is decided by the format's magic bytes and
    Content-Length - not by Content-Type alone, and not by a size threshold
    (real textbook files can be a few hundred KB).

    `progress(written, expected)` is called as bytes arrive and `cancelled()`
    is polled to abort mid-stream, so a UI can drive this directly; `quiet`
    suppresses the CLI prints.
    """
    if not urls:
        say(f"❌ No {kind} URLs available for {book_name}")
        return False

    say = (lambda *a, **k: None) if quiet else print
    magic = ASSET_KINDS[kind]["magic"]
    suffix = extension or kind
    work_dir = Path(work_path) if work_path else OUTPUT_DIR
    work_dir.mkdir(parents=True, exist_ok=True)
    final_path = work_dir / f"{sanitize_filename(book_name)}.{suffix}"

    if final_path.exists() and not overwrite:
        say(f"    ⏭️  Already downloaded ({human_size(final_path.stat().st_size)}): "
              f"{final_path.name}")
        return True

    part_path = final_path.with_name(final_path.name + ".part")
    for url in urls:
        mirror = urlsplit(url).netloc.split(".")[0]
        try:
            with SESSION.get(url, timeout=(15, 120), stream=True) as response:
                if response.status_code != 200:
                    say(f"    ❌ {mirror}: HTTP {response.status_code}")
                    continue

                expected = int(response.headers.get("content-length") or 0)
                written = 0
                head = b""
                with open(part_path, "wb") as handle:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if not chunk:
                            continue
                        if not written:
                            head = chunk[:8]
                            if not head.startswith(magic):
                                say(f"    ⚠️ {mirror}: not a valid {kind} "
                                      f"(content-type "
                                      f"{response.headers.get('content-type')})")
                                break
                        handle.write(chunk)
                        written += len(chunk)
                        if progress:
                            progress(written, expected)
                        if cancelled and cancelled():
                            say(f"    ⏹️  {mirror}: cancelled")
                            part_path.unlink(missing_ok=True)
                            return False

                if not head.startswith(magic):
                    part_path.unlink(missing_ok=True)
                    continue
                if written < MIN_ASSET_BYTES:
                    say(f"    ⚠️ {mirror}: implausibly small response ({written} bytes)")
                    part_path.unlink(missing_ok=True)
                    continue
                if expected and written < expected:
                    say(f"    ⚠️ {mirror}: truncated ({written}/{expected} bytes)")
                    part_path.unlink(missing_ok=True)
                    continue

            part_path.replace(final_path)
            say(f"    💾 Downloaded: {final_path.name}  {human_size(written)}")
            return True

        except requests.exceptions.Timeout:
            say(f"    ⏰ {mirror}: timed out")
            part_path.unlink(missing_ok=True)
        except requests.exceptions.RequestException as exc:
            say(f"    ❌ {mirror}: network error ({exc})")
            part_path.unlink(missing_ok=True)
        except OSError as exc:
            say(f"    ❌ Failed writing {part_path.name}: {exc}")
            part_path.unlink(missing_ok=True)
            return False

    say(f"❌ All CDN mirrors failed for {book_name} ({kind})")
    return False


def download_pdf_with_cdn_fallback(
    pdf_urls: List[str],
    book_name: str,
    headers: Optional[Dict[str, str]] = None,
    work_path: Optional[str] = None,
    overwrite: bool = False,
) -> bool:
    """Backwards-compatible wrapper around `download_asset(..., 'pdf')`."""
    return download_asset(pdf_urls, book_name, "pdf", work_path, overwrite)


def download_book(
    book: Dict[str, Any],
    work_path: Path,
    formats: Sequence[str] = ("pdf",),
    overwrite: bool = False,
    dry_run: bool = False,
) -> List[Tuple[str, bool, str]]:
    """
    Resolve and download the requested formats for one catalog entry.

    Returns one (kind, ok, reason) tuple per requested format.
    """
    name = book_display_name(book)
    book_id = book.get("id", "")
    print(f"📖 {name}")

    details, status = get_book_details(book_id)
    if details is None:
        if status == "restricted":
            print("    🔒 No downloadable file published (online preview only)")
            return [(kind, False, "restricted by platform") for kind in formats]
        print("    ❌ Could not fetch details metadata")
        return [(kind, False, "details unavailable") for kind in formats]

    results: List[Tuple[str, bool, str]] = []
    for kind in formats:
        item = pick_asset(details, kind)
        if item is None:
            offered = available_kinds(details)
            note = f"no {kind} published" + (f" (has: {', '.join(offered)})" if offered else "")
            print(f"    ⚠️ {note}")
            results.append((kind, False, note))
            continue

        urls = storages_to_public_urls(item["ti_storages"])
        extension = (item.get("ti_format") or kind).lower()
        if dry_run:
            print(f"    🔗 [{kind}] {urls[0]}")
            print(f"    ℹ️  flag={item.get('ti_file_flag')} "
                  f"size={human_size(item.get('ti_size') or 0)} (dry run)")
            results.append((kind, True, "resolved"))
            continue

        ok = download_asset(urls, name, kind, str(work_path), overwrite, extension)
        results.append((kind, ok, "ok" if ok else "download failed"))

    return results


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


def _label(book_name: str, kind: str, prefix: str = "") -> str:
    return f"{prefix}{book_name} [{kind}]"


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
    formats: Optional[Sequence[str]] = None,
    all_pptx: bool = False,
    list_format: Optional[str] = None,
    refresh_index: bool = False,
) -> None:
    """Entry point dispatching to the requested download mode."""
    work_path = OUTPUT_DIR
    try:
        work_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"❌ Failed to create output directory {work_path}: {exc}")
        return

    if list_format:
        _list_mode(list_format, refresh_index)
        return

    print("🚀 Starting textbook download...")

    if all_pptx:
        _download_all_of_format("pptx", work_path, formats, overwrite, dry_run,
                                download_limit, refresh_index)
        print(f"📁 Files are in {work_path}")
        return

    formats = formats or ("pdf",)
    if book_id:
        _download_by_book_id(book_id, work_path, formats, overwrite, dry_run)
    elif sequence_number is not None:
        _download_by_sequence_number(sequence_number, work_path, formats, overwrite, dry_run)
    elif book_range:
        _download_by_book_range(book_range, work_path, formats, overwrite, dry_run)
    elif single_book is not None or download_limit is not None or table > 0 or item > 0:
        _download_legacy_mode(table, item, single_book, download_limit,
                              work_path, formats, overwrite, dry_run)
    else:
        print("❌ No download mode specified. Use --help to see available options.")
        return

    print(f"📁 Files are in {work_path}")


def _list_mode(kind: str, refresh_index: bool) -> None:
    """Print every book that publishes the given format."""
    entries = build_asset_index(refresh=refresh_index)
    index_summary(entries)

    matches = books_with_format(entries, kind)
    total = sum(e["formats"][kind]["size"] for e in matches)
    print(f"\n📄 {len(matches)} books publish a {ASSET_KINDS[kind]['label']} "
          f"file ({human_size(total)} total):\n")
    for entry in matches:
        info = entry["formats"][kind]
        others = ", ".join(k for k in entry["formats"] if k != kind)
        extra = f"  (+{others})" if others else ""
        print(f"  #{entry['seq']:<5d} {human_size(info['size']):>9s}  "
              f"{entry['title'][:52]}{extra}")
    if kind == "pptx":
        print("\nDownload them all with: --all-pptx")


def _download_all_of_format(
    kind: str,
    work_path: Path,
    formats: Optional[Sequence[str]],
    overwrite: bool,
    dry_run: bool,
    limit: Optional[int],
    refresh_index: bool,
) -> None:
    """Dedicated route: download every book that publishes `kind`."""
    entries = build_asset_index(refresh=refresh_index)
    matches = books_with_format(entries, kind)
    if limit:
        matches = matches[:limit]

    # Default to this route's own format; an explicit --format wins.
    wanted = list(formats) if formats else [kind]
    estimate = sum(
        entry["formats"][k]["size"] for entry in matches for k in wanted if k in entry["formats"]
    )
    print(f"\n📦 {len(matches)} books publish a {ASSET_KINDS[kind]['label']} file; "
          f"fetching {', '.join(wanted)} (~{human_size(estimate)})")

    results: List[Tuple[str, bool, str]] = []
    for position, entry in enumerate(matches, 1):
        print(f"\n[{position}/{len(matches)}] #{entry['seq']}", end=" ")
        book = {"id": entry["id"], "title": entry["title"]}
        for asset_kind, ok, reason in download_book(book, work_path, wanted, overwrite, dry_run):
            results.append((_label(entry["title"], asset_kind, f"#{entry['seq']} "), ok, reason))
        if position != len(matches):
            time.sleep(1)  # be polite to the CDN
    _report(results)


def _download_by_book_id(book_id: str, work_path: Path, formats: Sequence[str],
                         overwrite: bool, dry_run: bool) -> None:
    print(f"🔍 Book ID: {book_id}")
    details, status = get_book_details(book_id)
    if details is None:
        if status == "restricted":
            print("🔒 No downloadable file published (online preview only)")
        else:
            print("❌ Could not fetch details metadata for this ID")
        return

    details.setdefault("id", book_id)
    name = book_display_name(details)
    results = download_book(details, work_path, formats, overwrite, dry_run)
    _report([(_label(name, kind), ok, reason) for kind, ok, reason in results])


def _download_by_sequence_number(sequence_number: int, work_path: Path, formats: Sequence[str],
                                 overwrite: bool, dry_run: bool) -> None:
    print(f"🔍 Sequence number: {sequence_number}")
    book, catalog_index, position = get_book_by_sequence_number(None, sequence_number)
    if not book:
        return
    print(f"📍 Catalog {catalog_index + 1}, position {position + 1}")
    name = book_display_name(book)
    results = download_book(book, work_path, formats, overwrite, dry_run)
    _report([(_label(name, kind), ok, reason) for kind, ok, reason in results])


def _download_by_book_range(book_range: str, work_path: Path, formats: Sequence[str],
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
        name = book_display_name(book)
        for kind, ok, reason in download_book(book, work_path, formats, overwrite, dry_run):
            results.append((_label(name, kind, f"#{offset} "), ok, reason))
        if offset != end:
            time.sleep(1)  # be polite to the CDN
    _report(results)


def _download_legacy_mode(table: int, item: int, single_book: Optional[int],
                          download_limit: Optional[int], work_path: Path,
                          formats: Sequence[str], overwrite: bool, dry_run: bool) -> None:
    print("📚 Legacy catalog mode...")
    parts = get_catalog_parts()
    results: List[Tuple[str, bool, str]] = []
    downloaded = 0
    counter = 0
    start_item = item

    for catalog_index in range(table, len(parts)):
        books = parts[catalog_index]
        print(f"\n正在下载目录 {catalog_index + 1}/{len(parts)} 中的电子教材")

        for book in books[start_item:]:
            counter += 1
            if single_book is not None and counter != single_book:
                continue
            if download_limit is not None and downloaded >= download_limit:
                print(f"已达到下载限制 ({download_limit} 本教材)")
                _report(results)
                return

            name = book_display_name(book)
            for kind, ok, reason in download_book(book, work_path, formats, overwrite, dry_run):
                results.append((_label(name, kind), ok, reason))
            downloaded += 1

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
            "Download textbooks from the National Smart Education Platform "
            "(国家中小学智慧教育平台). Files are saved to ~/Downloads/textbook_download/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
DOWNLOAD MODES
  --sequence N     the Nth book across all catalogs (1-based)
  --range A-B      books A through B (also accepts a single number)
  --book-id UUID   one book by its platform id
  --all-pptx       every book that publishes a PowerPoint deck (252 of them)
  --single N       legacy: only the Nth book encountered
  --limit N        legacy: stop after N books (also caps --all-pptx)
  --table N        legacy: start at catalog N (0-based)
  --item N         legacy: start at item N within the first catalog (0-based)

FORMATS
  --format pdf     PDF only (default)
  --format pptx    PowerPoint only
  --format all     every format the book publishes

  Of the 3743 catalogued titles, 3384 publish a PDF and 252 publish a .pptx
  deck under flag `source` alongside a PDF under flag `pdf`. `--format` works
  with every mode above.

LISTING
  --list-pptx      list the books that publish a PowerPoint deck
  --list-pdf       list the books that publish a PDF
  --refresh-index  rebuild the cached format index (~3700 requests)

  For a browsable inventory of the whole catalogue - one row per book with
  its PDF/PPTX availability and sizes - run `python textbook_info.py`, which
  writes a CSV keyed by the same sequence numbers used here.

OPTIONS
  --dry-run        resolve and print the download URL without downloading
  --overwrite      re-download files that already exist locally

EXAMPLES
  python pdf_book_download_from_zxxeducn.py --sequence 1
  python pdf_book_download_from_zxxeducn.py --range 1-5
  python pdf_book_download_from_zxxeducn.py --list-pptx
  python pdf_book_download_from_zxxeducn.py --all-pptx
  python pdf_book_download_from_zxxeducn.py --all-pptx --format all --limit 5
  python pdf_book_download_from_zxxeducn.py --sequence 1981 --format pptx

NOTES
  359 catalogued entries publish no file of their own: 302 are course
  container nodes (their child books are catalogued separately and download
  fine) and 57 are special-education entries whose parent course lists no
  file. Special-education books resolve automatically via their parent
  course listing.
""",
    )
    parser.add_argument("--sequence", type=int, help="Download by global sequence number")
    parser.add_argument("--range", type=str, help='Download a range, e.g. "200-250"')
    parser.add_argument("--book-id", type=str, help="Download by platform book id (UUID)")
    parser.add_argument("--all-pptx", action="store_true",
                        help="Download every book that publishes a PowerPoint deck")
    parser.add_argument("--single", type=int, help="Legacy: download only book number N")
    parser.add_argument("--limit", type=int, help="Legacy: stop after N books")
    parser.add_argument("--table", type=int, default=0, help="Legacy: starting catalog index")
    parser.add_argument("--item", type=int, default=0, help="Legacy: starting item index")
    parser.add_argument("--format", choices=("pdf", "pptx", "all"), default=None,
                        help="Which file format(s) to download "
                             "(default: pdf, or pptx for --all-pptx)")
    parser.add_argument("--list-pptx", action="store_true",
                        help="List books that publish a PowerPoint deck, then exit")
    parser.add_argument("--list-pdf", action="store_true",
                        help="List books that publish a PDF, then exit")
    parser.add_argument("--refresh-index", action="store_true",
                        help="Rebuild the cached format index")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-download files that already exist locally")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve the download URL without downloading")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    list_format = "pptx" if args.list_pptx else ("pdf" if args.list_pdf else None)
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
            formats=resolve_formats(args.format) if args.format else None,
            all_pptx=args.all_pptx,
            list_format=list_format,
            refresh_index=args.refresh_index,
        )
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
