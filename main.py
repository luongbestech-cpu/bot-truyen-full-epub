import asyncio
import os
import re
import time
import threading
from urllib.parse import urldefrag, urljoin, urlparse
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
REQUEST_TIMEOUT = 15
MAX_PAGES = 2000
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
    containers = soup.select("#list-chapter") or soup.select(".list-chapter") or [soup]
    for container in containers:
        for a in container.find_all("a", href=True):
            href = normalize_url(urljoin(page_url, a.get("href")))
            if not href:
                continue
            name = get_chapter_name(a, href)
            if not is_chapter_link(name, href):
                continue
            number = extract_chapter_number(name + " " + href)
            if number is None:
                continue
            result[href] = {"name": name, "url": href, "number": number}
    return result

def get_page_number(url):
    patterns = [r"/page[-/](\d+)", r"/trang[-_](\d+)", r"[?&](?:page|paged)=(\d+)", r"/(\d+)$"]
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
    nodes = soup.select(".pagination") or soup.select("ul.pagination") or [soup]
    for node in nodes:
        for a in node.find_all("a", href=True):
            href = normalize_url(urljoin(page_url, a.get("href")))
            if not href:
                continue
            text = clean_text(a.get_text(" ", strip=True)).lower()
            if is_chapter_link(text, href):
                continue
            low = href.lower()
            is_page = False
            if re.search(r"/(?:page|trang)[-_/]?\d+", low) or re.search(r"[?&](?:page|paged)=\d+", low):
                is_page = True
            if text.isdigit() and 1 <= int(text) <= MAX_PAGES:
                is_page = True
            if text in {"next", "tiếp", "trang sau", "sau", "»", "›", "→"}:
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
        if link not in visited:
            return link
    return None

WP_CONTENT_SELECTORS = [
    ".entry-content", ".post-content", ".chapter-content", ".chapter-c",
    ".reading-content", ".wp-block-post-content", "article .content", "article"
]

WP_IGNORE_CLASSES = {
    "related", "related-posts", "related-post", "yarpp-related", "jp-relatedposts",
    "post-related", "similar-posts", "recommended-posts", "comments",
    "comment-respond", "comments-area", "share", "sharedaddy"
}

def is_wordpress_url(url, soup=None):
    host = urlparse(url).netloc.lower()
    if "wordpress.com" in host or "wp-content" in url.lower() or "wp-includes" in url.lower():
        return True
    if soup is not None:
        meta = soup.select_one("meta[name='generator']")
        if meta and "wordpress" in (meta.get("content") or "").lower():
            return True
        if soup.select_one("link[href*='wp-content'], script[src*='wp-content'], link[href*='wp-includes'], script[src*='wp-includes']"):
            return True
        if soup.select_one("body[class*='wp-']"):
            return True
    return False

def looks_like_chapter_title(text):
    return bool(re.search(r"\b(?:chương|chuong|chapter|chap)\s*\d+\b", clean_text(text), re.I))

def get_wp_content(soup):
    if soup is None:
        return None
    for selector in WP_CONTENT_SELECTORS[:-2]:
        el = soup.select_one(selector)
        if el and len(clean_text(el.get_text(" ", strip=True))) >= 100:
            return el
    candidates = []
    for el in soup.select("article"):
        n = len(clean_text(el.get_text(" ", strip=True)))
        if n >= 100:
            candidates.append((n, el))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
    return None

def get_wp_chapter_title(soup, content=None):
    elements = []
    if content:
        elements.extend(content.find_all(["h1", "h2", "h3", "h4"], limit=20))
    elements.extend(soup.select("h1.entry-title, h1.post-title, h1, h2.entry-title, h2.post-title"))
    seen = set()
    for el in elements:
        if id(el) in seen:
            continue
        seen.add(id(el))
        text = clean_text(el.get_text(" ", strip=True))
        if looks_like_chapter_title(text):
            return text
    meta = soup.select_one("meta[property='og:title']")
    if meta:
        text = clean_text(meta.get("content", ""))
        if looks_like_chapter_title(text):
            return text
    return ""

def is_related_anchor(a):
    parent = a
    for _ in range(6):
        parent = parent.parent
        if parent is None:
            break
        classes = {c.lower() for c in (parent.get("class") or [])}
        ident = (parent.get("id") or "").lower()
        if classes & WP_IGNORE_CLASSES or any(x in ident for x in ["related", "recommend", "similar", "comment", "share"]):
            return True
        text = clean_text(parent.get_text(" ", strip=True)).lower()
        if "có liên quan" in text and len(text) < 500:
            return True
    return False

