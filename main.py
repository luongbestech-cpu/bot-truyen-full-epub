import asyncio
import os
import re
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, urljoin
import cloudscraper
from bs4 import BeautifulSoup
from ebooklib import epub
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ============================================================
# WEB SERVER CHO RENDER
# ============================================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot TruyenFull & Multi-Site đang hoạt động!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# ============================================================
# CẤU HÌNH BOT & CHỐNG TƯỜNG LỬA (CLOUDSCRAPER)
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def get_soup(url):
    try:
        response = scraper.get(url, timeout=35)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        print(f"Lỗi tải {url}: {e}")
    return None

def download_chapter(url):
    soup = get_soup(url)
    if not soup: return None
    
    # Mở rộng các class chứa nội dung phổ biến của các trang truyện chữ
    content = (soup.select_one(".chapter-content") or 
               soup.select_one("#chapter-c") or 
               soup.select_one(".chapter-c") or 
               soup.select_one(".entry-content") or 
               soup.select_one(".post-content"))
               
    if not content: return None
    
    for tag in content.find_all(["script", "style", "div", "ins", "iframe", "button"]):
        tag.decompose()
        
    return str(content)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang kết nối và quét danh sách chương chuẩn xác...")
    
    story_url = url[0].strip()
    main_soup = get_soup(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối tới trang truyện. Web có thể đang chặn hoặc sai link.")
        return
        
    # --- LẤY TÊN TRUYỆN CHUẨN XÁC HƠN ---
    title_el = (
        main_soup.select_one("h1") 
        or main_soup.select_one(".title") 
        or main_soup.select_one(".truyen-title")
        or main_soup.select_one("[itemprop='name']")
    )
    
    if title_el:
        title = title_el.get_text().strip()
    else:
        # Fallback: Lấy trực tiếp từ thẻ <title> nếu không tìm thấy thẻ tiêu đề thông thường
        page_title = main_soup.select_one("title")
        title = page_title.get_text().strip() if page_title else "Truyện"

    if "|" in title:
        title = title.split('|')[0].strip()
    # Loại bỏ các đuôi thừa thường gặp như " - Truyện Full"
    title = re.sub(r'\s*-\s*(Truyện.*|Đọc.*)$', '', title, flags=re.IGNORECASE).strip()
    if not title:
        title = "Truyện Không Tên"
        
    # Xác định domain và đường dẫn gốc của truyện để lọc link chống lấy nhầm
    parsed_url = urlparse(story_url)
    base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    story_path = parsed_url.path.rstrip('/')

    # --- LẤY ẢNH BÌA LINH HOẠT HƠN ---
    cover_url = None
    img_tag = (
        main_soup.select_one(".book img") 
        or main_soup.select_one(".truyen-info img") 
        or main_soup.select_one(".info-holder img")
        or main_soup.select_one("article img")
        or main_soup.select_one(".img-responsive")
        or main_soup.select_one(".box-image img")
    )
    if img_tag:
        cover_url = img_tag.get('data-src') or img_tag.get('src') or img_tag.get('data-lazy-src')

    # --- QUÉT PHÂN TRANG THÔNG MINH & CHỐNG LẤY NHẦM TRUYỆN KHÁC ---
    links = []
    current_page_url = story_url
    page_num = 1
    
    while current_page_url:
        soup = get_soup(current_page_url)
        if not soup: break
            
        chapter_tags = soup.select("#list-chapter a, .list-chapter a, .chapter-list a, .zaraz-list a")
        if not chapter_tags:
            break
            
        new_chapters_in_page = 0
        for a in chapter_tags:
            href = a.get('href', '')
            if href:
                full_url = href if href.startswith("http") else base_domain + href
                
                # KIỂM TRA NGHIÊM NGẶT: Link chương phải thuộc đúng path của truyện này
                if story_path not in full_url:
                    continue

                text = a.get_text().strip()
                if text and not any(l['url'] == full_url for l in links):
                    links.append({"name": text, "url": full_url})
                    new_chapters_in_page += 1
                    
        if new_chapters_in_page == 0:
            break
            
        # Tìm nút phân trang trang tiếp theo
        pagination_links = soup.select(".pagination a, .pages a")
        next_url = None
        for p_link in pagination_links:
            text_p = p_link.get_text().strip()
            if "Trang sau" in text_p or ">" in text_p or str(page_num + 1) == text_p:
                next_url = p_link.get('href')
                break
                
        if next_url:
            next_full_url = next_url if next_url.startswith("http") else base_domain + next_url
            if next_full_url == current_page_url: break
            current_page_url = next_full_url
            page_num += 1
        else:
            # Tự động đoán cấu trúc phân trang kiểu /trang-2/ nếu không tìm thấy nút bấm
            if page_num < 40:
                if story_url.endswith("/"):
                    guessed_url = f"{story_url}trang-{page_num + 1}/"
                else:
                    guessed_url = f"{story_url}/trang-{page
