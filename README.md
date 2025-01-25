# pdf_book_download_from_zxxeducn.py
从国家中小学智慧教育平台下载所有小学，初中，高中电子课本的程序。电子课本为能在该网站上所看到的最新版本 （部分课本是2024年版的），包括所有出版社版本，还包括五四学制的所有版本，甚至还包括特殊教育教材。

国家中小学智慧教育平台网站处于活跃更新状态，先前版本的程序虽然还可运行，但所获取的源pdf的url是旧版本的，所以某些课本不是像在线浏览到的最新版本。
目前的网站更新加入了注册机制，并且几乎所有的pdf文件源都更改成了唯一的名字，包含中文的课本名称（包括出版社等），而且每个课本还有一个独有的序列号，这使得pdf源的url没有统一的模式可循。更新后的程序旨在解决这些问题，并同时进行了优化和加入了一些新的功能，使得运行起来更灵活和容易。

更新后的程序：
- 终端命令行运行
- 绕过用户认证
- 另辟蹊径找到每个电子课本pdf源的名字及URL
- 跨操作系统运行
- 下载更灵活，可自定义开始下载的位置和数量
- 电子课本统一下载到你所在用户下的Downloads/textbook_download文件夹内

# textbook_info.py
该程序收集所有后台的电子课本的ID和名字（包含出版社）以及在后台的存储序列，并导出在一个csv文件内。

建议先运行该程序，以方便更灵活的下载想要的电子课本。比如你只想下载某一本，就可以直接对照该文件，得到该书的存储序列，比如是第1482本，那么你就能知道这本书是第二个目录下的第482本书。这样就可以设置`pdf_download`函数的参数为 `table=1, single_book=482`
  
安装python3.9及以上版本。 

# PDF Textbook Downloader for Smart Education of China
This script downloads digital textbooks from Smart Education of China (国家中小学智慧教育平台).

## Features
- Downloads textbooks in PDF format
- Command-line run
- Avoide site authetication
- Cross plateform operation
- Organizes downloads by publisher
- Supports resume functionality for interrupted downloads
- Supports download from any point
- Saves files to `~/Downloads/textbook_download/` directory
  
## Usage
usage: 

`pdf_book_download_from_zxxeducn.py [-h] [--single SINGLE] [--limit LIMIT] [--table TABLE] [--item ITEM]`

Download textbooks from the National Smart Education Platform (国家中小学智慧教育平台)

This script allows you to download textbooks in PDF format from the National Smart 
Education Platform. You can download all textbooks, a single specific book, or a 
limited number of books. The script also supports resuming downloads from a specific 
point if the previous download was interrupted.

The downloaded PDFs will be saved to:
`~/Downloads/textbook_download/`

options:  

  `-h, --help       show this help message and exit`
  
  `--single SINGLE  Download only one specific book number (e.g., --single 50 will download only the 50th book in the catalog)`
  
  `--limit LIMIT    Limit the number of books to download (e.g., --limit 10 will download only 10 books and then stop)`
  
  `--table TABLE    Start from specific table index (0-based). Use this with --item to resume an interrupted download. There are 4
                   tables in total, therefore index range is 0-3. Default: 0`
                   
  `--item ITEM      Start from specific item index (0-based). Use this with --table to resume an interrupted download. There are
                   1000 items in each of the first 3 tables, therefore index range is 0-999. Default: 0`

Detailed Usage:
-------------
1. Download all textbooks:
   Simply run the script without any arguments
   
   `python pdf_book_download_from_zxxeducn.py`

3. Download a specific book from table 1 (Default --table=0):
   Use the `--single` argument with the book number
   
   `python pdf_book_download_from_zxxeducn.py --single 50`

5. Download a limited number of books from table 2:
   Use the `--limit` argument to specify how many books to download
   
   `python pdf_book_download_from_zxxeducn.py --table 1 --limit 10`

7. Resume interrupted download from table 3, item 6 (5 items have been downloaded):
   Use `--table` and `--item` to specify where to resume
   
   `python pdf_book_download_from_zxxeducn.py --table 2 --item 5`

9. Combine arguments:
   You can combine different arguments for more specific control (e.g. download 10 books from table 3, start from item 6)
   
   `python pdf_book_download_from_zxxeducn.py --table 2 --item 5 --limit 10`

Note: 
- There are 4 tables in total, and there are 1000 items (books) in each of the first 3 tables
- Table and item indices start from 0, so the first table is table 0, the first item is item 0
- To pass --item argument, you need to know from which item you want to start downloading, e.g. if you want to start from the 20th item, you need to pass --item 19
- Downloads are saved to your Downloads folder in a 'textbook_download' directory
- The script requires an active internet connection
- Replace `python` with `python3` if you're using Python 3 explicitly

For more information or bug reports, please visit:
https://github.com/maplexuci/pdf_book_download_from_zxxeducn

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
