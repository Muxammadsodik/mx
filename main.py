import os
import logging
import easyocr
from PIL import Image
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from flask import Flask
import threading

# Set up logging (to help you see what’s happening)
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# Initialize EasyOCR reader (we use English)
reader = easyocr.Reader(['en'])

# Set up your Telegram Bot Token (Replit will provide it from Secrets)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Function: Combine OCR (here, we only use EasyOCR)
def ocr_image(image_path):
    # EasyOCR returns a list of texts (and more); we join them into one string.
    text_list = reader.readtext(image_path, detail=0)
    return " ".join(text_list)

# Telegram: When a user sends a photo, process it
async def handle_photo(update: Update, context: CallbackContext) -> None:
    try:
        # Get the highest resolution photo from the message
        photo_file = await update.message.photo[-1].get_file()
        file_path = f"{photo_file.file_id}.jpg"
        await photo_file.download_to_drive(file_path)
        
        # Use EasyOCR to extract text from the image
        extracted_text = ocr_image(file_path)
        response = f"Extracted Text:\n{extracted_text}" if extracted_text else "Sorry, I couldn't read any text."
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
    finally:
        # Clean up: delete the image file after processing
        if os.path.exists(file_path):
            os.remove(file_path)

# Telegram: /start command to welcome users
async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Hello! Send me a photo, and I'll try to read the text from it using EasyOCR.")

# Flask app to keep the project awake
app = Flask(__name__)
@app.route('/')
def home():
    return "I'm alive! Your Telegram bot is running."

def run_flask():
    app.run(host="0.0.0.0", port=8080)

def main():
    # Create the Telegram bot application using the async API
    app_bot = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers for commands and photos
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # Start the Flask server on a separate thread to keep the bot awake
    threading.Thread(target=run_flask).start()
    
    # Start polling Telegram for messages (this will run until you stop it)
    app_bot.run_polling()

if __name__ == "__main__":
    main()
