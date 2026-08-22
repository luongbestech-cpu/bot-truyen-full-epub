import asyncio
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# CẤU HÌNH ĐỂ KHÔNG BỊ CHẶN
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://truyenfull.live/",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
}

def get_soup(url):
    try:
        # Tăng thời gian chờ lên 30 giây để tránh Timed out
        response = requests.get(url, headers=HEADERS, timeout=30)
        if response.status_code == 200:
            return BeautifulSoup(response.content, "lxml")
    except:
        return None
    return None

def download_chapter(url):
    soup = get_soup(url)
    if not soup: return None
    
    # Tìm vùng nội dung - TruyenFull thường là .chapter-content hoặc #chapter-c
    content = soup.select_one(".chapter-content") or soup.select_one("#chapter-c")
    if not content: return None
    
    # Xóa quảng cáo/rác
    for tag in content.find_all(["script", "style", "div", "ins"]):
        tag.decompose()
        
    return str(content)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang kết nối TruyenFull... (chế độ an toàn)")
    
    # 1. Lấy danh sách chương (Vét cạn trang)
    main_soup = get_soup(url[0])
    if not main_soup:
        await status.edit_text("❌ Lỗi kết nối đến trang truyện.")
        return
        
    title = main_soup.select_one("h1").get_text().strip()
    links = []
    # Đơn giản hóa: Lấy tất cả link chương trong list-chapter
    for a in main_soup.select("#list-chapter a"):
        links.append({"name": a.get_text().strip(), "url": "https://truyenfull.live" + a['href']})
    
    await status.edit_text(f"📚 {title}\n✅ Tìm thấy {len(links)} chương. Đang tải tuần tự...")
    
    # 2. Tải từng chương một (Không tải song song để tránh bị chặn)
    book = epub.EpubBook()
    book.set_title(title)
    
    for i, item in enumerate(links):
        content = download_chapter(item['url'])
        if content:
            chap = epub.EpubHtml(title=item['name'], file_name=f"chap_{i}.xhtml")
            chap.content = f"<h2>{item['name']}</h2>{content}"
            book.add_item(chap)
            book.spine.append(chap)
        
        # Cập nhật tiến độ mỗi 10 chương
        if i % 10 == 0:
            pct = int((i / len(links)) * 100)
            await status.edit_text(f"📚 {title}\n⏳ Đang tải: {pct}%\n({i}/{len(links)})")
        
        # Quan trọng: Nghỉ 0.5 giây sau mỗi chương để tránh bị web khóa IP
        time.sleep(0.5)

    # 3. Xuất file
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    file_name = f"{title}.epub"
    epub.write_epub(file_name, book)
    
    await update.message.reply_document(document=open(file_name, "rb"), caption=f"✅ Xong: {title}")
    await status.delete()
    os.remove(file_name)

if __name__ == "__main__":
    app = Application.builder().token(os.getenv("BOT_TOKEN")).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
