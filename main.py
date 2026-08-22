import os
import re
import time
import threading
import requests
from flask import Flask
from bs4 import BeautifulSoup
from ebooklib import epub
from urllib.parse import urljoin, urldefrag
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ============================================================
# 🔑 DÁN TOKEN BOTFATHER CỦA BẠN VÀO ĐÂY
# ============================================================
BOT_TOKEN = "8761120605:AAGGOEpFEQRZChufPR454jOgCdHb_OTD8vs"

# --- WEB SERVER GIẢ LẬP ĐỂ RENDER CHẠY 24/7 KHÔNG BÁO LỖI PORT ---
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot TruyenFull đang hoạt động 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# --- CẤU HÌNH CÀO DỮ LIỆU TỪ COLAB ---
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

session = requests.Session()
session.headers.update(HEADERS)

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
    # 1. Ưu tiên span.chapter-text
    span = a.select_one(".chapter-text")
    if span:
        name = clean_text(span.get_text(" ", strip=True))
        if name:
            return name

    # 2. Ưu tiên title thuộc tính của <a>
    title = clean_text(a.get("title"))
    if title:
        return title

    # 3. Ưu tiên text của <a>
    text = clean_text(a.get_text(" ", strip=True))
    if text:
        return text

    # 4. Lấy từ URL nếu không tìm thấy text
    match = re.search(r"/chuong[-_](\d+(?:[-_]\d+)?)[^/]*", href, flags=re.I)
    if match:
        return f"Chương {match.group(1)}"

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
    patterns = [r"/page[-/](\d+)", r"/trang[-_](\d+)", r"[?&]page=(\d+)"]
    for pattern in patterns:
        match = re.search(pattern, url, flags=re.I)
        if match:
            return int(match.group(1))
    return None

def find_pagination_links(page_url, soup):
    page_urls = set()
    if soup is None:
        return []

    pagination = soup.select_one(".pagination") or soup.select_one("#pagination")
    if pagination:
        for a in pagination.find_all("a", href=True):
            href = urljoin(page_url, a.get("href"))
            href = normalize_url(href)
            if href:
                page_urls.add(href)

    return list(page_urls)

def get_all_chapters(story_url):
    all_chapters_dict = {}
    visited_pages = set()
    pages_to_visit = [normalize_url(story_url)]

    page_count = 0
    while pages_to_visit and page_count < 200:
        current_page = pages_to_visit.pop(0)
        if current_page in visited_pages:
            continue

        visited_pages.add(current_page)
        page_count += 1

        try:
            res = session.get(current_page, timeout=15)
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, 'lxml')
            
            page_chaps = parse_chapters(current_page, soup)
            all_chapters_dict.update(page_chaps)

            new_pages = find_pagination_links(current_page, soup)
            for p_url in new_pages:
                if p_url not in visited_pages and p_url not in pages_to_visit:
                    pages_to_visit.append(p_url)

            pages_to_visit.sort(key=lambda u: get_page_number(u) or 1)
            time.sleep(0.2)

        except Exception:
            continue

    sorted_chapters = sorted(all_chapters_dict.values(), key=lambda x: x['number'])
    return sorted_chapters

def get_cover_image(soup):
    try:
        books_div = soup.find('div', class_='books')
        if books_div and books_div.find('img'):
            img_url = books_div.find('img')['src']
            res = session.get(img_url, timeout=10)
            if res.status_code == 200:
                return res.content
    except Exception:
        pass
    return None

def get_chapter_content(chapter_url):
    try:
        res = session.get(chapter_url, timeout=15)
        if res.status_code != 200:
            return ""

        soup = BeautifulSoup(res.text, 'lxml')
        content_div = soup.find('div', class_='chapter-c')

        if content_div:
            for ads in content_div.find_all(['script', 'ins', 'div', 'iframe']):
                ads.decompose()
            text = content_div.decode_contents()
            text = text.replace('<br>', '<br/>').replace('<br/>', '</p><p>')
            return f"<p>{text}</p>"
    except Exception:
        pass
    return ""

def create_epub(story_title, chapters, cover_bytes, output_filename):
    book = epub.EpubBook()
    book.set_title(story_title)
    book.set_language('vi')

    if cover_bytes:
        book.set_cover("cover.jpg", cover_bytes)

    epub_chapters = []
    spine = ['nav']

    for idx, chap in enumerate(chapters, 1):
        content = get_chapter_content(chap['url'])
        if not content:
            continue

        c = epub.EpubHtml(
            title=chap['name'],
            file_name=f'chap_{idx}.xhtml',
            lang='vi'
        )
        c.content = f"<h2>{chap['name']}</h2>{content}"

        book.add_item(c)
        epub_chapters.append(c)
        spine.append(c)

    # TẠO MỤC LỤC CHUẨN ĐỂ ĐỌC TRÊN TELEPHONE / KINDLE
    book.toc = tuple(epub_chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    epub.write_epub(output_filename, book, {})
    return output_filename

# --- XỬ LÝ LỆNH BOT TELEGRAM ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "truyenfull" not in url:
        await update.message.reply_text("⚠️ Vui lòng gửi link trang chủ của bộ truyện trên TruyenFull!")
        return

    msg = await update.message.reply_text("🔍 Đang quét phân trang để thu thập toàn bộ chương...")

    try:
        res = session.get(url, timeout=15)
        soup = BeautifulSoup(res.text, 'lxml')

        title_tag = soup.find('h3', class_='title')
        story_title = title_tag.text.strip() if title_tag else "Truyện TruyenFull"
        cover_bytes = get_cover_image(soup)

        chapters = get_all_chapters(url)
        if not chapters:
            await msg.edit_text("❌ Không tìm thấy chương nào!")
            return

        total = len(chapters)
        await msg.edit_text(f"📚 Tìm thấy TỔNG CỘNG {total} chương!\n⏳ Đang cào nội dung & đóng gói file ePub có bìa + mục lục...")

        clean_title = re.sub(r'[\\/*?:"<>|]', "", story_title)
        file_name = f"{clean_title}.epub"

        create_epub(story_title, chapters, cover_bytes, file_name)

        await update.message.reply_text(f"✅ Hoàn tất bộ truyện: **{story_title}** ({total} chương).\nĐang gửi file qua cho bạn...")
        with open(file_name, 'rb') as f:
            await update.message.reply_document(document=f, filename=file_name)

        if os.path.exists(file_name):
            os.remove(file_name)

    except Exception as e:
        await update.message.reply_text(f"❌ Có lỗi xảy ra: {str(e)}")

def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot TruyenFull Colab-Paginated Engine đang chạy...")
    app.run_polling()

if __name__ == '__main__':
    main()
