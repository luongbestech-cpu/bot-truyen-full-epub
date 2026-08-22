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
        self.wfile.write("Bot Truyen Multi-Source đang chạy!".encode('utf-8'))

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
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
# BỘ BẮT NỘI DUNG TƯƠNG THÍCH ĐA TRANG
# ============================================================
def extract_story_info(story_url):
    soup = get_soup(story_url)
    if not soup:
        raise RuntimeError("Không thể kết nối đến trang truyện.")
    
    # Tiêu đề
    title = ""
    for selector in ["h1.title", "h1", ".book-info h1", "meta[property='og:title']"]:
        el = soup.select_one(selector)
        if el:
            title = clean_text(el.get("content", "")) if el.name == "meta" else clean_text(el.get_text())
            if title: break
    title = title or "Truyện"

    # Ảnh bìa
    cover_url = None
    for selector in ["meta[property='og:image']", ".book-info img", ".info-holder img", ".book-img img"]:
        el = soup.select_one(selector)
        if el:
            src = el.get("content") if el.name == "meta" else (el.get("src") or el.get("data-src"))
            if src:
                cover_url = urljoin(story_url, src)
                break

    return title, cover_url, soup

def parse_all_chapters(story_url, main_soup):
    chapters = []
    seen_urls = set()

    # 1. TRUYỆN FULL
    if "truyenfull" in story_url:
        current_url = urldefrag(story_url)[0].rstrip("/")
        visited_pages = set()
        while current_url and current_url not in visited_pages:
            visited_pages.add(current_url)
            soup = get_soup(current_url) if current_url != story_url else main_soup
            if not soup: break
            
            for a in soup.select("#list-chapter a, .list-chapter a"):
                href = urljoin(current_url, a.get("href", ""))
                if href and href not in seen_urls:
                    seen_urls.add(href)
                    name = clean_text(a.get_text())
                    chapters.append({"name": name, "url": href})
            
            # Tìm trang kế tiếp
            next_a = soup.select_one(".pagination .next a, .pagination a[links-next]")
            current_url = urljoin(current_url, next_a.get("href")) if next_a and next_a.get("href") else None

    # 2. TÀNG THƯ VIỆN
    elif "tangthuvien" in story_url:
        for a in main_soup.select(".story-chap-list a, #list-chap a"):
            href = urljoin(story_url, a.get("href", ""))
            if href and href not in seen_urls:
                seen_urls.add(href)
                chapters.append({"name": clean_text(a.get_text()), "url": href})

    # 3. MÊ TRUYỆN CHỮ / DẠNG KHÁC (GENERIC)
    else:
        for a in main_soup.select("a[href*='/chuong']"):
            href = urljoin(story_url, a.get("href", ""))
            if href and href not in seen_urls:
                seen_urls.add(href)
                chapters.append({"name": clean_text(a.get_text()), "url": href})

    return chapters

def download_chapter_content(chap_info):
    soup = get_soup(chap_info["url"])
    if not soup: return None
    
    content_el = None
    # Selector nội dung theo từng trang
    for selector in [".chapter-c", "#chapter-c", ".chap-content", "#chap-content", ".reading-content", ".content-body"]:
        el = soup.select_one(selector)
        if el and len(clean_text(el.get_text())) > 50:
            content_el = el
            break
            
    if not content_el: return None
    
    # Dọn dẹp rác quảng cáo
    for tag in content_el.find_all(["script", "style", "iframe", "form", "ins", "a"]):
        tag.decompose()
        
    return str(content_el)

