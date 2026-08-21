#!/usr/bin/env python3
"""
Local web UI for the National Smart Education Platform textbook downloader.

Runs on the standard library only (plus `requests`, which the downloader
already needs), so there is nothing to install:

    python webapp/server.py            # then open http://127.0.0.1:8000

It reuses `pdf_book_download_from_zxxeducn` for every network operation, so
resolution, validation and CDN fallback behave exactly as they do on the CLI.

Design notes
------------
* Browsing is driven by the platform's own tag dimensions - 学段 / 学科 /
  版本 / 年级 / 册次 - which arrive on each catalogue entry as `tag_list`
  entries carrying a `tag_dimension_id`.
* ~17% of books carry no tags at all, so every dimension also offers an
  "未标注" bucket; those books stay reachable instead of vanishing.
* Single downloads stream through this server so the saved file keeps its
  proper 出版社+书名 filename.
* Bulk downloads run as background jobs writing into the same
  ~/Downloads/textbook_download/ folder the CLI uses, with progress pushed
  to the browser over SSE.

This binds to 127.0.0.1 and has no authentication or rate limiting - it is a
local tool. See README before exposing it to a network.
"""

import json
import mimetypes
import os
import queue
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pdf_book_download_from_zxxeducn as dl  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
WEB_INDEX_PATH = dl.CACHE_DIR / "web_index.json"
WEB_INDEX_MAX_AGE = 7 * 24 * 3600

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

MAX_BULK_ITEMS = 500
JOB_WORKERS = 2


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

_index_lock = threading.Lock()
_index: Optional[List[Dict[str, Any]]] = None
_index_state = {"building": False, "message": "", "ready": False}


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

    # 241 特殊教育 titles (聋校 / 培智) are tagged with BOTH 特殊教育 and the
    # grade-level 学段 they correspond to, which would otherwise surface
    # subjects like 律动 under 小学. The platform lists them under 特殊教育
    # only, so that tag wins. 学段 is the sole dimension ever multi-valued.
    if len(grouped["stage"]) > 1 and "特殊教育" in grouped["stage"]:
        grouped["stage"] = ["特殊教育"]
    return grouped


