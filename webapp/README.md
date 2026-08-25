# webapp

Local web UI for the textbook downloader.

```bash
python3 webapp/server.py                 # http://127.0.0.1:8000
python3 webapp/server.py --port 9000
python3 webapp/server.py --refresh-index # rescan formats before starting
```

| File | Purpose |
|---|---|
| `server.py` | Stdlib HTTP server: facet index, JSON API, streaming download proxy, bulk job queue, SSE |
| `static/index.html` | Page structure (Simplified Chinese) |
| `static/app.js` | Filters, results, expandable course bundles, selection, job panel, history |
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
- The view lives in the query string, so Back steps through filters instead of leaving the
  app, and a filtered page can be reloaded or shared. Typing in the search box replaces the
  current entry rather than pushing one per keystroke pause.
- A 📦 course bundle holds no file of its own; expanding it lists its books as indented rows
  in their own colour, each with its own checkbox and download buttons.
- 582 rows are children of a compound resource and reach the catalogue with `tag_list` and
  `tag_paths` both empty, so every dimension read 未标注. They now take their 分类 from the
  parent course - marked `↑` in the 分类 column, because the row does not declare it itself.
  A parent the catalogue omits states its taxonomy as one flat tag list, which is sorted into
  dimensions by matching names against the vocabularies the properly tagged rows establish.
- Rows whose parent is itself uncatalogued fall back, in order, to their own title
  (`✎`), to 学段 derived from 年级 plus the school system the title names (`⊢`), to the
  publisher some PDFs are stored under, and finally to `catalogue_overrides.json` (`✓`)
  for facts no request reaches. Each rule was validated against the rows the platform
  tagged itself - 版本 and 学段 at 100%, the 学段 derivation at 99.5% - and a row is
  labelled by the WEAKEST source it relies on, so a verified 版本 never makes a parsed
  年级 look authoritative.
- What is still blank is written nowhere: 58 rows lack 学段 (体育与健康 教师用书 and
  学生用书, which have no numeric grade and whose PDF filenames are a single field), and
  301 of the rows lacking 版本 are 特殊教育, which the platform gives no publisher.
- ♻️ 本条无文件 is a row the platform published twice while wiring the files to only one
  copy (3 rows). It is not a gap: the row states that IT has nothing and names the
  sequence number that does, with that file's size. ⚠️ 缺失 therefore means only
  "genuinely unobtainable", and currently counts 0.
- ♊ 同名多版本 (134 rows) is the other duplication: both copies DO have a file. Either a
  separate scan of the same book (#150 at 74.2 MB, #3380 at 38.9 MB) or the very same
  file published twice (byte-identical, 52 rows) - the row says which. These stay
  downloadable and selectable; they just warn that a bulk download would otherwise fetch
  one book twice. Two rows count as the same book only when 版本 is known for both, or
  when the file is byte-identical: without a publisher a shared title proves nothing,
  since 体能（全一册）rows with no 版本 include both a 111 MB and a 5 MB book.
- The web index is cached at `~/Library/Caches/textbook-downloader/web_index.json` for 7 days,
  and is keyed by book id - the catalogue reorders itself, so joining on sequence number would
  hand a row another book's files.
- Bulk jobs run 2 at a time with a 1 s gap, matching the CLI's politeness to the CDN.
- Downloads resolve to the same folder the CLI uses, and existing files are skipped.
