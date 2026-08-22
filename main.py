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

# Lấy Token từ biến môi trường Render của bạn
BOT_TOKEN = os.environ.get("BOT_TOKEN_TRUYENFULL", "ĐIỀN_TOKEN_THẬT_VÀO_ĐÂY")

# --- WEB SERVER DÙNG CHO RENDER ---
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot TruyenFull Engine đang hoạt động 24/7!"

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

def get_chapter_name_smart(a, href):
    """Trích xuất tên chương chính xác, không bao giờ để trơ trọi chữ 'Chương'"""
    title_attr = clean_text(a.get("title"))
    text_attr = clean_text(a.get_text(" ", strip=True))
    
    # 1. Nếu text hoặc title có chứa số thì dùng luôn
    if title_attr and re.search(r"\d+", title_attr):
        return title_attr

    if text_attr and re.search(r"\d+", text_attr):
        return text_attr

    # 2. Bắt số chương từ URL nếu text bị lỗi trơ trọi chữ "Chương"
    match = re.search(r"/chuong[-_](\d+(?:[-_]\d+)?)[^/]*", href, flags=re.I)
    if match:
        chap_num = match.group(1).replace("-", ".")
        if text_attr and text_attr.lower() != "chương":
            return f"Chương {chap_num}: {text_attr}"
        return f"Chương {chap_num}"

    return text_attr or title_attr or "Chương"

def get_all_chapters_full(story_url):
    """Lấy 100% danh sách chương, không bị kẹt ở 50 chương"""
    chapters = []
    seen_urls = set()
    base_url = normalize_url(story_url)

    try:
        res = session.get(base_url, timeout=10)
        if res.status_code != 200:
            return []

        soup = BeautifulSoup(res.text, 'lxml')
        
        # Thử phương pháp 1: Lấy thông qua AJAX nếu có truyen-id
        story_id_input = soup.find('input', id='truyen-id')
        if story_id_input:
            story_id = story_id_input.get('value')
            ajax_first = session.get(f"{base_url}/ajax-list-chap/?truyen_id={story_id}&page=1", timeout=10)
            if ajax_first.status_code == 200:
                ajax_soup = BeautifulSoup(ajax_first.text, 'lxml')
                
                # Tìm trang tối đa từ AJAX
                max_page = 1
                pagination = ajax_soup.select_one(".pagination") or ajax_soup.select_one("#pagination")
                if pagination:
                    for a in pagination.find_all("a", href=True):
                        match = re.search(r"trang-(\d+)|page=(\d+)", a.get("href", ""), flags=re.I)
                        if match:
                            p = int(match.group(1) or match.group(2))
                            if p > max_page:
                                max_page = p

                # Lặp qua toàn bộ các trang AJAX
                for p in range(1, max_page + 1):
                    r = session.get(f"{base_url}/ajax-list-chap/?truyen_id={story_id}&page={p}", timeout=10)
                    if r.status_code == 200:
                        p_soup = BeautifulSoup(r.text, 'lxml')
                        for a in p_soup.find_all('a', href=True):
                            href = normalize_url(urljoin(base_url, a.get('href')))
                            if href and href not in seen_urls and "/chuong-" in href:
                                seen_urls.add(href)
                                title = get_chapter_name_smart(a, href)
                                chapters.append({'name': title, 'url': href})
                
                if chapters:
                    return format_final_chapters(chapters)

        # Phương pháp 2: Quét phân trang HTML trực tiếp (Fallback)
        max_page = 1
        pagination = soup.select_one(".pagination") or soup.select_one("#pagination")
        if pagination:
            for a in pagination.find_all("a", href=True):
                match = re.search(r"trang-(\d+)", a.get("href", ""), flags=re.I)
                if match:
                    p = int(match.group(1))
                    if p > max_page:
                        max_page = p

        for page in range(1, max_page + 1):
            page_url = base_url if page == 1 else f"{base_url}/trang-{page}/"
            res_page = session.get(page_url, timeout=10)
            if res_page.status_code == 200:
                p_soup = BeautifulSoup(res_page.text, 'lxml')
                for ul in p_soup.find_all('ul', class_='list-chapter'):
                    for a in ul.find_all('a', href=True):
                        href = normalize_url(urljoin(page_url, a.get('href')))
                        if href and href not in seen_urls:
                            seen_urls.add(href)
                            title = get_chapter_name_smart(a, href)
                            chapters.append({'name': title, 'url': href})

    except Exception as e:
        print("Lỗi quét danh sách chương:", e)

    return format_final_chapters(chapters)

def format_final_chapters(chapters):
    formatted = []
    for idx, chap in enumerate(chapters, 1):
        t = chap['name']
        if t.strip().lower() == "chương":
            t = f"Chương {idx}"
        formatted.append({'name': t, 'url': chap['url']})
    return formatted

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

    with ThreadPoolExecutor(max_workers=10) as executor:
        fetched_results = list(executor.map(fetch_single_chapter, chapters))

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

    msg = await update.message.reply_text("🔍 Đang quét toàn bộ danh sách chương...")

    try:
        res = session.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'lxml')

        title_tag = soup.find('h3', class_='title')
        story_title = title_tag.text.strip() if title_tag else "Truyện TruyenFull"
        cover_bytes = get_cover_image(soup)

        chapters = get_all_chapters_full(url)
        if not chapters:
            await msg.edit_text("❌ Không tìm thấy chương nào!")
            return

        total = len(chapters)
        await msg.edit_text(f"⚡ Đã quét xong! Tìm thấy TỔNG CỘNG {total} chương.\n🚀 Đang cào dữ liệu & tạo file EPUB...")

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

    if not BOT_TOKEN or "ĐIỀN_TOKEN" in BOT_TOKEN:
        print("❌ LỖI: Bạn chưa cài đặt BOT_TOKEN_TRUYENFULL!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot TruyenFull đang hoạt động...")
    app.run_polling()

if __name__ == '__main__':
    main()
