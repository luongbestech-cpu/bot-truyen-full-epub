import asyncio
import os
import re
import time
import threading
from urllib.parse import urldefrag, urljoin
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup
from ebooklib import epub
import requests
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ============================================================
# 🔑 TOKEN & WEB SERVER DUMMY
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("bot_token_truyenfull") or os.getenv("BOT_TOKEN")

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot Truyen All-in-One đang hoạt động!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# ============================================================
# CẤU HÌNH MẠNG
# ============================================================
REQUEST_TIMEOUT = 12
CONCURRENT_DOWNLOADS = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

session = requests.Session()
session.headers.update(HEADERS)

def fetch(url):
    try:
        res = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        res.raise_for_status()
        return res
    except Exception:
        return None

def get_soup(url):
    res = fetch(url)
    return BeautifulSoup(res.text, "lxml") if res else None

def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()

# ============================================================
# BỘ QUÉT DỮ LIỆU ĐA TRANG (MONGTRUYEN / TRUYENFULL / WORDPRESS)
# ============================================================
def extract_story_info(story_url):
    soup = get_soup(story_url)
    if not soup: raise RuntimeError("Không thể kết nối đến trang truyện.")
    
    title = ""
    for selector in ["h1.title", "h1.entry-title", "h1", "meta[property='og:title']"]:
        el = soup.select_one(selector)
        if el:
            title = clean_text(el.get("content", "")) if el.name == "meta" else clean_text(el.get_text())
            if title: break
    
    cover_url = None
    for selector in ["meta[property='og:image']", ".book-info img", ".info-holder img"]:
        el = soup.select_one(selector)
        if el:
            src = el.get("content") or el.get("src")
            if src and not src.endswith("avatar"):
                cover_url = urljoin(story_url, src)
                break
    return title or "Truyện", cover_url, soup

def parse_all_chapters(story_url, main_soup):
    chapters = []
    seen_urls = set()

    # 1. TRANG MONGTRUYEN
    if "mongtruyen" in story_url:
        clean_base = story_url.split('?')[0].split('#')[0]
        for page_num in range(1, 100):
            p_url = f"{clean_base}?page={page_num}"
            soup = main_soup if page_num == 1 else get_soup(p_url)
            if not soup: break
            
            found = 0
            for a in soup.find_all("a", href=True):
                href = urljoin(p_url, a.get("href"))
                if re.search(r"/(?:chuong|chapter|chap)[-_/]\d+", href, flags=re.I) and href not in seen_urls:
                    seen_urls.add(href)
                    chapters.append({"name": clean_text(a.get_text()), "url": href})
                    found += 1
            if found == 0: break
            time.sleep(0.1)

    # 2. TRANG WORDPRESS
    elif "wordpress.com" in story_url or main_soup.select_one(".entry-content"):
        content_area = main_soup.select_one(".entry-content") or main_soup.select_one(".post-content")
        if content_area:
            for a in content_area.find_all("a", href=True):
                href = urldefrag(urljoin(story_url, a.get("href")))[0]
                text = clean_text(a.get_text())
                if href and href not in seen_urls and len(text) > 1:
                    seen_urls.add(href)
                    chapters.append({"name": text, "url": href})

    # 3. TRUYỆN FULL (CẢI TIẾN: VÉT CẠN THEO SỐ TRANG)
    else:
        base_url = urldefrag(story_url)[0].rstrip("/")
        # Lấy tổng số trang từ pagination
        max_page = 1
        page_links = main_soup.select(".pagination a")
        for a in page_links:
            if a.get_text().isdigit():
                max_page = max(max_page, int(a.get_text()))
        
        for p in range(1, max_page + 1):
            p_url = f"{base_url}/trang-{p}/" if p > 1 else base_url
            soup = get_soup(p_url)
            if not soup: continue
            for a in soup.select("#list-chapter a, .list-chapter a"):
                href = urljoin(p_url, a.get("href", ""))
                if href and href not in seen_urls:
                    seen_urls.add(href)
                    chapters.append({"name": clean_text(a.get_text()), "url": href})
    return chapters

def download_chapter_content(chap_info):
    soup = get_soup(chap_info["url"])
    if not soup: return None
    content_el = soup.select_one(".chapter-content, .box-chap, .entry-content, .post-content")
    if not content_el: return None
    for tag in content_el.find_all(["script", "style", "iframe", "a"]): tag.decompose()
    return str(content_el)

# ============================================================
# TELEGRAM BOT HANDLER (Tương tự code cũ phía trên)
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    urls = re.findall(r"https?://[^\s]+", update.message.text)
    if not urls: return
    story_url = urls[0]
    status = await update.message.reply_text("⏳ Đang kết nối...")
    try:
        title, cover_url, main_soup = await asyncio.to_thread(extract_story_info, story_url)
        chapters = await asyncio.to_thread(parse_all_chapters, story_url, main_soup)
        await status.edit_text(f"📚 **{title}**\n✅ Tìm thấy {len(chapters)} chương. Đang tải...")
        # (Phần đóng gói EPUB giữ nguyên như code trước)...
        # [Để tránh quá dài, bạn copy đoạn đóng gói EPUB từ code trước vào đây]
    except Exception as e:
        await status.edit_text(f"❌ Lỗi: {e}")

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
