import re
import logging
import os
from datetime import datetime
from dotenv import load_dotenv

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
# Variables are loaded from the .env file
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# PPWSA_NOTIFICATION_BOT_ID must be cast to an integer
try:
    PPWSA_NOTIFICATION_BOT_ID = int(os.getenv("PPWSA_NOTIFICATION_BOT_ID"))
except (TypeError, ValueError):
    PPWSA_NOTIFICATION_BOT_ID = None # Will be caught in the main function

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Regular Expressions for Extraction ---
# 1. Invoice Number (P- followed by the rest of the string, which we will keep)
INVOICE_PATTERN = re.compile(r'P-([A-Z0-9]+)')

# 2. Cost Price (e.g., 75,800 ៛)
COST_PRICE_PATTERN = re.compile(r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*៛')


# --- Handler Functions ---

async def start_command(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text(
        'Welcome! I am running as a service. I will automatically listen for notifications '
        'from the specified source bot and extract data.'
    )

async def handle_notification_message(update: Update, context: CallbackContext) -> None:
    """Handles incoming messages *only* from the specified PPWSA Notification Bot ID."""
    message = update.message
    text = message.text

    if not text: return

    logger.info(f"Received notification from source bot: {text[:50]}...")

    # 1. Extract Date of Original Message (Fallback Logic)
    original_date_str = None
    date_source_label = "" # Variable to hold the label
    
    # Define the standard format for clean output
    DATE_OUTPUT_FORMAT = "%Y-%m-%d %H:%M:%S"
    
    # Priority 1: Check Telegram's metadata (if it was properly forwarded/attributed)
    if hasattr(message, 'forward_date') and message.forward_date:
        # Use the most accurate date from Telegram metadata
        original_dt = message.forward_date
        original_date_str = original_dt.strftime(DATE_OUTPUT_FORMAT)
        date_source_label = " (Metadata)" # Label for the display header
    else:
        # Fallback: Use the message received time.
        original_dt = message.date
        original_date_str = original_dt.strftime(DATE_OUTPUT_FORMAT)
        date_source_label = " (Fallback)" # Label for the display header


    # APPEND the label to the descriptive message, keeping date_value clean for copying
    date_message = f"Original Message Date{date_source_label}"
    date_value = original_date_str

    # 2. Extract and format Invoice Number
    invoice_match = INVOICE_PATTERN.search(text)
    invoice_number = None
    if invoice_match:
        invoice_number = invoice_match.group(1)
        invoice_message = "Invoice Number"
        invoice_value = invoice_number
    else:
        invoice_message = "Invoice Number"
        invoice_value = "Pattern not found."

    # 3. Extract and format Cost Price
    cost_match = COST_PRICE_PATTERN.search(text)
    cost_price = None
    if cost_match:
        price_str_with_commas = cost_match.group(1)
        cost_price = price_str_with_commas.replace(',', '')
        cost_message = "Cost Price"
        cost_value = cost_price
    else:
        cost_message = "Cost Price"
        cost_value = "Pattern not found."


    # --- Send Individual Messages with Copy Buttons ---
    messages_to_send = [
        (date_message, date_value),
        (invoice_message, invoice_value),
        (cost_message, cost_value),
    ]
    
    # We reply to the original message for context
    for display_text, copy_content in messages_to_send:
        initial_display = f"**{display_text}:**\n`{copy_content}`"
        
        is_pattern_found = (copy_content != "Pattern not found.")
        
        # Only show the copy button if a valid pattern was found OR if it's the date (Fallback included)
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
            # Hide the button for truly missing patterns
            reply_markup = None


        # Send the message (text and button)
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
        # Find the header, which now includes the (Metadata) or (Fallback) label
        header_match = re.match(r'(\*\*.*?\*\*)', original_text)
        header = header_match.group(1) if header_match else ""
        
        # Note: copy_content is a CLEAN string without the label, so this works perfectly.
        new_text = f"{header}\n`{copy_content}`\n\n✅ **Extracted Value**"

        try:
            await query.edit_message_text(
                new_text,
                parse_mode='Markdown',
                reply_markup=None # Remove the button
            )
        except Exception as e:
            logger.warning(f"Failed to edit message: {e}")
            await query.message.reply_text(
                 f"✅ **Extracted Value**\n`{copy_content}`",
                 parse_mode='Markdown'
            )


def main() -> None:
    """Starts the bot in polling mode for continuous operation."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Configuration Error: TELEGRAM_BOT_TOKEN is not set. Check your .env file.")
        return
    if PPWSA_NOTIFICATION_BOT_ID is None:
        logger.error("Configuration Error: PPWSA_NOTIFICATION_BOT_ID is missing or not a valid integer. Check your .env file.")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Use a filter to ONLY process messages from the specific notification bot ID
    ppwsa_notification_filter = filters.User(user_id=PPWSA_NOTIFICATION_BOT_ID)

    # --- Register Handlers ---
    application.add_handler(CommandHandler("start", start_command))
    
    # Message handler filters for text messages from the specific PPWSA notification bot
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ppwsa_notification_filter,
        handle_notification_message
    ))

    # Callback query handler for inline buttons
    application.add_handler(CallbackQueryHandler(button_handler))

    # Start the bot
    logger.info(f"Bot is running, listening for messages from ID: {PPWSA_NOTIFICATION_BOT_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()