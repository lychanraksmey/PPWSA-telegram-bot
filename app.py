import re
import logging
import os
import threading
from datetime import datetime
from dotenv import load_dotenv

# We need Flask to run a dummy web server to satisfy the Koyeb Free Tier health check
from flask import Flask 

# Load environment variables from the .env file in the root directory
load_dotenv()

# Import core modules from python-telegram-bot (v20.8+)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    CallbackQueryHandler
)

# --- Configuration ---
# Variables are loaded from the .env file or environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# PPWSA_NOTIFICATION_BOT_ID must be cast to an integer
try:
    PPWSA_NOTIFICATION_BOT_ID = int(os.getenv("PPWSA_NOTIFICATION_BOT_ID"))
except (TypeError, ValueError):
    PPWSA_NOTIFICATION_BOT_ID = None 

# Define the port needed to satisfy the Koyeb Web Service health check (default 8000)
KOYEB_PORT = int(os.environ.get('PORT', 8000))

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Regular Expressions for Extraction ---
INVOICE_PATTERN = re.compile(r'P-([A-Z0-9]+)')
COST_PRICE_PATTERN = re.compile(r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*៛')
DATE_OUTPUT_FORMAT = "%Y-%m-%d %H:%M:%S"


# --- Telegram Bot Logic (Unchanged) ---
async def start_command(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text(
        'Bot is operational. It is running as a background service listening for notifications.'
    )

async def handle_notification_message(update: Update, context: CallbackContext) -> None:
    """Handles incoming messages *only* from the specified PPWSA Notification Bot ID."""
    message = update.message
    text = message.text

    if not text: return

    logger.info(f"Received notification from source bot: {text[:50]}...")

    # Date Extraction Logic
    if hasattr(message, 'forward_date') and message.forward_date:
        original_dt = message.forward_date
        date_source_label = " (Metadata)" 
    else:
        original_dt = message.date
        date_source_label = " (Fallback)" 

    original_date_str = original_dt.strftime(DATE_OUTPUT_FORMAT)
    date_message = f"Original Message Date{date_source_label}"
    date_value = original_date_str

    # Invoice and Price Extraction Logic (same as before)
    invoice_match = INVOICE_PATTERN.search(text)
    invoice_value = invoice_match.group(1) if invoice_match else "Pattern not found."
    invoice_message = "Invoice Number"

    cost_match = COST_PRICE_PATTERN.search(text)
    cost_value = cost_match.group(1).replace(',', '') if cost_match else "Pattern not found."
    cost_message = "Cost Price"

    # --- Send Individual Messages with Copy Buttons ---
    messages_to_send = [
        (date_message, date_value),
        (invoice_message, invoice_value),
        (cost_message, cost_value),
    ]
    
    for display_text, copy_content in messages_to_send:
        initial_display = f"**{display_text}:**\n`{copy_content}`"
        
        is_pattern_found = (copy_content != "Pattern not found.")
        
        if is_pattern_found or "(Fallback)" in display_text or "(Metadata)" in display_text:
             callback_data = f"copy_value|{copy_content}"
             keyboard = [
                 [
                     InlineKeyboardButton(
                         "📋 Copy Value",
                         callback_data=callback_data
                     )
                 ]
             ]
             reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None

        await message.reply_text(
            initial_display,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def button_handler(update: Update, context: CallbackContext) -> None:
    """Handles the 'Copy Value' button callback."""
    query = update.callback_query
    await query.answer(text="Value is ready to copy!")

    data = query.data.split('|', 1)
    action = data[0]
    copy_content = data[1]

    if action == "copy_value":
        original_text = query.message.text
        header_match = re.match(r'(\*\*.*?\*\*)', original_text)
        header = header_match.group(1) if header_match else ""
        
        new_text = f"{header}\n`{copy_content}`\n\n✅ **Extracted Value**"

        try:
            await query.edit_message_text(
                new_text,
                parse_mode='Markdown',
                reply_markup=None 
            )
        except Exception as e:
            logger.warning(f"Failed to edit message: {e}")
            await query.message.reply_text(
                 f"✅ **Extracted Value**\n`{copy_content}`",
                 parse_mode='Markdown'
            )

# --- Web Server Logic for Koyeb Health Check ---
app = Flask(__name__)

@app.route('/', methods=['GET'])
def home():
    """Simple route to satisfy the health check."""
    return "PPWSA Telegram Listener Bot is running.", 200

def run_flask_server():
    """Runs the minimal Flask server in the main thread."""
    logger.info(f"Starting minimal Flask server on port {KOYEB_PORT} for health check.")
    # Use 0.0.0.0 to listen on all interfaces, which is required for containers.
    # Set host explicitly to '0.0.0.0'
    from werkzeug.serving import run_simple
    run_simple('0.0.0.0', KOYEB_PORT, app)

def run_telegram_bot():
    """Initializes and runs the Telegram bot."""
    logger.info("Initializing Telegram Bot...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Use a filter to ONLY process messages from the specific notification bot ID
    ppwsa_notification_filter = filters.User(user_id=PPWSA_NOTIFICATION_BOT_ID)

    # Register Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ppwsa_notification_filter,
        handle_notification_message
    ))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Start the bot
    logger.info(f"Telegram Bot is running, listening for messages from ID: {PPWSA_NOTIFICATION_BOT_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    """Entry point: Starts both the Flask server and the Telegram bot concurrently."""
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Configuration Error: TELEGRAM_BOT_TOKEN is not set. Did you set it in the Koyeb environment variables?")
        return
    if PPWSA_NOTIFICATION_BOT_ID is None:
        logger.error("Configuration Error: PPWSA_NOTIFICATION_BOT_ID is missing or not a valid integer. Check Koyeb environment variables.")
        return

    # 1. Start the Telegram Bot in a separate thread
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()
    
    # 2. Start the Flask server in the main thread (this blocks and keeps the container alive)
    run_flask_server()

if __name__ == '__main__':
    # Flask uses werkzeug's run_simple, which can cause issues with reloader=True.
    # We call main directly.
    main()
