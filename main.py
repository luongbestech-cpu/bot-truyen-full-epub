import asyncio
import os
import re
import time
import threading
from urllib.parse import urldefrag, urljoin
from bs4 import BeautifulSoup
from ebooklib import epub
import nest_asyncio
import requests
from flask import Flask
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ============================================================
# 🔑 LẤY TOKEN TỪ ENVIRONMENT VARIABLE TRÊN RENDER
# ============================================================
BOT_TOKEN = os.getenv("bot_token_truyenfull") or os.getenv("BOT_TOKEN_TRUYENFULL")

# ============================================================
# 🌐 FLASK WEB SERVER DÙNG CHO RENDER (GIỮ APP LIVE 24/7)
# ============================================================
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot TruyenFull Colab-Engine đang chạy 24/7 trên Render!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# ============================================================
# CẤU HÌNH
# ============================================================
REQUEST_TIMEOUT = 15
MAX_PAGES = 2000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
session = requests.Session()
session.headers.update(HEADERS)

# ============================================================
# REQUEST
# ============================================================
def fetch(url, timeout=REQUEST_TIMEOUT):
    try:
        response = session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response
    except Exception as e:
        print(f"⚠️ Không tải được: {url}")
        print(f"   {type(e).__name__}: {e}")
        return None

def get_soup(url):
    response = fetch(url)
    if response is None:
        return None
    return BeautifulSoup(response.text, "lxml")

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def normalize_url(url):
    if not url:
        return ""
    url = urldefrag(url)[0]
    return url.rstrip("/")

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
        number = match.group(1)
        return "Chương " + number
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
    containers = []
    main = soup.select_one("#list-chapter")
    if main:
        containers.append(main)
    if not containers:
        containers = soup.select(".list-chapter")
    if not containers:
        containers = [soup]
    for container in containers:
        for a in container.find_all("a", href=True):
            href = urljoin(page_url, a.get("href"))
            href = normalize_url(href)
            if not href:
                continue
            name = get_chapter_name(a, href)
            if not is_chapter_link(name, href):
                continue
            number = extract_chapter_number(name + " " + href)
            if number is None:
                continue
            result[href] = {
                "name": name,
                "url": href,
                "number": number,
            }
    return result

def get_page_number(url):
    patterns = [
        r"/page[-/](\d+)",
        r"/trang[-_](\d+)",
        r"[?&]page=(\d+)",
    ]
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
    selectors = [
        ".pagination",
        "ul.pagination",
        ".page-navigation",
        ".pager",
        "nav",
    ]
    nodes = []
    for selector in selectors:
        found = soup.select(selector)
        if found:
            nodes.extend(found)
    if not nodes:
        nodes = [soup]
    for node in nodes:
        for a in node.find_all("a", href=True):
            href = urljoin(page_url, a.get("href"))
            href = normalize_url(href)
            if not href:
                continue
            text = clean_text(a.get_text(" ", strip=True)).lower()
            if is_chapter_link(text, href):
                continue
            low = href.lower()
            is_page = False
            if (
                re.search(r"/page[-/]?\d+", low)
                or re.search(r"/trang[-_]\d+", low)
                or re.search(r"[?&]page=\d+", low)
            ):
                is_page = True
            if text.isdigit():
                n = int(text)
                if 1 <= n <= MAX_PAGES:
                    is_page = True
            if text in {
                "next",
                "tiếp",
                "trang sau",
                "sau",
                "»",
                "›",
                "→",
            }:
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
        return link
    return None

def get_story_info(story_url):
    soup = get_soup(story_url)
    if soup is None:
        raise RuntimeError("Không truy cập được trang truyện.")
    title = ""
    selectors = [
        "h1.title",
        "h1",
        ".title-book",
        ".book-title",
        ".truyen-title",
        "meta[property='og:title']",
        "title",
    ]
    for selector in selectors:
        element = soup.select_one(selector)
        if not element:
            continue
        if element.name == "meta":
            title = clean_text(element.get("content", ""))
        else:
            title = clean_text(element.get_text(" ", strip=True))
        if title:
            break
    if not title:
        title = "Truyện"
    cover_url = None
    selectors = [
        "meta[property='og:image']",
        ".book img",
        ".book-info img",
        ".book-cover img",
        ".info img",
        ".thumbnail img",
    ]
    for selector in selectors:
        element = soup.select_one(selector)
        if not element:
            continue
        if element.name == "meta":
            src = element.get("content")
        else:
            src = (
                element.get("src")
                or element.get("data-src")
                or element.get("data-original")
            )
        if src:
            cover_url = urljoin(story_url, src)
            break
    return title, cover_url, soup

