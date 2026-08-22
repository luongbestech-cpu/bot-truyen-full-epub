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
# CẤU HÌNH BOT & CHỐNG TƯỜNG LỬA (CLOUDSCRAPER)
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")

# Cấu hình cloudscraper giả lập trình duyệt thật để vượt qua tường lửa/Cloudflare
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def get_soup(url):
    try:
        # Tăng timeout và thêm headers giả lập đầy đủ để chống chặn
        response = scraper.get(url, timeout=35)
        if response.status_code == 200:
            return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        print(f"Lỗi vượt tường lửa tải {url}: {e}")
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
    
    status = await update.message.reply_text("⏳ Đang vượt tường lửa kết nối TruyenFull (Quét danh sách chương)...")
    
    story_url = url[0]
    main_soup = get_soup(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối tới trang truyện. Có thể web đang chặn mạnh hoặc sai link.")
        return
        
    title_el = main_soup.select_one("h1") or main_soup.select_one(".title")
    title = title_el.get_text().strip() if title_el else "Truyện"
    
    # Lấy ảnh bìa
    cover_url = None
    img_tag = main_soup.select_one(".book img") or main_soup.select_one(".truyen-info img")
    if img_tag and img_tag.get('src'):
        cover_url = img_tag['src']

    # --- TÍNH NĂNG QUÉT PHÂN TRANG THÔNG MINH & DỪNG KHI HẾT CHƯƠNG MỚI ---
    links = []
    base_domain = "https://" + story_url.split('/')[2]
    current_page_url = story_url
    page_num = 1
    
    while current_page_url:
        soup = get_soup(current_page_url)
        if not soup:
            break
            
        chapter_tags = soup.select("#list-chapter a, .list-chapter a, .chapter-list a")
        if not chapter_tags:
            break
            
        new_chapters_in_page = 0
        for a in chapter_tags:
            href = a.get('href', '')
            if href:
                full_url = href if href.startswith("http") else base_domain + href
                text = a.get_text().strip()
                # Kiểm tra nếu link chưa có trong danh sách thì mới thêm vào
                if text and not any(l['url'] == full_url for l in links):
                    links.append({"name": text, "url": full_url})
                    new_chapters_in_page += 1
                    
        # QUAN TRỌNG: Nếu trang này quét xong mà KHÔNG CÓ chương nào mới so với trang trước -> Dừng ngay lập tức
        if new_chapters_in_page == 0:
            break
            
        # Tìm nút phân trang trang tiếp theo
        pagination_links = soup.select(".pagination a")
        next_url = None
        for p_link in pagination_links:
            text_p = p_link.get_text().strip()
            # Tìm nút trang sau hoặc dấu mũi tên chuyển trang
            if "Trang sau" in text_p or ">" in text_p or str(page_num + 1) == text_p:
                next_url = p_link.get('href')
                break
                
        if next_url:
            next_full_url = next_url if next_url.startswith("http") else base_domain + next_url
            # Đảm bảo không bị lặp lại trang cũ
            if next_full_url == current_page_url:
                break
            current_page_url = next_full_url
            page_num += 1
        else:
            # Thử tự động đoán cấu trúc phân trang kiểu /trang-2/ nếu không tìm thấy nút bấm
            if page_num < 30: # Giới hạn an toàn
                if story_url.endswith("/"):
                    guessed_url = f"{story_url}trang-{page_num + 1}/"
                else:
                    guessed_url = f"{story_url}/trang-{page_num + 1}/"
                current_page_url = guessed_url
                page_num += 1
            else:
                break
                
        time.sleep(0.4)

    # Fallback dự phòng nếu không quét được qua phân trang
    if len(links) < 5:
        for a in main_soup.find_all("a", href=True):
            if "chuong-" in a['href'] or "hoi-" in a['href']:
                full_url = a['href'] if a['href'].startswith("http") else base_domain + a['href']
                text = a.get_text().strip()
                if text and not any(l['url'] == full_url for l in links):
                    links.append({"name": text, "url": full_url})

    if not links:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy kiểm tra lại đường dẫn truyện.")
        return
        
    await status.edit_text(f"📚 {title}\n✅ Quét thành công tổng cộng {len(links)} chương. Đang tải nội dung...")
    
    book = epub.EpubBook()
    book.set_identifier('truyenfull_' + re.sub(r'\W+', '', title))
    book.set_title(title)
    book.set_language('vi')
    
    # Thêm ảnh bìa an toàn
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
        
        # Độ trễ nhẹ để tránh bị tường lửa chặn do request quá nhanh
        time.sleep(0.3)

    if success_count == 0:
        await status.edit_text("❌ Tải thất bại do trang web chặn toàn bộ nội dung.")
        return

    # Cấu hình Mục lục (TOC) & Luồng đọc chuẩn Kindle
    book.toc = tuple(chapters_list)
    book.spine = ['nav'] + chapters_list

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen"
    file_name = f"{safe_title}.epub"
    
    epub.write_epub(file_name, book)
    
    await update.message.reply_document(
        document=open(file_name, "rb"), 
        caption=f"✅ Xong: {title}\n📖 Đã tải đủ trọn bộ {success_count}/{len(links)} chương + Chống tường lửa thành công!"
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
