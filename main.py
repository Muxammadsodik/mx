import os
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters,
    ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
import logging
from PIL import Image
import pytesseract
import google.generativeai as genai
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import asyncpg

# === ENV SETUP ===
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "5103279067").split(",")))
DB_URL = os.getenv("DATABASE_URL") 

# === GLOBAL ===
DB_POOL = None
NAME, PHONE, REGION = range(3)
TASK_SELECT, QUESTION, ANSWER, CONFIRM_Q, CONFIRM_A = range(3, 8)

regions = ["Tashkent", "Andijan", "Fergana", "Namangan", "Samarkand", "Bukhara",
    "Navoi", "Kashkadarya", "Surkhandarya", "Jizzakh", "Sirdarya", "Khorezm",
    "Karakalpakstan", "Other"]
region_keyboard = [[r] for r in regions]

logging.basicConfig(level=logging.INFO)
GEMINI_MODEL = genai.GenerativeModel("gemini-2.0-flash")

# === DATABASE ===
async def init_db():
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(DB_URL)
    async with DB_POOL.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                full_name TEXT,
                phone_number TEXT,
                region TEXT,
                last_submission_date TEXT,
                task1_submitted BOOLEAN DEFAULT FALSE,
                task2_submitted BOOLEAN DEFAULT FALSE
            )
        ''')

async def create_user(user_id):
    async with DB_POOL.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id)

async def save_user_info(user_id, field, value):
    async with DB_POOL.acquire() as conn:
        await conn.execute(f"UPDATE users SET {field} = $1 WHERE user_id = $2", value, user_id)

async def reset_limits_if_needed(user_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT last_submission_date FROM users WHERE user_id = $1", user_id)
        if not row or not row["last_submission_date"] or today > row["last_submission_date"]:
            await conn.execute("""
                UPDATE users SET last_submission_date = $1,
                                 task1_submitted = FALSE,
                                 task2_submitted = FALSE
                WHERE user_id = $2
            """, today, user_id)

async def get_user_status(user_id):
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT last_submission_date, task1_submitted, task2_submitted FROM users WHERE user_id = $1
        """, user_id)
        return row

async def update_task_submission(user_id, task):
    col = "task1_submitted" if task == "Task 1" else "task2_submitted"
    async with DB_POOL.acquire() as conn:
        await conn.execute(f"UPDATE users SET {col} = TRUE WHERE user_id = $1", user_id)

# === OCR ===
def extract_text_from_image(file_path):
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image)
        image.close()
        os.remove(file_path)
        return text
    except Exception:
        return None

# === Gemini Evaluation ===
async def evaluate_with_gemini(task_type, question, answer, retries=3):
    prompt = f"""
You are an IELTS Academic Writing evaluator. You are responding through a Telegram bot.
Keep your message under 4000 characters to avoid errors.
Use Markdown formatting only where necessary and avoid long bullet lists.

Question: {question if question else '[Image Attached]'}
Answer: {answer}

Score and comment each:
- Task Achievement
- Coherence and Cohesion
- Lexical Resource
- Grammar
"""
    for attempt in range(retries):
        try:
            response = await asyncio.to_thread(GEMINI_MODEL.generate_content, prompt)
            return response.text[:4000]
        except Exception as e:
            logging.warning(f"Gemini API attempt {attempt + 1} failed: {str(e)}")
            await asyncio.sleep(2)
    return "❌ Gemini API error after retries. Please try again later."

# === Health Check Server ===
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

# === User Flow ===
WELCOME = (
    "👋 Welcome! What is your full name?\n"
    "By sharing your info, you agree to its use in marketing/advertising purposes.\n"
    "Your data is securely stored."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await create_user(update.effective_user.id)
    await update.message.reply_text(WELCOME)
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_user_info(update.effective_user.id, "full_name", update.message.text.strip())
    kb = [[KeyboardButton("📞 Share Contact", request_contact=True)]]
    await update.message.reply_text("📞 Please share your phone number:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True))
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.contact.phone_number if update.message.contact else update.message.text.strip()
    if len(phone) < 6:
        await update.message.reply_text("⚠️ Phone number seems invalid. Please try again.")
        return PHONE
    await save_user_info(update.effective_user.id, "phone_number", phone)
    await update.message.reply_text("🌍 Which region are you from:", reply_markup=ReplyKeyboardMarkup(region_keyboard, resize_keyboard=True, one_time_keyboard=True))
    return REGION

async def get_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    region = update.message.text.strip()
    if region not in regions:
        await update.message.reply_text("❌ Please choose from the list.")
        return REGION
    await save_user_info(update.effective_user.id, "region", region)
    await update.message.reply_text("✅ Registration complete! Choose your task:", reply_markup=ReplyKeyboardMarkup([["Task 1", "Task 2"]], resize_keyboard=True, one_time_keyboard=True))
    return ConversationHandler.END

async def choose_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task = update.message.text.strip()
    context.user_data["task"] = task
    if user_id not in ADMIN_IDS:
        await reset_limits_if_needed(user_id)
        stat = await get_user_status(user_id)
        if (task == "Task 1" and stat["task1_submitted"]) or (task == "Task 2" and stat["task2_submitted"]):
            await update.message.reply_text("❌ You have already submitted this task today. Please wait until 00:00 UTC.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
    await update.message.reply_text("📷 Please send your task question (text or image):", reply_markup=ReplyKeyboardRemove())
    return QUESTION

async def get_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        path = f"q_{update.effective_user.id}.jpg"
        await file.download_to_drive(path)
        context.user_data["question"] = "[Image Attached]"
        await update.message.reply_text("✅ Question image received. Now send your answer:")
        return ANSWER
    else:
        text = update.message.text.strip()
        if not text:
            await update.message.reply_text("❌ Could not read the question. Please try again.")
            return QUESTION
        context.user_data["question"] = text
        await update.message.reply_text("✅ Question received. Now send your answer:")
        return ANSWER

async def get_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        path = f"a_{update.effective_user.id}.jpg"
        await file.download_to_drive(path)
        text = await asyncio.to_thread(extract_text_from_image, path)
    else:
        text = update.message.text.strip()

    if not text:
        await update.message.reply_text("❌ Could not read the answer. Please try again.")
        return ANSWER

    context.user_data["answer"] = text
    await update.message.reply_text(f"✏️ Is this your answer?\n\n{text}", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes", callback_data="confirm_a_yes"), InlineKeyboardButton("No", callback_data="confirm_a_no")]
    ]))
    return CONFIRM_A

async def confirm_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    answer = context.user_data.get("answer")
    question = context.user_data.get("question")

    if query.data == "confirm_a_no":
        await query.edit_message_text(f"❌ Please resend your answer or correct it manually:\n\n`{escape_markdown(answer, version=2)}`", parse_mode=ParseMode.MARKDOWN_V2)
        return ANSWER

    await query.edit_message_text("⏳ Evaluating your writing...")
    task = context.user_data.get("task", "Unknown")
    result = await evaluate_with_gemini(task, question or "", answer or "")
    await update_task_submission(update.effective_user.id, task)
    await query.message.reply_text(f"🎓 *Evaluation Result:*\n\n{escape_markdown(result, version=2)}", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# === Main Entrypoint ===
def main():
    run_health_server()
    app = ApplicationBuilder().token(TOKEN).post_init(init_db).build()

    reg = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT | filters.CONTACT, get_phone)],
            REGION: [MessageHandler(filters.TEXT, get_region)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    task = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(Task 1|Task 2)$"), choose_task)],
        states={
            QUESTION: [MessageHandler(filters.TEXT | filters.PHOTO, get_question)],
            ANSWER: [MessageHandler(filters.TEXT | filters.PHOTO, get_answer)],
            CONFIRM_A: [CallbackQueryHandler(confirm_answer, pattern="^confirm_a_.*")],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(reg)
    app.add_handler(task)
    app.add_handler(CommandHandler("cancel", cancel))
    app.run_polling()

if __name__ == "__main__":
    main()
