# webapp

Local web UI for the textbook downloader.

```bash
python webapp/server.py                 # http://127.0.0.1:8000
python webapp/server.py --port 9000
python webapp/server.py --refresh-index # rescan formats before starting
```

| File | Purpose |
|---|---|
| `server.py` | Stdlib HTTP server: facet index, JSON API, streaming download proxy, bulk job queue, SSE |
| `static/index.html` | Page structure (Simplified Chinese) |
| `static/app.js` | Filters, results, selection, job panel |
| `static/style.css` | Styling, light + dark |

Every network operation delegates to `pdf_book_download_from_zxxeducn`, so resolution,
validation and CDN fallback behave exactly as on the CLI.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | index readiness, output directory, filter dimensions |
| `GET /api/facets?<filters>` | options + counts per dimension, counted against the other filters |
| `GET /api/books?<filters>&page=&page_size=` | paged results |
| `GET /api/download/{id}?fmt=pdf` | streamed proxy, RFC 5987 filename |
| `POST /api/jobs` | `{items:[{id,title,fmt}]}` → `{job_id}` |
| `POST /api/jobs/{id}/cancel` | cancel a running job |
| `GET /api/events` | SSE stream of job snapshots |
| `GET /api/library` | files already in the download folder |
| `POST /api/reindex` | rebuild the format index in the background |

## Notes

- Binds `127.0.0.1`, no authentication, no rate limiting - a local tool.
- The format index is cached at `~/Downloads/textbook_download/.web_index.json` for 7 days.
- Bulk jobs run 2 at a time with a 1 s gap, matching the CLI's politeness to the CDN.
- Downloads resolve to the same folder the CLI uses, and existing files are skipped.
