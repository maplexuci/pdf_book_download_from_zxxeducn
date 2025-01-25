import requests
import json
import os
from pathlib import Path
from urllib.parse import quote
import argparse


# Get user's home directory
home = str(Path.home())

# Construct the path using os.path.join for cross-platform compatibility
dir_path = os.path.join(home, "Downloads")

# Verify the directory exists
if not os.path.exists(dir_path):
    raise FileNotFoundError(f"Directory not found: {dir_path}")

def get_parts(return_type='json'):
    '''get urls return list'''
    url = 'https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/version/data_version.json'
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
        "Referer": "https://basic.smartedu.cn/",
        "Origin": "https://basic.smartedu.cn"
    }
    req = requests.get(url=url, headers=headers)
    
    if return_type == 'json':
        data = json.loads(req.text)
    else:
        data = req.text
    return data['urls'].split(',')

def get_pdf_url(book_id):
    """Get the PDF URL using the book ID"""
    try:
        # Get the JSON metadata
        json_url = f"https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{book_id}.json"
        response = requests.get(json_url)
        
        if response.ok:
            data = response.json()
            # Find the item with ti_file_flag = "source"
            for item in data['ti_items']:
                if item.get('ti_file_flag') == 'source':
                    # Extract the PDF filename from ti_storage
                    pdf_filename = item['ti_storage'].split('/')[-1]
                    
                    # Construct the viewer URL
                    pdf_path = f"https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets/{book_id}.pkg/{pdf_filename}"
                    
                    return pdf_path
            
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        import traceback
        print("Full traceback:")
        print(traceback.format_exc())

def pdf_download(table: int=0, item: int=0, single_book: int=None, download_limit: int=None):
    """
    该程序从"国家中小学智慧教育平台"网站下载电子教材。

    Args:
        table: Optional argument for the urls you want the download to continue, if the download is stopped in the middle.
               Value to pass should be the (current url value - 1)
        item: Optional argument for the number of textbooks that has already download.
        single_book: Optional argument to download only one specific book number.
        download_limit: Optional argument to limit how many books to download in this run.

    If no value is passed to these arguments, the download will start from the beginning and download all books.
    """
    url = get_parts()
    
    headers = {
        'Referer': 'https://basic.smartedu.cn/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
        'Origin': 'https://basic.smartedu.cn',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    t = 0 + table
    total_processed = 0
    book_counter = item

    for ref in url[table:]:
        print(f"正在下载目录{t+1}/{len(url)}中的电子教材")
        response = requests.get(ref, headers=headers)
        info = json.loads(response.text)

        c = 0 + item
        for i in info[item:]:
            book_counter += 1
            
            # Skip if not the requested single book
            if single_book is not None and book_counter != single_book:
                c += 1
                continue

            # Check if we've reached the download limit
            if download_limit is not None and total_processed >= download_limit:
                print(f"已达到下载限制 ({download_limit} 本教材)")
                return

            try:
                book_id = i['id']
                publisher = next((tag['tag_name'] for tag in i['tag_list'] if '版' in tag['tag_name']), '')
                book_name = f"{publisher}{i['title']}"
                
                # Get the PDF URL using the new function
                pdf_url = get_pdf_url(book_id)
                
                if pdf_url:
                    # Download the PDF
                    pdf_response = requests.get(pdf_url, headers=headers)
                    
                    if pdf_response.status_code == 200:
                        work_path = f"{dir_path}/textbook_download/"
                        if not os.path.exists(work_path):
                            os.makedirs(work_path)
                        
                        file_path = work_path + f"{book_name}.pdf"
                        with open(file_path, 'wb') as f:
                            f.write(pdf_response.content)
                        print(f"当前目录下共有{len(info)}本电子教材, 已下载 {c+1}/{len(info)}")
                        total_processed += 1
                    else:
                        print(f"Failed to download PDF for {book_name}: Status {pdf_response.status_code}")
                else:
                    print(f"Could not get PDF URL for {book_name}")
                
            except Exception as e:
                print(f"Error processing {book_name}: {str(e)}")
            
            # If we're downloading a single book and found it, we can return
            if single_book is not None and book_counter == single_book:
                return

            c += 1
        t += 1
        item = 0

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='''
Download textbooks from the National Smart Education Platform (国家中小学智慧教育平台)

This script allows you to download textbooks in PDF format from the National Smart 
Education Platform. You can download all textbooks, a single specific book, or a 
limited number of books. The script also supports resuming downloads from a specific 
point if the previous download was interrupted.

The downloaded PDFs will be saved to:
~/Downloads/textbook_download/
''',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Detailed Usage:
-------------
1. Download all textbooks:
   Simply run the script without any arguments
   $ python pdf_book_download_from_zxxeducn.py

2. Download a specific book from table 1 (Default --table=0):
   Use the --single argument with the book number
   $ python pdf_book_download_from_zxxeducn.py --single 50

3. Download a limited number of books from table 2:
   Use the --limit argument to specify how many books to download
   $ python pdf_book_download_from_zxxeducn.py --table 1 --limit 10

4. Resume interrupted download from table 3, item 6 (5 items have been downloaded):
   Use --table and --item to specify where to resume
   $ python pdf_book_download_from_zxxeducn.py --table 2 --item 5

5. Combine arguments:
   You can combine different arguments for more specific control (e.g. download 10 books from table 3, start from item 6)
   $ python pdf_book_download_from_zxxeducn.py --table 2 --item 5 --limit 10

Note: 
- There are 4 tables in total, and there are 1000 items (books) in each of the first 3 tables
- Table and item indices start from 0, so the first table is table 0, the first item is item 0
- To pass --item argument, you need to know from which item you want to start downloading, e.g. if you want to start from the 20th item, you need to pass --item 19
- Downloads are saved to your Downloads folder in a 'textbook_download' directory
- The script requires an active internet connection
- Replace python with python3 if you're using Python 3 explicitly

For more information or bug reports, please visit:
https://github.com/maplexuci/pdf_book_download_from_zxxeducn
        '''
    )
    
    parser.add_argument(
        '--single', 
        type=int,
        help='''Download only one specific book number (e.g., --single 50 will download
             only the 50th book in the catalog)'''
    )
    
    parser.add_argument(
        '--limit', 
        type=int,
        help='''Limit the number of books to download (e.g., --limit 10 will download
             only 10 books and then stop)'''
    )
    
    parser.add_argument(
        '--table', 
        type=int,
        default=0,
        help='''Start from specific table index (0-based). Use this with --item to
             resume an interrupted download. There are 4 tables in total,
             therefore index range is 0-3. Default: 0'''
    )
    
    parser.add_argument(
        '--item', 
        type=int,
        default=0,
        help='''Start from specific item index (0-based). Use this with --table to
             resume an interrupted download. There are 1000 items in each of the first 3 tables,
             therefore index range is 0-999. Default: 0'''
    )
    
    args = parser.parse_args()
    
    # Call pdf_download with the parsed arguments
    pdf_download(
        table=args.table,
        item=args.item,
        single_book=args.single,
        download_limit=args.limit
    )