# ============================================================
# TELEGRAM BOT HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    urls = re.findall(r"https?://[^\s]+", update.message.text)
    if not urls:
        await update.message.reply_text("❌ Vui lòng gửi một đường link truyện hợp lệ!")
        return

    story_url = urls[0]
    status = await update.message.reply_text("⏳ Đang kết nối tới trang truyện...")

    try:
        title, cover_url, main_soup = await asyncio.to_thread(extract_story_info, story_url)
        await status.edit_text(f"📚 **{title}**\n\n🔎 Đang quét danh sách chương...")

        chapters = await asyncio.to_thread(parse_all_chapters, story_url, main_soup)
        if not chapters:
            await status.edit_text("❌ Không lấy được danh sách chương. Vui lòng kiểm tra lại link.")
            return

        total = len(chapters)
        await status.edit_text(f"📚 **{title}**\n✅ Tìm thấy {total} chương.\n🚀 Đang tải dữ liệu chuẩn Kindle...")

        downloaded_data = {}
        loop = asyncio.get_event_loop()
        last_update = time.time()

        for i in range(0, total, CONCURRENT_DOWNLOADS):
            batch = chapters[i:i + CONCURRENT_DOWNLOADS]
            tasks = [loop.run_in_executor(None, download_chapter_content, c) for c in batch]
            results = await asyncio.gather(*tasks)

            for idx, res in enumerate(results):
                if res: downloaded_data[i + idx] = res

            if time.time() - last_update > 4 or (i + CONCURRENT_DOWNLOADS) >= total:
                done = min(i + CONCURRENT_DOWNLOADS, total)
                pct = int((done / total) * 100)
                try:
                    await status.edit_text(f"📚 **{title}**\n⏳ Tiến độ: {done}/{total} chương ({pct}%)\n████████▒▒ {pct}%")
                except Exception: pass
                last_update = time.time()

        await status.edit_text(f"📚 **{title}**\n📦 Đang tối ưu định dạng EPUB cho Kindle...")

        # ============================================================
        # TẠO FILE EPUB ĐÃ TỐI ƯU CHO KINDLE
        # ============================================================
        book = epub.EpubBook()
        book.set_identifier("kindle-epub-" + str(abs(hash(title))))
        book.set_title(title)
        book.set_language("vi")

        if cover_url:
            c_res = await asyncio.to_thread(fetch, cover_url)
            if c_res and len(c_res.content) > 1000:
                book.set_cover("cover.jpg", c_res.content)

        # CSS chuyên dụng cho màn hình E-Ink Kindle
        kindle_css = """
            @page { margin: 8pt; }
            body {
                font-family: "Bookerly", "Charis SIL", "Georgia", serif;
                line-height: 1.6;
                text-align: justify;
                margin: 0;
                padding: 0;
            }
            h2 {
                text-align: center;
                font-size: 1.3em;
                font-weight: bold;
                margin-top: 1.2em;
                margin-bottom: 1.2em;
            }
            p {
                text-indent: 1.5em;
                margin-top: 0;
                margin-bottom: 0.4em;
            }
        """
        css_item = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=kindle_css)
        book.add_item(css_item)

        epub_chaps = []
        spine = ["nav"]

        for idx, chap in enumerate(chapters):
            html_content = downloaded_data.get(idx)
            if not html_content: continue

            c_name = chap["name"]
            if not re.search(r"\d+", c_name):
                c_name = f"Chương {idx + 1}: {c_name}"

            doc_html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{c_name}</title><link rel='stylesheet' href='style.css'></head><body><h2>{c_name}</h2>{html_content}</body></html>"
            
            item = epub.EpubHtml(title=c_name, file_name=f"chap_{idx+1}.xhtml", lang="vi")
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

        await status.edit_text(f"📚 **{title}**\n✅ Tải xong {len(epub_chaps)}/{total} chương.\n📤 Đang gửi file EPUB...")

        with open(file_out, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=os.path.basename(file_out),
                caption=f"📖 {title}\n✅ File EPUB đã tối ưu chuẩn Kindle!\n👉 Dùng amazon.com/sendtokindle để gửi vào máy."
            )
        await status.delete()
        if os.path.exists(file_out): os.remove(file_out)

    except Exception as e:
        print(f"❌ Error: {e}")
        try: await status.edit_text(f"❌ Đã xảy ra lỗi:\n`{e}`")
        except Exception: pass

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
