import requests
import json
import csv
from pathlib import Path
import os

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

def save_textbook_info():
    """
    Extract textbook IDs and names and save them to a CSV file
    """
    # Get the URLs
    urls = get_parts()
    
    headers = {
        'Referer': 'https://basic.smartedu.cn/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
        'Origin': 'https://basic.smartedu.cn',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    # Prepare CSV file
    home = str(Path.home())
    dir_path = os.path.join(home, "Downloads")
    csv_path = os.path.join(dir_path, "textbook_info.csv")

    book_number = 1  # Initialize book counter

    # Add UTF-8 BOM to handle Chinese characters
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Number', 'Book ID', 'Book Name'])  # Updated header

        for index, ref in enumerate(urls, 1):
            print(f"Processing directory {index}/{len(urls)}")
            response = requests.get(ref, headers=headers)
            response.encoding = 'utf-8'  # Explicitly set response encoding
            info = json.loads(response.text)

            for book in info:
                try:
                    book_id = book['id']
                    publisher = next((tag['tag_name'] for tag in book['tag_list'] if '版' in tag['tag_name']), '')
                    book_name = f"{publisher}{book['title']}"
                    
                    writer.writerow([book_number, book_id, book_name])
                    book_number += 1
                    
                except Exception as e:
                    print(f"Error processing book: {str(e)}")

    print(f"CSV file has been saved to: {csv_path}")

if __name__ == "__main__":
    save_textbook_info()