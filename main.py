import asyncio
import os
import re
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import cloudscraper
from bs4 import BeautifulSoup
from ebooklib import epub
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ============================================================
# WEB SERVER & CẤU HÌNH BOT
# ============================================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot TruyenFull đang hoạt động!".encode('utf-8'))
    def log_message(self, format, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def get_soup(url):
    try:
        response = scraper.get(url, timeout=30)
        return BeautifulSoup(response.text, "lxml") if response.status_code == 200 else None
    except: return None

def download_chapter(url):
    soup = get_soup(url)
    if not soup: return None
    content = soup.select_one(".chapter-content") or soup.select_one("#chapter-c")
    if not content: return None
    for tag in content.find_all(["script", "style", "div", "ins", "iframe"]): tag.decompose()
    return str(content)

# ============================================================
# XỬ LÝ CHÍNH
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url_match = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url_match: return
    
    status = await update.message.reply_text("⏳ Đang xử lý truyện...")
    story_url = url_match[0]
    main_soup = get_soup(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối.")
        return
        
    title = main_soup.select_one("h1").get_text().strip() if main_soup.select_one("h1") else "Truyện"
    
    # LẤY ẢNH BÌA
    cover_url = main_soup.select_one(".book img")['src'] if main_soup.select_one(".book img") else None
    
    links = [{"name": a.get_text().strip(), "url": (a['href'] if a['href'].startswith("http") else "https://truyenfull.live" + a['href'])} 
             for a in main_soup.select("#list-chapter a")]
            
    if not links:
        await status.edit_text("❌ Không tìm thấy chương.")
        return
        
    # TẠO SÁCH
    book = epub.EpubBook()
    book.set_title(title)
    
    # Thêm bìa
    if cover_url:
        try:
            img_data = scraper.get(cover_url).content
            book.set_cover("cover.jpg", img_data)
        except: pass

    chapters_list = []
    for i, item in enumerate(links):
        content = download_chapter(item['url'])
        if content:
            chap = epub.EpubHtml(title=item['name'], file_name=f"chap_{i}.xhtml")
            chap.content = f"<h2>{item['name']}</h2>{content}"
            book.add_item(chap)
            chapters_list.append(chap)
            if i % 10 == 0: await status.edit_text(f"📚 {title}\n⏳ Tải: {int(i/len(links)*100)}%")

    # CẤU HÌNH MỤC LỤC
    book.toc = (tuple(chapters_list))
    book.spine = ['nav'] + chapters_list
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    file_name = f"{re.sub(r'[\\/*?:\"<>|]', '', title)}.epub"
    epub.write_epub(file_name, book)
    
    await update.message.reply_document(document=open(file_name, "rb"), caption=f"✅ Xong: {title}")
    await status.delete()
    if os.path.exists(file_name): os.remove(file_name)

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
