import requests
import json
import os
from pathlib import Path
from urllib.parse import quote

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

def pdf_download(table: int=0, item: int=0):
    """
    该程序从“国家中小学智慧教育平台”网站下载电子教材。

    table:  Optional argument for the urls you want the download to continue, if the download is stopped in the middle. Value to pass should be the (current url value - 1), 
            E.g., if the url that is using for download currently is 3, then you can pass value of 2 to this argument. There are 3 urls in total.
    item:   Optional argument for the number of textbooks that has already download.
            E.g., if you know 200 books have been downloaded, and you want to continue downloading without repeating downloading, then pass 200 to this argument.

    If no value is passed to these arguments, the download will start from the beginning, which may result repetitive downloading.
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
    for ref in url[table:]:
        print(f"正在下载目录{t+1}/{len(url)}中的电子教材")
        response = requests.get(ref, headers=headers)
        info = json.loads(response.text)

        c = 0 + item
        for i in info[item:]:
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
                    else:
                        print(f"Failed to download PDF for {book_name}: Status {pdf_response.status_code}")
                else:
                    print(f"Could not get PDF URL for {book_name}")
                
            except Exception as e:
                print(f"Error processing {book_name}: {str(e)}")
            
            c += 1
        t += 1
        item = 0

# start download
pdf_download(table=0, item=0)