def collect_chapters(story_url):
    print("\n" + "=" * 60 + "\n🔎 BẮT ĐẦU QUÉT CHƯƠNG\n" + "=" * 60)
    story_url = normalize_url(story_url)
    current_url = story_url
    visited = set()
    all_chapters = {}
    page_count = 0
    while True:
        if current_url in visited:
            print("🛑 Page này đã được quét → DỪNG.")
            break
        if page_count >= MAX_PAGES:
            print(f"🛑 Đạt giới hạn an toàn {MAX_PAGES} page.")
            break
        visited.add(current_url)
        page_count += 1
        print(f"\n🔎 PAGE {page_count}: {current_url}")
        soup = get_soup(current_url)
        if soup is None:
            print("⚠️ Không tải được page.\n🛑 DỪNG QUÉT.")
            break
        found = parse_chapters(current_url, soup)
        before = len(all_chapters)
        for url, item in found.items():
            if url not in all_chapters:
                all_chapters[url] = item
        new_count = len(all_chapters) - before
        print(f"📖 Page này có {len(found)} link")
        print(f"➕ Chương mới: {new_count}")
        print(f"📚 Tổng hiện tại: {len(all_chapters)}")
        if new_count == 0:
            print("\n🛑 PAGE NÀY KHÔNG CÓ CHƯƠNG MỚI.\n🛑 DỪNG QUÉT TẠI ĐÂY.")
            break
        next_url = find_next_page(current_url, soup, visited)
        if not next_url:
            print(
                "\n🛑 WEBSITE KHÔNG CÓ PAGE TIẾP THEO.\n🛑 ĐÃ QUÉT HẾT."
            )
            break
        print(f"➡️ Page tiếp theo: {next_url}")
        current_url = next_url
        time.sleep(0.3)
    chapters = list(all_chapters.values())
    chapters.sort(key=lambda x: x["number"])
    unique = {}
    for item in chapters:
        number = item["number"]
        if number not in unique:
            unique[number] = item
    chapters = list(unique.values())
    chapters.sort(key=lambda x: x["number"])
    if not chapters:
        print("❌ KHÔNG TÌM ĐƯỢC CHƯƠNG.")
        return []
    integer_numbers = set(int(x["number"]) for x in chapters)
    max_number = max(integer_numbers)
    missing = [
        n for n in range(1, max_number + 1) if n not in integer_numbers
    ]
    print("\n" + "=" * 60)
    print(f"✅ TỔNG CHƯƠNG: {len(chapters)}")
    print(f"🔢 CHƯƠNG CAO NHẤT: {max_number}")
    print(f"📑 SỐ PAGE ĐÃ QUÉT: {page_count}")
    print("=" * 60)
    print(f"\n📖 CHƯƠNG ĐẦU:\n{chapters[0]['name']}")
    print(f"\n📖 CHƯƠNG CUỐI:\n{chapters[-1]['name']}")
    if missing:
        print("\n⚠️ CÓ CHƯƠNG BỊ THIẾU:")
        if len(missing) <= 100:
            print(missing)
        else:
            print(missing[:100])
            print(f"... còn {len(missing)-100} chương.")
        print("\n🛑 KHÔNG TẠO EPUB.")
        return []
    print(f"\n🎉 ĐÃ XÁC NHẬN ĐỦ CHƯƠNG 1 → {max_number}")
    return chapters

def get_chapter_content(url):
    soup = get_soup(url)
    if soup is None:
        raise RuntimeError("Không mở được trang chương.")
    selectors = [
        ".chapter-c",
        "#chapter-c",
        ".chapter-content",
        "#chapter-content",
        ".chapter-content-wrap",
        ".reading-content",
        ".entry-content",
        ".text-content",
    ]
    content = None
    for selector in selectors:
        element = soup.select_one(selector)
        if not element:
            continue
        text = clean_text(element.get_text(" ", strip=True))
        if len(text) > 100:
            content = element
            break
    if content is None:
        candidates = soup.find_all(["article", "section", "div"])
        best = None
        best_length = 0
        for element in candidates:
            text = clean_text(element.get_text(" ", strip=True))
            if len(text) > best_length and len(text) < 500000:
                best = element
                best_length = len(text)
        content = best
    if content is None:
        raise RuntimeError("Không tìm thấy nội dung chương.")
    for tag in content.find_all(
        ["script", "style", "iframe", "form", "button", "noscript", "nav"]
    ):
        tag.decompose()
    return str(content)

