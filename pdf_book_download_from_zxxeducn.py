import requests
import json

def pdf_download(table: int=0, item: int=0):
    """
    该程序从“国家中小学智慧教育平台”网站下载电子教材。

    table:  Optional argument for the urls you want the download to continue, if the download is stopped in the middle. Value to pass should be the (current url value - 1), 
            E.g., if the url that is using for download currently is 3, then you can pass value of 2 to this argument. There are 3 urls in total.
    item:   Optional argument for the number of textbooks that has already download.
            E.g., if you know 200 books have been downloaded, and you want to continue downloading without repeating downloading, then pass 200 to this argument.

    If no value is passed to these arguments, the download will start from the beginning, which may result repetitive downloading.
    """
    url = ['https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/part_100.json',
           'https://s-file-2.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/part_101.json',
           'https://s-file-2.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/part_102.json',
            ]
    t = 0 + table
    for ref in url[table:]:
        print(f"正在下载目录{t+1}/3中的电子教材")
        headers = {
            'Referer': ref,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36 Edge/16.16299',
            }
        response = requests.get(ref, headers=headers)

        info = json.loads(response.text)

        c = 0 + item
        for i in info[:item]:
            for tag in i['tag_list']:
                if '版' in tag['tag_name']:
                    publisher = tag['tag_name']

            book_id = i['id']
            book_name = f"{publisher}{i['title']}.pdf"

            book_pdf_url = f"https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/{book_id}.pkg/pdf.pdf"
            dl_response = requests.get(book_pdf_url)

            file_path = f'/Users/zxu/Downloads/{book_name}'
            with open (file_path, 'wb') as f:
                f.write(dl_response.content)

            print(f"当前目录下共有{len(info)}本电子教材, 已下载 {c+1}/{len(info)}")
            c += 1

        t += 1
        item = 0


pdf_download(table=1, item=259)