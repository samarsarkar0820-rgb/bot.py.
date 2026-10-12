
import os
import re
import time
import uuid
import threading
import logging
from pathlib import Path
from urllib.parse import urlparse

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
import yt_dlp

# ================= CONFIGURATION =================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

BOT_USERNAME = "@AnycontentDownloaderbot"
CHANNEL_URL = "https://youtube.com/@bubiloworld_1m?feature=shared"

# Replace these with your actual Telegram links/usernames.
TUTORIALS_URL = os.environ.get(
    "TUTORIALS_URL", "https://youtube.com/@bubiloworld_1m"
)
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "@YourUsername")

PORT = int(os.environ.get("PORT", "10000"))
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 45 * 1024 * 1024  # Keep below common Bot API limits.

CAPTION = (
    "Downloaded Successfully! Power by: @AnycontentDownloaderbot"
)

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
    "instagram.com", "www.instagram.com",
    "facebook.com", "www.facebook.com",
    "fb.watch", "www.fb.watch",
    "m.facebook.com", "web.facebook.com",
}

if not BOT_TOKEN:
    raise RuntimeError("Please set the BOT_TOKEN environment variable.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# Prevent multiple simultaneous downloads from the same user.
user_locks = {}
locks_guard = threading.Lock()


# ================= FLASK KEEP-ALIVE =================

@app.route("/")
def home():
    return "Anycontent Downloader Bot is running!", 200


@app.route("/health")
def health():
    return {"status": "ok", "bot": BOT_USERNAME}, 200


def keep_alive():
    from waitress import serve
    serve(app, host="0.0.0.0", port=PORT)


# ================= KEYBOARDS =================

def main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)

    keyboard.add(
        InlineKeyboardButton(
            "📢 SUBSCRIBE CHANNEL",
            url=CHANNEL_URL
        ),
        InlineKeyboardButton(
            "🎬 ALL TUTORIALS",
            url=TUTORIALS_URL
        ),
        InlineKeyboardButton(
            "👨‍💻 CONTACT OWNER",
            url="https://t.me/" + OWNER_USERNAME.lstrip("@")
        )
    )
    return keyboard


# ================= START COMMAND =================

@bot.message_handler(commands=["start"])
def start_command(message):
    welcome = (
        "✨ <b>WELCOME TO ANYCONTENT DOWNLOADER</b> ✨\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎬 <b>Your All-in-One Video Downloader</b>\n\n"
        "📥 Download supported public videos from:\n"
        "• YouTube\n"
        "• Instagram Reels\n"
        "• Facebook Videos\n\n"
        "🚀 <b>How to use:</b>\n"
        "1️⃣ Copy a video link.\n"
        "2️⃣ Send the link here.\n"
        "3️⃣ Wait while your video is processed.\n\n"
        "⚡ Simple • Fast • Easy to Use\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💎 <i>Powered by Anycontent Downloader</i>"
    )

    bot.send_message(
        message.chat.id,
        welcome,
        reply_markup=main_keyboard()
    )


# ================= URL VALIDATION =================

def extract_valid_url(text):
    match = re.search(r"https?://[^\s<>]+", text or "")
    if not match:
        return None

    url = match.group(0).rstrip(".,!?)']\"")
    parsed = urlparse(url)

    if parsed.scheme != "https":
        return None

    host = (parsed.hostname or "").lower()

    if host not in ALLOWED_HOSTS:
        return None

    return url


# ================= DOWNLOAD HELPERS =================

