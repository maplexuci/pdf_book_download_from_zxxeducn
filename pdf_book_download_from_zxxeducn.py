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
        description='Download textbooks from the National Smart Education Platform (国家中小学智慧教育平台)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
    # Download all books
    python pdf_book_download_from_zxxeducn.py
    
    # Continue downloading from a specific point (e.g., from table 2, item 5),
    # if you know 5 books from table 2 have been downloaded
    python pdf_book_download_from_zxxeducn.py --table 1 --item 5
    
    # Download a single book (e.g., book number 50)
    python pdf_book_download_from_zxxeducn.py --single 50
    
    # Download 10 books starting from the beginning
    python pdf_book_download_from_zxxeducn.py --limit 10
    
    # Download 5 books starting from item 20 in table 3
    python pdf_book_download_from_zxxeducn.py --table 2 --item 20 --limit 5
    
    # Replace python with python3 if you are using Python 3
        '''
    )
    
    parser.add_argument('--single', type=int, 
                       help='Download only one specific book number')
    
    parser.add_argument('--limit', type=int, 
                       help='Limit the number of books to download')
    
    parser.add_argument('--table', type=int, default=0,
                       help='Start from specific table index (0-based)')
    
    parser.add_argument('--item', type=int, default=0,
                       help='Start from specific item index (0-based)')
    
    args = parser.parse_args()
    
    # Call pdf_download with the parsed arguments
    pdf_download(
        table=args.table,
        item=args.item,
        single_book=args.single,
        download_limit=args.limit
    )