def same_article_family(current_url, target_url):
    cur, tar = urlparse(current_url), urlparse(target_url)
    if cur.netloc.lower() != tar.netloc.lower():
        return False
    cp, tp = cur.path.rstrip("/").lower(), tar.path.rstrip("/").lower()
    if re.match(rf"^{re.escape(cp)}/(?:page[-/]?)?\d+$", tp):
        return True
    cparts, tparts = [x for x in cp.split("/") if x], [x for x in tp.split("/") if x]
    return bool(cparts and tparts and cparts[:-1] == tparts[:-1])

def is_next_text(text):
    text = clean_text(text).lower().strip(" ›»→")
    return bool(re.search(r"^(?:chương\s+(?:tiếp theo|sau)|chuong\s+(?:tiep theo|sau)|tiếp theo|tiep theo|chương sau|chuong sau|next|next chapter|next post|trang sau|sau|›|»|→)$", text, re.I))

def get_current_wp_page_number(url, chapter_number=None):
    page = get_page_number(url)
    if page is not None:
        return page
    if chapter_number is not None and float(chapter_number).is_integer():
        return int(chapter_number)
    return 1

def find_wp_next_url(current_url, soup, current_chapter_number=None, visited=None):
    if soup is None:
        return None
    visited = visited or set()
    # 1. rel=next
    for a in soup.select("a[rel~='next'], link[rel~='next']"):
        href = a.get("href")
        if not href:
            continue
        target = normalize_url(urljoin(current_url, href))
        if target and target not in visited and same_article_family(current_url, target):
            return target
    # 2. Chương tiếp theo / Chương sau / Next
    for a in soup.find_all("a", href=True):
        if is_related_anchor(a):
            continue
        if not is_next_text(a.get_text(" ", strip=True)):
            continue
        target = normalize_url(urljoin(current_url, a.get("href")))
        if target and target not in visited and target != normalize_url(current_url) and same_article_family(current_url, target):
            return target
    # 3. Phân trang số: chọn đúng current + 1, tránh "Có liên quan"
    current_page = get_current_wp_page_number(current_url, current_chapter_number)
    candidates = []
    for a in soup.find_all("a", href=True):
        if is_related_anchor(a):
            continue
        text = clean_text(a.get_text(" ", strip=True))
        if not text.isdigit() or int(text) != current_page + 1:
            continue
        target = normalize_url(urljoin(current_url, a.get("href")))
        if not target or target in visited or target == normalize_url(current_url):
            continue
        classes = " ".join(a.get("class") or []).lower()
        parent_classes = " ".join(a.parent.get("class") or []).lower() if a.parent else ""
        href_low = target.lower()
        is_pagination = (
            "post-page-numbers" in classes or "page-numbers" in classes or "pagination" in classes
            or "post-page-numbers" in parent_classes or "pagination" in parent_classes
            or bool(re.search(r"/(?:page/|page-|trang[-_/])?\d+/?$", href_low))
            or bool(re.search(r"[?&](?:page|paged)=\d+", href_low))
        )
        if is_pagination or same_article_family(current_url, target):
            candidates.append(target)
    return candidates[0] if candidates else None

def parse_wordpress_chapter(page_url, soup, fallback_number=None):
    content = get_wp_content(soup)
    if content is None:
        return None
    title = get_wp_chapter_title(soup, content)
    number = extract_chapter_number(title)
    if number is None:
        number = fallback_number
    if number is None:
        return None
    if not title:
        title = f"Chương {int(number) if float(number).is_integer() else number}"
    return {"name": clean_text(title), "url": normalize_url(page_url), "number": float(number)}