def progress_hook(status, message, chat_id):
    # Download progress updates are throttled.
    if status.get("status") != "downloading":
        return

    downloaded = status.get("downloaded_bytes", 0)
    total = status.get("total_bytes") or status.get("total_bytes_estimate")

    if total and downloaded >= total:
        return

    percent = int(downloaded * 100 / total) if total else 0

    # Update every ~20 percentage points.
    bucket = min(4, percent // 20)
    now = time.monotonic()
    key = (chat_id, message.message_id)

    if not hasattr(progress_hook, "last_updates"):
        progress_hook.last_updates = {}

    previous = progress_hook.last_updates.get(key, (-1, 0))
    previous_bucket, previous_time = previous

    if bucket > previous_bucket and now - previous_time >= 1.5:
        try:
            bot.edit_message_text(
                f"⬇️ <b>Downloading ({percent}%)...</b>",
                chat_id,
                message.message_id
            )
            progress_hook.last_updates[key] = (bucket, now)
        except Exception:
            pass


def download_video(url, chat_id, progress_message):
    unique_name = uuid.uuid4().hex
    output_template = str(DOWNLOAD_DIR / f"{unique_name}.%(ext)s")

    options = {
        "format": "best[ext=mp4][filesize<45M]/"
                  "best[filesize<45M]/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "max_filesize": MAX_FILE_SIZE,
        "socket_timeout": 30,
        "retries": 2,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "progress_hooks": [
            lambda status: progress_hook(
                status, progress_message, chat_id
            )
        ],
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        filename = downloader.prepare_filename(info)

    path = Path(filename)

    if not path.exists():
        candidates = list(DOWNLOAD_DIR.glob(unique_name + ".*"))
        if candidates:
            path = candidates[0]
        else:
            raise FileNotFoundError("Downloaded file was not found.")

    if path.stat().st_size > MAX_FILE_SIZE:
        path.unlink(missing_ok=True)
        raise ValueError(
            "This video exceeds the bot's 45 MB upload limit."
        )

    return path


# ================= VIDEO HANDLER =================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: not (
        message.text or ""
    ).startswith("/")
)
def handle_video_link(message):
    url = extract_valid_url(message.text)

    if not url:
        bot.reply_to(
            message,
            "❌ Please send a valid HTTPS YouTube, "
            "Instagram or Facebook video link."
        )
        return

    user_id = message.from_user.id

    with locks_guard:
        if user_id in user_locks:
            bot.reply_to(
                message,
                "⏳ Your previous download is still processing. "
                "Please wait."
            )
            return

        user_locks[user_id] = True

    progress_message = bot.reply_to(
        message,
        "🔎 <b>Analyzing...</b>"
    )

    file_path = None

    try:
        # The same message is edited for each progress stage.
        time.sleep(0.5)

        bot.edit_message_text(
            "⬇️ <b>Downloading (50%)...</b>",
            message.chat.id,
            progress_message.message_id
        )

        file_path = download_video(
            url, message.chat.id, progress_message
        )

        bot.edit_message_text(
            "⬆️ <b>Uploading (100%)...</b>",
            message.chat.id,
            progress_message.message_id
        )

        with open(file_path, "rb") as video:
            bot.send_video(
                message.chat.id,
                video,
                caption=CAPTION,
                supports_streaming=True,
                timeout=180
            )

        bot.edit_message_text(
            "✅ <b>Download completed successfully!</b>",
            message.chat.id,
            progress_message.message_id
        )

    except Exception as error:
        logging.exception("Download failed")
        error_text = str(error)

        if "45 MB" in error_text or "45 MB" in repr(error):
            reply = "❌ Video is too large. Maximum supported size: 45 MB."
        else:
            reply = (
                "❌ Unable to process this video.\n\n"
                "It may be private, restricted, unavailable, "
                "or unsupported. Please try another permitted link."
            )

        try:
            bot.edit_message_text(
                reply,
                message.chat.id,
                progress_message.message_id
            )
        except Exception:
            bot.send_message(message.chat.id, reply)

    finally:
        # Always remove temporary downloaded files.
        if file_path and file_path.exists():
            try:
                os.remove(file_path)
            except OSError:
                logging.exception("Could not remove downloaded file")

        # Also clean any leftover files created by yt-dlp.
        for leftover in DOWNLOAD_DIR.glob("*"):
            try:
                if leftover.is_file():
                    os.remove(leftover)
            except OSError:
                logging.exception("Temporary file cleanup failed")

        with locks_guard:
            user_locks.pop(user_id, None)


# ================= START BOT =================

if __name__ == "__main__":
    threading.Thread(
        target=keep_alive,
        daemon=True
    ).start()

    logging.info("Starting %s", BOT_USERNAME)
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
  )
      