def build_web_index(refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Merge the catalogue's facet tags with the downloader's format index.

    Cached on disk; the underlying format index is itself cached, so a warm
    start is instant.
    """
    global _index
    if _index is not None and not refresh:
        return _index

    with _index_lock:
        if _index is not None and not refresh:
            return _index

        if not refresh and WEB_INDEX_PATH.exists():
            try:
                cached = json.loads(WEB_INDEX_PATH.read_text(encoding="utf-8"))
                if (time.time() - cached.get("generated", 0) < WEB_INDEX_MAX_AGE
                        and cached.get("books")):
                    _index = cached["books"]
                    _index_state.update(ready=True, building=False, message="")
                    print(f"🗂️  Web index loaded from cache ({len(_index)} books)")
                    return _index
            except (json.JSONDecodeError, OSError):
                pass

        _index_state.update(building=True, ready=False, message="正在获取目录…")
        print("🔎 Building web index (catalogue + formats)...")
        assets = {entry["seq"]: entry for entry in dl.build_asset_index(refresh=refresh)}
        books = dl.flat_catalog()

        entries: List[Dict[str, Any]] = []
        for seq, book in enumerate(books, 1):
            asset = assets.get(seq, {})
            formats = asset.get("formats") or {}
            entry: Dict[str, Any] = {
                "seq": seq,
                "id": book.get("id", ""),
                "title": dl.book_display_name(book),
                "status": asset.get("status", "error"),
                "formats": {k: v.get("size", 0) for k, v in formats.items()},
            }
            entry.update(_tags_for(book))
            entries.append(entry)

        _index = entries
        try:
            WEB_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            WEB_INDEX_PATH.write_text(
                json.dumps({"generated": time.time(), "books": entries}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"⚠️ Could not cache web index: {exc}")

        _index_state.update(building=False, ready=True, message="")
        print(f"✅ Web index ready: {len(entries)} books")
        return _index


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _matches_dimension(entry: Dict[str, Any], key: str, wanted: str) -> bool:
    values = entry.get(key) or []
    if wanted == UNTAGGED:
        return not values
    return wanted in values


def filter_books(params: Dict[str, str], skip: Optional[str] = None) -> List[Dict[str, Any]]:
    """Apply the active filters, optionally ignoring one dimension (for facet counts)."""
    books = build_web_index()
    query = (params.get("q") or "").strip()
    fmt = params.get("fmt") or ""
    availability = params.get("availability") or ""

    result = []
    for entry in books:
        ok = True
        for key, _, _ in DIMENSIONS:
            if key == skip:
                continue
            wanted = params.get(key)
            if wanted and not _matches_dimension(entry, key, wanted):
                ok = False
                break
        if not ok:
            continue
        if fmt in ("pdf", "pptx") and fmt not in entry["formats"]:
            continue
        if availability == "public" and entry["status"] != "ok":
            continue
        if availability == "restricted" and entry["status"] != "restricted":
            continue
        if query and query not in entry["title"]:
            continue
        result.append(entry)
    return result


def facet_counts(params: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Options and counts per dimension.

    Each dimension is counted against the other active filters, so the numbers
    reflect what selecting that value would actually yield.
    """
    facets: Dict[str, List[Dict[str, Any]]] = {}
    for key, _, _ in DIMENSIONS:
        subset = filter_books(params, skip=key)
        counts: Dict[str, int] = {}
        untagged = 0
        for entry in subset:
            values = entry.get(key) or []
            if not values:
                untagged += 1
            for value in values:
                counts[value] = counts.get(value, 0) + 1
        options = [{"value": v, "count": c} for v, c in
                   sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        if untagged:
            options.append({"value": UNTAGGED, "count": untagged})
        facets[key] = options
    return facets


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobManager:
    """Background bulk downloads with progress, backed by a small worker pool."""

    def __init__(self, workers: int = JOB_WORKERS) -> None:
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.work: "queue.Queue[Tuple[str, int]]" = queue.Queue()
        self.subscribers: List["queue.Queue[str]"] = []
        self.dirty = threading.Event()
        for _ in range(workers):
            threading.Thread(target=self._worker, daemon=True).start()

    # -- pub/sub ---------------------------------------------------------
    def subscribe(self) -> "queue.Queue[str]":
        channel: "queue.Queue[str]" = queue.Queue()
        with self.lock:
            self.subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: "queue.Queue[str]") -> None:
        with self.lock:
            if channel in self.subscribers:
                self.subscribers.remove(channel)

    def publish(self, job_id: str) -> None:
        with self.lock:
            payload = json.dumps(self._snapshot(job_id), ensure_ascii=False)
            channels = list(self.subscribers)
        for channel in channels:
            channel.put(payload)

    # -- job lifecycle ---------------------------------------------------
    def create(self, items: List[Dict[str, str]]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.jobs[job_id] = {
                "id": job_id,
                "created": time.time(),
                "cancel": False,
                "items": [
                    {
                        "id": item["id"],
                        "title": item.get("title") or item["id"],
                        "fmt": item.get("fmt", "pdf"),
                        "status": "pending",
                        "written": 0,
                        "total": 0,
                        "error": "",
                    }
                    for item in items
                ],
            }
        for position in range(len(items)):
            self.work.put((job_id, position))
        self.publish(job_id)
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            job["cancel"] = True
            for item in job["items"]:
                if item["status"] == "pending":
                    item["status"] = "cancelled"
        self.publish(job_id)
        return True

    def _snapshot(self, job_id: str) -> Dict[str, Any]:
        job = self.jobs[job_id]
        items = job["items"]
        done = sum(1 for i in items if i["status"] in ("done", "failed", "skipped", "cancelled"))
        active = any(i["status"] in ("pending", "running") for i in items)
        return {
            "id": job_id,
            "created": job["created"],
            "cancelled": job["cancel"],
            "finished": not active,
            "total": len(items),
            "completed": done,
            "succeeded": sum(1 for i in items if i["status"] in ("done", "skipped")),
            "failed": sum(1 for i in items if i["status"] == "failed"),
            "items": items,
        }

    def snapshot(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self._snapshot(job_id) if job_id in self.jobs else None

    def all_snapshots(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [self._snapshot(jid) for jid in
                    sorted(self.jobs, key=lambda j: self.jobs[j]["created"], reverse=True)]

    # -- worker ----------------------------------------------------------
    def _worker(self) -> None:
        while True:
            job_id, position = self.work.get()
            try:
                self._run_item(job_id, position)
            except Exception as exc:  # a worker must never die
                print(f"⚠️ job {job_id} item {position} crashed: {exc}")
            finally:
                self.work.task_done()

    def _run_item(self, job_id: str, position: int) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["cancel"]:
                return
            item = job["items"][position]
            if item["status"] != "pending":
                return
            item["status"] = "running"
        self.publish(job_id)

        details, status = dl.get_book_details(item["id"], quiet=True)
        if details is None:
            self._finish(job_id, position, "failed",
                         "未发布可下载文件" if status == "restricted" else "无法获取资源信息")
            return

        asset = dl.pick_asset(details, item["fmt"])
        if asset is None:
            self._finish(job_id, position, "failed", f"未提供 {item['fmt'].upper()} 文件")
            return

        with self.lock:
            item["total"] = asset.get("ti_size") or 0

        last_push = [0.0]

        def on_progress(written: int, expected: int) -> None:
            with self.lock:
                item["written"] = written
                if expected:
                    item["total"] = expected
            now = time.time()
            if now - last_push[0] > 0.4:   # throttle SSE traffic
                last_push[0] = now
                self.publish(job_id)

        def is_cancelled() -> bool:
            with self.lock:
                return bool(self.jobs.get(job_id, {}).get("cancel"))

        urls = dl.storages_to_public_urls(asset["ti_storages"])
        ok = dl.download_asset(
            urls, item["title"], item["fmt"], str(dl.OUTPUT_DIR),
            overwrite=False,
            extension=(asset.get("ti_format") or item["fmt"]).lower(),
            progress=on_progress, cancelled=is_cancelled, quiet=True,
        )
        if is_cancelled():
            self._finish(job_id, position, "cancelled", "已取消")
        else:
            self._finish(job_id, position, "done" if ok else "failed",
                         "" if ok else "下载失败")
        time.sleep(0.5)  # stay polite to the CDN between items

    def _finish(self, job_id: str, position: int, status: str, error: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            item = job["items"][position]
            item["status"] = status
            item["error"] = error
            if status == "done" and item["total"] and not item["written"]:
                item["written"] = item["total"]
        self.publish(job_id)


JOBS = JobManager()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def content_disposition(filename: str) -> str:
    """RFC 5987 header so Chinese filenames survive the trip to the browser."""
    ascii_fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "download"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


class Handler(BaseHTTPRequestHandler):
    server_version = "TextbookWeb/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def handle_one_request(self) -> None:
        """
        Swallow abrupt client disconnects.

        Browsers routinely drop keep-alive connections (and cancel downloads
        mid-stream), which otherwise makes socketserver dump a traceback for
        an entirely normal event.
        """
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            self.close_connection = True

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("TEXTBOOK_WEB_VERBOSE"):
            super().log_message(fmt, *args)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> Dict[str, str]:
        raw = parse_qs(urlsplit(self.path).query)
        return {k: v[0] for k, v in raw.items() if v and v[0] != ""}

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            elif path == "/api/status":
                self._send_json({
                    "ready": _index_state["ready"],
                    "building": _index_state["building"],
                    "output_dir": str(dl.OUTPUT_DIR),
                    "dimensions": [{"key": k, "label": l} for k, l, _ in DIMENSIONS],
                })
            elif path == "/api/facets":
                params = self._query()
                self._send_json({"facets": facet_counts(params),
                                 "total": len(filter_books(params))})
            elif path == "/api/books":
                self._books()
            elif path == "/api/jobs":
                self._send_json({"jobs": JOBS.all_snapshots()})
            elif path == "/api/events":
                self._events()
            elif path == "/api/library":
                self._library()
            elif path.startswith("/api/download/"):
                self._download(unquote(path[len("/api/download/"):]))
            else:
                self._send_json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # keep the server alive on unexpected errors
            print(f"⚠️ GET {path} failed: {exc}")
            try:
                self._send_json({"error": str(exc)}, 500)
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path == "/api/jobs":
                self._create_job()
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path[len("/api/jobs/"):-len("/cancel")]
                self._send_json({"ok": JOBS.cancel(job_id)})
            elif path == "/api/reindex":
                threading.Thread(target=build_web_index, kwargs={"refresh": True},
                                 daemon=True).start()
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            print(f"⚠️ POST {path} failed: {exc}")
            self._send_json({"error": str(exc)}, 500)

    # -- endpoints -------------------------------------------------------
    def _serve_static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _books(self) -> None:
        params = self._query()
        page = max(1, int(params.get("page", "1") or 1))
        page_size = min(200, max(1, int(params.get("page_size", "50") or 50)))
        matches = filter_books(params)
        start = (page - 1) * page_size
        self._send_json({
            "total": len(matches),
            "page": page,
            "page_size": page_size,
            "books": matches[start:start + page_size],
        })

    def _library(self) -> None:
        files = []
        if dl.OUTPUT_DIR.exists():
            for entry in sorted(dl.OUTPUT_DIR.iterdir()):
                if entry.is_file() and entry.suffix.lower() in (".pdf", ".pptx"):
                    files.append({"name": entry.name, "size": entry.stat().st_size})
        self._send_json({"dir": str(dl.OUTPUT_DIR), "files": files})

    def _create_job(self) -> None:
        payload = self._body()
        items = payload.get("items") or []
        if not items:
            self._send_json({"error": "没有选择任何资源"}, 400)
            return
        if len(items) > MAX_BULK_ITEMS:
            self._send_json({"error": f"一次最多 {MAX_BULK_ITEMS} 项"}, 400)
            return
        self._send_json({"job_id": JOBS.create(items)})

    def _download(self, book_id: str) -> None:
        """Stream one asset through the server so the filename is preserved."""
        params = self._query()
        kind = params.get("fmt", "pdf")
        if kind not in dl.ASSET_KINDS:
            self._send_json({"error": "未知格式"}, 400)
            return

        details, status = dl.get_book_details(book_id, quiet=True)
        if details is None:
            message = "该资源未发布可下载文件（仅提供在线预览）" if status == "restricted" else "无法获取资源信息"
            self._send_json({"error": message}, 403 if status == "restricted" else 502)
            return

        asset = dl.pick_asset(details, kind)
        if asset is None:
            self._send_json({"error": f"该资源未提供 {kind.upper()} 文件"}, 404)
            return

        title = params.get("title") or dl.book_display_name(details)
        extension = (asset.get("ti_format") or kind).lower()
        filename = f"{dl.sanitize_filename(title)}.{extension}"

        for url in dl.storages_to_public_urls(asset["ti_storages"]):
            try:
                upstream = dl.SESSION.get(url, timeout=(15, 120), stream=True)
            except Exception:
                continue
            if upstream.status_code != 200:
                upstream.close()
                continue
            length = upstream.headers.get("content-length")
            self.send_response(200)
            self.send_header("Content-Type", upstream.headers.get("content-type",
                                                                  "application/octet-stream"))
            if length:
                self.send_header("Content-Length", length)
            self.send_header("Content-Disposition", content_disposition(filename))
            self.end_headers()
            try:
                for chunk in upstream.iter_content(dl.CHUNK_SIZE):
                    if chunk:
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser cancelled the download
            finally:
                upstream.close()
            return

        self._send_json({"error": "所有 CDN 节点均不可用"}, 502)

    def _events(self) -> None:
        """Server-sent events carrying job snapshots."""
        channel = JOBS.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for snapshot in JOBS.all_snapshots():
                self._sse(json.dumps(snapshot, ensure_ascii=False))
            while True:
                try:
                    payload = channel.get(timeout=15)
                    self._sse(payload)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")   # keep the connection open
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            JOBS.unsubscribe(channel)

    def _sse(self, payload: str) -> None:
        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Local web UI for the textbook downloader")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default: 127.0.0.1, local only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--refresh-index", action="store_true",
                        help="rebuild the catalogue/format index before starting")
    args = parser.parse_args()

    threading.Thread(target=build_web_index,
                     kwargs={"refresh": args.refresh_index}, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"🌐 教材下载器已启动: http://{args.host}:{args.port}")
    print(f"📁 下载目录: {dl.OUTPUT_DIR}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("⚠️  正在监听非本地地址，该服务没有任何鉴权与限流。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹️  已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
