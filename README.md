# Enhanced Textbook Downloader from National Smart Education Platform

[English](#english) | [中文](#中文)

---

## English

### 📚 Overview

This enhanced script downloads complete PDF textbooks from the Chinese National Smart Education Platform (国家中小学智慧教育平台) with multiple download modes, comprehensive error handling, and flexible download controls.

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
python pdf_book_download_from_zxxeducn.py --sequence 2548
```

#### 2. **By Book Range** (`--range`)
Downloads multiple books within a specified range.

```bash
python pdf_book_download_from_zxxeducn.py --range "200-250"
python pdf_book_download_from_zxxeducn.py --range "200"  # Single book
```

#### 3. **By Book ID** (`--book-id`)
Downloads a specific book by its unique identifier (UUID).

```bash
python pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"
```

#### 4. **Legacy Modes**
- `--single N`: Download only the Nth textbook from the catalog
- `--limit N`: Download only N textbooks (starting from the beginning)
- `--table N`: Start from catalog N (0-based indexing)
- `--item N`: Start from item N within the catalog (0-based indexing)

#### Options
- `--dry-run`: Resolve and print the PDF URL without downloading
- `--overwrite`: Re-download books that already exist locally

#### Restricted Titles
A minority of titles (e.g. parts of the 体育与健康 series) are marked download-restricted by the
platform: their details metadata returns HTTP 403 and no PDF is published. The script reports
these as restricted and skips them.

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
python pdf_book_download_from_zxxeducn.py --sequence 2548

# Download a range of books
python pdf_book_download_from_zxxeducn.py --range "1-5"

# Download by book ID
python pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"

# Legacy single book download
python pdf_book_download_from_zxxeducn.py --single 1

# Legacy limited download
python pdf_book_download_from_zxxeducn.py --limit 10

# Resume interrupted download
python pdf_book_download_from_zxxeducn.py --table 1 --item 5
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

### 📊 Companion Script: textbook_info.py

The `textbook_info.py` script is a companion tool that collects metadata for all available textbooks and exports it to a CSV file. This is useful for:

- **Finding specific textbooks**: Search through the CSV to locate books by title, publisher, or other criteria
- **Planning downloads**: See the complete catalog before deciding what to download
- **Resume functionality**: Use the sequence numbers to resume interrupted downloads

#### Usage:
```bash
python textbook_info.py
```

#### Output:
- Creates a CSV file in your Downloads folder
- Contains: Book ID, Title, Publisher, Catalog position, and Global sequence number
- Useful for determining the correct parameters for the main download script

#### Example CSV structure:
```csv
sequence_number,catalog_index,catalog_position,book_id,title,publisher
1,0,0,bdc00134-465d-454b-a541-dcd0cec4d86e,义务教育教科书·道德与法治一年级上册,统编版
2,0,1,bdc00135-465d-454b-a541-dcd0cec4d86e,义务教育教科书·道德与法治一年级下册,统编版
...
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
python pdf_book_download_from_zxxeducn.py --sequence 2548
```

#### 2. **按书籍范围下载** (`--range`)
下载指定范围内的多本书籍。

```bash
python pdf_book_download_from_zxxeducn.py --range "200-250"
python pdf_book_download_from_zxxeducn.py --range "200"  # 单本书
```

#### 3. **按书籍ID下载** (`--book-id`)
通过唯一标识符（UUID）下载特定书籍。

```bash
python pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"
```

#### 4. **传统模式**
- `--single N`: 仅下载目录中的第N本教材
- `--limit N`: 限制本次运行下载的书籍数量
- `--table N`: 从目录N开始（基于0的索引）
- `--item N`: 从目录中的项目N开始（基于0的索引）

#### 可选参数
- `--dry-run`: 仅解析并打印 PDF 链接，不实际下载
- `--overwrite`: 重新下载本地已存在的书籍

#### 受限资源
少部分资源（如部分体育与健康系列）被平台标记为不可下载：其详情接口返回 HTTP 403，
且未发布 PDF。脚本会将其标记为受限并跳过。

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
python pdf_book_download_from_zxxeducn.py --sequence 2548

# 下载书籍范围
python pdf_book_download_from_zxxeducn.py --range "1-5"

# 按书籍ID下载
python pdf_book_download_from_zxxeducn.py --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"

# 传统单本书下载
python pdf_book_download_from_zxxeducn.py --single 1

# 传统限制下载
python pdf_book_download_from_zxxeducn.py --limit 10

# 恢复中断的下载
python pdf_book_download_from_zxxeducn.py --table 1 --item 5
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

### 📊 配套脚本：textbook_info.py

`textbook_info.py` 脚本是一个配套工具，用于收集所有可用教材的元数据并导出到CSV文件。这对于以下情况很有用：

- **查找特定教材**: 通过CSV搜索按标题、出版社或其他条件定位书籍
- **规划下载**: 在决定下载内容之前查看完整目录
- **恢复功能**: 使用序列号恢复中断的下载

#### 使用方法：
```bash
python textbook_info.py
```

#### 输出：
- 在Downloads文件夹中创建CSV文件
- 包含：书籍ID、标题、出版社、目录位置和全局序列号
- 有助于确定主下载脚本的正确参数

#### CSV结构示例：
```csv
sequence_number,catalog_index,catalog_position,book_id,title,publisher
1,0,0,bdc00134-465d-454b-a541-dcd0cec4d86e,义务教育教科书·道德与法治一年级上册,统编版
2,0,1,bdc00135-465d-454b-a541-dcd0cec4d86e,义务教育教科书·道德与法治一年级下册,统编版
...
```

### 🐛 故障排除

- **网络错误**: 检查网络连接和防火墙设置
- **权限错误**: 确保对Downloads文件夹有写入权限
- **超时错误**: 脚本将自动尝试不同的CDN端点

### 📝 许可证

开源 - 可自由使用和修改。

---

## 🔄 Version History

- **v3.0.0**: Modified the download path and added more download mods. Enhanced documentation, type hints, and modular architecture
- **v2.0.0**: Modified the download path and added new download control
- **v1.0.0**: Original script with basic functionality

## 🤝 Contributing

Feel free to submit issues, feature requests, or pull requests to improve this script.

## 📞 Support

If you encounter any issues or have questions, please check the troubleshooting section above or create an issue in the repository.
