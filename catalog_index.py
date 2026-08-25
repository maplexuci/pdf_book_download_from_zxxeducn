#!/usr/bin/env python3
"""
The catalogue index: one row per catalogued book, carrying everything a caller
needs to decide what a row IS.

Built by joining three sources the platform keeps apart:
  - the catalogue part files, for titles and facet tags
  - the downloader's asset index, for which formats each book publishes
  - the course-bundle listings, for children of a compound resource

and then repairing two things the raw data gets wrong: a child of a compound
resource reaches the catalogue with no tags at all (it inherits its parent's),
and a title the platform published twice is not a missing book (it points at
the copy that carries the file).

Shared by the web UI and by textbook_info.py so both describe a row the same
way; neither should re-derive any of this.
"""

import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pdf_book_download_from_zxxeducn as dl


def _notify(on_status: Optional[Callable[..., None]], **fields: Any) -> None:
    if on_status is not None:
        on_status(**fields)


INDEX_PATH = dl.CACHE_DIR / "web_index.json"
INDEX_MAX_AGE = 7 * 24 * 3600
# Bumped whenever an entry gains or changes a field, so stale caches written by
# an older build are rebuilt instead of served with a shape the UI cannot use.
INDEX_SCHEMA = 10

# Platform tag dimensions, in the order the UI cascades them.
DIMENSIONS: List[Tuple[str, str, str]] = [
    ("stage", "学段", "zxxxd"),
    ("subject", "学科", "zxxxk"),
    ("version", "版本", "zxxbb"),
    ("grade", "年级", "zxxnj"),
    ("volume", "册次", "zxxcc"),
]
DIM_BY_KEY = {key: dim_id for key, _, dim_id in DIMENSIONS}
UNTAGGED = "未标注"


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

_index_lock = threading.Lock()
_index: Optional[List[Dict[str, Any]]] = None


def _tags_for(book: Dict[str, Any]) -> Dict[str, List[str]]:
    """Group a book's tags by the dimensions the UI filters on."""
    grouped: Dict[str, List[str]] = {key: [] for key, _, _ in DIMENSIONS}
    for tag in book.get("tag_list") or []:
        dim_id = tag.get("tag_dimension_id")
        name = tag.get("tag_name")
        if not name:
            continue
        for key, _, wanted in DIMENSIONS:
            if dim_id == wanted and name not in grouped[key]:
                grouped[key].append(name)

    return _collapse_stage(grouped)


