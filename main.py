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
# CẤU HÌNH BOT & CLOUDSCRAPER
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def get_content(url):
    try:
        res = scraper.get(url, timeout=30)
        if res.status_code == 200:
            return BeautifulSoup(res.text, "lxml")
    except Exception as e:
        print(f"Lỗi tải {url}: {e}")
    return None

def extract_chapter_number(name):
    name_lower = name.lower()
    if "cuối" in name_lower or "ngoại" in name_lower or "ngoai" in name_lower or "extra" in name_lower:
        return 999999
        
    numbers = re.findall(r'\d+', name)
    if numbers:
        return int(numbers[0])
    return 0

def download_chap(url):
    soup = get_content(url)
    if not soup: return None
    
    container = (
        soup.select_one(".chapter-text") or
        soup.select_one(".reading-content") or
        soup.select_one(".rd-container") or
        soup.select_one("#content") or
        soup.select_one(".box-content") or
        soup.select_one(".chapter-content") or 
        soup.select_one("#chapter-content") or
        soup.select_one("#chapter-c") or 
        soup.select_one(".chapter-c") or 
        soup.select_one(".entry-content") or 
        soup.select_one("article") or
        soup.body
    )
    
    if not container:
        container = soup

    # Dọn dẹp các thành phần thừa, nút bấm, hình ảnh, quảng cáo
    for media in container.find_all(["img", "svg", "iframe", "picture", "hr"]):
        media.decompose()
        
    for box in container.find_all(True):
        classes = " ".join(box.get("class", [])) if box.get("class") else ""
        if re.search(r"ads|banner|ebook|download|promo|nav|menu|box-h|truyen-hot", classes, re.I):
            box.decompose()

    # Tìm tên chương thực tế bên trong trang (Ví dụ: "Chương 1: Khải hoàn")
    real_title = ""
    for h in container.find_all(["h1", "h2", "h3", "div", "p"]):
        text = h.get_text().strip()
        if re.match(r"^(chương|chuong|hồi|hoi)\s*\d+", text, re.I) and len(text) < 100:
            real_title = text
            h.decompose() # Xóa tiêu đề thừa nằm lẫn trong nội dung để không bị lặp
            break

    ignore_keywords = [
        "bỏ qua nội dung", "trang chủ", "lượt xem:", "cập nhật:", "chia sẻ", 
        "thích", "đang tải", "có liên quan", "báo lỗi", "khám phá thêm", 
        "đăng nhập", "bình luận", "viết:", "lúc", "danh sách",
        "phím mũi tên", "sang chương", "truyện hot mới", "tải ebook",
        "chương trước", "chương sau", "« chương", "chương tiếp »"
    ]

    # Quét và xóa các thẻ chứa từ khóa rác điều hướng trước đó
    for element in list(container.find_all(True)):
        if element.parent is None:
            continue
        text = element.get_text().strip().lower()
        if any(kw in text for kw in ignore_keywords) and len(text) < 300:
            if element not in [container, soup.body]:
                element.decompose()

    paragraphs = container.find_all(["p", "div"])
    valid_p = []
    seen_texts = set()
    
    for p in paragraphs:
        text = p.get_text().strip()
        if not text:
            continue
        lower_text = text.lower()
        
        if any(kw in lower_text for kw in ignore_keywords):
            continue
            
        if text in seen_texts:
            continue
        seen_texts.add(text)
            
        valid_p.append(str(p))
        
    content_html = "".join(valid_p) if valid_p else str(container)
    return real_title, content_html

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url_match = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url_match: return
    
    status = await update.message.reply_text("⏳ Đang kết nối, nhận diện nguồn và quét danh sách chương...")
    story_url = url_match[0].strip()
    
    main_soup = get_content(story_url)
    if not main_soup:
        await status.edit_text("❌ Không thể kết nối tới trang truyện. Web có thể đang chặn hoặc sai link.")
        return
        
    og_title = main_soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]
    else:
        title_el = main_soup.select_one("h1") or main_soup.title
        title = title_el.get_text().strip() if title_el else "Truyện"
        
    if "|" in title:
        title = title.split('|')[0].strip()
        
    cover_url = None
    og_img = (
        main_soup.find("meta", property="og:image") or 
        main_soup.find("meta", property="product:image") or 
        main_soup.find("meta", attrs={"name": "twitter:image"})
    )
    if og_img and og_img.get("content"):
        cover_url = og_img["content"]
    
    if not cover_url:
        img_el = main_soup.select_one(".book img, .info-image img, .story-image img, .product-image img, img.cover, .detail img, .col-image img, .book-image img, .truyen-info img, article img")
        if img_el:
            cover_url = img_el.get("data-src") or img_el.get("data-original") or img_el.get("src")

    if cover_url:
        cover_url = urljoin(story_url, cover_url)

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
            soup = main_soup if page == 1 else get_content(page_url)
            if not soup: continue

            for a in soup.find_all("a", href=True):
                href = urldefrag(urljoin(page_url, a.get("href")))[0]
                text = a.get_text().strip()
                is_chap = re.match(r"^(chương|chuong|hồi|hoi|quyển|quyen|c\s*\d+|\d+|phần|phan|pn\s*\d+|nt\s*\d+|ngoại truyện)", text, flags=re.IGNORECASE)
                
                if is_chap and len(text) < 80:
                    if ("chuong-" in href or "/chap-" in href or "id=" in href) and not any(c['url'] == href for c in links):
                        links.append({"name": text, "url": href})
            time.sleep(0.3)
    else:
        current_page_url = story_url
        page_num = 1
        
        while current_page_url:
            soup = get_content(current_page_url)
            if not soup: break
                
            chapter_tags = soup.select("#list-chapter a, .list-chapter a, .chapter-list a, .zaraz-list a, a")
            if not chapter_tags:
                break
                
            new_chapters_in_page = 0
            for a in chapter_tags:
                href = a.get('href', '')
                if href:
                    full_url = urldefrag(urljoin(current_page_url, href))[0]
                    if story_path not in full_url and "truyenfull" in base_domain:
                        continue

                    text = a.get_text().strip()
                    is_chap = re.match(r"^(chương|chuong|hồi|hoi|quyển|quyen|c\s*\d+|\d+|phần|phan|pn\s*\d+|nt\s*\d+|ngoại truyện)", text, flags=re.IGNORECASE)
                    
                    if is_chap and len(text) < 80:
                        if not any(l['url'] == full_url for l in links):
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

    links.sort(key=lambda x: extract_chapter_number(x['name']))

    if not links:
        await status.edit_text("❌ Không tìm thấy chương nào hoặc link không hợp lệ.")
        return
        
    await status.edit_text(f"📚 {title}\n✅ Quét thành công {len(links)} chương. Đang tải nội dung...")
    
    results = {}
    for i, c in enumerate(links):
        real_title, content = download_chap(c["url"])
        results[i] = {"name": real_title if real_title else c["name"], "content": content}
        
        if i % 15 == 0 or i == len(links) - 1:
            pct = int(((i + 1) / len(links)) * 100)
            try:
                await status.edit_text(f"📚 {title}\n⏳ Đang tải: {pct}%\n({i+1}/{len(links)})")
            except:
                pass
        time.sleep(0.3)

    book = epub.EpubBook()
    book.set_identifier('truyen_' + re.sub(r'\W+', '', title))
    book.set_title(title)
    book.set_language('vi')
    
    if cover_url:
        try:
            img_res = scraper.get(cover_url, timeout=15)
            if img_res.status_code == 200:
                book.set_cover("cover.jpg", img_res.content)
        except Exception as e:
            print(f"Lỗi tải ảnh bìa: {e}")

    chapters_list = []
    success_count = 0
    for i, c in enumerate(links):
        chap_data = results.get(i)
        if chap_data and chap_data["content"]:
            chap_title = chap_data["name"]
            chap = epub.EpubHtml(title=chap_title, file_name=f"chap_{i+1}.xhtml")
            chap.content = f"<h2>{chap_title}</h2>{chap_data['content']}"
            book.add_item(chap)
            chapters_list.append(chap)
            success_count += 1

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
            caption=f"✅ Hoàn tất: {title}\n📖 Trọn bộ {success_count}/{len(links)} chương (Đã dọn sạch rác, giữ đúng tên chương và nội dung)!"
        )

    await status.delete()
    if os.path.exists(file_name):
        os.remove(file_name)

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    if not BOT_TOKEN:
        print("❌ Lỗi: Thiếu BOT_TOKEN!")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
