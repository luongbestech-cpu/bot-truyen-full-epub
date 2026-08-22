import os
import re
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ebooklib import epub

TELEGRAM_TOKEN = "8761120605:AAGGOEpFEQRZChufPR454jOgCdHb_OTD8vs"

# Giả lập trình duyệt để tránh bị TruyenFull chặn
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_all_chapters(story_url):
    """Lấy danh sách link tất cả các chương từ trang chính của truyện"""
    chapters = []
    current_url = story_url
    
    while current_url:
        res = requests.get(current_url, headers=HEADERS)
        if res.status_code != 200:
            break
        
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Tìm danh sách chương ở trang hiện tại
        chapter_list = soup.find_all('ul', class_='list-chapter')
        for ul in chapter_list:
            for a in ul.find_all('a'):
                chapters.append({
                    'title': a.text.strip(),
                    'url': a['href']
                })
        
        # Tìm nút Chuyển trang (Phân trang của danh sách chương)
        next_page = soup.find('li', class_='next')
        if next_page and next_page.find('a'):
            current_url = next_page.find('a')['href']
            # Đảm bảo URL đầy đủ
            if not current_url.startswith('http'):
                current_url = 'https://truyenfull.vn' + current_url
        else:
            current_url = None
            
    return chapters

def get_chapter_content(chapter_url):
    """Cào nội dung văn bản của 1 chương"""
    res = requests.get(chapter_url, headers=HEADERS)
    if res.status_code != 200:
        return ""
    
    soup = BeautifulSoup(res.text, 'html.parser')
    content_div = soup.find('div', class_='chapter-c')
    
    if content_div:
        # Xóa các quảng cáo chèn trong nội dung nếu có
        for ads in content_div.find_all(['script', 'ins', 'div']):
            ads.decompose()
        
        # Lấy văn bản và xuống dòng
        text = content_div.decode_contents()
        # Chuyển đổi các thẻ <br> thành đoạn văn
        text = text.replace('<br>', '<br/>').replace('<br/>', '</p><p>')
        return f"<p>{text}</p>"
    return ""

def build_full_epub(story_title, chapters, output_filename="story.epub"):
    """Tạo file ePub chứa tất cả các chương"""
    book = epub.EpubBook()
    book.set_title(story_title)
    book.set_language('vi')
    
    spine = ['nav']
    
    for idx, chap in enumerate(chapters, 1):
        content = get_chapter_content(chap['url'])
        if not content:
            continue
            
        c = epub.EpubHtml(
            title=chap['title'], 
            file_name=f'chap_{idx}.xhtml', 
            lang='vi'
        )
        c.content = f"<h2>{chap['title']}</h2>{content}"
        
        book.add_item(c)
        spine.append(c)
        
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine
    
    epub.write_epub(output_filename, book, {})
    return output_filename

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "truyenfull" not in url:
        await update.message.reply_text("⚠️ Vui lòng gửi link trang chủ bộ truyện trên TruyenFull!")
        return

    msg = await update.message.reply_text("🔍 Đang thu thập danh sách chương...")

    try:
        # 1. Lấy thông tin truyện
        res = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(res.text, 'html.parser')
        title_tag = soup.find('h3', class_='title')
        story_title = title_tag.text.strip() if title_tag else "Truyện TruyenFull"

        # 2. Quét toàn bộ chương
        chapters = get_all_chapters(url)
        if not chapters:
            await msg.edit_text("❌ Không tìm thấy danh sách chương nào!")
            return

        await msg.edit_text(f"📚 Tìm thấy {len(chapters)} chương. Đang cào nội dung và đóng gói ePub (sẽ mất vài phút)...")

        # 3. Tạo file ePub
        clean_title = re.sub(r'[\\/*?:"<>|]', "", story_title)
        file_name = f"{clean_title}.epub"
        
        build_full_epub(story_title, chapters, file_name)

        # 4. Gửi file
        await update.message.reply_text("✅ Hoàn tất! Đang gửi file cho bạn...")
        with open(file_name, 'rb') as f:
            await update.message.reply_document(document=f, filename=file_name)

        if os.path.exists(file_name):
            os.remove(file_name)

    except Exception as e:
        await update.message.reply_text(f"❌ Có lỗi xảy ra: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot TruyenFull đang chạy...")
    app.run_polling()

if __name__ == '__main__':
    main()