def _collapse_stage(grouped: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    241 特殊教育 titles (聋校 / 培智) are tagged with BOTH 特殊教育 and the
    grade-level 学段 they correspond to, which would otherwise surface subjects
    like 律动 under 小学. The platform lists them under 特殊教育 only, so that
    tag wins. 学段 is the sole dimension ever multi-valued.
    """
    if len(grouped["stage"]) > 1 and "特殊教育" in grouped["stage"]:
        grouped["stage"] = ["特殊教育"]
    return grouped


def _dimension_vocabulary(books: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    tag name -> dimension key, learned from the rows the platform tagged properly.

    An uncatalogued parent states its taxonomy as one flat tag list under a
    single opaque dimension id, so the id cannot say which dimension a name
    belongs to. The names themselves are drawn from closed vocabularies
    (小学, 教科版, 体育与健康 ...), so the 3142 correctly tagged rows are enough
    to place them, with no hardcoded lists to drift out of date.
    """
    vocab: Dict[str, str] = {}
    for book in books:
        for tag in book.get("tag_list") or []:
            name = tag.get("tag_name")
            if not name:
                continue
            for key, _, wanted in DIMENSIONS:
                if tag.get("tag_dimension_id") == wanted:
                    vocab.setdefault(name, key)
    return vocab


def _tags_from_flat(tag_list: List[Dict[str, Any]], vocab: Dict[str, str]) -> Dict[str, List[str]]:
    """Sort a flat tag list into dimensions by name. Unrecognised names are dropped."""
    grouped: Dict[str, List[str]] = {key: [] for key, _, _ in DIMENSIONS}
    for tag in tag_list or []:
        name = tag.get("tag_name")
        key = vocab.get(name or "")
        if key and name not in grouped[key]:
            grouped[key].append(name)
    return _collapse_stage(grouped)


# The taxonomy of a compound resource lives on the parent node, so a child
# ("compound_node": "sub") reaches the catalogue with tag_list and tag_paths
# both empty. Where the parent is known its id also appears in the child's
# preview image URLs, which covers children the course listing does not.
_PARENT_IN_PREVIEW = re.compile(r"/thematic_course/([0-9a-f-]{36})\.t/")


def _parent_course_id(book: Dict[str, Any], parent_of: Dict[str, str]) -> Optional[str]:
    known = parent_of.get(book.get("id", ""))
    if known:
        return known
    preview = (book.get("custom_properties") or {}).get("preview") or {}
    for url in preview.values():
        found = _PARENT_IN_PREVIEW.search(url or "")
        if found:
            return found.group(1)
    return None


def _inherit_parent_tags(entries: List[Dict[str, Any]], books: List[Dict[str, Any]],
                         parent_of: Dict[str, str],
                         course_tags: Dict[str, List[Dict[str, Any]]],
                         vocab: Dict[str, str]) -> int:
    """
    Give an untagged child the 分类 of the course it belongs to.

    649 of 3791 rows are children of a compound resource and carry no tags of
    their own, which left every dimension reading 未标注 and made them invisible
    to the filters. The parent states the real 学段/学科/版本/年级/册次, from one
    of two places: a catalogued parent carries proper per-dimension tags, while
    a parent the catalogue omits states them as one flat list that has to be
    sorted by name. Either way the row is marked, so the UI can say the 分类 is
    inherited rather than declared. Only the first pass's own tags are read, so
    nothing inherits from a row that itself inherited.
    """
    keys = [key for key, _, _ in DIMENSIONS]
    own = {entry["id"]: {key: list(entry.get(key) or []) for key in keys}
           for entry in entries}
    filled = 0
    for entry, book in zip(entries, books):
        if any(entry.get(key) for key in keys):
            continue
        parent_id = _parent_course_id(book, parent_of) or ""
        inherited = own.get(parent_id)
        if not inherited or not any(inherited.values()):
            flat = course_tags.get(parent_id)
            inherited = _tags_from_flat(flat, vocab) if flat else None
        if not inherited or not any(inherited.values()):
            continue
        for key in keys:
            entry[key] = list(inherited[key])
        entry["tags_inherited"] = True
        filled += 1
    return filled


def _mark_duplicates(entries: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    The catalogue publishes some books twice, in two distinguishable ways.

    1. Only one copy got its files. Three 体育与健康 学生用书 volumes went up on
       2026-08-20 at 10:12, then again at 15:01 under a different parent, and
       only the later entries carry a PDF. Calling the earlier row 缺失 says the
       book is unavailable when it is one row away -> `kind: duplicate`, naming
       the row that has the file.
    2. Both copies got files, of different sizes: a re-scan of the same book,
       e.g. #150 at 74.2 MB and #3380 at 38.9 MB. Both are downloadable and stay
       so; they just each note the other, so a bulk download is not silently the
       same book twice -> `alternates`.

    Matching requires identical 分类 as well as an identical title, because a
    title alone is not a book: 体育与健康教师用书 体能（全一册）exists under 14
    different 版本/学段 pairs and those are genuinely different books, not
    re-scans of one.
    """
    def signature(entry: Dict[str, Any]) -> Tuple[Any, ...]:
        return (entry["title"],) + tuple(
            tuple(entry.get(key) or []) for key, _, _ in DIMENSIONS)

    best: Dict[str, Dict[str, Any]] = {}
    same_book: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for entry in entries:
        if entry["kind"] != "downloadable":
            continue
        current = best.get(entry["title"])
        if current is None or sum(entry["formats"].values()) > sum(current["formats"].values()):
            best[entry["title"]] = entry
        same_book.setdefault(signature(entry), []).append(entry)

    marked = 0
    for entry in entries:
        if entry["kind"] != "missing":
            continue
        twin = best.get(entry["title"])
        if not twin:
            continue
        entry["kind"] = "duplicate"
        entry["duplicate_of"] = {"seq": twin["seq"], "id": twin["id"],
                                 "formats": dict(twin["formats"])}
        marked += 1

    alternates = 0
    for group in same_book.values():
        if len(group) < 2:
            continue
        if all(entry.get("version") for entry in group):
            # full identity: same title, same 学段/学科/版本/年级/册次
            clusters = [group]
        else:
            # 版本 unknown, so the 分类 does NOT establish which book this is:
            # 体能（全一册）rows with no publisher include a 111 MB one and a
            # 5 MB one, which are two publishers' books, not one re-scanned.
            # Only a byte-identical file proves they are the same book.
            by_size: Dict[Any, List[Dict[str, Any]]] = {}
            for entry in group:
                by_size.setdefault(tuple(sorted(entry["formats"].items())), []).append(entry)
            clusters = [c for c in by_size.values() if len(c) > 1]

        for cluster in clusters:
            for entry in cluster:
                entry["alternates"] = [
                    {"seq": other["seq"], "id": other["id"],
                     "formats": dict(other["formats"]),
                     # byte-identical means the very same file published twice;
                     # a different size means a separate scan of the same book
                     "same_file": other["formats"] == entry["formats"]}
                    for other in cluster if other["id"] != entry["id"]]
                alternates += 1
    return marked, alternates


def _infer_from_title_twins(entries: List[Dict[str, Any]]) -> int:
    """
    Last resort for a row no parent can describe: agree-only consensus across
    rows of the identical title.

    67 rows are children of a parent the catalogue omits AND whose details
    endpoint 403s, so nothing authoritative states their 分类. Their titles do
    recur elsewhere, but copying one twin wholesale would invent a publisher:
    "体育与健康教师用书 体能（全一册）" exists under 14 different 版本/学段
    combinations. So copy a dimension only when EVERY twin agrees on it - that
    fills 学科/年级/册次 for 63 rows while correctly leaving 版本 blank on the
    ambiguous ones. Marked separately from parent inheritance, because agreement
    among namesakes is weaker evidence than a parent's own statement.
    """
    keys = [key for key, _, _ in DIMENSIONS]
    twins: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        if any(entry.get(key) for key in keys):
            twins.setdefault(entry["title"], []).append(entry)

    filled = 0
    for entry in entries:
        if any(entry.get(key) for key in keys):
            continue
        candidates = twins.get(entry["title"])
        if not candidates:
            continue
        agreed = False
        for key in keys:
            values = {tuple(c.get(key) or []) for c in candidates}
            if len(values) == 1:
                only = next(iter(values))
                if only:
                    entry[key] = list(only)
                    agreed = True
        if agreed:
            entry["tags_inferred"] = True
            filled += 1
    return filled


def build_index(refresh: bool = False,
                on_status: Optional[Callable[..., None]] = None) -> List[Dict[str, Any]]:
    """
    Merge the catalogue's facet tags with the downloader's format index.

    Cached on disk; the underlying format index is itself cached, so a warm
    start is instant. `on_status` receives building/ready/message keywords, so a
    caller with a UI can report progress without this module knowing about one.
    """
    global _index
    if _index is not None and not refresh:
        return _index

    with _index_lock:
        if _index is not None and not refresh:
            return _index

        if not refresh and INDEX_PATH.exists():
            try:
                cached = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
                if (time.time() - cached.get("generated", 0) < INDEX_MAX_AGE
                        and cached.get("schema") == INDEX_SCHEMA
                        and cached.get("books")):
                    _index = cached["books"]
                    _notify(on_status, ready=True, building=False, message="")
                    print(f"🗂️  Catalogue index loaded from cache ({len(_index)} books)")
                    return _index
            except (json.JSONDecodeError, OSError):
                pass

        _notify(on_status, building=True, ready=False, message="正在获取目录…")
        print("🔎 Building catalogue index (tags + formats)...")
        # Join on id, never on catalogue position: the platform inserts and
        # withdraws books, so a book's seq is only true for the scan that
        # recorded it. Joining by seq pairs a book with a neighbour's files.
        assets_by_id = {entry["id"]: entry
                        for entry in dl.build_asset_index(refresh=refresh)}
        books = dl.flat_catalog()

        # Course bundles hold no file themselves; map them to the child books
        # they contain so the UI can distinguish "is a container" from
        # "genuinely has nothing".
        special = dl.build_special_edu_index()
        course_children = special.get("courses") or {}
        seq_by_id = {b.get("id"): i for i, b in enumerate(books, 1)}
        by_id = {b.get("id"): b for b in books}
        parent_of: Dict[str, str] = {}
        for course_id, child_ids in course_children.items():
            for child_id in child_ids:
                parent_of.setdefault(child_id, course_id)

        entries: List[Dict[str, Any]] = []
        for seq, book in enumerate(books, 1):
            asset = assets_by_id.get(book.get("id", ""), {})
            formats = asset.get("formats") or {}
            entry: Dict[str, Any] = {
                "seq": seq,
                "id": book.get("id", ""),
                "title": dl.book_display_name(book),
                "status": asset.get("status", "error"),
                "formats": {k: v.get("size", 0) for k, v in formats.items()},
            }

            # kind: what this row IS, which is what the user needs to see
            #   downloadable - has a file
            #   course       - a thematic_course bundle; its books are separate rows
            #   duplicate    - a second entry for a book downloadable elsewhere
            #   missing      - a real gap: a document with no resolvable file
            if entry["formats"]:
                entry["kind"] = "downloadable"
            elif book.get("resource_type_code") == "thematic_course":
                entry["kind"] = "course"
                held = []
                for child_id in course_children.get(book.get("id", ""), []):
                    child = assets_by_id.get(child_id)
                    if child and child.get("formats"):
                        held.append({
                            "seq": seq_by_id.get(child_id),
                            "id": child_id,
                            "title": child.get("title", ""),
                            # same {kind: size} shape as a top-level row, so the
                            # UI can render a child as an ordinary book row
                            "formats": {k: v.get("size", 0)
                                        for k, v in child["formats"].items()},
                        })
                entry["children"] = held
            else:
                entry["kind"] = "missing"

            # Which book or course this row sits under. A child of a compound
            # resource is often titled just "第2课 使用数字设备", which says
            # nothing on its own about the book it came from.
            if (book.get("resource_custom_properties") or {}).get("compound_node") == "sub":
                parent_id = _parent_course_id(book, parent_of)
                if parent_id and parent_id != entry["id"]:
                    entry["parent"] = {
                        "id": parent_id,
                        "seq": seq_by_id.get(parent_id),
                        "title": dl.book_display_name(by_id[parent_id])
                                 if parent_id in by_id else "",
                    }

            entry.update(_tags_for(book))
            entries.append(entry)

        # Tags first: _mark_duplicates matches on 分类 as well as title, so it
        # has to see the inherited and inferred values, not the raw blanks.
        filled = _inherit_parent_tags(entries, books, parent_of,
                                      special.get("course_tags") or {},
                                      _dimension_vocabulary(books))
        if filled:
            print(f"🏷️  {filled} child resources took their 分类 from their course")
        inferred = _infer_from_title_twins(entries)
        if inferred:
            print(f"🔖 {inferred} rows took the 分类 their namesakes agree on")

        dupes, alts = _mark_duplicates(entries)
        if dupes or alts:
            print(f"♻️  {dupes} rows are a fileless second entry; "
                  f"{alts} rows have a same-book copy under another number")

        _index = entries
        try:
            INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            INDEX_PATH.write_text(
                json.dumps({"generated": time.time(), "schema": INDEX_SCHEMA,
                            "books": entries}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"⚠️ Could not cache catalogue index: {exc}")

        _notify(on_status, building=False, ready=True, message="")
        print(f"✅ Catalogue index ready: {len(entries)} books")
        return _index
