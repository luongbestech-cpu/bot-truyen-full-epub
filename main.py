import asyncio
import os
import re
import time
import threading
from urllib.parse import urldefrag, urljoin
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup
from ebooklib import epub
import requests
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# CẤU HÌNH
BOT_TOKEN = os.getenv("BOT_TOKEN_TRUYENFULL") or os.getenv("BOT_TOKEN")
CONCURRENT_DOWNLOADS = 10 
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}

session = requests.Session()
session.headers.update(HEADERS)

def get_soup(url):
    try:
        res = session.get(url, timeout=15)
        return BeautifulSoup(res.text, "lxml")
    except: return None

# BỘ QUÉT TỐI ƯU
def parse_all_chapters(story_url, main_soup):
    chapters = []
    seen = set()
    base_url = urldefrag(story_url)[0].rstrip("/")
    
    # TruyenFull Logic: Vét cạn số trang
    max_page = 1
    for a in main_soup.select(".pagination a"):
        txt = a.get_text()
        if txt.isdigit(): max_page = max(max_page, int(txt))
    
    for p in range(1, max_page + 1):
        p_url = f"{base_url}/trang-{p}/" if p > 1 else base_url
        soup = main_soup if p == 1 else get_soup(p_url)
        if not soup: continue
        for a in soup.select("#list-chapter a"):
            href = urljoin(p_url, a.get("href", ""))
            if href and href not in seen:
                seen.add(href)
                chapters.append({"name": a.get_text().strip(), "url": href})
    return chapters

def download_chap(url):
    soup = get_soup(url)
    if not soup: return None
    content = soup.select_one(".chapter-content") or soup.select_one("#chapter-c")
    if not content: return None
    for t in content.find_all(["script", "style", "ins", "a", "div.ads"]): t.decompose()
    return str(content)

# TELEGRAM HANDLER
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang kết nối...")
    try:
        # Lấy info
        main_soup = get_soup(url[0])
        title = main_soup.select_one("h1").get_text() if main_soup else "Truyện"
        chapters = parse_all_chapters(url[0], main_soup)
        
        await status.edit_text(f"📚 {title}\n✅ Tìm thấy {len(chapters)} chương. Bắt đầu tải...")

        # Tải chương với %
        results = {}
        sem = asyncio.Semaphore(CONCURRENT_DOWNLOADS)
        async def fetch_task(idx, c):
            async with sem:
                content = await asyncio.to_thread(download_chap, c["url"])
                results[idx] = content
                return True

        tasks = [fetch_task(i, c) for i, c in enumerate(chapters)]
        
        # Hiển thị %
        done = 0
        for future in asyncio.as_completed(tasks):
            await future
            done += 1
            if done % 5 == 0 or done == len(chapters): # Cập nhật mỗi 5 chương để không bị spam
                pct = int((done / len(chapters)) * 100)
                await status.edit_text(f"📚 {title}\n⏳ Đang tải: {done}/{len(chapters)} chương ({pct}%)\n" + "█"*(pct//10) + "▒"*(10-pct//10))

        # Đóng gói EPUB
        await status.edit_text(f"📦 Đang đóng gói EPUB...")
        book = epub.EpubBook()
        book.set_title(title)
        
        for i, chap in enumerate(chapters):
            content = results.get(i)
            if not content: continue
            c = epub.EpubHtml(title=chap["name"], file_name=f"c{i}.xhtml")
            c.content = f"<h2>{chap['name']}</h2>{content}"
            book.add_item(c)
            book.spine.append(c)
        
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        file_out = "truyen.epub"
        epub.write_epub(file_out, book)
        
        await update.message.reply_document(document=open(file_out, "rb"), caption=f"✅ {title} - Đã xong!")
        await status.delete()
        os.remove(file_out)
        
    except Exception as e:
        await status.edit_text(f"❌ Lỗi: {e}")

# Mấy hàm chạy bot giữ nguyên...
if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
