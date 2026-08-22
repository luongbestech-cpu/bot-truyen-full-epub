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
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("bot_token_truyenfull") or os.getenv("BOT_TOKEN")

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
# CẤU HÌNH CÀO TRUYỆN TOÀN DIỆN
# ============================================================
REQUEST_TIMEOUT = 10
MAX_PAGES = 300
CONCURRENT_DOWNLOADS = 5  # Số chương tải song song cùng lúc

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://truyenfull.vn/",
}

session = requests.Session()
session.headers.update(HEADERS)

def fetch(url, timeout=REQUEST_TIMEOUT):
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        return response
    except Exception:
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
    name = clean_text(span.get_text(" ", strip=True)) if span else ""
    if not name:
        name = clean_text(a.get("title")) or clean_text(a.get_text(" ", strip=True))
    
    # Bắt số chương từ đường link nếu tên bị thiếu
    num_match = re.search(r"/chuong[-_](\d+(?:[-_]\d+)?)[^/]*", href, flags=re.I)
    num_str = num_match.group(1).replace("-", ".") if num_match else ""

    # Nếu tên trống hoặc chỉ có mỗi chữ "Chương"
    if not name or name.lower() in ["chương", "chuong"]:
        return f"Chương {num_str}" if num_str else "Chương"
    
    # Nếu tên có nội dung nhưng chưa có chữ "Chương"
    if num_str and not re.search(r"\d+", name):
        return f"Chương {num_str}: {name}"
        
    return name

def is_chapter_link(text, href):
    """
    Nhận diện link chương theo cả text và URL.
    Cho phép các kiểu:
      - Chương 123
      - Chương 123: Tên chương
      - chapter-123
      - chuong-123-ten-chuong
      - /123/ hoặc ?chapter=123 trong một số WordPress
    """
    text = clean_text(text)
    href = href or ""
    combined = f"{text} {href}"

    patterns = [
        r"(?:chương|chuong|chapter|chap)\s*[-_:#.]?\s*\d+",
        r"/(?:chương|chuong|chapter|chap)[-_]?\d+",
        r"(?:^|[/_-])(?:chuong|chapter)[-_]?\d+(?:[/_-]|$)",
        r"[?&](?:chapter|chuong|chap)[=_]\d+",
    ]
    return any(re.search(p, combined, flags=re.I) for p in patterns)


