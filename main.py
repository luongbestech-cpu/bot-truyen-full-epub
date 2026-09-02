import asyncio
import os
import re
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, urljoin, urldefrag
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
        self.wfile.write("Bot TruyenFull & Wikidich đang hoạt động!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# ============================================================
# CẤU HÌNH BOT & CHỐNG TƯỜNG LỬA (CLOUDSCRAPER)
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def get_soup(url):
    try:
        response = scraper.get(url, timeout=35)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        print(f"Lỗi tải {url}: {e}")
    return None

def extract_chapter_number(name):
    """Trích xuất số từ tên chương để sắp xếp chuẩn xác, đẩy chương cuối và ngoại truyện xuống cuối"""
    name_lower = name.lower()
    # Nếu tên có chữ "cuối", "ngoại truyện", "extra", đẩy xuống cuối cùng
    if "cuối" in name_lower or "ngoại" in name_lower or "ngoai" in name_lower or "extra" in name_lower:
        return 999999
        
    numbers = re.findall(r'\d+', name)
    if numbers:
        return int(numbers[0])
    return 0

def download_chapter(url):
    soup = get_soup(url)
    if not soup: return None
    
    content = (soup.select_one(".chapter-content") or 
               soup.select_one("#chapter-content") or
               soup.select_one("#chapter-c") or 
               soup.select_one(".chapter-c") or 
               soup.select_one(".entry-content") or 
               soup.select_one(".post-content"))
               
    if not content: return None
    
    for tag in content.find_all(["script", "style", "div", "ins", "iframe", "button"]):
        tag.decompose()
        
    return str(content)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url_match = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url_match: return
    
    status = await update.message.reply_text("⏳ Đang kết nối, nhận diện nguồn và quét danh sách chương...")
    story_url = url_match[0].strip()
    
    main_soup = get_soup(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối tới trang truyện. Web có thể đang chặn hoặc sai link.")
        return
        
    title_el = main_soup.select_one("h1") or main_soup.select_one(".title")
    title = title_el.get_text().strip() if title_el else "Truyện"
    if "|" in title:
        title = title.split('|')[0].strip()
        
    cover_url = None
    img_tag = main_soup.select_one(".book img") or main_soup.select_one(".truyen-info img") or main_soup.select_one(".book-info img") or main_soup.select_one("article img")
    if img_tag:
        cover_url = img_tag.get('data-src') or img_tag.get('src')

    parsed_url = urlparse(story_url)
    base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    story_path = parsed_url.path.rstrip('/')

    links = []
    is_wikidich = "wikidich" in story_url.lower()

    if is_wikidich:
        max_page = 1
        pagination = main_soup.select(".pagination a, .page-item a")
        for p in pagination:
            try:
                val = int(p.get_text().strip())
                if val > max_page: max_page = val
            except:
                pass

        base_clean_url = story_url.split("?")[0].rstrip("/")
        for page in range(1, max_page + 1):
            page_url = f"{base_clean_url}?page={page}" if page > 1 else base_clean_url
            soup = main_soup if page == 1 else get_soup(page_url)
            if not soup: continue

            chapter_tags = soup.select("a")
            for a in chapter_tags:
                href = a.get('href', '')
                if href:
                    full_url = href if href.startswith("http") else urljoin(page_url, href)
                    text = a.get_text().strip()
                    is_chap = re.match(r"^(chương|chuong|hồi|hoi|quyển|quyen|c\s*\d+|\d+|phần|phan)", text, flags=re.IGNORECASE)
                    
                    if is_chap and len(text) < 80:
                        if ("chuong-" in full_url or "/chap-" in full_url or "id=" in full_url) and not any(l['url'] == full_url for l in links):
                            links.append({"name": text, "url": full_url})
            time.sleep(0.2)
    else:
        current_page_url = story_url
        page_num = 1
        
        while current_page_url:
            soup = get_soup(current_page_url)
            if not soup: break
                
            chapter_tags = soup.select("#list-chapter a, .list-chapter a, .chapter-list a, .zaraz-list a")
            if not chapter_tags:
                break
                
            new_chapters_in_page = 0
            for a in chapter_tags:
                href = a.get('href', '')
                if href:
                    full_url = href if href.startswith("http") else base_domain + href
                    if story_path not in full_url:
                        continue

                    text = a.get_text().strip()
                    if text and not any(l['url'] == full_url for l in links):
                        links.append({"name": text, "url": full_url})
                        new_chapters_in_page += 1
                        
            if new_chapters_in_page == 0:
                break
                
            pagination_links = soup.select(".pagination a, .pages a")
            next_url = None
            for p_link in pagination_links:
                text_p = p_link.get_text().strip()
                if "Trang sau" in text_p or ">" in text_p or str(page_num + 1) == text_p:
                    next_url = p_link.get('href')
                    break
                    
            if next_url:
                next_full_url = next_url if next_url.startswith("http") else base_domain + next_url
                if next_full_url == current_page_url: break
                current_page_url = next_full_url
                page_num += 1
            else:
                if page_num < 40:
                    guessed_url = f"{story_url.rstrip('/')}/trang-{page_num + 1}/"
                    current_page_url = guessed_url
                    page_num += 1
                else:
                    break
            time.sleep(0.3)

    # Sắp xếp lại thứ tự chương chuẩn số học (đã sửa lỗi Chương cuối bị nhảy lên đầu)
    links.sort(key=lambda x: extract_chapter_number(x['name']))

    if not links:
        await status.edit_text("❌ Không tìm thấy chương nào hoặc link không hợp lệ.")
        return
        
    await status.edit_text(f"📚 {title}\n✅ Quét thành công {len(links)} chương. Đang tải nội dung...")
    
    book = epub.EpubBook()
    book.set_identifier('truyen_' + re.sub(r'\W+', '', title))
    book.set_title(title)
    book.set_language('vi')
    
    if cover_url:
        try:
            full_cover_url = cover_url if cover_url.startswith("http") else base_domain + cover_url
            img_data = scraper.get(full_cover_url, timeout=15).content
            book.set_cover("cover.jpg", img_data)
        except Exception as e:
            print(f"Lỗi tải ảnh bìa: {e}")

    chapters_list = []
    success_count = 0
    
    for i, item in enumerate(links):
        content = download_chapter(item['url'])
        if content:
            chap = epub.EpubHtml(title=item['name'], file_name=f"chap_{i+1}.xhtml")
            chap.content = f"<h2>{item['name']}</h2>{content}"
            book.add_item(chap)
            chapters_list.append(chap)
            success_count += 1
        
        if i % 15 == 0 or i == len(links) - 1:
            pct = int((i / len(links)) * 100)
            try:
                await status.edit_text(f"📚 {title}\n⏳ Đang tải: {pct}%\n({i+1}/{len(links)})")
            except:
                pass
        time.sleep(0.2)

    if success_count == 0:
        await status.edit_text("❌ Tải thất bại do trang web chặn toàn bộ nội dung.")
        return

    book.toc = tuple(chapters_list)
    book.spine = ['nav'] + chapters_list

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen"
    file_name = f"{safe_title}.epub"
    epub.write_epub(file_name, book)
    
    await status.edit_text("⬆️ Đang gửi file EPUB qua Telegram...")
    with open(file_name, "rb") as f:
        await update.message.reply_document(
            document=f, 
            caption=f"✅ Xong: {title}\n📖 {success_count}/{len(links)} chương + Sắp xếp chuẩn xác (Đã fix lỗi Chương cuối)!"
        )

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
