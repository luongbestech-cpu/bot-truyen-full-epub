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
# 🔑 LẤY TOKEN TỪ ENVIRONMENT VARIABLE TRÊN RENDER
# ============================================================
BOT_TOKEN = os.getenv("bot_token_truyenfull") or os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")

# ============================================================
# 🌐 DUMMY WEB SERVER ĐỂ RENDER BẮT PORT (10000)
# ============================================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot TruyenFull Colab-Engine đang hoạt động 24/7!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌐 Web Server đang lắng nghe Port {port}...")
    server.serve_forever()

# ============================================================
# CẤU HÌNH CÀO TRUYỆN
# ============================================================
REQUEST_TIMEOUT = 15
MAX_PAGES = 2000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
session = requests.Session()
session.headers.update(HEADERS)

def fetch(url, timeout=REQUEST_TIMEOUT):
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        return response
    except Exception as e:
        print(f"⚠️ Không tải được: {url} | {e}")
        return None

def get_soup(url):
    response = fetch(url)
    if response is None:
        return None
    return BeautifulSoup(response.text, "lxml")

def normalize_url(url):
    if not url:
        return ""
    return urldefrag(url)[0].rstrip("/")

def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()

def extract_chapter_number(text):
    text = clean_text(text)
    patterns = [
        r"(?:chương|chuong|chapter|chap)\s*(\d+)(?:\s*[-–]\s*(\d+))?",
        r"/chuong[-_](\d+)(?:[-_](\d+))?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        first = int(match.group(1))
        second = match.group(2)
        if second:
            return first + int(second) / 10
        return float(first)
    return None

def get_chapter_name(a, href):
    span = a.select_one(".chapter-text")
    if span:
        name = clean_text(span.get_text(" ", strip=True))
        if name:
            return name
    title = clean_text(a.get("title"))
    if title:
        return title
    text = clean_text(a.get_text(" ", strip=True))
    if text:
        return text
    match = re.search(r"/chuong[-_](\d+(?:[-_]\d+)?)[^/]*", href, flags=re.I)
    if match:
        return "Chương " + match.group(1)
    return "Chương"

def is_chapter_link(text, href):
    combined = clean_text(text) + " " + (href or "")
    if re.search(r"(?:chương|chuong|chapter|chap)\s*\d+", combined, flags=re.I):
        return True
    if re.search(r"/chuong[-_]\d+", href or "", flags=re.I):
        return True
    return False

def parse_chapters(page_url, soup):
    result = {}
    if soup is None:
        return result
    containers = soup.select("#list-chapter") or soup.select(".list-chapter") or [soup]
    for container in containers:
        for a in container.find_all("a", href=True):
            href = normalize_url(urljoin(page_url, a.get("href")))
            if not href:
                continue
            name = get_chapter_name(a, href)
            if not is_chapter_link(name, href):
                continue
            number = extract_chapter_number(name + " " + href)
            if number is None:
                continue
            result[href] = {"name": name, "url": href, "number": number}
    return result

def get_page_number(url):
    patterns = [r"/page[-/](\d+)", r"/trang[-_](\d+)", r"[?&]page=(\d+)"]
    for pattern in patterns:
        match = re.search(pattern, url, flags=re.I)
        if match:
            return int(match.group(1))
    return None

def find_pagination_links(page_url, soup):
    pages = []
    if soup is None:
        return pages
    seen = set()
    nodes = soup.select(".pagination") or soup.select("ul.pagination") or [soup]
    for node in nodes:
        for a in node.find_all("a", href=True):
            href = normalize_url(urljoin(page_url, a.get("href")))
            if not href:
                continue
            text = clean_text(a.get_text(" ", strip=True)).lower()
            if is_chapter_link(text, href):
                continue
            low = href.lower()
            is_page = False
            if re.search(r"/(?:page|trang)[-_/]?\d+", low) or "[?&]page=\d+" in low:
                is_page = True
            if text.isdigit() and 1 <= int(text) <= MAX_PAGES:
                is_page = True
            if text in {"next", "tiếp", "trang sau", "sau", "»", "›", "→"}:
                is_page = True
            if is_page and href not in seen:
                seen.add(href)
                pages.append(href)
    return pages

def find_next_page(current_url, soup, visited):
    links = find_pagination_links(current_url, soup)
    candidates = []
    for link in links:
        if link in visited:
            continue
        number = get_page_number(link)
        if number is not None:
            candidates.append((number, link))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        current_number = get_page_number(current_url) or 1
        for number, link in candidates:
            if number == current_number + 1:
                return link
        for number, link in candidates:
            if number > current_number:
                return link
        return None
    for link in links:
        if link not in visited:
            return link
    return None

def get_story_info(story_url):
    soup = get_soup(story_url)
    if soup is None:
        raise RuntimeError("Không truy cập được trang truyện.")
    title = ""
    for selector in ["h1.title", "h1", "meta[property='og:title']", "title"]:
        element = soup.select_one(selector)
        if not element:
            continue
        title = clean_text(element.get("content", "")) if element.name == "meta" else clean_text(element.get_text(" ", strip=True))
        if title:
            break
    title = title or "Truyện"
    cover_url = None
    for selector in ["meta[property='og:image']", ".book img", ".info img"]:
        element = soup.select_one(selector)
        if not element:
            continue
        src = element.get("content") if element.name == "meta" else (element.get("src") or element.get("data-src"))
        if src:
            cover_url = urljoin(story_url, src)
            break
    return title, cover_url, soup

def collect_chapters(story_url):
    story_url = normalize_url(story_url)
    current_url = story_url
    visited = set()
    all_chapters = {}
    page_count = 0
    while True:
        if current_url in visited or page_count >= MAX_PAGES:
            break
        visited.add(current_url)
        page_count += 1
        soup = get_soup(current_url)
        if soup is None:
            break
        found = parse_chapters(current_url, soup)
        before = len(all_chapters)
        for url, item in found.items():
            if url not in all_chapters:
                all_chapters[url] = item
        new_count = len(all_chapters) - before
        if new_count == 0:
            break
        next_url = find_next_page(current_url, soup, visited)
        if not next_url:
            break
        current_url = next_url
        time.sleep(0.2)
    chapters = list(all_chapters.values())
    chapters.sort(key=lambda x: x["number"])
    unique = {}
    for item in chapters:
        if item["number"] not in unique:
            unique[item["number"]] = item
    chapters = sorted(list(unique.values()), key=lambda x: x["number"])
    if not chapters:
        return []
    integer_numbers = set(int(x["number"]) for x in chapters)
    missing = [n for n in range(1, max(integer_numbers) + 1) if n not in integer_numbers]
    if missing:
        print(f"⚠️ Thiếu các chương: {missing[:10]}")
        return []
    return chapters

def get_chapter_content(url):
    soup = get_soup(url)
    if soup is None:
        raise RuntimeError("Không mở được trang chương.")
    content = None
    for selector in [".chapter-c", "#chapter-c", ".chapter-content", ".reading-content"]:
        element = soup.select_one(selector)
        if element and len(clean_text(element.get_text(" ", strip=True))) > 100:
            content = element
            break
    if content is None:
        raise RuntimeError("Không tìm thấy nội dung chương.")
    for tag in content.find_all(["script", "style", "iframe", "form", "noscript", "nav"]):
        tag.decompose()
    return str(content)

def create_epub(title, cover_url, chapters):
    book = epub.EpubBook()
    book.set_identifier("truyenfull-" + str(abs(hash(title))))
    book.set_title(title)
    book.set_language("vi")
    if cover_url:
        res = fetch(cover_url)
        if res and len(res.content) > 1000:
            book.set_cover("cover.jpg", res.content)
    css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content="body{font-family:sans-serif;line-height:1.8;margin:5%;}h2{text-align:center;}p{text-align:justify;}")
    book.add_item(css)
    epub_chapters = []
    spine = ["nav"]
    for index, chapter in enumerate(chapters, start=1):
        c_name = clean_text(chapter["name"]) or f"Chương {chapter['number']}"
        try:
            content = get_chapter_content(chapter["url"])
            html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{c_name}</title><link rel='stylesheet' href='style.css'></head><body>2>{c_name}</h2>{content}</body></html>"
            item = epub.EpubHtml(title=c_name, file_name=f"chapter_{index}.xhtml", lang="vi")
            item.content = html
            item.add_item(css)
            book.add_item(item)
            epub_chapters.append(item)
            spine.append(item)
        except Exception as e:
            print(f"Lỗi chương {index}: {e}")
        time.sleep(0.1)
    book.toc = tuple(epub_chapters)
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    filename = (re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen") + ".epub"
    epub.write_epub(filename, book)
    return filename

# ============================================================
# TELEGRAM BOT HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    urls = re.findall(r"https?://[^\s]+", update.message.text)
    if not urls:
        await update.message.reply_text("❌ Hãy gửi link truyện hợp lệ nhé.")
        return
    story_url = urls[0]
    status = await update.message.reply_text("⏳ Đã nhận link. Đang kiểm tra truyện...")
    try:
        title, cover_url, _ = await asyncio.to_thread(get_story_info, story_url)
        await status.edit_text(f"📚 {title}\n\n🔎 Đang quét tất cả các chương...")
        chapters = await asyncio.to_thread(collect_chapters, story_url)
        if not chapters:
            await status.edit_text(f"📚 {title}\n\n❌ Không tìm đủ chương hoặc bị thiếu chương.")
            return
        await status.edit_text(f"📚 {title}\n\n✅ Xác nhận đủ {len(chapters)} chương.\n📖 Đang tiến hành tải & đóng gói EPUB...")
        filename = await asyncio.to_thread(create_epub, title, cover_url, chapters)
        await status.edit_text(f"📚 {title}\n\n✅ Đã tạo EPUB hoàn tất! Đang tải lên Telegram...")
        with open(filename, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(filename), caption=f"📚 {title}\n📖 {len(chapters)} chương")
        await status.delete()
        if os.path.exists(filename):
            os.remove(filename)
    except Exception as e:
        print(f"❌ Lỗi: {e}")
        try:
            await status.edit_text(f"❌ Lỗi: {e}")
        except Exception:
            pass

def main():
    # Khởi chạy Web Server
    threading.Thread(target=run_web_server, daemon=True).start()
    
    if not BOT_TOKEN:
        print("❌ Chưa cấu hình BOT_TOKEN trên Render Environment Variables!")
        return

    print("🤖 Đang khởi tạo Bot Telegram...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Bot đã sẵn sàng chạy Polling!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
