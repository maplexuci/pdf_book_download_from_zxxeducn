# pdf_book_download_from_zxxeducn.py
从国家中小学智慧教育平台下载所有小学，初中，高中电子课本的程序。电子课本为能在该网站上所看到的最新版本 （部分课本是2024年版的），包括所有出版社版本，还包括五四学制的所有版本，甚至还包括特殊教育教材。

网站更新后，大部分的源pdf文件都更改成唯一的名字，其中有中文的课本名称（包括版本等），而且每个课本还有一个独有的序列号，这使得源pdf的url没有统一的模式可循。虽然之前版本的程序还可运行，但所获取的源pdf的url是旧版本的，所以某些课本不是像在线浏览到的最新版本。更新后的网站还加入了注册机制。

更新后的程序：
- 绕过用户认证
- 另辟蹊径找到每个电子课本pdf源的名字及URL
- 跨操作系统运行
- 电子课本统一下载到你所在用户下的Downloads/textbook_download文件夹内

# textbook_info.py
该程序收集所有后台的电子课本的ID和名字（包含出版社）以及在后台的存储序列，并导出在一个csv文件内。

建议先运行该程序，以方便更灵活的下载想要的电子课本。比如你只想下载某一本，就可以直接对照该文件，得到该书的存储序列，比如是第1482本，那么你就能知道这本书是第二个目录下的第482本书。这样就可以设置`pdf_download`函数的参数为 `table=1, single_book=482`
  
安装python3.9及以上版本。 

# PDF Textbook Downloader for Smart Education of China
This script downloads digital textbooks from Smart Education of China (国家中小学智慧教育平台).

## Features
- Downloads textbooks in PDF format
- Avoide site authetication
- Cross plateform operation
- Organizes downloads by publisher
- Supports resume functionality for interrupted downloads
- Saves files to `~/Downloads/textbook_download/` directory
  
## Usage
### Basic Usage
Simply run the script to download all textbooks:

`python pdf_book_download_from_zxxeducn.py`

### Resume Interrupted Downloads
The script supports two parameters to resume interrupted downloads:
1. `table` (default=0): Specifies which URL catalog to start from
  - Values range from 0 to 3 (there are 4 catalogs in total)
  - First three catalogs has 1000 textbooks each
  - Use (current catalog number, e.g., 1) to resume from a the 2nd catalog
2. `item` (default=0): Specifies how many books to skip in the current catalog
  - Useful when you know how many books were already downloaded
    
Example to resume download:

`# To resume from the second catalog (index 1), after downloading 200 books`

`pdf_download(table=1, item=200)`

## Output Location
- All PDFs are saved to: `~/Downloads/textbook_download/`
- Files are named using the format: `[Publisher][Book Title].pdf`
  
## Progress Tracking
The script provides progress information during download:
- Shows current catalog progress (e.g., "正在下载目录1/4中的电子教材")
- Shows download progress within each catalog (e.g., "当前目录下共有X本电子教材, 已下载 Y/X")
- Displays error messages if downloads fail
  
## Requirements
- Python 3.x
- `requests` library
- Internet connection
- Sufficient disk space for downloaded PDFs
