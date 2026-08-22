import os
import re
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
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

# --- WEB SERVER GIẢ LẬP ĐỂ RENDER CHẠY 24/7 ---
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot TruyenFull Fast-Engine đang hoạt động 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
        return f"Chương {match.group(1)}"

    return "Chương"

def get_all_chapters_fast(story_url):
    """ Quét tăng dần từ trang 1, 2, 3... Không có chương mới -> DỪNG NGAY """
    chapters = []
    seen_urls = set()
    base_url = normalize_url(story_url)

    page = 1
    while page <= 2000:
        page_url = base_url if page == 1 else f"{base_url}/trang-{page}/"

        try:
            res = session.get(page_url, timeout=10)
            if res.status_code != 200:
                break

            soup = BeautifulSoup(res.text, 'lxml')
            chapter_list = soup.find_all('ul', class_='list-chapter')

            new_found = 0
            for ul in chapter_list:
                for a in ul.find_all('a', href=True):
                    href = urljoin(page_url, a.get('href'))
                    href = normalize_url(href)

                    if href and href not in seen_urls:
                        seen_urls.add(href)
                        c_title = get_chapter_name(a, href)
                        chapters.append({'name': c_title, 'url': href})
                        new_found += 1

            # NẾU TRANG HIỆN TẠI KHÔNG TÌM THẤY CHƯƠNG MỚI -> DỪNG NGAY LẬP TỨC
            if new_found == 0:
                break

            page += 1
            time.sleep(0.1)

        except Exception:
            break

    return chapters

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

def fetch_single_chapter(chap_info):
    try:
        res = session.get(chap_info['url'], timeout=10)
        if res.status_code != 200:
            return chap_info, ""

        soup = BeautifulSoup(res.text, 'lxml')
        content_div = soup.find('div', class_='chapter-c')

        if content_div:
            for ads in content_div.find_all(['script', 'ins', 'div', 'iframe']):
                ads.decompose()
            text = content_div.decode_contents()
            text = text.replace('<br>', '<br/>').replace('<br/>', '</p><p>')
            return chap_info, f"<p>{text}</p>"
    except Exception:
        pass
    return chap_info, ""

def create_epub(story_title, chapters, cover_bytes, output_filename):
    book = epub.EpubBook()
    book.set_title(story_title)
    book.set_language('vi')

    if cover_bytes:
        book.set_cover("cover.jpg", cover_bytes)

    # CÀO NỘI DUNG ĐA LUỒNG (10 LUỒNG SONG SONG)
    fetched_results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_single_chapter, chapters))
        fetched_results = results

    epub_chapters = []
    spine = ['nav']

    for idx, (chap_info, content) in enumerate(fetched_results, 1):
        if not content:
            continue

        c = epub.EpubHtml(
            title=chap_info['name'],
            file_name=f'chap_{idx}.xhtml',
            lang='vi'
        )
        c.content = f"<h2>{chap_info['name']}</h2>{content}"

        book.add_item(c)
        epub_chapters.append(c)
        spine.append(c)

    # ĐÓNG GÓI MỤC LỤC EPUB
    book.toc = tuple(epub_chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    epub.write_epub(output_filename, book, {})
    return output_filename

# --- XỬ LÝ TELEGRAM BOT ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "truyenfull" not in url:
        await update.message.reply_text("⚠️ Vui lòng gửi link trang chủ của bộ truyện trên TruyenFull!")
        return

    msg = await update.message.reply_text("🔍 Đang quét danh sách chương (chế độ dừng ngay khi hết trang)...")

    try:
        res = session.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'lxml')

        title_tag = soup.find('h3', class_='title')
        story_title = title_tag.text.strip() if title_tag else "Truyện TruyenFull"
        cover_bytes = get_cover_image(soup)

        chapters = get_all_chapters_fast(url)
        if not chapters:
            await msg.edit_text("❌ Không tìm thấy chương nào!")
            return

        total = len(chapters)
        await msg.edit_text(f"⚡ Tìm thấy TỔNG CỘNG {total} chương!\n🚀 Đang cào nội dung siêu tốc & đóng gói file ePub...")

        clean_title = re.sub(r'[\\/*?:"<>|]', "", story_title)
        file_name = f"{clean_title}.epub"

        create_epub(story_title, chapters, cover_bytes, file_name)

        await update.message.reply_text(f"✅ Hoàn tất bộ truyện: **{story_title}** ({total} chương).\nĐang gửi file qua Telegram...")
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
    print("Bot TruyenFull Fast-Engine đang chạy...")
    app.run_polling()

if __name__ == '__main__':
    main()
