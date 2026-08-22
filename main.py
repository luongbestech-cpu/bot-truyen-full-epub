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
        response = scraper.get(url, timeout=30)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        print(f"Lỗi lấy soup {url}: {e}")
    return None

def download_chapter(url):
    soup = get_soup(url)
    if not soup: return None
    
    content = soup.select_one(".chapter-content") or soup.select_one("#chapter-c") or soup.select_one(".chapter-c")
    if not content: return None
    
    for tag in content.find_all(["script", "style", "div", "ins", "iframe"]):
        tag.decompose()
        
    return str(content)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang kết nối TruyenFull (Quét toàn bộ danh sách chương)...")
    
    story_url = url[0].strip('/')
    main_soup = get_soup(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối tới trang truyện. Web có thể đang chặn mạnh.")
        return
        
    title_el = main_soup.select_one("h1") or main_soup.select_one(".title")
    title = title_el.get_text().strip() if title_el else "Truyện"
    
    # Lấy ảnh bìa
    cover_url = None
    img_tag = main_soup.select_one(".book img") or main_soup.select_one(".truyen-info img")
    if img_tag and img_tag.get('src'):
        cover_url = img_tag['src']

    links = []
    domain = "https://" + story_url.split('/')[2]

    # --- THUẬT TOÁN QUÉT ĐA TRANG (PHÂN TRANG TRUYENFULL) ---
    # Lấy các link phân trang nếu có (ví dụ: ?page=1, ?page=2...) hoặc quét qua ajax/api mục lục của trang
    pages_to_crawl = [story_url]
    
    # Tìm xem có thanh phân trang pagination không để lấy tất cả các trang danh sách chương
    pagination = main_soup.select_one(".pagination") or main_soup.select(".pages")
    if pagination:
        page_links = pagination.find_all("a", href=True)
        for a in page_links:
            p_href = a['href']
            full_p_url = p_href if p_href.startswith("http") else domain + p_href
            if full_p_url not in pages_to_crawl:
                pages_to_crawl.append(full_p_url)

    # Nếu web dùng dạng AJAX lấy id truyện, ta thử tìm ajax chapter list (nếu có cấu trúc đặc trưng)
    # Hoặc tiến hành quét qua danh sách các trang phân trang thu được:
    for page_url in pages_to_crawl:
        soup_p = main_soup if page_url == story_url else get_soup(page_url)
        if not soup_p: continue
        
        chapter_tags = soup_p.select("#list-chapter a, .list-chapter a, .chapter-list a")
        if not chapter_tags:
            chapter_tags = [a for a in soup_p.find_all("a", href=True) if "chuong-" in a['href'] or "hoi-" in a['href']]

        for a in chapter_tags:
            href = a.get('href', '')
            if href:
                full_url = href if href.startswith("http") else domain + href
                text = a.get_text().strip()
                # Kiểm tra xem có đúng là link chương truyện không
                if text and ("chuong-" in full_url or "hoi-" in full_url or re.search(r'\d+', text)):
                    if not any(l['url'] == full_url for l in links):
                        links.append({"name": text, "url": full_url})
        
        # Tránh gửi request quá nhanh gây quá tải
        if len(pages_to_crawl) > 1:
            time.sleep(0.5)

    if not links:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy kiểm tra lại đường dẫn truyện.")
        return
        
    await status.edit_text(f"📚 {title}\n✅ Tìm thấy tổng cộng {len(links)} chương. Đang tiến hành tải nội dung...")
    
    book = epub.EpubBook()
    book.set_identifier('truyenfull_' + re.sub(r'\W+', '', title))
    book.set_title(title)
    book.set_language('vi')
    
    # Thêm ảnh bìa
    if cover_url:
        try:
            full_cover_url = cover_url if cover_url.startswith("http") else domain + cover_url
            img_data = scraper.get(full_cover_url, timeout=15).content
            book.set_cover("cover.jpg", img_data)
        except Exception as e:
            print(f"Lỗi tải ảnh bìa TruyenFull: {e}")

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

    # Cấu hình Mục lục (TOC) chuẩn Kindle
    book.toc = tuple(chapters_list)
    book.spine = ['nav'] + chapters_list

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen"
    file_name = f"{safe_title}.epub"
    
    epub.write_epub(file_name, book)
    
    await update.message.reply_document(
        document=open(file_name, "rb"), 
        caption=f"✅ Xong: {title}\n📖 Đã tải thành công {success_count}/{len(links)} chương + Ảnh bìa & Mục lục chuẩn Kindle!"
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
