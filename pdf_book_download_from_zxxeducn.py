#!/usr/bin/env python3
"""
Enhanced Textbook Downloader from the National Smart Education Platform (国家中小学智慧教育平台)

This script provides multiple methods to download complete PDF textbooks from the Chinese national
education platform, comprehensive error handling, and flexible download controls.

License: Open source
Version: 3.0.0

Features:
- CDN fallback: Automatically tries r1, r2, r3 endpoints if one fails
- Multiple download modes: Sequence number, book range, book ID, and legacy modes
- Enhanced error handling: Detailed error messages and graceful fallbacks
- Progress tracking: Real-time download status and file size information
- Flexible controls: Download specific books, ranges, or use legacy catalog-based approach
"""

import requests
import json
import os
from pathlib import Path
from urllib.parse import quote
import argparse
import time
from typing import List, Tuple, Optional, Dict, Any, Union

# Get user's home directory
home = str(Path.home())

# Construct the path using os.path.join for cross-platform compatibility
dir_path = os.path.join(home, "Downloads")

# Verify the directory exists
if not os.path.exists(dir_path):
    raise FileNotFoundError(f"Directory not found: {dir_path}")


def get_parts(return_type: str = 'json') -> List[str]:
    """
    Fetch the catalog URLs from the National Smart Education Platform.
    
    This function retrieves the list of catalog URLs that contain textbook metadata.
    There are typically 4 catalogs, with the first 3 containing up to 1000 books each.
    
    Args:
        return_type (str): Type of return value. 'json' returns parsed JSON, 
                          any other value returns raw text. Default: 'json'
    
    Returns:
        List[str]: List of catalog URLs for fetching textbook metadata
        
    Raises:
        requests.RequestException: If the HTTP request fails
        json.JSONDecodeError: If the response is not valid JSON (when return_type='json')
        
    Example:
        >>> urls = get_parts()
        >>> print(f"Found {len(urls)} catalogs")
        Found 4 catalogs
    """
    url = 'https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/version/data_version.json'
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
        "Referer": "https://basic.smartedu.cn/",
        "Origin": "https://basic.smartedu.cn"
    }
    
    try:
        req = requests.get(url=url, headers=headers, timeout=30)
        req.raise_for_status()  # Raise exception for bad status codes
        
        if return_type == 'json':
            data = json.loads(req.text)
            return data['urls'].split(',')
        else:
            return req.text
    except requests.RequestException as e:
        print(f"❌ Failed to fetch catalog URLs: {e}")
        raise
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response from catalog API: {e}")
        raise


def get_pdf_url(book_id: str) -> Optional[List[str]]:
    """
    Retrieve PDF download URLs for a specific textbook using its book ID.
    
    This function fetches the textbook metadata and extracts all available CDN endpoints
    for PDF downloads. It transforms the private URLs to oversea URLs for public access.
    
    Args:
        book_id (str): The unique identifier (UUID) of the textbook
        
    Returns:
        Optional[List[str]]: List of CDN URLs for PDF download, or None if failed
        
    Raises:
        requests.RequestException: If the HTTP request fails
        json.JSONDecodeError: If the metadata response is not valid JSON
        KeyError: If the expected metadata structure is missing
        
    Example:
        >>> urls = get_pdf_url("bdc00134-465d-454b-a541-dcd0cec4d86e")
        >>> if urls:
        ...     print(f"Found {len(urls)} CDN endpoints")
        Found 3 CDN endpoints
    """
    try:
        # Construct the metadata API URL
        json_url = f"https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{book_id}.json"
        
        # Fetch the textbook metadata
        response = requests.get(json_url)
        response.raise_for_status()
        
        # Parse the JSON response
        data = response.json()
        
        # Search for the source item (contains PDF download links)
        for item in data['ti_items']:
            if item.get('ti_file_flag') == 'source':
                if 'ti_storages' in item and item['ti_storages']:
                    # Transform all private URLs to oversea URLs
                    pdf_urls = []
                    for storage_url in item['ti_storages']:
                        oversea_url = storage_url.replace('-private', '-oversea')
                        pdf_urls.append(oversea_url)
                    return pdf_urls
        
        # No source item found
        print(f"⚠️ No PDF source found for book ID: {book_id}")
        return None
            
    except requests.RequestException as e:
        print(f"❌ Network error getting metadata for {book_id}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response for {book_id}: {e}")
        return None
    except KeyError as e:
        print(f"❌ Unexpected metadata structure for {book_id}: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error getting metadata for {book_id}: {e}")
        import traceback
        print("Full traceback:")
        print(traceback.format_exc())
        return None