def collect_wordpress_chapters(story_url):
    current_url = normalize_url(story_url)
    visited, chapters, seen_numbers = set(), [], set()
    fallback_number = 1
    for _ in range(MAX_PAGES):
        if current_url in visited:
            break
        visited.add(current_url)
        soup = get_soup(current_url)
        if soup is None:
            break
        item = parse_wordpress_chapter(current_url, soup, fallback_number)
        if item is None:
            print(f"⚠️ Không nhận diện được chương: {current_url}")
            break
        if item["number"] in seen_numbers:
            print(f"🛑 Chương {item['number']} bị lặp, dừng.")
            break
        seen_numbers.add(item["number"])
        chapters.append(item)
        print(f"📖 WordPress: {item['name']}")
        next_url = find_wp_next_url(current_url, soup, item["number"], visited)
        if not next_url:
            print("🛑 Không còn chương tiếp theo.")
            break
        current_url = next_url
        fallback_number = int(item["number"]) + 1 if float(item["number"]).is_integer() else fallback_number + 1
        time.sleep(0.1)
    return sorted(chapters, key=lambda x: x["number"])

def slug_to_title(url):
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    return clean_text(re.sub(r"[-_]+", " ", slug)).title() if slug else "Truyện"

def get_story_info(story_url):
    soup = get_soup(story_url)
    if soup is None:
        raise RuntimeError("Không truy cập được trang truyện.")
    title = ""
    if is_wordpress_url(story_url, soup):
        for selector in [".site-title a", ".site-title", "header .site-title a", "header .site-title"]:
            el = soup.select_one(selector)
            if el:
                candidate = clean_text(el.get_text(" ", strip=True))
                if candidate and not looks_like_chapter_title(candidate):
                    title = candidate
                    break
    if not title:
        for selector in ["h1.title", "h1.entry-title", "h1.post-title", "h1", "meta[property='og:title']", "title"]:
            el = soup.select_one(selector)
            if not el:
                continue
            title = clean_text(el.get("content", "")) if el.name == "meta" else clean_text(el.get_text(" ", strip=True))
            if title:
                break
    if is_wordpress_url(story_url, soup) and looks_like_chapter_title(title):
        title = slug_to_title(story_url)
    title = title or "Truyện"
    cover_url = None
    for selector in ["meta[property='og:image']", ".book img", ".info img", ".post-thumbnail img", "article img"]:
        el = soup.select_one(selector)
        if not el:
            continue
        src = el.get("content") if el.name == "meta" else (el.get("src") or el.get("data-src") or el.get("data-lazy-src"))
        if src:
            cover_url = urljoin(story_url, src)
            break
    return title, cover_url, soup

def collect_chapters(story_url):
    soup0 = get_soup(story_url)
    if is_wordpress_url(story_url, soup0):
        print(f"🟣 Phát hiện WordPress: {story_url}")
        return collect_wordpress_chapters(story_url)
    print(f"🟢 Phát hiện TruyenFull: {story_url}")
    story_url = normalize_url(story_url)
    current_url = story_url
    visited = set()
    all_chapters = {}
    page_count = 0
    while True:
        if current_url in visited or page_count >= MAX_PAGES:
            break
        visited.add(current_url)
        page_count += 1
        soup = get_soup(current_url)
        if soup is None:
            break
        found = parse_chapters(current_url, soup)
        before = len(all_chapters)
        for url, item in found.items():
            if url not in all_chapters:
                all_chapters[url] = item
        new_count = len(all_chapters) - before
        if new_count == 0:
            break
        next_url = find_next_page(current_url, soup, visited)
        if not next_url:
            break
        current_url = next_url
        time.sleep(0.1)
    chapters = list(all_chapters.values())
    chapters.sort(key=lambda x: x["number"])
    unique = {}
    for item in chapters:
        if item["number"] not in unique:
            unique[item["number"]] = item
    chapters = sorted(list(unique.values()), key=lambda x: x["number"])
    return chapters

def download_single_chapter(chapter_info):
    url = chapter_info["url"]
    soup = get_soup(url)
    if soup is None:
        return None
    if is_wordpress_url(url, soup):
        content = get_wp_content(soup)
        if content is None:
            return None
        for tag in content.find_all(["script", "style", "iframe", "form", "noscript", "nav", "footer", "aside", "button"]):
            tag.decompose()
        for element in list(content.find_all(True)):
            classes = {c.lower() for c in (element.get("class") or [])}
            ident = (element.get("id") or "").lower()
            class_text = " ".join(classes)
            if (classes & WP_IGNORE_CLASSES
                    or any(x in ident for x in ["related", "recommend", "comment", "share"])
                    or any(x in class_text for x in ["pagination", "post-page-numbers", "page-numbers"])):
                element.decompose()
        for a in list(content.find_all("a", href=True)):
            if is_next_text(a.get_text(" ", strip=True)):
                a.decompose()
        return str(content)
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
