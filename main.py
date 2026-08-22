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
# WEB SERVER CHO RENDER
# ============================================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot TruyenFull đang hoạt động!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# ============================================================
# CẤU HÌNH BOT & SCRAPER
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN")

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def get_soup(url):
    try:
        response = scraper.get(url, timeout=30)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        print(f"Lỗi lấy soup {url}: {e}")
    return None

def download_chapter(url):
    soup = get_soup(url)
    if not soup: return None
    
    # Tìm nội dung theo nhiều kiểu cấu trúc phổ biến
    content = soup.select_one(".chapter-content") or soup.select_one("#chapter-c") or soup.select_one(".chapter-c")
    if not content: return None
    
    for tag in content.find_all(["script", "style", "div", "ins", "iframe"]):
        tag.decompose()
        
    return str(content)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang kết nối TruyenFull (Quét danh sách chương)...")
    
    story_url = url[0]
    main_soup = get_soup(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối tới trang truyện. Web có thể đang chặn mạnh.")
        return
        
    title_el = main_soup.select_one("h1") or main_soup.select_one(".title")
    title = title_el.get_text().strip() if title_el else "Truyện"
    
    links = []
    
    # THỬ NHIỀU CÁCH TÌM DANH SÁCH CHƯƠNG KHÁC NHAU ĐỂ KHÔNG BỊ SÓT
    chapter_tags = main_soup.select("#list-chapter a, .list-chapter a, .chapter-list a")
    
    # Nếu không tìm thấy bằng các class thông thường, quét toàn bộ thẻ a có chứa từ 'chuong' trong link
    if not chapter_tags:
        chapter_tags = [a for a in main_soup.find_all("a", href=True) if "chuong-" in a['href'] or "hoi-" in a['href']]

    for a in chapter_tags:
        href = a.get('href', '')
        if href:
            # Lọc lấy domain gốc để ghép link chuẩn
            domain = "https://" + story_url.split('/')[2]
            full_url = href if href.startswith("http") else domain + href
            text = a.get_text().strip()
            if text and not any(l['url'] == full_url for l in links):
                links.append({"name": text, "url": full_url})
            
    if not links:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy kiểm tra lại đường dẫn truyện.")
        return
        
    await status.edit_text(f"📚 {title}\n✅ Tìm thấy {len(links)} chương. Đang tải nội dung...")
    
    book = epub.EpubBook()
    book.set_title(title)
    
    success_count = 0
    for i, item in enumerate(links):
        content = download_chapter(item['url'])
        if content:
            chap = epub.EpubHtml(title=item['name'], file_name=f"chap_{i}.xhtml")
            chap.content = f"<h2>{item['name']}</h2>{content}"
            book.add_item(chap)
            book.spine.append(chap)
            success_count += 1
        
        if i % 15 == 0 or i == len(links) - 1:
            pct = int((i / len(links)) * 100)
            try:
                await status.edit_text(f"📚 {title}\n⏳ Đang tải: {pct}%\n({i+1}/{len(links)})")
            except:
                pass
        
        time.sleep(0.3)

    if success_count == 0:
        await status.edit_text("❌ Tải thất bại do trang web chặn toàn bộ nội dung.")
        return

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    file_name = (re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen") + ".epub"
    epub.write_epub(file_name, book)
    
    await update.message.reply_document(document=open(file_name, "rb"), caption=f"✅ Xong: {title}\n📖 Đã tải thành công {success_count}/{len(links)} chương chuẩn Kindle!")
    await status.delete()
    if os.path.exists(file_name):
        os.remove(file_name)

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    if not BOT_TOKEN:
        print("❌ Thiếu BOT_TOKEN!")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
