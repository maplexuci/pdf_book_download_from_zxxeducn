#!/usr/bin/env python3
"""
Textbook Downloader for the National Smart Education Platform (国家中小学智慧教育平台)

Downloads the freely published textbook files from basic.smartedu.cn.

Version: 3.3.0

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

Audio is published somewhere else entirely. A book's own details JSON never
mentions it; the site fetches AUDIO_PATH per book id, which returns a LIST of
`listening` records, each with its own ti_items. So audio is one book -> many
files, not the one book -> one file the rest of this module assumes, and it
gets its own resolver (`audio_tracks`) rather than going through `pick_asset`.

The flag that works is the opposite of the PDF case: for audio, `source`
(mp3 or wav) returns 403 on every mirror, and the items that actually serve are
`href` (mp3) and `href-ogg` (ogg). AUDIO_FLAGS records that, and audio picking
is restricted to those flags rather than merely preferring them.

317 of the 3791 catalogued books carry audio - 18158 tracks, 33.7 GiB as mp3 -
and every one of them is a language textbook (英语 270, 法语 18, 西班牙语 12,
德语 9, 俄语 5, 日语 3).

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

# A book's audio, which is nowhere in its details JSON. Returns a list of
# `listening` records, each carrying its own ti_items. This is the request the
# 教材 detail page fires; there is no other route to these files.
AUDIO_PATH = "/zxx/ndrs/resources/{book_id}/relation_audios.json"

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
# this kind; `magic` are the leading bytes a valid file must start with;
# `source` is where the item is found - a book's own details JSON, or the
# separate per-book audio listing.
ASSET_KINDS: Dict[str, Dict[str, Any]] = {
    "pdf": {
        "aliases": {"pdf"},
        "magic": (b"%PDF",),
        "label": "PDF",
        "source": "details",
    },
    "pptx": {
        # .pptx is a zip container, hence the PK signature.
        "aliases": {"pptx", "ppt"},
        "magic": (b"PK\x03\x04", b"PK\x05\x06", b"\xd0\xcf\x11\xe0"),
        "label": "PowerPoint",
        "source": "details",
    },
    "mp3": {
        "aliases": {"mp3"},
        # An ID3 tag, or a bare MPEG frame sync for a file without one.
        "magic": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
        "label": "MP3 音频",
        "source": "audio",
    },
    "ogg": {
        "aliases": {"ogg"},
        "magic": (b"OggS",),
        "label": "OGG 音频",
        "source": "audio",
    },
}

DOCUMENT_KINDS = tuple(k for k, v in ASSET_KINDS.items() if v["source"] == "details")
AUDIO_KINDS = tuple(k for k, v in ASSET_KINDS.items() if v["source"] == "audio")

# When several items share a format, prefer the one filed under `source`.
FLAG_PRIORITY = {"source": 0, "pdf": 1, "pptx": 1}

# Audio inverts that rule, so it gets its own table - and this one is an
# allow-list, not a preference. Measured 2026-08-25 across four publishers:
# the `source` item (mp3 or wav) answers 403 on every mirror, while `href`
# and `href-ogg` serve the full file at the byte count ti_size declares. The
# `-clip` variants are the same recording a few hundred bytes shorter; they
# are the fallback, not the pick. Never widen this to `source`.
AUDIO_FLAGS: Dict[str, Tuple[str, ...]] = {
    "mp3": ("href", "href-clip"),
    "ogg": ("href-ogg", "href-ogg-clip"),
}

# 409 of the 18158 track titles, across 21 books, end in ".mp3" - the source
# filename leaking into the title - and would otherwise be filed as
# "… .mp3.mp3". Stripped whatever encoding was asked for, because the suffix
# names the platform's file rather than the one being written here.
AUDIO_TITLE_SUFFIX = re.compile(r"\.(mp3|ogg|wav|m4a)$", re.IGNORECASE)

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
# Bumped when the bundle index gains a field or widens the ids it resolves.
SPECIAL_EDU_SCHEMA = 2
LEGACY_INDEX_PATH = OUTPUT_DIR / ".asset_index.json"
# Bumped when an index entry gains a field. A cached index written before the
# bump is missing that field for every book, and no amount of reconciling adds
# it - only a full rescan does, so a mismatch forces one.
ASSET_INDEX_SCHEMA = 2
# A warm index is reconciled against the live catalogue on every load - seqs
# re-derived, withdrawn books dropped, new and revised ones probed - so it does
# not go stale merely by sitting there. This is the backstop for whatever that
# reconciliation cannot see; `--refresh-index` forces a full rescan at any time.
INDEX_MAX_AGE = 30 * 24 * 3600

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
    """
    Turn a --format value into an ordered list of asset kinds.

    `all` takes one audio encoding, not both: the ogg listing is the same
    recordings as the mp3 one, half again as large, so fetching both would
    double the bytes for nothing. `--format ogg` asks for it explicitly.
    """
    if spec == "all":
        return list(DOCUMENT_KINDS) + ["mp3"]
    if spec == "audio":
        return ["mp3"]
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

def bundle_course_ids(books: Sequence[Dict[str, Any]]) -> List[str]:
    """
    Every id worth asking SPECIAL_EDU_PATH about.

    Two sources, because the catalogue lists only some parents:
      - `thematic_course` entries, which are parents in their own right
      - the `teachmeterial_ids` a compound child names, which is the only route
        to a parent the catalogue never lists. Those children 403 on the
        per-document endpoint, so without this they are unreachable.
    """
    ids = [b["id"] for b in books
           if b.get("resource_type_code") == "thematic_course" and b.get("id")]
    for book in books:
        props = book.get("resource_custom_properties") or {}
        if props.get("compound_node") != "sub":
            continue
        ids.extend(t for t in (book.get("teachmeterial_ids") or []) if t)
    return list(dict.fromkeys(ids))          # de-duplicated, order preserved


def build_special_edu_index(refresh: bool = False) -> Dict[str, Any]:
    """
    Index the course bundles that hold their children's files.

    Returns {"children": {child_id: record},
             "courses":  {course_id: [child_id]},
             "course_tags": {course_id: tag_list}}
    - `children` resolves a document that 403s on the per-document endpoint
    - `courses` lets a UI show what a container node actually holds
    - `course_tags` carries the taxonomy of a parent the catalogue omits, the
      only place its children's 学段/学科/版本 are stated

    Each bundle exposes its children at SPECIAL_EDU_PATH, and those children
    carry the ti_items (including the source PDF) that the per-document details
    endpoint refuses to serve.
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
                if (time.time() - cached.get("generated", 0) < INDEX_MAX_AGE
                        and cached.get("schema") == SPECIAL_EDU_SCHEMA
                        and cached.get("children") and cached.get("courses")):
                    _SPECIAL_EDU = {"children": cached["children"],
                                    "courses": cached["courses"],
                                    "course_tags": cached.get("course_tags") or {}}
                    return _SPECIAL_EDU
            except (json.JSONDecodeError, OSError):
                pass

        books = flat_catalog()
        catalogued = {b.get("id") for b in books}
        courses = bundle_course_ids(books)
        print(f"🔎 Resolving {len(courses)} course bundles...")

        children: Dict[str, Dict[str, Any]] = {}
        courses_map: Dict[str, List[str]] = {}
        course_tags: Dict[str, List[Dict[str, Any]]] = {}
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
                    held = []
                    for item in items or []:
                        if item.get("id"):
                            children[item["id"]] = item
                            held.append(item["id"])
                    courses_map[course_id] = held
                return

        with ThreadPoolExecutor(10) as pool:
            list(pool.map(fetch, courses))

        # A parent the catalogue lists carries its own tags already; one it
        # omits does not, so its children have nothing to inherit. Fetch those.
        absent = [cid for cid in courses_map if cid not in catalogued]

        def fetch_tags(course_id: str) -> None:
            # Deliberately not get_book_details(): its 403 fallback re-enters
            # this function, and _SPECIAL_EDU_LOCK is held right now. A parent
            # that 403s has no tags to give anyway.
            for host in DETAILS_HOSTS:
                url = f"https://{host}.ykt.cbern.com.cn" + DETAILS_PATH.format(
                    book_id=course_id)
                try:
                    response = thread_session().get(url, timeout=30)
                except requests.RequestException:
                    continue
                if not response.ok:
                    continue
                try:
                    tags = (response.json() or {}).get("tag_list") or []
                except json.JSONDecodeError:
                    return
                if tags:
                    with lock:
                        course_tags[course_id] = tags
                return

        if absent:
            print(f"🏷️  Fetching tags for {len(absent)} uncatalogued parents...")
            with ThreadPoolExecutor(10) as pool:
                list(pool.map(fetch_tags, absent))

        _SPECIAL_EDU = {"children": children, "courses": courses_map,
                        "course_tags": course_tags}
        try:
            SPECIAL_EDU_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            SPECIAL_EDU_INDEX_PATH.write_text(
                json.dumps({"generated": time.time(), "schema": SPECIAL_EDU_SCHEMA,
                            "children": children, "courses": courses_map,
                            "course_tags": course_tags}, ensure_ascii=False),
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
        record = build_special_edu_index()["children"].get(book_id)
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
    """
    Which document kinds this book's details JSON publishes.

    Audio is deliberately not consulted here: it is not in `ti_items`, and
    answering for it would cost a second request per call.
    """
    return [kind for kind in DOCUMENT_KINDS if pick_asset(details, kind)]


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
# Audio resolution
#
# Audio does not go through pick_asset because it is not shaped like the rest:
# a book's audio is a separate listing of many records, each a track with its
# own ti_items, so one book resolves to many files.
# ---------------------------------------------------------------------------

def get_audio_records(
    book_id: str, session: Optional[requests.Session] = None, quiet: bool = False
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetch a book's audio listing.

    Returns (records, status) using the same vocabulary as get_book_details:
      'ok'         - the listing was served, though it may be empty
      'restricted' - platform refuses public access (HTTP 401/403)
      'error'      - network / parse failure

    An empty list under 'ok' is the platform saying this book has no audio,
    which is the answer for most of the catalogue - not a failure. The books
    that answer 401/403 are the 体育与健康 titles and course containers, none
    of which publish audio anywhere else either.
    """
    session = session or SESSION
    last_status = "error"
    for host in DETAILS_HOSTS:
        url = f"https://{host}.ykt.cbern.com.cn" + AUDIO_PATH.format(book_id=book_id)
        try:
            response = session.get(url, timeout=30)
        except requests.RequestException as exc:
            if not quiet:
                print(f"    ❌ {host}: network error ({exc})")
            continue

        if response.ok:
            try:
                return response.json() or [], "ok"
            except json.JSONDecodeError:
                if not quiet:
                    print(f"    ❌ {host}: audio listing was not valid JSON")
                continue

        if response.status_code in (401, 403):
            last_status = "restricted"
        elif not quiet:
            print(f"    ❌ {host}: audio listing returned HTTP {response.status_code}")

    return [], last_status


def pick_audio_item(record: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    """
    The downloadable item of `kind` in one audio record.

    AUDIO_FLAGS is an allow-list rather than a preference order: a track is
    also published under `source`, and that item answers 403 on every mirror.
    Preferring `href` while still falling back to `source` would turn a
    working download into a failing one, so `source` is never a candidate.
    """
    aliases = ASSET_KINDS[kind]["aliases"]
    order = AUDIO_FLAGS[kind]
    candidates = [
        item
        for item in record.get("ti_items") or []
        if (item.get("ti_format") or "").lower() in aliases
        and item.get("ti_file_flag") in order
        and item.get("ti_storages")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda i: order.index(i["ti_file_flag"]))
    return candidates[0]


def audio_track_title(record: Dict[str, Any]) -> str:
    """What the platform calls this track, e.g. 'Unit 1_Part A_2. Listen and say'."""
    return (record.get("title")
            or (record.get("global_title") or {}).get("zh-CN")
            or record.get("id", "")
            or "untitled")


def audio_tracks(
    book_id: str,
    kind: str = "mp3",
    session: Optional[requests.Session] = None,
    quiet: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Every downloadable track of `kind` for a book, in the order the listing
    gives them - which is teaching order, Unit 1 before Unit 2.

    Each track carries its `position` in that listing, counted over all
    records rather than over the ones that resolved, so a track's number
    means the same thing whatever format is being fetched. The number is also
    what keeps a book's files apart: 16 of the 317 books with audio reuse a
    track title within the same book.
    """
    records, status = get_audio_records(book_id, session=session, quiet=quiet)
    tracks: List[Dict[str, Any]] = []
    for position, record in enumerate(records, 1):
        item = pick_audio_item(record, kind)
        if item is None:
            continue
        tracks.append({
            "position": position,
            "id": record.get("id", ""),
            "title": audio_track_title(record),
            "urls": storages_to_public_urls(item["ti_storages"]),
            "size": item.get("ti_size") or 0,
            "flag": item.get("ti_file_flag"),
        })
    return tracks, status


# ---------------------------------------------------------------------------
# Asset index (which books publish which formats)
# ---------------------------------------------------------------------------

def probe_book_formats(seq: int, book: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve one catalogue book into an asset-index entry."""
    book_id = book.get("id", "")
    details, status = get_book_details(book_id, session=thread_session(), quiet=True)
    entry: Dict[str, Any] = {
        "seq": seq,
        "id": book_id,
        "title": book_display_name(book),
        "status": status,
        # what the catalogue claimed when this was probed, so a later run can
        # tell whether the book has moved on
        "update_time": book.get("update_time") or "",
        "formats": {},
    }
    if details is not None:
        for kind in DOCUMENT_KINDS:
            item = pick_asset(details, kind)
            if item:
                entry["formats"][kind] = {
                    "flag": item.get("ti_file_flag"),
                    "size": item.get("ti_size") or 0,
                }

    # A second request, because a book's audio is nowhere in its details JSON.
    # It is asked of every book rather than only the language ones: which
    # subjects publish audio is an observation about today's catalogue, not a
    # rule the platform states, and guessing would quietly miss the first
    # 音乐 book that ships a track.
    tracks, _ = audio_tracks(book_id, "mp3", session=thread_session(), quiet=True)
    if tracks:
        entry["audio"] = {
            "count": len(tracks),
            "size": sum(track["size"] for track in tracks),
            "flag": tracks[0]["flag"],
        }
    return entry


def _probe_books(pairs: Sequence[Tuple[int, Dict[str, Any]]], workers: int,
                 report_every: int = 500) -> List[Dict[str, Any]]:
    """Probe (seq, book) pairs concurrently, printing progress as they land."""
    entries: List[Dict[str, Any]] = []
    lock = threading.Lock()
    counter = {"n": 0}

    def work(pair: Tuple[int, Dict[str, Any]]) -> None:
        entry = probe_book_formats(*pair)
        with lock:
            entries.append(entry)
            counter["n"] += 1
            if report_every and counter["n"] % report_every == 0:
                print(f"   • scanned {counter['n']}/{len(pairs)}")

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(work, pairs))
    return entries


def _save_asset_index(entries: List[Dict[str, Any]]) -> None:
    try:
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDEX_PATH.write_text(
            json.dumps({"generated": time.time(), "schema": ASSET_INDEX_SCHEMA,
                        "books": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"💾 Asset index cached at {INDEX_PATH}")
    except OSError as exc:
        print(f"⚠️ Could not cache asset index: {exc}")


def _reconcile_asset_index(entries: List[Dict[str, Any]],
                           workers: int) -> Optional[List[Dict[str, Any]]]:
    """
    Re-align a cached index with the catalogue as it stands now.

    An entry's `seq` is only its position at scan time, and the platform adds
    and withdraws books between scans. Callers join catalogue book -> entry, so
    a stale `seq` does not merely mislabel a row: it pairs a book with another
    book's files. Re-derive every `seq` from the live catalogue by id, forget
    entries the catalogue no longer lists, and probe the ones it has gained.

    Books the catalogue has since revised, and books that resolved to no file
    last time, are re-probed; the rest are taken as they stand. A full scan is
    ~3700 requests and several minutes, and almost all of it re-confirms books
    that have not been touched in months.

    Returns None when the drift is large enough that a full rescan is cheaper.
    """
    books = flat_catalog()
    seq_by_id: Dict[str, int] = {}
    for seq, book in enumerate(books, 1):
        seq_by_id.setdefault(book.get("id", ""), seq)

    indexed = {entry.get("id") for entry in entries}
    added = [(seq, book) for seq, book in enumerate(books, 1)
             if book.get("id") not in indexed]
    if len(added) > max(200, len(books) // 4):
        print(f"🔄 Catalogue has {len(added)} books the index never saw; rescanning")
        return None

    book_by_id = {b.get("id"): b for b in books}
    kept: List[Dict[str, Any]] = []
    stale: List[Tuple[int, Dict[str, Any]]] = []
    moved = 0
    for entry in entries:
        book_id = entry.get("id", "")
        seq = seq_by_id.get(book_id)
        if seq is None:
            continue                       # withdrawn from the catalogue
        if seq != entry.get("seq"):
            moved += 1
        entry["seq"] = seq

        book = book_by_id[book_id]
        revised = (book.get("update_time") or "") != (entry.get("update_time") or "")
        # A book that resolved to nothing is re-probed regardless: files are
        # sometimes attached after publication without the catalogue's
        # update_time moving, which is exactly how three 体育与健康 volumes sat
        # looking unavailable for hours after their PDFs went up. A
        # thematic_course is exempt - it is a container whose children hold the
        # files, so it has nothing to gain, and re-asking about all 320 of them
        # every run was most of what the incremental pass still cost.
        container = book.get("resource_type_code") == "thematic_course"
        if revised or (not entry.get("formats") and not container):
            stale.append((seq, book))
        else:
            kept.append(entry)

    dropped = len(entries) - len(kept) - len(stale)
    if not (added or dropped or moved or stale):
        return kept

    if added or stale:
        work = stale + added
        print(f"➕ Probing {len(work)} books ({len(added)} new, "
              f"{sum(1 for s, b in stale if (b.get('update_time') or '') != '')} revised "
              f"or previously unresolved) - {len(kept)} unchanged books skipped")
        kept.extend(_probe_books(work, workers, report_every=200))
    if dropped or moved:
        print(f"🔧 Catalogue drift: {moved} books moved, {dropped} withdrawn")

    kept.sort(key=lambda e: e["seq"])
    _save_asset_index(kept)
    return kept


def build_asset_index(workers: int = 12, refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Probe every catalogued book's details JSON and record which formats it
    publishes. Cached on disk because it costs ~3700 requests; a warm cache is
    reconciled against the live catalogue so its sequence numbers stay true.
    """
    source = INDEX_PATH if INDEX_PATH.exists() else LEGACY_INDEX_PATH
    if not refresh and source.exists():
        try:
            cached = json.loads(source.read_text(encoding="utf-8"))
            age = time.time() - cached.get("generated", 0)
            if cached.get("books") and cached.get("schema") != ASSET_INDEX_SCHEMA:
                print("🔄 Cached index predates the audio column; rescanning")
            elif age < INDEX_MAX_AGE and cached.get("books"):
                print(f"🗂️  Using cached asset index ({len(cached['books'])} books, "
                      f"{age / 3600:.0f}h old; --refresh-index to rebuild)")
                reconciled = _reconcile_asset_index(cached["books"], workers)
                if reconciled is not None:
                    return reconciled
        except (json.JSONDecodeError, OSError):
            pass

    books = flat_catalog()
    print(f"🔎 Scanning {len(books)} books for available formats "
          f"({workers} workers, this takes a few minutes)...")
    entries = _probe_books(list(enumerate(books, 1)), workers)
    entries.sort(key=lambda e: e["seq"])
    _save_asset_index(entries)
    return entries


def index_summary(entries: Sequence[Dict[str, Any]]) -> None:
    total = len(entries)
    restricted = sum(1 for e in entries if e["status"] == "restricted")
    errored = sum(1 for e in entries if e["status"] == "error")
    with_pdf = sum(1 for e in entries if "pdf" in e["formats"])
    with_pptx = sum(1 for e in entries if "pptx" in e["formats"])
    both = sum(1 for e in entries if {"pdf", "pptx"} <= set(e["formats"]))
    with_audio = [e for e in entries if e.get("audio")]
    tracks = sum(e["audio"]["count"] for e in with_audio)
    print(f"\n📊 Catalog: {total} books")
    print(f"   • PDF available        : {with_pdf}")
    print(f"   • PowerPoint available : {with_pptx}  (of which {both} also have a PDF)")
    print(f"   • audio available      : {len(with_audio)}  ({tracks} tracks)")
    print(f"   • no downloadable file : {restricted}")
    if errored:
        print(f"   • metadata unreachable  : {errored}")


def books_with_format(entries: Sequence[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    """
    The indexed books publishing `kind`.

    Audio is recorded per book rather than per format - the index stores the
    track count and total bytes of the mp3 listing, and the ogg listing holds
    the same recordings - so both audio kinds select on the same field.
    """
    if kind in AUDIO_KINDS:
        return [e for e in entries if e.get("audio")]
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
    say = (lambda *a, **k: None) if quiet else print
    if not urls:
        say(f"❌ No {kind} URLs available for {book_name}")
        return False

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


def download_audio(
    book_name: str,
    book_id: str,
    work_path: Path,
    kind: str = "mp3",
    overwrite: bool = False,
    dry_run: bool = False,
) -> Tuple[bool, str]:
    """
    Download every audio track a book publishes, into a folder of its own.

    A book's audio is dozens of small files - 50 at the median, 165 at the
    most - so they go into `<output>/<book>/` rather than beside the PDFs,
    numbered by their position in the platform's listing to keep teaching
    order and to keep repeated titles apart.

    Returns (ok, note), ok being true only if every track landed.
    """
    tracks, status = audio_tracks(book_id, kind)
    if not tracks:
        if status == "restricted":
            return False, "audio listing restricted by platform"
        if status == "error":
            return False, "audio listing unavailable"
        return False, "no audio published"

    total = sum(track["size"] for track in tracks)
    folder = Path(work_path) / sanitize_filename(book_name)
    print(f"    🎧 {len(tracks)} {kind} tracks, {human_size(total)} → {folder.name}/")

    if dry_run:
        for track in tracks[:3]:
            print(f"    🔗 [{kind}] {track['position']:02d} {track['title']}")
            print(f"        {track['urls'][0]}")
        if len(tracks) > 3:
            print(f"    … and {len(tracks) - 3} more (dry run)")
        return True, f"resolved {len(tracks)} tracks"

    width = max(2, len(str(tracks[-1]["position"])))
    done = 0
    for track in tracks:
        title = AUDIO_TITLE_SUFFIX.sub("", track["title"]).strip()
        stem = f"{track['position']:0{width}d} {title or track['id']}"
        if download_asset(track["urls"], stem, kind, str(folder), overwrite, kind):
            done += 1

    if done == len(tracks):
        return True, f"{done} tracks"
    return False, f"only {done}/{len(tracks)} tracks downloaded"


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

    outcome: Dict[str, Tuple[bool, str]] = {}

    # Audio is resolved first and separately: it comes from its own endpoint,
    # so a book whose details JSON 403s can still have downloadable tracks and
    # must not be written off before they are asked for.
    for kind in (k for k in formats if ASSET_KINDS[k]["source"] == "audio"):
        outcome[kind] = download_audio(name, book_id, work_path, kind, overwrite, dry_run)

    documents = [k for k in formats if ASSET_KINDS[k]["source"] == "details"]
    if not documents:
        return [(kind, *outcome[kind]) for kind in formats]

    details, status = get_book_details(book_id)
    if details is None:
        if status == "restricted":
            print("    🔒 No downloadable file published (online preview only)")
            note = "restricted by platform"
        else:
            print("    ❌ Could not fetch details metadata")
            note = "details unavailable"
        for kind in documents:
            outcome[kind] = (False, note)
        return [(kind, *outcome[kind]) for kind in formats]

    for kind in documents:
        item = pick_asset(details, kind)
        if item is None:
            offered = available_kinds(details)
            note = f"no {kind} published" + (f" (has: {', '.join(offered)})" if offered else "")
            print(f"    ⚠️ {note}")
            outcome[kind] = (False, note)
            continue

        urls = storages_to_public_urls(item["ti_storages"])
        extension = (item.get("ti_format") or kind).lower()
        if dry_run:
            print(f"    🔗 [{kind}] {urls[0]}")
            print(f"    ℹ️  flag={item.get('ti_file_flag')} "
                  f"size={human_size(item.get('ti_size') or 0)} (dry run)")
            outcome[kind] = (True, "resolved")
            continue

        ok = download_asset(urls, name, kind, str(work_path), overwrite, extension)
        outcome[kind] = (ok, "ok" if ok else "download failed")

    return [(kind, *outcome[kind]) for kind in formats]


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
    all_of: Optional[str] = None,
    list_format: Optional[str] = None,
    refresh_index: bool = False,
    filters: Optional[Dict[str, Optional[str]]] = None,
) -> None:
    """Entry point dispatching to the requested download mode."""
    work_path = OUTPUT_DIR
    try:
        work_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"❌ Failed to create output directory {work_path}: {exc}")
        return

    filters = filters or {}
    if list_format:
        _list_mode(list_format, refresh_index, filters)
        return

    print("🚀 Starting textbook download...")

    if describe_filters(filters):
        _download_filtered(filters, work_path, formats or ("pdf",), overwrite,
                           dry_run, download_limit, refresh_index)
        print(f"📁 Files are in {work_path}")
        return

    if all_of:
        _download_all_of_format(all_of, work_path, formats, overwrite, dry_run,
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


FILTER_DIMENSIONS = ("stage", "subject", "version", "grade", "volume")


def catalog_rows(refresh: bool = False) -> List[Dict[str, Any]]:
    """
    The enriched index the web UI and the CSV both use.

    Imported inside the function on purpose: catalog_index imports this module,
    so a module-level import here would be circular. It also keeps the cost off
    the download modes, which have no use for tags.
    """
    import catalog_index
    return catalog_index.build_index(refresh=refresh)


def filter_rows(rows: Sequence[Dict[str, Any]],
                filters: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    """Keep rows matching every dimension given, and the search term if any."""
    search = (filters.get("search") or "").strip()
    out = []
    for row in rows:
        if any(filters.get(dim) and filters[dim] not in (row.get(dim) or [])
               for dim in FILTER_DIMENSIONS):
            continue
        if search and search not in row["title"]:
            continue
        out.append(row)
    return out


def describe_filters(filters: Dict[str, Optional[str]]) -> str:
    bits = [f"{dim}={filters[dim]}" for dim in FILTER_DIMENSIONS if filters.get(dim)]
    if filters.get("search"):
        bits.append(f"search={filters['search']!r}")
    return ", ".join(bits)


def row_holds(row: Dict[str, Any], kind: str) -> bool:
    """Does this catalogue row (or index entry) publish `kind`?"""
    if kind in AUDIO_KINDS:
        return bool(row.get("audio"))
    formats = row.get("formats") or {}
    return kind in formats


def row_size(row: Dict[str, Any], kind: str) -> int:
    """
    How many bytes of `kind` this row holds.

    Rows come in two shapes - the asset index stores {"size": n} per format,
    the catalogue index flattens that to the number - and audio lives in its
    own field for both. Sizes are recorded for the mp3 listing; the ogg
    listing is the same recordings at roughly half again the size, so an ogg
    estimate is deliberately not offered here.
    """
    if kind in AUDIO_KINDS:
        return (row.get("audio") or {}).get("size", 0)
    value = (row.get("formats") or {}).get(kind)
    return value.get("size", 0) if isinstance(value, dict) else (value or 0)


def _row_detail(row: Dict[str, Any]) -> str:
    """The second line of a listing: what this book is and what it duplicates."""
    tags = " · ".join((row.get(dim) or [""])[0] for dim in FILTER_DIMENSIONS
                      if row.get(dim))
    bits = [tags or "未标注"]
    parent = row.get("parent") or {}
    if parent.get("seq"):
        bits.append(f"← #{parent['seq']} {(parent.get('title') or '')[:30]}")
    if row.get("alternates"):
        same = all(a.get("same_file") for a in row["alternates"])
        others = " ".join(f"#{a['seq']}" for a in row["alternates"])
        bits.append(f"{'♻️ same file as' if same else '♊ also scanned as'} {others}")
    return "  ".join(bits)


def _list_mode(kind: str, refresh_index: bool,
               filters: Optional[Dict[str, Optional[str]]] = None) -> None:
    """Print the books publishing the given format, with what each one is."""
    rows = [r for r in catalog_rows(refresh=refresh_index) if row_holds(r, kind)]
    filters = filters or {}
    if describe_filters(filters):
        rows = filter_rows(rows, filters)
        print(f"\n🔎 Filter: {describe_filters(filters)}")

    audio = kind in AUDIO_KINDS
    total = sum(row_size(r, kind) for r in rows)
    if audio:
        tracks = sum((r.get("audio") or {}).get("count", 0) for r in rows)
        print(f"\n🎧 {len(rows)} books publish audio - {tracks} tracks, "
              f"{human_size(total)} as mp3:\n")
    else:
        print(f"\n📄 {len(rows)} books publish a {ASSET_KINDS[kind]['label']} "
              f"file ({human_size(total)} total):\n")
    for row in rows:
        others = ", ".join(k for k in (row.get("formats") or {}) if k != kind)
        extra = f"  (+{others})" if others else ""
        count = f"{(row.get('audio') or {}).get('count', 0):>4d} ×" if audio else ""
        print(f"  #{row['seq']:<5d} {count}{human_size(row_size(row, kind)):>9s}  "
              f"{row['title'][:56]}{extra}")
        print(f"         {_row_detail(row)}")
    if rows:
        dupes = sum(1 for r in rows if r.get("alternates"))
        if dupes:
            print(f"\n⚠️  {dupes} of these are the same book as another row; "
                  f"downloading all of them fetches some books twice.")
        print("\nDownload exactly this set by repeating the filters without --list-*.")


def _download_filtered(filters: Dict[str, Optional[str]], work_path: Path,
                       formats: Sequence[str], overwrite: bool, dry_run: bool,
                       limit: Optional[int], refresh_index: bool) -> None:
    """Download every book matching the filters - the CLI's bulk selection."""
    rows = filter_rows(catalog_rows(refresh=refresh_index), filters)
    rows = [r for r in rows if any(row_holds(r, f) for f in formats)]
    if not rows:
        print(f"❌ Nothing matches {describe_filters(filters)} in {', '.join(formats)}")
        return
    if limit:
        rows = rows[:limit]

    estimate = sum(row_size(r, f) for r in rows for f in formats)
    print(f"\n📦 {len(rows)} books match {describe_filters(filters)}; "
          f"fetching {', '.join(formats)} (~{human_size(estimate)})")
    dupes = sum(1 for r in rows if r.get("alternates"))
    if dupes:
        print(f"⚠️  {dupes} of them duplicate another row in this set "
              f"(--list-{formats[0]} shows which)")

    results: List[Tuple[str, bool, str]] = []
    for position, row in enumerate(rows, 1):
        print(f"\n[{position}/{len(rows)}] #{row['seq']}", end=" ")
        book = {"id": row["id"], "title": row["title"]}
        for asset_kind, ok, reason in download_book(book, work_path, formats,
                                                    overwrite, dry_run):
            results.append((_label(row["title"], asset_kind, f"#{row['seq']} "), ok, reason))
        if position != len(rows):
            time.sleep(1)      # be polite to the CDN
    _report(results)


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
    available = len(matches)
    if limit:
        matches = matches[:limit]

    # Default to this route's own format; an explicit --format wins.
    wanted = list(formats) if formats else [kind]
    estimate = sum(row_size(entry, k) for entry in matches for k in wanted)
    # Say how many books publish this, not just how many --limit left, or the
    # line reads as though the catalogue held only the capped number.
    capped = f" - taking the first {len(matches)}" if len(matches) < available else ""
    print(f"\n📦 {available} books publish {ASSET_KINDS[kind]['label']}{capped}; "
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
        # Audio hangs off its own endpoint, so a book whose details JSON is
        # refused may still have tracks. Only give up when the caller wanted
        # something that does come from the details.
        if any(ASSET_KINDS[k]["source"] == "details" for k in formats):
            if status == "restricted":
                print("🔒 No downloadable file published (online preview only)")
            else:
                print("❌ Could not fetch details metadata for this ID")
            return
        details = {"id": book_id}

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
  --all-audio      every book that publishes audio (317 of them, 33.7 GB)
  --single N       legacy: only the Nth book encountered
  --limit N        legacy: stop after N books (also caps --all-pptx)
  --table N        legacy: start at catalog N (0-based)
  --item N         legacy: start at item N within the first catalog (0-based)

FORMATS
  --format pdf     PDF only (default)
  --format pptx    PowerPoint only
  --format audio   the book's audio tracks as mp3
  --format ogg     the same tracks as ogg (~50% larger; mp3 unless you care)
  --format all     pdf, pptx and mp3 - one audio encoding, not both

  Of the 3743 catalogued titles, 3384 publish a PDF and 252 publish a .pptx
  deck under flag `source` alongside a PDF under flag `pdf`. `--format` works
  with every mode above.

AUDIO
  317 books publish audio - 18158 tracks, 33.7 GB as mp3 - and all of them are
  language textbooks (英语 270, 法语 18, 西班牙语 12, 德语 9, 俄语 5, 日语 3).
  It comes from a separate per-book listing, not from the book's own metadata,
  so a book with no PDF can still have tracks.

  A book's tracks go into a folder of their own, numbered in the platform's
  order:  ~/Downloads/textbook_download/<book>/01 Unit 1_Part A.mp3

LISTING
  --list-pptx      list the books that publish a PowerPoint deck
  --list-pdf       list the books that publish a PDF
  --list-audio     list the books that publish audio, with track counts
  --refresh-index  rebuild the cached format index (~7500 requests: the
                   details JSON and the audio listing, per book)

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
  python pdf_book_download_from_zxxeducn.py --list-audio
  python pdf_book_download_from_zxxeducn.py --sequence 93 --format audio
  python pdf_book_download_from_zxxeducn.py --subject 英语 --format audio --limit 5

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
    parser.add_argument("--all-audio", action="store_true",
                        help="Download every book that publishes audio (317 books, 33.7 GB)")
    parser.add_argument("--single", type=int, help="Legacy: download only book number N")
    parser.add_argument("--limit", type=int, help="Legacy: stop after N books")
    parser.add_argument("--table", type=int, default=0, help="Legacy: starting catalog index")
    parser.add_argument("--item", type=int, default=0, help="Legacy: starting item index")
    parser.add_argument("--format",
                        choices=("pdf", "pptx", "audio", "mp3", "ogg", "all"),
                        default=None,
                        help="Which file format(s) to download (default: pdf, or "
                             "the format the --all-* route is named after). "
                             "'audio' means mp3.")
    parser.add_argument("--list-pptx", action="store_true",
                        help="List books that publish a PowerPoint deck, then exit")
    parser.add_argument("--list-pdf", action="store_true",
                        help="List books that publish a PDF, then exit")
    parser.add_argument("--list-audio", action="store_true",
                        help="List books that publish audio, then exit")
    selection = parser.add_argument_group(
        "selection by 分类",
        "Pick books the way the web UI does. Combine freely; with --list-pdf or "
        "--list-pptx they narrow the listing, and on their own they download "
        "everything that matches.")
    selection.add_argument("--stage", help="学段, e.g. 小学 / 初中 / 高中 / 特殊教育")
    selection.add_argument("--subject", help="学科, e.g. 英语 / 数学 / 体育与健康")
    selection.add_argument("--version", help="版本 (publisher), e.g. 人教版 / 北师大版")
    selection.add_argument("--grade", help="年级, e.g. 三年级 / 教师用书")
    selection.add_argument("--volume", help="册次, e.g. 上册 / 全一册")
    selection.add_argument("--search", help="substring of the book title")

    parser.add_argument("--refresh-index", action="store_true",
                        help="Rebuild the cached format index")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-download files that already exist locally")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve the download URL without downloading")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    list_format = ("pptx" if args.list_pptx else
                   "pdf" if args.list_pdf else
                   "mp3" if args.list_audio else None)
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
            all_of="pptx" if args.all_pptx else ("mp3" if args.all_audio else None),
            list_format=list_format,
            refresh_index=args.refresh_index,
            filters={"stage": args.stage, "subject": args.subject,
                     "version": args.version, "grade": args.grade,
                     "volume": args.volume, "search": args.search},
        )
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