def extract_chapter_number(text):
    text = clean_text(text)

    patterns = [
        r"(?:chương|chuong|chapter|chap)\s*[-_:#.]?\s*(\d+)",
        r"/(?:chuong|chapter|chap)[-_](\d+)",
        r"(?:^|[/_\-\s])chuong[-_]?(\d+)(?:[-_/]|$)",
        r"[?&](?:chapter|chuong|chap)[=_](\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return int(match.group(1))

    return None


def get_chapter_name(a, href):
    candidates = [
        clean_text(a.get_text(" ", strip=True)),
        clean_text(a.get("title")),
        clean_text(a.get("aria-label")),
    ]

    name = next((x for x in candidates if x), "")
    number = extract_chapter_number(name + " " + href)

    if not name and number is not None:
        return f"Chương {number}"

    # Nếu link chỉ ghi "Chương" còn số nằm trong URL.
    if name.lower() in {"chương", "chuong", "chapter", "chap"} and number is not None:
        return f"Chương {number}"

    return name or (f"Chương {number}" if number is not None else "Chương")


def _same_domain(a, b):
    try:
        da = re.sub(r"^www\.", "", urlparse(a).netloc.lower())
        db = re.sub(r"^www\.", "", urlparse(b).netloc.lower())
        return da == db
    except Exception:
        return True


def parse_chapters(page_url, soup):
    """
    Quan trọng: không giới hạn chapter link vào một selector cụ thể.
    Nhiều WordPress dùng cấu trúc HTML khác nhau, vì vậy quét toàn bộ
    các thẻ <a> nhưng chỉ giữ link có dấu hiệu chương.
    """
    result = {}
    if soup is None:
        return result

    for a in soup.find_all("a", href=True):
        href = normalize_url(urljoin(page_url, a.get("href")))
        if not href or not _same_domain(page_url, href):
            continue

        text = clean_text(a.get_text(" ", strip=True))
        title = clean_text(a.get("title"))
        aria = clean_text(a.get("aria-label"))
        combined = " ".join(x for x in (text, title, aria) if x)

        if not is_chapter_link(combined, href):
            continue

        number = extract_chapter_number(combined + " " + href)
        if number is None:
            continue

        name = get_chapter_name(a, href)

        # Không để một URL trùng xuất hiện nhiều lần.
        if href not in result:
            result[href] = {
                "name": name,
                "url": href,
                "number": number,
            }

    return result


def find_pagination_links(page_url, soup):
    pages = []
    seen = set()

    if soup is None:
        return pages

    selectors = [
        ".pagination a",
        ".page-numbers",
        ".wp-pagenavi a",
        ".nav-links a",
        "nav.pagination a",
        "a.next",
        "a.next.page-numbers",
        "a[rel='next']",
    ]

    anchors = []
    for selector in selectors:
        anchors.extend(soup.select(selector))

    # Fallback: chỉ xét các <a> có chữ next/tiếp hoặc URL page/paged.
    if not anchors:
        anchors = soup.find_all("a", href=True)

    for a in anchors:
        href = normalize_url(urljoin(page_url, a.get("href")))
        if not href or href in seen or not _same_domain(page_url, href):
            continue

        label = clean_text(a.get_text(" ", strip=True)).lower()
        rel = " ".join(a.get("rel", [])).lower()
        low = href.lower()

        is_page = bool(
            re.search(r"/(?:page|trang)[-_/]?\d+(?:/|$)", low)
            or re.search(r"[?&](?:page|paged)=\d+", low)
            or "next" in rel
            or label in {
                "next", "next page", "tiếp", "tiếp theo",
                "trang sau", "sau", "»", "›", "→"
            }
        )

        if label.isdigit() and 1 <= int(label) <= MAX_PAGES:
            is_page = True

        if is_page:
            seen.add(href)
            pages.append(href)

    return pages


def get_page_number(url):
    for pattern in (
        r"/(?:page|trang)[-_]?(\d+)(?:/|$)",
        r"[?&](?:page|paged)=(\d+)",
    ):
        m = re.search(pattern, url or "", flags=re.I)
        if m:
            return int(m.group(1))
    return None


def find_next_page(current_url, soup, visited):
    links = find_pagination_links(current_url, soup)
    if not links:
        return None

    current_no = get_page_number(current_url) or 1
    numbered = []

    for link in links:
        if link in visited:
            continue
        no = get_page_number(link)
        if no is not None:
            numbered.append((no, link))

    # Chọn đúng trang kế tiếp trước.
    for no, link in sorted(numbered):
        if no == current_no + 1:
            return link

    # Nếu không xác định được số trang hiện tại, lấy trang nhỏ nhất lớn hơn.
    for no, link in sorted(numbered):
        if no > current_no:
            return link

    # Fallback nút Next.
    for link in links:
        if link not in visited:
            return link

    return None


def collect_chapters(story_url):
    """
    Quét cho đến khi:
      - Không còn pagination; hoặc
      - Trang kế tiếp không có chương mới.

    Không quét MAX_PAGES một cách mù quáng.
    """
    story_url = normalize_url(story_url)
    current_url = story_url
    visited = set()
    all_by_number = {}
    page_count = 0

    while current_url and current_url not in visited and page_count < MAX_PAGES:
        visited.add(current_url)
        page_count += 1

        soup = get_soup(current_url)
        if soup is None:
            print(f"⚠️ Không tải được page: {current_url}")
            break

        found = parse_chapters(current_url, soup)

        added = 0
        for item in found.values():
            no = item["number"]
            if no not in all_by_number:
                all_by_number[no] = item
                added += 1

        print(
            f"📄 Page {page_count}: tìm {len(found)} chương, "
            f"thêm {added}, tổng {len(all_by_number)}"
        )

        next_url = find_next_page(current_url, soup, visited)
        if not next_url:
            break

        # Kiểm tra trang kế tiếp trước khi chuyển sang đó.
        next_soup = get_soup(next_url)
        if next_soup is None:
            break

        next_found = parse_chapters(next_url, next_soup)

        if not any(item["number"] not in all_by_number for item in next_found.values()):
            print("🛑 Page kế tiếp không có chương mới → dừng quét.")
            break

        current_url = next_url

    chapters = sorted(all_by_number.values(), key=lambda x: x["number"])

    print(f"📚 Tổng cộng: {len(chapters)} chương")
    return chapters


def download_single_chapter(chapter_info):
    url = chapter_info["url"]
    soup = get_soup(url)
    if soup is None:
        return None
    content = None
    for selector in [".chapter-c", "#chapter-c", ".chapter-content", ".reading-content"]:
        element = soup.select_one(selector)
        if element and len(clean_text(element.get_text(" ", strip=True))) > 50:
            content = element
            break
    if content is None:
        return None
    for tag in content.find_all(["script", "style", "iframe", "form", "noscript", "nav"]):
        tag.decompose()
    return str(content)

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
        await status.edit_text(f"📚 **{title}**\n\n🔎 Đang quét danh sách chương...")
        
        chapters = await asyncio.to_thread(collect_chapters, story_url)
        if not chapters:
            await status.edit_text(f"📚 **{title}**\n\n❌ Không quét được danh sách chương.")
            return

        total_chaps = len(chapters)
        await status.edit_text(f"📚 **{title}**\n\n✅ Tìm thấy {total_chaps} chương.\n🚀 Đang tải nội dung tốc độ cao...")

        # Tải song song đa luồng
        downloaded_contents = {}
        last_update_time = time.time()
        loop = asyncio.get_event_loop()
        
        for i in range(0, total_chaps, CONCURRENT_DOWNLOADS):
            batch = chapters[i:i + CONCURRENT_DOWNLOADS]
            tasks = [loop.run_in_executor(None, download_single_chapter, c) for c in batch]
            results = await asyncio.gather(*tasks)
            
            for idx, content in enumerate(results):
                chap_idx = i + idx
                if content:
                    downloaded_contents[chap_idx] = content

            # Cập nhật phần trăm tiến độ
            if time.time() - last_update_time > 4 or (i + CONCURRENT_DOWNLOADS) >= total_chaps:
                completed = min(i + CONCURRENT_DOWNLOADS, total_chaps)
                percent = int((completed / total_chaps) * 100)
                try:
                    await status.edit_text(
                        f"📚 **{title}**\n\n"
                        f"⏳ Đang tải: {completed}/{total_chaps} chương ({percent}%)\n"
                        f"████████▒▒ {percent}%"
                    )
                except Exception:
                    pass
                last_update_time = time.time()

        await status.edit_text(f"📚 **{title}**\n\n📦 Đang đóng gói file EPUB...")

        # Tạo file EPUB
        book = epub.EpubBook()
        book.set_identifier("truyenfull-" + str(abs(hash(title))))
        book.set_title(title)
        book.set_language("vi")
        if cover_url:
            res = await asyncio.to_thread(fetch, cover_url)
            if res and len(res.content) > 1000:
                book.set_cover("cover.jpg", res.content)
                
        css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content="body{font-family:sans-serif;line-height:1.8;margin:5%;}h2{text-align:center;}p{text-align:justify;}")
        book.add_item(css)
        epub_chapters = []
        spine = ["nav"]

        for index, chapter in enumerate(chapters):
            content = downloaded_contents.get(index)
            if not content:
                continue
            
            # Xử lý tên chương chuẩn xác
            c_name = clean_text(chapter["name"])
            num = chapter["number"]
            num_str = str(int(num)) if float(num).is_integer() else str(num)
            
            if not c_name or c_name.lower() in ["chương", "chuong"]:
                c_name = f"Chương {num_str}"

            html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{c_name}</title><link rel='stylesheet' href='style.css'></head><body><h2>{c_name}</h2>{content}</body></html>"
            item = epub.EpubHtml(title=c_name, file_name=f"chapter_{index+1}.xhtml", lang="vi")
            item.content = html
            item.add_item(css)
            book.add_item(item)
            epub_chapters.append(item)
            spine.append(item)

        book.toc = tuple(epub_chapters)
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        
        filename = (re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen") + ".epub"
        await asyncio.to_thread(epub.write_epub, filename, book)

        await status.edit_text(f"📚 **{title}**\n\n✅ Tải thành công {len(epub_chapters)}/{total_chaps} chương!\n📤 Đang gửi file...")
        
        with open(filename, "rb") as f:
            await update.message.reply_document(
                document=f, 
                filename=os.path.basename(filename), 
                caption=f"📚 {title}\n📖 {len(epub_chapters)} chương\n✅ EPUB hoàn tất!"
            )
        await status.delete()
        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        print(f"❌ Lỗi: {e}")
        try:
            await status.edit_text(f"❌ Có lỗi xảy ra trong quá trình xử lý:\n`{e}`")
        except Exception:
            pass

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    
    if not BOT_TOKEN:
        print("❌ Chưa cấu hình BOT_TOKEN trên Render Environment Variables!")
        return

    print("🤖 Đang khởi tạo Bot Telegram...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Bot đã sẵn sàng chạy!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