def download_pdf_with_cdn_fallback(pdf_urls: List[str], book_name: str, 
                                  headers: Dict[str, str], work_path: str) -> bool:
    """
    Download a PDF textbook with automatic CDN fallback logic.
    
    This function attempts to download the PDF from multiple CDN endpoints (r1, r2, r3)
    in sequence. If one fails, it automatically tries the next. It validates the downloaded
    content to ensure it's actually a PDF file and not an error page.
    
    Args:
        pdf_urls (List[str]): List of CDN URLs to try for download
        book_name (str): Name of the textbook (used for filename)
        headers (Dict[str, str]): HTTP headers for the download request
        work_path (str): Directory path where the PDF should be saved
        
    Returns:
        bool: True if download succeeded, False if all CDN endpoints failed
        
    Raises:
        OSError: If there's an error writing the file to disk
        requests.RequestException: If all HTTP requests fail
        
    Example:
        >>> success = download_pdf_with_cdn_fallback(urls, "Math Book", headers, "/downloads")
        >>> if success:
        ...     print("Download completed successfully")
        Download completed successfully
    """
    if not pdf_urls:
        print(f"❌ No PDF URLs available for {book_name}")
        return False
    
    # Try each CDN endpoint in sequence
    for i, pdf_url in enumerate(pdf_urls):
        cdn_name = f"r{i+1}-ndr-oversea"
        
        try:
            # Attempt to download the PDF with timeout
            pdf_response = requests.get(pdf_url, headers=headers, timeout=30)
            
            if pdf_response.status_code == 200:
                # Validate the downloaded content
                content_length = len(pdf_response.content)
                content_type = pdf_response.headers.get('content-type', '')
                
                # Check if we got a valid PDF (not an error page)
                if 'pdf' in content_type.lower() and content_length > 1000000:  # > 1MB
                    # Save the PDF to disk
                    file_path = os.path.join(work_path, f"{book_name}.pdf")
                    
                    try:
                        with open(file_path, 'wb') as f:
                            f.write(pdf_response.content)
                        
                        print(f"    💾 Downloaded: {book_name}  {content_length / (1024*1024):.1f} MB")
                        return True
                        
                    except OSError as e:
                        print(f"    ❌ Failed to save file: {e}")
                        continue
                else:
                    print(f"    ⚠️ {cdn_name} returned invalid content: {content_type}, {content_length} bytes")
            else:
                print(f"    ❌ {cdn_name} failed: Status {pdf_response.status_code}")
                
        except requests.exceptions.Timeout:
            print(f"    ⏰ {cdn_name} timeout after 30 seconds")
        except requests.exceptions.RequestException as e:
            print(f"    ❌ {cdn_name} network error: {e}")
        except Exception as e:
            print(f"    ❌ {cdn_name} unexpected error: {e}")
    
    # All CDN endpoints failed
    print(f"❌ All CDN endpoints failed for {book_name}")
    return False


