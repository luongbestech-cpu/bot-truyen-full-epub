import os
import trafilatura
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ebooklib import epub

# Nhập Token từ BotFather vào giữa 2 dấu ngoặc kép bên dưới
TELEGRAM_TOKEN = "8761120605:AAGGOEpFEQRZChufPR454jOgCdHb_OTD8vs"

def create_epub(title, text, output_filename="article.epub"):
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language('vi')

    # Tạo chương nội dung
    c1 = epub.EpubHtml(title=title, file_name='chap_1.xhtml', lang='vi')
    paragraphs = "".join([f"<p>{p}</p>" for p in text.split('\n') if p.strip()])
    c1.content = f"<h1>{title}</h1>{paragraphs}"
    
    book.add_item(c1)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    book.spine = ['nav', c1]
    epub.write_epub(output_filename, book, {})
    return output_filename

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("Vui lòng gửi một đường dẫn (URL) hợp lệ!")
        return

    await update.message.reply_text("⏳ Đang cào dữ liệu và đóng gói ePub...")

    try:
        downloaded = trafilatura.fetch_url(url)
        extracted_text = trafilatura.extract(downloaded, include_comments=False)
        metadata = trafilatura.extract_metadata(downloaded)

        title = metadata.title if (metadata and metadata.title) else "Ebook"

        if not extracted_text:
            await update.message.reply_text("❌ Không thể trích xuất nội dung từ trang web này.")
            return

        file_name = "output.epub"
        create_epub(title, extracted_text, file_name)

        with open(file_name, 'rb') as f:
            await update.message.reply_document(document=f, filename=f"{title}.epub")

        if os.path.exists(file_name):
            os.remove(file_name)

    except Exception as e:
        await update.message.reply_text(f"❌ Có lỗi xảy ra: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot đang chạy...")
    app.run_polling()

if __name__ == '__main__':
    main()