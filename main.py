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
# 🔑 CẤU HÌNH & WEB SERVER DUMMY
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")
CONCURRENT_DOWNLOADS = 10  # Tải song song 10 luồng cho nhanh
REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)

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

def get_soup(url):
    try:
        res = session.get(url, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        return BeautifulSoup(res.text, "lxml")
    except: 
        return None

def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()

# ============================================================
# BỘ QUÉT CHƯƠNG VỚI CƠ CHẾ "DỪNG THÔNG MINH"
# ============================================================
def parse_all_chapters(story_url, main_soup):
    chapters = []
    seen_urls = set()
    base_url = urldefrag(story_url)[0].rstrip("/")
    
    # 1. Tìm tổng số trang từ phân trang
    max_page = 1
    for a in main_soup.select(".pagination a"):
        txt = a.get_text()
        if txt.isdigit(): 
            max_page = max(max_page, int(txt))
    
    # 2. Vét cạn từng trang có cơ chế tự dừng khi hết chương mới
    for p in range(1, max_page + 1):
        p_url = f"{base_url}/trang-{p}/" if p > 1 else base_url
        soup = main_soup if p == 1 else get_soup(p_url)
        if not soup: 
            continue
        
        found_new = False
        page_chapters = soup.select("#list-chapter a")
        if not page_chapters:
            break
            
        for a in page_chapters:
            href = urljoin(p_url, a.get("href", ""))
            if href and href not in seen_urls:
                seen_urls.add(href)
                chapters.append({"name": clean_text(a.get_text()), "url": href})
                found_new = True
        
        # Nếu trang này không còn chương nào mới so với trước -> Dừng luôn không quét nữa
        if not found_new:
            break
            
    return chapters

def download_chap(url):
    try:
        soup = get_soup(url)
        if not soup: 
            return None
        content = soup.select_one(".chapter-content") or soup.select_one("#chapter-c")
        if not content: 
            return None
        for t in content.find_all(["script", "style", "ins", "a", "div.ads"]): 
            t.decompose()
        return str(content)
    except: 
        return None

# ============================================================
# TELEGRAM HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: 
        return
    urls = re.findall(r"https?://[^\s]+", update.message.text)
    if not urls: 
        return
    
    story_url = urls[0]
    status = await update.message.reply_text("⏳ Đang kết nối tới trang truyện...")
    
    try:
        main_soup = get_soup(story_url)
        if not main_soup:
            await status.edit_text("❌ Không thể kết nối đến trang truyện.")
            return

        title_el = main_soup.select_one("h1")
        title = clean_text(title_el.get_text()) if title_el else "Truyện"
        
        await status.edit_text(f"📚 **{title}**\n🔎 Đang quét danh sách chương...")
        chapters = parse_all_chapters(story_url, main_soup)
        
        if not chapters:
            await status.edit_text("❌ Không tìm thấy chương nào.")
            return

        total = len(chapters)
        await status.edit_text(f"📚 **{title}**\n✅ Tìm thấy {total} chương. Bắt đầu tải song song...")

        results = {}
        sem = asyncio.Semaphore(CONCURRENT_DOWNLOADS)
        
        async def fetch_task(idx, c):
            async with sem:
                content = await asyncio.to_thread(download_chap, c["url"])
                results[idx] = content
                return True

        tasks = [fetch_task(i, c) for i, c in enumerate(chapters)]
        
        # Hiển thị % tiến độ thời gian thực
        done = 0
        for future in asyncio.as_completed(tasks):
            await future
            done += 1
            if done % 5 == 0 or done == total:
                pct = int((done / total) * 100)
                bar = "█" * (pct // 10) + "▒" * (10 - pct // 10)
                try:
                    await status.edit_text(f"📚 **{title}**\n⏳ Đang tải: {done}/{total} chương ({pct}%)\n{bar}")
                except:
                    pass

        # Đóng gói file EPUB chuẩn Kindle
        await status.edit_text(f"📚 **{title}**\n📦 Đang đóng gói file EPUB...")
        
        book = epub.EpubBook()
        book.set_identifier("kindle-epub-" + str(abs(hash(title))))
        book.set_title(title)
        book.set_language("vi")

        kindle_css = """
            @page { margin: 8pt; }
            body { font-family: "Bookerly", "Georgia", serif; line-height: 1.6; text-align: justify; }
            h2 { text-align: center; font-size: 1.3em; margin-bottom: 1.2em; }
            p { text-indent: 1.5em; margin-top: 0; margin-bottom: 0.4em; }
        """
        css_item = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=kindle_css)
        book.add_item(css_item)

        epub_chaps = []
        spine = ["nav"]

        for i, chap in enumerate(chapters):
            content = results.get(i)
            if not content: 
                continue
            c_name = chap["name"]
            doc_html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{c_name}</title><link rel='stylesheet' href='style.css'></head><body><h2>{c_name}</h2>{content}</body></html>"
            
            item = epub.EpubHtml(title=c_name, file_name=f"chap_{i+1}.xhtml", lang="vi")
            item.content = doc_html
            item.add_item(css_item)
            book.add_item(item)
            epub_chaps.append(item)
            spine.append(item)

        book.toc = tuple(epub_chaps)
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        file_out = (re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen_Kindle") + ".epub"
        await asyncio.to_thread(epub.write_epub, file_out, book)

        await status.edit_text(f"📚 **{title}**\n✅ Đã hoàn tất! Đang gửi file EPUB...")

        with open(file_out, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=os.path.basename(file_out),
                caption=f"📖 {title}\n✅ Đã tải xong trọn bộ chuẩn Kindle!"
            )
        await status.delete()
        if os.path.exists(file_out): 
            os.remove(file_out)

    except Exception as e:
        print(f"Lỗi: {e}")
        try:
            await status.edit_text(f"❌ Đã xảy ra lỗi:\n`{e}`")
        except:
            pass

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