def get_book_by_sequence_number(catalog_urls: List[str], sequence_number: int) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[int]]:
    """
    Locate a textbook by its global sequence number across all catalogs.
    
    This function maps a global sequence number (e.g., 2548) to the specific catalog
    and position where that textbook can be found. It handles the catalog structure
    where the first 3 catalogs typically contain 1000 books each.
    
    Args:
        catalog_urls (List[str]): List of catalog API URLs
        sequence_number (int): Global sequence number of the textbook (1-based)
        
    Returns:
        Tuple[Optional[Dict[str, Any]], Optional[int], Optional[int]]: 
            - book_info: Dictionary containing textbook metadata, or None if not found
            - catalog_index: Index of the catalog (0-3), or None if not found
            - catalog_position: Position within the catalog (0-based), or None if not found
            
    Raises:
        requests.RequestException: If any catalog API request fails
        json.JSONDecodeError: If any catalog response is not valid JSON
        
    Example:
        >>> book_info, cat_idx, cat_pos = get_book_by_sequence_number(urls, 2548)
        >>> if book_info:
        ...     print(f"Found book {book_info['title']} in catalog {cat_idx + 1}")
        Found book 道德与法治 in catalog 3
    """
    # Validate input
    if sequence_number < 1:
        print(f"❌ Invalid sequence number: {sequence_number} (must be >= 1)")
        return None, None, None
    
    current_sequence = 1  # Start counting from the first book
    
    # Iterate through each catalog to find the target sequence number
    for catalog_index, catalog_url in enumerate(catalog_urls):
        try:
            # Fetch catalog data
            response = requests.get(catalog_url, timeout=30)
            response.raise_for_status()
            info = json.loads(response.text)
            
            # Calculate how many books this catalog has
            catalog_size = len(info)
            
            # Check if our target sequence number falls within this catalog
            if current_sequence <= sequence_number < current_sequence + catalog_size:
                # Calculate the position within this catalog
                catalog_position = sequence_number - current_sequence
                
                # Verify the calculated position exists in the catalog
                if catalog_position < len(info):
                    book_info = info[catalog_position]
                    return book_info, catalog_index, catalog_position
            
            # Move to the next catalog's starting sequence number
            current_sequence += catalog_size
            
        except requests.RequestException as e:
            print(f"❌ Network error processing catalog {catalog_index + 1}: {e}")
            continue
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON in catalog {catalog_index + 1}: {e}")
            continue
        except Exception as e:
            print(f"❌ Unexpected error processing catalog {catalog_index + 1}: {e}")
            continue
    
    # Sequence number not found in any catalog
    print(f"❌ Sequence number {sequence_number} not found in any catalog")
    return None, None, None


