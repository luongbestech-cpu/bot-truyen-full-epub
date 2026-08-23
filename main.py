import asyncio
import os
import re
import time
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
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
        self.wfile.write("Bot TruyenFull & Multi-Site đang hoạt động!".encode('utf-8'))

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

# Cấu hình email gửi Kindle (Lấy từ biến môi trường trên Render)
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")       # Email của bạn (ví dụ: gmail)
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "") # Mật khẩu ứng dụng (App Password)
KINDLE_EMAIL = os.getenv("KINDLE_EMAIL", "")       # Email Kindle của bạn (@kindle.com)

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

def download_chapter(url):
    soup = get_soup(url)
    if not soup: return None
    
    # Mở rộng các class chứa nội dung phổ biến của các trang truyện chữ
    content = (soup.select_one(".chapter-content") or 
               soup.select_one("#chapter-c") or 
               soup.select_one(".chapter-c") or 
               soup.select_one(".entry-content") or 
               soup.select_one(".post-content"))
               
    if not content: return None
    
    for tag in content.find_all(["script", "style", "div", "ins", "iframe", "button"]):
        tag.decompose()
        
    return str(content)

def send_to_kindle_email(file_path, title):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not KINDLE_EMAIL:
        return False, "Chưa cấu hình thông tin Email trên Render."
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = KINDLE_EMAIL
        msg['Subject'] = f"Convert {title}"

        msg.attach(MIMEText("Gửi file EPUB từ Telegram Bot tự động."))

        with open(file_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(file_path))
            part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(file_path))
            msg.attach(part)

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, KINDLE_EMAIL, msg.as_string())
        server.quit()
        return True, "Đã gửi thành công vào Kindle!"
    except Exception as e:
        return False, f"Lỗi gửi Kindle: {str(e)}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang kết nối và quét danh sách chương (Hỗ trợ đa trang)...")
    
    story_url = url[0]
    main_soup = get_soup(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối tới trang truyện. Web có thể đang chặn hoặc sai link.")
        return
        
    title_el = main_soup.select_one("h1") or main_soup.select_one(".title")
    title = title_el.get_text().strip() if title_el else "Truyện"
    if "|" in title:
        title = title.split('|')[0].strip()
        
    # Lấy ảnh bìa
    cover_url = None
    img_tag = main_soup.select_one(".book img") or main_soup.select_one(".truyen-info img") or main_soup.select_one("article img")
    if img_tag and img_tag.get('src'):
        cover_url = img_tag['src']

    # --- QUÉT PHÂN TRANG THÔNG MINH CHO CÁC TRANG CÙNG CẤU TRÚC ---
    links = []
    base_domain = "https://" + story_url.split('/')[2]
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
                text = a.get_text().strip()
                if text and not any(l['url'] == full_url for l in links):
                    links.append({"name": text, "url": full_url})
                    new_chapters_in_page += 1
                    
        if new_chapters_in_page == 0:
            break
            
        # Tìm nút phân trang trang tiếp theo
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
            # Tự động đoán cấu trúc phân trang kiểu /trang-2/ nếu không tìm thấy nút bấm
            if page_num < 40:
                if story_url.endswith("/"):
                    guessed_url = f"{story_url}trang-{page_num + 1}/"
                else:
                    guessed_url = f"{story_url}/trang-{page_num + 1}/"
                current_page_url = guessed_url
                page_num += 1
            else:
                break
        time.sleep(0.3)

    # Fallback dự phòng quét toàn bộ thẻ a nếu phân trang không khớp
    if len(links) < 5:
        for a in main_soup.find_all("a", href=True):
            if "chuong-" in a['href'] or "hoi-" in a['href'] or "chapter-" in a['href']:
                full_url = a['href'] if a['href'].startswith("http") else base_domain + a['href']
                text = a.get_text().strip()
                if text and not any(l['url'] == full_url for l in links):
                    links.append({"name": text, "url": full_url})

    if not links:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy kiểm tra lại đường dẫn truyện.")
        return
        
    await status.edit_text(f"📚 {title}\n✅ Quét thành công {len(links)} chương. Đang tải nội dung...")
    
    book = epub.EpubBook()
    book.set_identifier('truyen_' + re.sub(r'\W+', '', title))
    book.set_title(title)
    book.set_language('vi')
    
    # Thêm ảnh bìa
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

    # Cấu hình Mục lục (TOC) chuẩn Kindle
    book.toc = tuple(chapters_list)
    book.spine = ['nav'] + chapters_list

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen"
    file_name = f"{safe_title}.epub"
    epub.write_epub(file_name, book)
    
    # 1. Gửi file trực tiếp qua Telegram
    await status.edit_text("⬆️ Đang gửi file EPUB qua Telegram và Kindle...")
    await update.message.reply_document(
        document=open(file_name, "rb"), 
        caption=f"✅ Xong: {title}\n📖 {success_count}/{len(links)} chương + Ảnh bìa & Mục lục chuẩn Kindle!"
    )
    
    # 2. Gửi file qua Email Kindle (nếu đã cấu hình)
    if KINDLE_EMAIL and SENDER_EMAIL:
        success, msg_note = send_to_kindle_email(file_name, title)
        await update.message.reply_text(f"📧 Trạng thái gửi Kindle: {msg_note}")

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