def create_epub(title, cover_url, chapters):
    print("\n" + "=" * 60 + "\n📚 BẮT ĐẦU TẠO EPUB\n" + "=" * 60)
    book = epub.EpubBook()
    book.set_identifier("truyenfull-" + str(abs(hash(title))))
    book.set_title(title)
    book.set_language("vi")
    if cover_url:
        print("🖼️ Đang tải ảnh bìa...")
        response = fetch(cover_url)
        if response is not None and len(response.content) > 1000:
            content_type = response.headers.get("Content-Type", "").lower()
            if "png" in content_type:
                book.set_cover("cover.png", response.content)
            else:
                book.set_cover("cover.jpg", response.content)
            print("✅ Đã lấy ảnh bìa")
    style = """
body { font-family: sans-serif; line-height: 1.8; margin: 5%; }
h2 { text-align: center; margin-bottom: 1.5em; }
p { text-align: justify; margin-bottom: 0.8em; }
"""
    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=style,
    )
    book.add_item(css)
    epub_chapters = []
    spine = ["nav"]
    total = len(chapters)
    success = 0
    for index, chapter in enumerate(chapters, start=1):
        chapter_name = clean_text(chapter["name"])
        if not chapter_name or chapter_name.lower() == "chương":
            number = chapter["number"]
            number_text = (
                str(int(number))
                if float(number).is_integer()
                else str(number)
            )
            chapter_name = "Chương " + number_text
        print(f"📖 [{index}/{total}] {chapter_name}")
        try:
            content = get_chapter_content(chapter["url"])
            html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{chapter_name}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<h2>{chapter_name}</h2>
{content}
</body>
</html>"""
            item = epub.EpubHtml(
                title=chapter_name,
                file_name=f"chapter_{index}.xhtml",
                lang="vi",
            )
            item.content = html
            item.add_item(css)
            book.add_item(item)
            epub_chapters.append(item)
            spine.append(item)
            success += 1
        except Exception as e:
            print(f"   ⚠️ Lỗi chương {index}: {type(e).__name__}: {e}")
        time.sleep(0.15)
    if success != total:
        raise RuntimeError(f"Chỉ tải thành công {success}/{total} chương.")
    book.toc = tuple(epub_chapters)
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen"
    filename = safe_title + ".epub"
    epub.write_epub(filename, book)
    print(
        f"\n"
        + "=" * 60
        + f"\n✅ ĐÃ TẠO EPUB: {filename}\n📖 {success}/{total} chương\n"
        + "=" * 60
    )
    return filename

# ============================================================
# TELEGRAM HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = (update.message.text or "").strip()
    urls = re.findall(r"https?://[^\s]+", text)
    if not urls:
        await update.message.reply_text("❌ Hãy gửi link truyện nhé.")
        return
    story_url = urls[0]
    status = await update.message.reply_text(
        "⏳ Đã nhận link.\n🔎 Đang kiểm tra truyện..."
    )
    try:
        title, cover_url, _ = await asyncio.to_thread(
            get_story_info, story_url
        )
        await status.edit_text(
            f"📚 {title}\n\n🔎 Đang quét danh sách chương..."
        )
        chapters = await asyncio.to_thread(collect_chapters, story_url)
        if not chapters:
            await status.edit_text(
                f"📚 {title}\n\n❌ Không lấy được danh sách chương.\n🛑 EPUB chưa được tạo."
            )
            return
        await status.edit_text(
            f"📚 {title}\n\n✅ Đã xác nhận {len(chapters)} chương.\n📖 Đang tải nội dung..."
        )
        filename = await asyncio.to_thread(
            create_epub, title, cover_url, chapters
        )
        await status.edit_text(
            f"📚 {title}\n\n✅ EPUB đã tạo xong.\n📖 {len(chapters)} chương.\n📦 Đang gửi file..."
        )
        with open(filename, "rb") as file:
            await update.message.reply_document(
                document=file,
                filename=os.path.basename(filename),
                read_timeout=180,
                write_timeout=180,
                connect_timeout=180,
                pool_timeout=180,
                caption=f"📚 {title}\n📖 {len(chapters)} chương\n✅ EPUB hoàn tất",
            )
        try:
            await status.delete()
        except Exception:
            pass
        try:
            os.remove(filename)
        except Exception:
            pass
    except Exception as e:
        print("\n" + "=" * 60 + f"\n❌ LỖI:\n{repr(e)}\n" + "=" * 60)
        try:
            await status.edit_text(
                f"❌ Có lỗi xảy ra.\n\n{type(e).__name__}: {e}"
            )
        except Exception:
            pass

# ============================================================
# KHỞI ĐỘNG BOT (CHẠY TRÊN CLOUD / RENDER)
# ============================================================
nest_asyncio.apply()

async def start_bot():
    print("\n" + "=" * 60 + "\n🤖 BOT ĐANG KHỞI ĐỘNG...\n" + "=" * 60)
    
    if not BOT_TOKEN:
        raise ValueError("❌ Không tìm thấy biến môi trường 'bot_token_truyenfull' hoặc 'BOT_TOKEN_TRUYENFULL'. Vui lòng kiểm tra lại cấu hình trên Render.")
    
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(20)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .build()
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    try:
        await app.initialize()
    except Exception as e:
        print(
            "\n❌ TOKEN KHÔNG HỢP LỆ HOẶC TELEGRAM KHÔNG KẾT NỐI ĐƯỢC."
        )
        print(type(e).__name__, str(e))
        raise
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("\n" + "=" * 60 + "\n✅ BOT ĐÃ SẴN SÀNG!\n" + "=" * 60)
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        try:
            await app.updater.stop()
        except Exception:
            pass
        try:
            await app.stop()
        except Exception:
            pass
        try:
            await app.shutdown()
        except Exception:
            pass

if __name__ == "__main__":
    # Chạy Web Server nền cho Render
    threading.Thread(target=run_flask, daemon=True).start()
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        print("Bot đã dừng.")