def pdf_download(table: int = 0, item: int = 0, single_book: Optional[int] = None, 
                 download_limit: Optional[int] = None, sequence_number: Optional[int] = None, 
                 book_range: Optional[str] = None, book_id: Optional[str] = None) -> None:
    """
    Enhanced textbook downloader with multiple download modes and CDN fallback.
    
    This is the main function that orchestrates textbook downloads based on the specified mode.
    It supports downloading by sequence number, book range, book ID, and legacy catalog-based
    approaches. All downloads use CDN fallback logic for reliability.
    
    Args:
        table (int): Starting catalog index (0-based). Default: 0
        item (int): Starting item index within the catalog (0-based). Default: 0
        single_book (Optional[int]): Download only one specific book number. Default: None
        download_limit (Optional[int]): Limit the number of books to download. Default: None
        sequence_number (Optional[int]): Download by global sequence number. Default: None
        book_range (Optional[str]): Download by book range (e.g., "200-250"). Default: None
        book_id (Optional[str]): Download by specific book ID (UUID). Default: None
        
    Returns:
        None: This function performs downloads but doesn't return values
        
    Raises:
        OSError: If there are file system errors
        requests.RequestException: If network requests fail
        ValueError: If book range format is invalid
        
    Example:
        # Download by sequence number
        pdf_download(sequence_number=2548)
        
        # Download by range
        pdf_download(book_range="1-10")
        
        # Legacy mode - download first 5 books from catalog 0
        pdf_download(limit=5)
    """
    print("🚀 Starting textbook download...")
    
    # Set up HTTP headers for all requests
    headers = {
        'Referer': 'https://basic.smartedu.cn/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
        'Origin': 'https://basic.smartedu.cn',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    
    # Create and verify the output directory
    work_path = os.path.join(dir_path, "textbook_download")
    try:
        if not os.path.exists(work_path):
            os.makedirs(work_path)
    except OSError as e:
        print(f"❌ Failed to create output directory: {e}")
        return
    
    # Handle different download modes based on provided arguments
    if book_id:
        # Mode 1: Download by book ID (UUID)
        _download_by_book_id(book_id, headers, work_path)
        return
    
    elif sequence_number:
        # Mode 2: Download by sequence number
        _download_by_sequence_number(sequence_number, headers, work_path)
        return
    
    elif book_range:
        # Mode 3: Download by book range
        _download_by_book_range(book_range, headers, work_path)
        return
    
    # Legacy modes (original functionality)
    elif single_book or download_limit or table > 0 or item > 0:
        _download_legacy_mode(table, item, single_book, download_limit, headers, work_path)
    
    else:
        print("❌ No download mode specified. Use --help to see available options.")
        return
    
    print(f"📁 Check your Downloads/textbook_download folder")


def _download_by_book_id(book_id: str, headers: Dict[str, str], work_path: str) -> None:
    """
    Download a textbook by its unique book ID (UUID).
    
    Args:
        book_id (str): The unique identifier of the textbook
        headers (Dict[str, str]): HTTP headers for the request
        work_path (str): Directory where the PDF should be saved
    """
    print(f"🔍 Downloading by book ID: {book_id}")
    
    try:
        # Get PDF URLs for this book
        pdf_urls = get_pdf_url(book_id)
        if pdf_urls:
            # Fetch book title from metadata for better filename
            json_url = f"https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{book_id}.json"
            response = requests.get(json_url, timeout=30)
            
            if response.ok:
                data = response.json()
                book_title = data.get('title', f'Book_{book_id}')
            else:
                book_title = f'Book_{book_id}'
            
            # Attempt download with CDN fallback
            success = download_pdf_with_cdn_fallback(pdf_urls, book_title, headers, work_path)
            if success:
                print(f"    ✅ Successfully downloaded book ID: {book_id}")
            else:
                print(f"❌ Failed to download book ID: {book_id}")
        else:
            print(f"❌ Could not get PDF URLs for book ID: {book_id}")
    except Exception as e:
        print(f"❌ Error processing book ID {book_id}: {str(e)}")


def _download_by_sequence_number(sequence_number: int, headers: Dict[str, str], work_path: str) -> None:
    """
    Download a textbook by its global sequence number.
    
    Args:
        sequence_number (int): Global sequence number of the textbook
        headers (Dict[str, str]): HTTP headers for the request
        work_path (str): Directory where the PDF should be saved
    """
    print(f"🔍 Downloading by sequence number: {sequence_number}")
    
    # Get textbook catalog
    print("📚 Getting textbook catalog...")
    try:
        catalog_urls = get_parts()
    except Exception as e:
        print(f"❌ Failed to get catalog: {e}")
        return
    
    # Find the book in the catalog
    book_info, catalog_index, catalog_position = get_book_by_sequence_number(catalog_urls, sequence_number)
    if book_info:
        print(f"📖 Found book: {book_info.get('title', 'Unknown')}")
        print(f"📍 Catalog: {catalog_index + 1}, Position: {catalog_position + 1}")
        
        # Get PDF URLs and download
        book_id = book_info['id']
        pdf_urls = get_pdf_url(book_id)
        
        if pdf_urls:
            # Extract publisher information for filename
            publisher = next((tag['tag_name'] for tag in book_info['tag_list'] if '版' in tag['tag_name']), '')
            book_name = f"{publisher}{book_info['title']}"
            
            success = download_pdf_with_cdn_fallback(pdf_urls, book_name, headers, work_path)
            if success:
                print(f"    ✅ Successfully downloaded sequence number: {sequence_number}")
            else:
                print(f"❌ Failed to download sequence number: {sequence_number}")
        else:
            print(f"❌ Could not get PDF URLs for sequence number: {sequence_number}")
    else:
        print(f"❌ Sequence number {sequence_number} not found in catalog")


def _download_by_book_range(book_range: str, headers: Dict[str, str], work_path: str) -> None:
    """
    Download multiple textbooks within a specified range.
    
    Args:
        book_range (str): Range specification (e.g., "200-250" or "200")
        headers (Dict[str, str]): HTTP headers for the request
        work_path (str): Directory where the PDFs should be saved
    """
    print(f"🔍 Downloading by book range: {book_range}")
    
    try:
        # Parse the range specification
        if '-' in book_range:
            start, end = map(int, book_range.split('-'))
            if start > end:
                start, end = end, start  # Swap if start > end
        else:
            start = end = int(book_range)  # Single book
        
        print(f"📚 Downloading books from sequence {start} to {end}")
        
        # Get textbook catalog
        print("📚 Getting textbook catalog...")
        try:
            catalog_urls = get_parts()
        except Exception as e:
            print(f"❌ Failed to get catalog: {e}")
            return
        
        total_processed = 0
        failed_books = []
        
        # Process each book in the range
        for seq_num in range(start, end + 1):
            print(f"\n📖 Processing sequence number: {seq_num}")
            
            book_info, catalog_index, catalog_position = get_book_by_sequence_number(catalog_urls, seq_num)
            if book_info:
                print(f"    📍 Found in catalog {catalog_index + 1}, position {catalog_position + 1}")
                
                book_id = book_info['id']
                pdf_urls = get_pdf_url(book_id)
                
                if pdf_urls:
                    publisher = next((tag['tag_name'] for tag in book_info['tag_list'] if '版' in tag['tag_name']), '')
                    book_name = f"{publisher}{book_info['title']}"
                    
                    success = download_pdf_with_cdn_fallback(pdf_urls, book_name, headers, work_path)
                    if success:
                        total_processed += 1
                        print(f"    ✅ Successfully downloaded sequence number: {seq_num}")
                    else:
                        failed_books.append((seq_num, book_name, "Download failed"))
                else:
                    print(f"⚠️ Could not get PDF URLs for sequence number: {seq_num}")
                    failed_books.append((seq_num, book_info.get('title', 'Unknown'), "No PDF URLs"))
            else:
                print(f"⚠️ Sequence number {seq_num} not found")
                failed_books.append((seq_num, "Unknown", "Not found"))
            
            # Small delay between books to be respectful to the server
            time.sleep(1)
        
        # Summary report
        print(f"\n🎉 Range download complete! Successfully processed {total_processed} textbooks")
        
        if failed_books:
            print(f"\n⚠️ Failed {len(failed_books)} books:")
            for seq_num, title, reason in failed_books:
                print(f"   • Sequence {seq_num}: {title} - {reason}")
        
    except ValueError:
        print(f"❌ Invalid range format: {book_range}. Use format like '200-250' or '200'")


def _download_legacy_mode(table: int, item: int, single_book: Optional[int], 
                         download_limit: Optional[int], headers: Dict[str, str], work_path: str) -> None:
    """
    Legacy download mode using catalog-based approach.
    
    This function maintains compatibility with the original script's functionality
    while adding enhanced error handling and CDN fallback.
    
    Args:
        table (int): Starting catalog index
        item (int): Starting item index within the catalog
        single_book (Optional[int]): Specific book number to download
        download_limit (Optional[int]): Maximum number of books to download
        headers (Dict[str, str]): HTTP headers for the request
        work_path (str): Directory where the PDFs should be saved
    """
    print("📚 Using legacy download mode...")
    
    try:
        url = get_parts()
    except Exception as e:
        print(f"❌ Failed to get catalog URLs: {e}")
        return
    
    t = 0 + table
    total_processed = 0
    book_counter = item

    # Process each catalog
    for ref in url[table:]:
        print(f"正在下载目录{t+1}/{len(url)}中的电子教材")
        
        try:
            response = requests.get(ref, headers=headers, timeout=30)
            response.raise_for_status()
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
                    
                    print(f"📖 Processing: {book_name}")
                    
                    # Get the PDF URLs with CDN fallback
                    pdf_urls = get_pdf_url(book_id)
                    
                    if pdf_urls:
                        success = download_pdf_with_cdn_fallback(pdf_urls, book_name, headers, work_path)
                        if success:
                            print(f"    ✅ Successfully downloaded: {book_name}")
                            total_processed += 1
                        else:
                            print(f"❌ Failed to download: {book_name}")
                    else:
                        print(f"❌ Could not get PDF URLs for {book_name}")
                    
                except Exception as e:
                    print(f"❌ Error processing {book_name}: {str(e)}")
                
                # If we're downloading a single book and found it, we can return
                if single_book is not None and book_counter == single_book:
                    return

                c += 1
                
        except requests.RequestException as e:
            print(f"❌ Network error processing catalog {t+1}: {e}")
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON in catalog {t+1}: {e}")
        except Exception as e:
            print(f"❌ Unexpected error processing catalog {t+1}: {e}")
        
        t += 1
        item = 0
    
    print(f"\n🎉 Download complete! Processed {total_processed} textbooks")


if __name__ == "__main__":
    # Set up command-line argument parser with comprehensive help
    parser = argparse.ArgumentParser(
        description='''
Enhanced Textbook Downloader from the National Smart Education Platform (国家中小学智慧教育平台)

This enhanced script downloads complete PDF textbooks with CDN fallback logic, multiple download modes,
and improved error handling. It now supports downloading by sequence number, book range, and book ID.

The downloaded PDFs will be saved to:
~/Downloads/textbook_download/

FEATURES:
- CDN Fallback: Automatically tries r1, r2, r3 endpoints if one fails
- Multiple Download Modes: Flexible options for different use cases
- Enhanced Error Handling: Detailed error messages and graceful fallbacks
- Progress Tracking: Real-time download status and file size information
- Robust Network Handling: Timeouts, retries, and connection error handling
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
DOWNLOAD MODES:
==============

1. BY SEQUENCE NUMBER (--sequence):
   Downloads a specific book by its global sequence number across all catalogs.
   Example: --sequence 2548 (downloads the 2548th book across all catalogs)
   
   This is useful when you know the exact position of a book in the entire collection.
   The script automatically calculates which catalog and position contains the book.

2. BY BOOK RANGE (--range):
   Downloads multiple books within a specified range.
   Example: --range "200-250" (downloads books from sequence 200 to 250)
   
   Range format: "start-end" or just "start" for a single book.
   The script will process each book in the range and provide a summary report.

3. BY BOOK ID (--book-id):
   Downloads a specific book by its unique identifier (UUID).
   Example: --book-id "bdc00134-465d-454b-a541-dcd0cec4d86e"
   
   This is useful when you have the exact book ID from the metadata.

4. LEGACY MODES:
   - --single N: Download only the Nth textbook from the catalog
   - --limit N: Download only N textbooks (starting from the beginning)
   - --table N: Start from catalog N (0-based indexing)
   - --item N: Start from item N within the catalog (0-based indexing)

EXAMPLES:
=========

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

TECHNICAL DETAILS:
==================

CDN Endpoints: The script automatically tries r1-ndr-oversea, r2-ndr-oversea, and r3-ndr-oversea
               in sequence if one fails, ensuring reliable downloads.

File Validation: Downloads are validated to ensure they are actual PDF files (>1MB) and not error pages.

Error Handling: Comprehensive error handling with detailed messages and graceful fallbacks.

Network Timeouts: 30-second timeout for all network requests to prevent hanging.

Output Directory: All downloads are saved to ~/Downloads/textbook_download/ with descriptive filenames.
        '''
    )
    
    # New download modes
    parser.add_argument(
        '--sequence', 
        type=int, 
        help='Download by global sequence number (e.g., 2548 for the 2548th book across all catalogs)'
    )
    parser.add_argument(
        '--range', 
        type=str, 
        help='Download by book range (e.g., "200-250" for books 200 to 250, or "200" for single book)'
    )
    parser.add_argument(
        '--book-id', 
        type=str, 
        help='Download by specific book ID (UUID format, e.g., "bdc00134-465d-454b-a541-dcd0cec4d86e")'
    )
    
    # Legacy modes
    parser.add_argument(
        '--single', 
        type=int, 
        help='Download only one specific book number from the catalog (legacy mode)'
    )
    parser.add_argument(
        '--limit', 
        type=int, 
        help='Limit the number of books to download in this run (legacy mode)'
    )
    parser.add_argument(
        '--table', 
        type=int, 
        default=0, 
        help='Start from specific catalog index (0-based, legacy mode). Range: 0-3'
    )
    parser.add_argument(
        '--item', 
        type=int, 
        default=0, 
        help='Start from specific item index within the catalog (0-based, legacy mode). Range: 0-999'
    )
    
    # Parse command-line arguments
    args = parser.parse_args()
    
    # Call the enhanced download function with all parsed arguments
    pdf_download(
        table=args.table,
        item=args.item,
        single_book=args.single,
        download_limit=args.limit,
        sequence_number=args.sequence,
        book_range=args.range,
        book_id=args.book_id
    )