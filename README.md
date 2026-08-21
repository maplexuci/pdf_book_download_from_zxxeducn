# Enhanced Textbook Downloader from National Smart Education Platform

[English](#english) | [中文](#中文)

---

## English

### 📚 Overview

This enhanced script downloads complete PDF textbooks from the Chinese National Smart Education Platform (国家中小学智慧教育平台) with multiple download modes, comprehensive error handling, and flexible download controls.

### 📊 Companion Script: textbook_info.py

**Start here.** `textbook_info.py` builds a CSV inventory of the whole catalogue - one row
per book - and it is what gives every book the **sequence number** that the download modes
below refer to.

This step exists because the catalogue files the platform publishes carry an empty
`ti_items`: neither a book's position nor its available formats are knowable until that
book's own details JSON is fetched. `textbook_info.py` does that once for all 3743 books
and caches the result.

```bash
python3 textbook_info.py                 # full catalogue
python3 textbook_info.py --pptx-only     # only books with a PowerPoint deck
```

Written to `~/Downloads/textbook_info/textbook_info.csv`:

| Number | Book ID | Book Name | Has PDF | Has PPTX | PDF Size (MB) | PPTX Size (MB) | Availability |
|---|---|---|---|---|---|---|---|
| 1 | bdc00134-... | 统编版...道德与法治一年级上册 | yes | no | 48.2 | | public |
| 1981 | 6890be8d-... | 第5课 美化处理图片 | yes | yes | 4.6 | 35.1 | public |
| 2262 | a826f33f-... | 沪教版...体育与健康 一年级（全一册） | no | no | | | restricted by platform |

**`Number` is the sequence number** taken by `--sequence` and `--range`. Find the row you
want, then pass its Number:

```bash
python3 pdf_book_download_from_zxxeducn.py --sequence 1981 --format pptx
```

The remaining columns tell you what to expect before committing to a download: `Has PDF` /
`Has PPTX` with their sizes, and `Availability`, which separates a book that merely has no
deck from one the platform blocks outright (`restricted by platform`).

Both scripts share the same cached format index (7 days; `--refresh-index` rebuilds it), so
whichever you run first pays the one-time scan cost.

### 🌐 Web Interface

A local web UI wraps the same downloader with browsing, filtering and bulk downloads:

```bash
python3 webapp/server.py          # then open http://127.0.0.1:8000
```

It needs no extra packages - standard library plus the `requests` the CLI already uses.
The server runs in the foreground; press `Ctrl-C` to stop it. Nothing is served once it exits,
so **"127.0.0.1 refused to connect" almost always means the server is not running** - start it
again with the command above. On macOS use `python3`; there is usually no bare `python`.
To keep it running after closing the terminal:

```bash
nohup python3 webapp/server.py > /tmp/textbook-webapp.log 2>&1 &   # start in background
pkill -f webapp/server.py                                          # stop it
```

The first launch spends a few minutes building the format index; later starts read the cache
and are instant. The page shows 正在建立索引 while that runs.

- **Cascading filters** using the platform's own tag dimensions: 学段 → 学科 → 版本 → 年级 → 册次,
  each option showing how many books it would yield, plus format and title search.
- **Resource status** makes coverage explicit, with a running count of each in the header:
  ✅ 有可下载文件 (3384), 📦 课程合集 (302 - bundles whose child books are separate, linked rows),
  ⚠️ 缺失 (57 - genuine gaps, shown tinted with the reason and no download button).
- **Single downloads** stream through the server so the file keeps its proper 出版社+书名 name.
- **Bulk downloads** run as background jobs (2 at a time, 1 s apart) writing into the same
  `~/Downloads/textbook_download/` folder, with live progress over SSE and a cancel button.
- The interface is in Simplified Chinese; the ~17% of books with no tags stay reachable through
  each filter's 未标注 option.

**This is a local tool.** It binds to `127.0.0.1` and has no authentication or rate limiting.
Before exposing it to a network, note that every download would flow through that machine's
bandwidth and all CDN requests would originate from one IP; a public deployment would also make
it a redistribution point for the whole corpus, which is a different act from downloading for
personal use.

### ✨ Features

- **Multiple Download Modes**: Sequence number, book range, book ID, and legacy catalog-based approaches
- **CDN Fallback Logic**: Automatically tries r1, r2, r3 endpoints if one fails
- **Enhanced Error Handling**: Detailed error messages and graceful fallbacks
- **Progress Tracking**: Real-time download status and file size information
- **Flexible Controls**: Download specific books, ranges, or use legacy catalog-based approach
- **Robust Network Handling**: Timeouts, retries, and connection error handling

### 🚀 Download Modes

#### 1. **By Sequence Number** (`--sequence`)
Downloads a specific book by its global sequence number across all catalogs.

```bash
python3 pdf_book_download_from_zxxeducn.py --sequence 2548
```

#### 2. **By Book Range** (`--range`)
Downloads multiple books within a specified range.

```bash
python3 pdf_book_download_from_zxxeducn.py --range "200-250"
python3 pdf_book_download_from_zxxeducn.py --range "200"  # Single book
```

#### 3. **By Book ID** (`--book-id`)
Downloads a specific book by its unique identifier (UUID).

```bash
python3 pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"
```

#### 4. **Legacy Modes**
- `--single N`: Download only the Nth textbook from the catalog
- `--limit N`: Download only N textbooks (starting from the beginning)
- `--table N`: Start from catalog N (0-based indexing)
- `--item N`: Start from item N within the catalog (0-based indexing)

#### 5. **PowerPoint Materials** (`--all-pptx`)
252 of the catalogued titles - mostly 信息科技 lesson materials - publish a `.pptx` deck under
flag `source` alongside a PDF under flag `pdf`. They have a dedicated route:
```bash
python3 pdf_book_download_from_zxxeducn.py --list-pptx          # list all 252 (2.0 GB total)
python3 pdf_book_download_from_zxxeducn.py --all-pptx           # download every deck
python3 pdf_book_download_from_zxxeducn.py --all-pptx --format all --limit 5
```

#### Formats
`--format` works with every mode above:
- `--format pdf`: PDF only (default)
- `--format pptx`: PowerPoint only
- `--format all`: every format the book publishes

```bash
python3 pdf_book_download_from_zxxeducn.py --sequence 1981 --format pptx
```

#### Listing
- `--list-pptx`: list the books that publish a PowerPoint deck
- `--list-pdf`: list the books that publish a PDF
- `--refresh-index`: rebuild the cached format index (~3700 requests, cached for 7 days)
  in `~/Library/Caches/textbook-downloader/` (macOS) or `~/.cache/textbook-downloader/`,
  deliberately outside the downloads folder so clearing downloads does not force a rescan

See [Companion Script: textbook_info.py](#-companion-script-textbook_infopy) for a CSV
inventory of which books offer which formats.

#### Options
- `--dry-run`: Resolve and print the download URL without downloading
- `--overwrite`: Re-download files that already exist locally

#### What the catalog contains
Surveyed across all 3743 catalogued titles:

| | Count |
|---|---|
| PDF available | 3384 |
| PowerPoint available | 252 (all 252 also have a PDF) |
| No file of their own | 359 (302 course containers + 57 unresolved) |

#### Special-Education Titles, And What Is Left

Special-education books (the 体育与健康 series and others) are published as **course bundles**,
not standalone documents. Requesting the child document's own id from the usual details
endpoint returns HTTP 403 - not because it is restricted, but because that id belongs to a
`thematic_course`. The real record lives in the parent course listing:

```
https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/special_edu/thematic_course/{course_id}/resources/list.json
```

The downloader consults that listing automatically, which makes **372 previously unreachable
titles** downloadable.

What remains without a file of its own: 302 `thematic_course` container nodes (course bundles
rather than books - their child books are catalogued separately and download fine) and 57
special-education entries whose parent course lists no file for them.

### 📋 Requirements

- Python 3.6+
- `requests` library
- Internet connection
- Access to the National Smart Education Platform

### 🛠️ Installation

1. Clone or download the script
2. Install required dependencies:
```bash
pip install requests
```

### 📖 Usage Examples

```bash
# Download by sequence number
python3 pdf_book_download_from_zxxeducn.py --sequence 2548

# Download a range of books
python3 pdf_book_download_from_zxxeducn.py --range "1-5"

# Download by book ID
python3 pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"

# Legacy single book download
python3 pdf_book_download_from_zxxeducn.py --single 1

# Legacy limited download
python3 pdf_book_download_from_zxxeducn.py --limit 10

# Resume interrupted download
python3 pdf_book_download_from_zxxeducn.py --table 1 --item 5
```

### 🔧 Technical Details

- **CDN Endpoints**: Automatically tries r1-ndr-oversea, r2-ndr-oversea, and r3-ndr-oversea in sequence
- **PDF Detection**: The PDF is chosen by `ti_format == "pdf"` (usually flag `source`, but some
  materials keep a `.pptx` under `source` and the PDF under flag `pdf`)
- **File Validation**: Validated by `%PDF` magic bytes and Content-Length, not by size guessing
- **Safe Writes**: Streamed to a `.pdf.part` file and renamed only when complete
- **Network Timeouts**: 30-second timeout for all network requests
- **Output Directory**: All downloads are saved to `~/Downloads/textbook_download/`

### 📁 Output

Downloaded PDFs are saved to:
```
~/Downloads/textbook_download/
├── 统编版（根据2022年版课程标准修订）义务教育教科书·道德与法治一年级上册.pdf
├── 统编版（根据2022年版课程标准修订）义务教育教科书·道德与法治一年级下册.pdf
└── ...
```

### 🐛 Troubleshooting

- **Network Errors**: Check your internet connection and firewall settings
- **Permission Errors**: Ensure you have write access to the Downloads folder
- **Timeout Errors**: The script will automatically retry with different CDN endpoints

### 📝 License

Open source - feel free to use and modify as needed.

---

## 中文

### 📚 概述

这是一个增强版的脚本，用于从国家中小学智慧教育平台下载完整的PDF教材，支持多种下载模式、全面的错误处理和灵活的下载控制。

### 📊 配套脚本：textbook_info.py

**请从这里开始。** `textbook_info.py` 会导出整个目录的 CSV 清单（每本书一行），
下方各下载模式所使用的**序列号**正是由它给出的。

之所以需要这一步，是因为平台发布的目录文件中 `ti_items` 为空：在获取每本书自身的
详情接口之前，既无法得知其位置，也无法得知其提供哪些格式。`textbook_info.py`
会对全部 3743 个资源完成这一步，并缓存结果。

```bash
python3 textbook_info.py                 # 完整目录
python3 textbook_info.py --pptx-only     # 仅含 PowerPoint 课件的资源
```

保存至 `~/Downloads/textbook_info/textbook_info.csv`：

| Number | Book ID | Book Name | Has PDF | Has PPTX | PDF Size (MB) | PPTX Size (MB) | Availability |
|---|---|---|---|---|---|---|---|
| 1 | bdc00134-... | 统编版...道德与法治一年级上册 | yes | no | 48.2 | | public |
| 1981 | 6890be8d-... | 第5课 美化处理图片 | yes | yes | 4.6 | 35.1 | public |
| 2262 | a826f33f-... | 沪教版...体育与健康 一年级（全一册） | no | no | | | restricted by platform |

**`Number` 即 `--sequence` 与 `--range` 所用的序列号。** 在 CSV 中找到所需的行，
然后传入其 Number 即可：

```bash
python3 pdf_book_download_from_zxxeducn.py --sequence 1981 --format pptx
```

其余列可在下载前告知预期内容：`Has PDF` / `Has PPTX` 及其文件大小，以及
`Availability`——用于区分"该资源本就没有课件"与"平台完全禁止下载"
（`restricted by platform`）两种情况。

两个脚本共用同一份格式索引缓存（7 天；`--refresh-index` 可重建），
因此先运行哪个都可以，一次扫描的开销只需付出一次。

### 🌐 网页界面

配套的本地网页界面在同一套下载逻辑之上提供浏览、筛选与批量下载：

```bash
python3 webapp/server.py          # 然后打开 http://127.0.0.1:8000
```

无需安装额外依赖——仅使用标准库以及命令行脚本已经依赖的 `requests`。
服务在前台运行，按 `Ctrl-C` 即可停止。进程退出后不再提供任何服务，
因此**出现"127.0.0.1 拒绝连接"时，几乎都是因为服务没有在运行**——重新执行上面的命令即可。
macOS 上请使用 `python3`，系统通常没有单独的 `python` 命令。
若希望关闭终端后继续运行：

```bash
nohup python3 webapp/server.py > /tmp/textbook-webapp.log 2>&1 &   # 后台启动
pkill -f webapp/server.py                                          # 停止
```

首次启动需要几分钟建立格式索引，之后启动会直接读取缓存、瞬间完成。
索引建立期间页面会显示"正在建立索引"。

- **级联筛选**，直接采用平台自身的标签维度：学段 → 学科 → 版本 → 年级 → 册次，
  每个选项都会显示对应的资源数量，另有格式与书名搜索。
- **资源状态**一目了然，并在标题栏实时统计：
  ✅ 有可下载文件（3384）、📦 课程合集（302，其子教材为可点击的独立条目）、
  ⚠️ 缺失（57，标红并说明原因，且不提供下载按钮）。
- **单个下载**经由服务器转发，因此文件名会保留完整的"出版社+书名"。
- **批量下载**以后台任务运行（同时 2 个、间隔 1 秒），保存至相同的
  `~/Downloads/textbook_download/` 目录，并通过 SSE 实时显示进度，可随时取消。
- 界面为简体中文；约 17% 没有标签的资源可通过各筛选项中的"未标注"继续访问。

**这是一个本地工具。** 它仅监听 `127.0.0.1`，没有任何鉴权与限流。
若要对外提供访问，请注意：所有下载流量都会经过该主机的带宽，且全部 CDN 请求都来自同一个 IP；
公开部署还会使其成为整个资源库的再分发点，这与个人下载的性质并不相同。

### ✨ 功能特点

- **多种下载模式**: 序列号、书籍范围、书籍ID和传统目录方式
- **CDN故障转移**: 自动尝试r1、r2、r3端点，如果一个失败则切换到下一个
- **增强错误处理**: 详细的错误信息和优雅的故障转移
- **进度跟踪**: 实时下载状态和文件大小信息
- **灵活控制**: 下载特定书籍、范围或使用传统目录方式
- **稳健网络处理**: 超时、重试和连接错误处理

### 🚀 下载模式

#### 1. **按序列号下载** (`--sequence`)
通过全局序列号下载特定书籍（跨所有目录）。

```bash
python3 pdf_book_download_from_zxxeducn.py --sequence 2548
```

#### 2. **按书籍范围下载** (`--range`)
下载指定范围内的多本书籍。

```bash
python3 pdf_book_download_from_zxxeducn.py --range "200-250"
python3 pdf_book_download_from_zxxeducn.py --range "200"  # 单本书
```

#### 3. **按书籍ID下载** (`--book-id`)
通过唯一标识符（UUID）下载特定书籍。

```bash
python3 pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"
```

#### 4. **传统模式**
- `--single N`: 仅下载目录中的第N本教材
- `--limit N`: 限制本次运行下载的书籍数量
- `--table N`: 从目录N开始（基于0的索引）
- `--item N`: 从目录中的项目N开始（基于0的索引）

#### 5. **PowerPoint 课件** (`--all-pptx`)
目录中有 252 个资源（主要为信息科技课程课件）在 `source` 标记下提供 `.pptx` 课件，
同时在 `pdf` 标记下提供对应 PDF。这类内容有独立的下载通道：
```bash
python3 pdf_book_download_from_zxxeducn.py --list-pptx          # 列出全部 252 个（共约 2.0 GB）
python3 pdf_book_download_from_zxxeducn.py --all-pptx           # 下载全部课件
python3 pdf_book_download_from_zxxeducn.py --all-pptx --format all --limit 5
```

#### 文件格式
`--format` 适用于以上所有模式：
- `--format pdf`: 仅 PDF（默认）
- `--format pptx`: 仅 PowerPoint
- `--format all`: 该资源发布的所有格式

```bash
python3 pdf_book_download_from_zxxeducn.py --sequence 1981 --format pptx
```

#### 列表查询
- `--list-pptx`: 列出提供 PowerPoint 课件的资源
- `--list-pdf`: 列出提供 PDF 的资源
- `--refresh-index`: 重建格式索引缓存（约 3700 次请求，缓存 7 天）；
  缓存位于 `~/Library/Caches/textbook-downloader/`（macOS）或 `~/.cache/textbook-downloader/`，
  刻意放在下载目录之外，因此清理下载文件不会导致重新扫描

各资源提供哪些格式，请参见 [配套脚本：textbook_info.py](#-配套脚本textbook_infopy) 导出的 CSV 清单。

#### 可选参数
- `--dry-run`: 仅解析并打印下载链接，不实际下载
- `--overwrite`: 重新下载本地已存在的文件

#### 目录内容统计
对全部 3743 个资源的完整扫描结果：

| | 数量 |
|---|---|
| 提供 PDF | 3384 |
| 提供 PowerPoint | 252（全部同时提供 PDF） |
| 自身无可下载文件 | 359（302 个课程合集 + 57 个未解析） |

#### 特殊教育资源，以及仍未解析的部分

特殊教育类教材（体育与健康系列等）以**课程合集**（`thematic_course`）形式发布，
并非独立文档。用子文档自身的 ID 请求常规详情接口会返回 HTTP 403——这并不是受限，
而是因为该 ID 属于课程合集。真正的记录位于父课程的资源列表中：

```
https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/special_edu/thematic_course/{course_id}/resources/list.json
```

脚本会自动回退到该列表，从而使 **372 个此前无法获取的资源**变为可下载。

仍然自身没有文件的部分：302 个 `thematic_course` 课程合集节点（它们是合集而非书籍，
其子书籍在目录中单独列出且可正常下载），以及 57 个父课程未列出文件的特殊教育资源。

### 📋 系统要求

- Python 3.6+
- `requests` 库
- 网络连接
- 访问国家中小学智慧教育平台的权限

### 🛠️ 安装

1. 克隆或下载脚本
2. 安装所需依赖：
```bash
pip install requests
```

### 📖 使用示例

```bash
# 按序列号下载
python3 pdf_book_download_from_zxxeducn.py --sequence 2548

# 下载书籍范围
python3 pdf_book_download_from_zxxeducn.py --range "1-5"

# 按书籍ID下载
python3 pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"

# 传统单本书下载
python3 pdf_book_download_from_zxxeducn.py --single 1

# 传统限制下载
python3 pdf_book_download_from_zxxeducn.py --limit 10

# 恢复中断的下载
python3 pdf_book_download_from_zxxeducn.py --table 1 --item 5
```

### 🔧 技术细节

- **CDN端点**: 自动按顺序尝试r1-ndr-oversea、r2-ndr-oversea和r3-ndr-oversea
- **PDF 定位**: 依据 `ti_format == "pdf"` 选取（通常为 `source`，但部分资源的 `source` 是 .pptx，
  真正的 PDF 位于 `pdf` 标记下）
- **文件验证**: 通过 `%PDF` 文件头和 Content-Length 校验，而非依赖文件大小
- **安全写入**: 先流式写入 `.pdf.part`，完整下载后才重命名
- **网络超时**: 所有网络请求30秒超时
- **输出目录**: 所有下载保存到`~/Downloads/textbook_download/`

### 📁 输出

下载的PDF文件保存到：
```
~/Downloads/textbook_download/
├── 统编版（根据2022年版课程标准修订）义务教育教科书·道德与法治一年级上册.pdf
├── 统编版（根据2022年版课程标准修订）义务教育教科书·道德与法治一年级下册.pdf
└── ...
```

### 🐛 故障排除

- **网络错误**: 检查网络连接和防火墙设置
- **权限错误**: 确保对Downloads文件夹有写入权限
- **超时错误**: 脚本将自动尝试不同的CDN端点

### 📝 许可证

开源 - 可自由使用和修改。

---

## 🔄 Version History

- **v3.5.0**: Web UI distinguishes ✅ downloadable / 📦 course bundle / ⚠️ missing, with live counts, child-book links on bundles, and a status filter
- **v3.4.0**: Resolve special-education titles through their parent `thematic_course` listing, recovering 372 previously unreachable books (PDF 3012 → 3384, PowerPoint 179 → 252)
- **v3.3.0**: Added a local web interface (`webapp/`) with cascading 学段/学科/版本/年级/册次 filters, streamed single downloads and background bulk jobs
- **v3.2.0**: Added the PowerPoint (`.pptx`) download route (`--all-pptx`, `--format`, `--list-pptx`), and rebuilt `textbook_info.py` to record per-book PDF/PPTX availability
- **v3.1.0**: Fixed PDF resolution against the platform's current metadata (select by `ti_format`, magic-byte validation, cached catalogue, streamed writes)
- **v3.0.0**: Modified the download path and added more download mods. Enhanced documentation, type hints, and modular architecture
- **v2.0.0**: Modified the download path and added new download control
- **v1.0.0**: Original script with basic functionality

## 🤝 Contributing

Feel free to submit issues, feature requests, or pull requests to improve this script.

## 📞 Support

If you encounter any issues or have questions, please check the troubleshooting section above or create an issue in the repository.
