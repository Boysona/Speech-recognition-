import os
import re
import uuid
import shutil
import logging
import requests
import telebot
import json
from flask import Flask, request, abort
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import speech_recognition as sr
import imageio_ffmpeg as ffmpeg
from pydub import AudioSegment
import threading
import time
import subprocess
import io

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BOT CONFIGURATION ---
TOKEN = "7790991731:AAHZks7W-iEwp6pcKD56eOeq3wduPjAiwow" # Replace with your actual bot token
WEBHOOK_URL = "https://speech-recognition-6i0c.onrender.com" # Replace with your actual Render URL
ADMIN_ID = 5978150981 # Replace with your actual Admin ID

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Download directory (used for temporary file download before in-memory processing)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- User tracking files ---
users_file = 'users.json'
user_data = {}
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        try:
            user_data = json.load(f)
        except json.JSONDecodeError:
            user_data = {}

# User-specific language settings for translate/summarize
user_language_settings_file = 'user_language_settings.json'
user_language_settings = {}
if os.path.exists(user_language_settings_file):
    with open(user_language_settings_file, 'r') as f:
        try:
            user_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_language_settings = {}

# User-specific media language settings for speech recognition
user_media_language_settings_file = 'user_media_language_settings.json'
user_media_language_settings = {}
if os.path.exists(user_media_language_settings_file):
    with open(user_media_language_settings_file, 'r') as f:
        try:
            user_media_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_media_language_settings = {}

def save_user_data():
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)

def save_user_language_settings():
    with open(user_language_settings_file, 'w') as f:
        json.dump(user_language_settings, f, indent=4)

def save_user_media_language_settings():
    with open(user_media_language_settings_file, 'w') as f:
        json.dump(user_media_language_settings, f, indent=4)

# In-memory chat history and transcription store
user_memory = {}
user_transcriptions = {}
processing_message_ids = {} # To store message IDs of progress messages

# Statistics counters (global variables)
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock() # To prevent race conditions

GEMINI_API_KEY = "AIzaSyAto78yGVZobkOwPXnl8wCE9ZW8Do2R8HA" # Replace with your actual Gemini API Key

# --- Constants for progress bar and chunking ---
PROGRESS_BAR_LENGTH = 15
CHUNK_LENGTH_MS = 20000 # 20 seconds
OVERLAP_MS = 1000 # 1 second overlap for smoother transitions

# --- Data Retention ---
DATA_RETENTION_DAYS = 7

def cleanup_old_data():
    """Cleans up old user_transcriptions and user_memory."""
    logging.info("Starting cleanup of old data...")
    now = datetime.now()
    
    # Clean up user_transcriptions
    transcriptions_cleaned = 0
    for user_id in list(user_transcriptions.keys()):
        messages_to_remove = []
        for msg_id, data in user_transcriptions[user_id].items():
            # If transcription is stored as a dict with 'timestamp'
            if isinstance(data, dict) and 'timestamp' in data:
                transcription_time = datetime.fromisoformat(data['timestamp'])
                if (now - transcription_time).days > DATA_RETENTION_DAYS:
                    messages_to_remove.append(msg_id)
            # If transcription is stored directly as string (older format)
            elif isinstance(data, str) and (now - bot_start_time).days > DATA_RETENTION_DAYS: # rudimentary check
                # This part might need more precise logic if old entries don't have timestamps
                # For now, if no timestamp, it will eventually be removed after bot_start_time + retention
                pass 
        for msg_id in messages_to_remove:
            del user_transcriptions[user_id][msg_id]
            transcriptions_cleaned += 1
        if not user_transcriptions[user_id]:
            del user_transcriptions[user_id]

    # Clean up user_memory (chat history)
    memory_cleaned = 0
    for user_id in list(user_memory.keys()):
        # Assuming user_memory entries are added recently, or we need a timestamp in user_memory
        # For simplicity, let's just clear if the last activity was old
        if user_id in user_data:
            last_activity = datetime.fromisoformat(user_data[user_id])
            if (now - last_activity).days > DATA_RETENTION_DAYS:
                del user_memory[user_id]
                memory_cleaned += 1

    logging.info(f"Cleanup complete. Removed {transcriptions_cleaned} old transcriptions and {memory_cleaned} user memories.")
    # Schedule next cleanup
    threading.Timer(86400, cleanup_old_data).start() # Run once every 24 hours

def ask_gemini(user_id, user_message):
    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    
    try:
        resp = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": parts}]})
        resp.raise_for_status() # Raise an exception for bad status codes
        result = resp.json()
        if "candidates" in result:
            reply = result['candidates'][0]['content']['parts'][0]['text']
            user_memory[user_id].append({"role": "model", "text": reply})
            return reply
        return "Error: " + json.dumps(result)
    except requests.exceptions.RequestException as e:
        logging.error(f"Gemini API request failed: {e}")
        return f"Error: Failed to connect to AI service. Please try again later. ({e})"
    except Exception as e:
        logging.error(f"Error parsing Gemini response: {e}")
        return f"Error: Failed to process AI response. ({e})"

FILE_SIZE_LIMIT = 20 * 1024 * 1024 # 20MB
admin_state = {}

def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "👋Get a welcome message and info"),
        telebot.types.BotCommand("status", "📊View Bot statistics"),
        telebot.types.BotCommand("help", "❓Get information on how to use the bot"),
        telebot.types.BotCommand("language", "🌐Change preferred language for translate/summarize"),
        telebot.types.BotCommand("media_language", "📝Set language for media transcription"),
        telebot.types.BotCommand("privacy", "👮Privacy Notice"),
    ]
    bot.set_my_commands(commands)

    bot.set_my_short_description(
        "Got media files? Let this free bot transcribe, summarize, and translate them in seconds!"
    )

    bot.set_my_description(
        """This bot quickly transcribes, summarizes, and translates voice messages, audio files, and videos—free!

     🔥Enjoy free usage and start now!👌🏻"""
    )

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

# Function to update uptime message
def update_uptime_message(chat_id, message_id, stop_event):
    """
    Live-update the admin uptime message every second, showing days, hours, minutes and seconds.
    """
    while not stop_event.is_set():
        try:
            # Compute total elapsed seconds since bot_start_time
            elapsed = datetime.now() - bot_start_time
            total_seconds = int(elapsed.total_seconds())
            days, rem = divmod(total_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)

            # Format with leading zeros for hours/minutes/seconds
            uptime_text = (
                f"**Bot Uptime:**\n"
                f"{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds"
            )

            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=uptime_text,
                parse_mode="Markdown"
            )
            # Wait exactly 1 second before next update
            time.sleep(1)

        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
            break # Exit loop if message is gone or other persistent error
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if user_id not in user_data:
        user_data[user_id] = datetime.now().isoformat()
        save_user_data()

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "Admin Panel and Uptime (updating live)...", reply_markup=keyboard)

        with admin_uptime_lock:
            # Stop any previous uptime thread for this admin if exists
            if ADMIN_ID in admin_uptime_message and admin_uptime_message[ADMIN_ID].get('stop_event'):
                admin_uptime_message[ADMIN_ID]['stop_event'].set()
                if admin_uptime_message[ADMIN_ID].get('thread') and admin_uptime_message[ADMIN_ID]['thread'].is_alive():
                    admin_uptime_message[ADMIN_ID]['thread'].join(timeout=1) # Give it a moment to stop

            stop_event = threading.Event()
            admin_uptime_message[ADMIN_ID] = {'message_id': sent_message.message_id, 'chat_id': message.chat.id, 'stop_event': stop_event}
            uptime_thread = threading.Thread(target=update_uptime_message, args=(message.chat.id, sent_message.message_id, stop_event))
            uptime_thread.daemon = True
            uptime_thread.start()
            admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread

    else:
        display_name = message.from_user.first_name or (f"@{message.from_user.username}" if message.from_user.username else "user")
        bot.send_message(
            message.chat.id,
            f"""👋🏻 Welcome dear {display_name}!
I'm your media transcription bot.
• Send me:
• Voice message
• Video message
• Audio file
• to transcribe for free!
**Before sending a media file for transcription, use /media_language to set the language of the audio.**
"""
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = (
        """ℹ️ How to use this bot:

This bot transcribes voice messages, audio files, and videos using advanced AI.

1.  **Send a File for Transcription:**
    * Send a voice message, audio file, or video to the bot.
    * **Crucially**, before sending your media, use the `/media_language` command to tell the bot the language of the audio. This ensures the most accurate transcription possible.
    * The bot will then process your media and send back the transcribed text. If the transcription is very long, it will be sent as a text file for easier reading.
    * After receiving the transcription, you'll see inline buttons with options to **Translate** or **Summarize** the text.

2.  **Commands:**
    * `/start`: Get a welcome message and info about the bot. (Admins see a live uptime panel).
    * `/status`: View detailed statistics about the bot's performance and usage.
    * `/help`: Display these instructions on how to use the bot.
    * `/language`: Change your preferred language for translations and summaries. This setting applies to text outputs, not the original media.
    * `/media_language`: Set the language of the audio in your media files for transcription. This is vital for accuracy.
    * `/privacy`: Read the bot's privacy notice to understand how your data is handled.

Enjoy transcribing, translating, and summarizing your media quickly and easily!
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    privacy_text = (
        """**Privacy Notice**

Your privacy is paramount. Here's a transparent look at how this bot handles your data in real-time:

1.  **Data We Process & Its Lifecycle:**
    * **Media Files (Voice, Audio, Video):** When you send a media file, it's temporarily downloaded for **immediate transcription**. Crucially, these files are **deleted instantly** from our servers once the transcription is complete. We do not store your media content.
    * **Transcriptions:** The text generated from your media is held **temporarily in the bot's memory** for a limited period. This allows for follow-up actions like translation or summarization. This data is not permanently stored on our servers and is cleared regularly (e.g., when new media is processed or the bot restarts).
    * **User IDs:** Your Telegram User ID is stored. This helps us remember your language preferences and track basic, aggregated activity (like when you last used the bot) to improve service and understand overall usage patterns. This ID is not linked to any personal identifying information outside of Telegram.
    * **Language Preferences:** Your chosen languages for translations/summaries and media transcription are saved. This ensures you don't need to re-select them for every interaction, making your experience smoother.

2.  **How Your Data is Used:**
    * To deliver the bot's core services: transcribing, translating, and summarizing your media.
    * To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total files processed).
    * To maintain your personalized language settings across sessions.

3.  **Data Sharing Policy:**
    * We **do not share** your personal data, media files, or transcriptions with any third parties.
    * Transcription, translation, and summarization are facilitated by integrating with advanced AI models (specifically, the Google Speech-to-Text API for transcription and the Gemini API for translation/summarization). Your input sent to these models is governed by their respective privacy policies, but we ensure that your data is **not stored by us** after processing by these services.

4.  **Data Retention:**
    * **Media files:** Deleted immediately post-transcription.
    * **Transcriptions:** Held temporarily in the bot's active memory for immediate use.
    * **User IDs and language preferences:** Retained to support your settings and for anonymous usage statistics. If you wish to have your stored preferences removed, you can cease using the bot or contact the bot administrator for explicit data deletion.

By using this bot, you acknowledge and agree to the data practices outlined in this Privacy Notice.

Should you have any questions or concerns regarding your privacy, please feel free to contact the bot administrator.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    update_user_activity(message.from_user.id)

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    today = datetime.now().date()
    active_today = sum(
        1 for timestamp in user_data.values()
        if datetime.fromisoformat(timestamp).date() == today
    )

    total_proc_seconds = int(total_processing_time)
    proc_hours = total_proc_seconds // 3600
    proc_minutes = (total_proc_seconds % 3600) // 60
    proc_seconds = total_proc_seconds % 60

    text = (
        "📊 Bot Statistics\n\n"
        "🟢 **Bot Status: Online**\n"
        f"⏳ Uptime: {days} days, {hours} hours, {minutes} minutes, {seconds} seconds\n\n"
        "👥 User Statistics\n"
        f"▫️ Total Users Today: {active_today}\n"
        f"▫️ Total Registered Users: {len(user_data)}\n\n"
        "⚙️ Processing Statistics\n"
        f"▫️ Total Files Processed: {total_files_processed}\n"
        f"▫️ Audio Files: {total_audio_files}\n"
        f"▫️ Voice Clips: {total_voice_clips}\n"
        f"▫️ Videos: {total_videos}\n"
        f"⏱️ Total Processing Time: {proc_hours} hours {proc_minutes} minutes {proc_seconds} seconds\n\n"
        "⸻\n\n"
        "Thanks for using our service! 🙌"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Total registered users: {len(user_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    for uid_key in user_data:
        uid = int(uid_key) # Ensure UID is int for telebot methods
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid}: {e}")
            fail += 1
    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

def keep_typing(chat_id, stop_event):
    """Sends 'typing' action to the chat until stop_event is set."""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except telebot.apihelper.ApiTelegramException as e:
            if "chat not found" in str(e).lower() or "user is deactivated" in str(e).lower():
                logging.warning(f"Typing action failed for chat {chat_id}: {e}. Stopping typing thread.")
                break
            logging.error(f"Error sending typing action: {e}")
            time.sleep(4) # Still wait to prevent spamming
        except Exception as e:
            logging.error(f"Unexpected error in typing thread: {e}")
            break

def update_progress_message(chat_id, message_id, current_step, total_steps, status_text, stop_event, file_size=0, current_download_size=0):
    """Updates a message with a dynamic progress bar and status text."""
    emojis = ['▓', '░']
    start_time = time.time()
    
    while not stop_event.is_set():
        try:
            # Calculate progress based on step or specific download progress
            progress = (current_step - 1) / total_steps if total_steps > 0 else 0
            if status_text.startswith("📥Downloading") and file_size > 0:
                progress = current_download_size / file_size if file_size > 0 else 0
            
            filled_blocks = int(PROGRESS_BAR_LENGTH * progress)
            empty_blocks = PROGRESS_BAR_LENGTH - filled_blocks
            
            progress_bar = f"[{emojis[0] * filled_blocks}{emojis[1] * empty_blocks}]"
            percentage = int(progress * 100)
            
            # Adaptive speed: faster for smaller files, slower for larger files
            # This is more about how quickly the UI updates, not actual processing speed
            # The sleep duration can be adjusted, but for a game-like bar, it should be noticeable.
            # For network-bound operations (download), actual progress updates are better.
            
            dynamic_status_text = status_text
            if status_text.startswith("📥Downloading") and file_size > 0:
                downloaded_mb = current_download_size / (1024 * 1024)
                total_mb = file_size / (1024 * 1024)
                dynamic_status_text = f"🔄 Step {current_step}/{total_steps}: 📥Downloading {downloaded_mb:.2f}MB / {total_mb:.2f}MB"
            elif status_text.startswith("Transcribing"):
                # For transcribing, just show the bar filling, actual percentage isn't easily calculable in chunks
                pass
            
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"🔄 {dynamic_status_text} {progress_bar} {percentage}%",
                parse_mode="Markdown"
            )
            
            # Sleep duration to control animation speed
            sleep_duration = 0.5 - (progress * 0.4) # Faster as it progresses
            time.sleep(max(0.1, sleep_duration)) # Minimum sleep to avoid excessive updates
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating progress message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in progress thread: {e}")
            break

def process_media_file_threaded(message, file_obj, original_file_extension):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
    uid = str(message.from_user.id)
    chat_id = message.chat.id
    message_id = message.message_id
    
    # Start typing indicator
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()

    progress_msg = None
    stop_progress = threading.Event()
    progress_thread = None

    try:
        progress_msg = bot.send_message(chat_id, "🔄 Step 1/3: 📥Downloading your file...")
        processing_message_ids[message.message_id] = progress_msg.message_id # Store for retry callback
        
        progress_thread = threading.Thread(target=update_progress_message, args=(chat_id, progress_msg.message_id, 1, 3, "📥Downloading your file", stop_progress, file_obj.file_size, 0))
        progress_thread.daemon = True
        progress_thread.start()

        file_info = bot.get_file(file_obj.file_id)
        download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        
        audio_bytes_io = io.BytesIO()
        downloaded_size = 0
        response = requests.get(download_url, stream=True)
        response.raise_for_status() # Raise an exception for bad status codes
        
        for chunk in response.iter_content(chunk_size=8192):
            audio_bytes_io.write(chunk)
            downloaded_size += len(chunk)
            # Update progress bar with actual download progress
            if progress_thread and progress_thread.is_alive():
                progress_thread._args = (chat_id, progress_msg.message_id, 1, 3, "📥Downloading your file", stop_progress, file_obj.file_size, downloaded_size)
        
        audio_bytes_io.seek(0) # Rewind to the beginning of the BytesIO object
        
        stop_progress.set()
        if progress_thread and progress_thread.is_alive():
            progress_thread.join(timeout=5) # Wait for progress thread to finish
        bot.edit_message_text("🔄 Step 2/3: Converting audio format...", chat_id, progress_msg.message_id)
        stop_progress.clear() # Reset for next stage
        progress_thread = threading.Thread(target=update_progress_message, args=(chat_id, progress_msg.message_id, 2, 3, "Converting audio format", stop_progress))
        progress_thread.daemon = True
        progress_thread.start()
        
        processing_start_time = datetime.now()
        
        # In-memory conversion using pydub and subprocess for ffmpeg
        # Use the derived original_file_extension here
        input_audio = AudioSegment.from_file(audio_bytes_io, format=original_file_extension)
        
        wav_buffer = io.BytesIO()
        input_audio.export(wav_buffer, format="wav", parameters=["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1"])
        wav_buffer.seek(0) # Rewind for speech recognition
        
        stop_progress.set()
        if progress_thread and progress_thread.is_alive():
            progress_thread.join(timeout=5)
        bot.edit_message_text("✍🏻️ Step 3/3: Transcribing...", chat_id, progress_msg.message_id)
        stop_progress.clear()
        progress_thread = threading.Thread(target=update_progress_message, args=(chat_id, progress_msg.message_id, 3, 3, "Transcribing", stop_progress))
        progress_thread.daemon = True
        progress_thread.start()

        media_lang_code = get_lang_code(user_media_language_settings[uid])
        if not media_lang_code:
            raise ValueError(f"The language *{user_media_language_settings[uid]}* does not have a valid code. Please re-select the language.")

        transcription = transcribe_audio_chunks(wav_buffer, media_lang_code) or ""
        
        user_transcriptions.setdefault(uid, {})[message.message_id] = {"text": transcription, "timestamp": datetime.now().isoformat()}

        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        elif message.video or message.video_note:
            total_videos += 1

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        total_processing_time += processing_time

        buttons = InlineKeyboardMarkup()
        buttons.add(
            InlineKeyboardButton("Translate ", callback_data=f"btn_translate|{message.message_id}"),
            InlineKeyboardButton("Summarize ", callback_data=f"btn_summarize|{message.message_id}")
        )
        
        stop_progress.set() # Ensure progress bar stops at 100%
        if progress_thread and progress_thread.is_alive():
            progress_thread.join(timeout=5)
        
        if len(transcription) > 4000:
            fn = 'transcription.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(transcription)
            bot.send_chat_action(chat_id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(
                    chat_id,
                    doc,
                    reply_to_message_id=message.message_id,
                    reply_markup=buttons,
                    caption="Here’s your transcription. Tap a button below for more options."
                )
            os.remove(fn)
        else:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg.message_id,
                text=transcription,
                reply_markup=buttons
            )

    except requests.exceptions.RequestException as e:
        error_message = f"😓 Sorry, I couldn't download the file. There was a network issue. Please try again. ({e})"
        logging.error(f"Download error: {e}")
        if progress_msg:
            bot.edit_message_text(error_message, chat_id, progress_msg.message_id)
        else:
            bot.send_message(chat_id, error_message)
    except subprocess.CalledProcessError as e:
        error_message = f"😓 Sorry, the audio conversion failed. The file might be corrupted or in an unsupported format. Please try again. (FFmpeg Error: {e.stderr.decode().strip()})"
        logging.error(f"FFmpeg conversion error: {e.stderr.decode()}")
        if progress_msg:
            bot.edit_message_text(error_message, chat_id, progress_msg.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
        else:
            bot.send_message(chat_id, error_message, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
    except sr.UnknownValueError:
        error_message = "😓 Sorry, I couldn't understand the audio. It might be too noisy or the language set might be incorrect. Please try again with clearer audio or double-check the language."
        logging.warning("Speech Recognition could not understand audio.")
        if progress_msg:
            bot.edit_message_text(error_message, chat_id, progress_msg.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
        else:
            bot.send_message(chat_id, error_message, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
    except sr.RequestError as e:
        error_message = f"😓 Sorry, I couldn't connect to the speech recognition service. Please check your internet connection or try again later. ({e})"
        logging.error(f"Speech Recognition service error: {e}")
        if progress_msg:
            bot.edit_message_text(error_message, chat_id, progress_msg.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
        else:
            bot.send_message(chat_id, error_message, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
    except ValueError as e:
        error_message = f"😓 Sorry, there was an issue with your request: {e}. Please try again."
        logging.error(f"Value error: {e}")
        if progress_msg:
            bot.edit_message_text(error_message, chat_id, progress_msg.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
        else:
            bot.send_message(chat_id, error_message, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
    except Exception as e:
        error_message = f"😓 Sorry, an unexpected error occurred: {e}. Please try again later."
        logging.critical(f"Unhandled error during file processing: {e}", exc_info=True)
        if progress_msg:
            bot.edit_message_text(error_message, chat_id, progress_msg.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
        else:
            bot.send_message(chat_id, error_message, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔁 Retry", callback_data=f"retry|{message.message_id}")))
    finally:
        stop_typing.set()
        if typing_thread and typing_thread.is_alive():
            typing_thread.join(timeout=1)
        
        stop_progress.set()
        if progress_thread and progress_thread.is_alive():
            progress_thread.join(timeout=5)
        
        # In-memory processing, no local file to clean up from `handle_file` itself.
        # The `DOWNLOAD_DIR` is only for other temporary uses or old code.


@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    update_user_activity(message.from_user.id)
    uid = str(message.from_user.id)

    if uid not in user_media_language_settings:
        bot.send_message(message.chat.id,
                         "⚠️ Please first select the language of the audio file using /media_language before sending the file.")
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "The file size you uploaded is too large (max allowed is 20MB).")

    file_extension = ""
    try:
        # Get file_info first to correctly determine extension from file_path
        file_info = bot.get_file(file_obj.file_id)
        if file_info.file_path:
            file_extension = os.path.splitext(file_info.file_path)[1].lstrip('.')
            if not file_extension: # Fallback if extension is empty for some reason
                if message.voice: file_extension = "ogg"
                elif message.audio: file_extension = "mp3" # Common audio fallback
                elif message.video or message.video_note: file_extension = "mp4" # Common video fallback
        else:
            # Fallback if file_path is not available (unlikely for Telegram media)
            if message.voice: file_extension = "ogg"
            elif message.audio: file_extension = "mp3"
            elif message.video: file_extension = "mp4"
            elif message.video_note: file_extension = "mp4"

        # Set reaction based on actual file type
        if message.voice:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=["audios", "😮"])
        elif message.audio:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=["🎧", "👂"])
        elif message.video or message.video_note:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=["🎬", "🎞️"])
    except Exception as e:
        logging.error(f"Error determining file extension or setting reaction: {e}")
        # As a last resort, if extension couldn't be determined, use a common one or raise error.
        # For now, let's fall back more gracefully.
        if not file_extension:
            if message.voice: file_extension = "ogg"
            elif message.audio: file_extension = "mp3"
            elif message.video or message.video_note: file_extension = "mp4"
            else:
                bot.send_message(message.chat.id, "😓 Sorry, I couldn't determine the file type. Please try again.")
                return

    # Start processing in a new thread
    threading.Thread(target=process_media_file_threaded, args=(message, file_obj, file_extension)).start()

@bot.callback_query_handler(func=lambda c: c.data.startswith("retry|"))
def retry_handler(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    original_message_id = int(call.data.split("|")[1])

    # Try to retrieve the original message to re-process it
    try:
        original_message = bot.get_message(call.message.chat.id, original_message_id)
        # Check if the original message still contains a media object
        if original_message.voice or original_message.audio or original_message.video or original_message.video_note:
            bot.edit_message_text("🔄 Retrying processing...", call.message.chat.id, call.message.message_id)
            # This calls the handle_file which will then start a new thread for process_media_file_threaded
            handle_file(original_message)
        else:
            bot.edit_message_text("❌ The original media file was not found. Please send it again.", call.message.chat.id, call.message.message_id)
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error retrieving original message for retry: {e}")
        bot.edit_message_text("❌ Could not re-process the file. The original message might have been deleted or is too old.", call.message.chat.id, call.message.message_id)
    except Exception as e:
        logging.error(f"Unexpected error during retry: {e}")
        bot.edit_message_text(f"❌ An unexpected error occurred during retry: {e}. Please try sending the file again.", call.message.chat.id, call.message.message_id)
    
    bot.answer_callback_query(call.id)


# --- Language Selection and Saving ---
LANGUAGES = [
    {"name": "English", "flag": "🇬🇧", "code": "en-US"},
    {"name": "Chinese", "flag": "🇨🇳", "code": "zh-CN"},
    {"name": "Spanish", "flag": "🇪🇸", "code": "es-ES"},
    {"name": "Hindi", "flag": "🇮🇳", "code": "hi-IN"},
    {"name": "Arabic", "flag": "🇸🇦", "code": "ar-SA"},
    {"name": "French", "flag": "🇫🇷", "code": "fr-FR"},
    {"name": "Bengali", "flag": "🇧🇩", "code": "bn-BD"},
    {"name": "Russian", "flag": "🇷🇺", "code": "ru-RU"},
    {"name": "Portuguese", "flag": "🇵🇹", "code": "pt-PT"},
    {"name": "Urdu", "flag": "🇵🇰", "code": "ur-PK"},
    {"name": "German", "flag": "🇩🇪", "code": "de-DE"},
    {"name": "Japanese", "flag": "🇯🇵", "code": "ja-JP"},
    {"name": "Korean", "flag": "🇰🇷", "code": "ko-KR"},
    {"name": "Vietnamese", "flag": "🇻🇳", "code": "vi-VN"},
    {"name": "Turkish", "flag": "🇹🇷", "code": "tr-TR"},
    {"name": "Italian", "flag": "🇮🇹", "code": "it-IT"},
    {"name": "Thai", "flag": "🇹🇭", "code": "th-TH"},
    {"name": "Swahili", "flag": "🇰🇪", "code": "sw-KE"},
    {"name": "Dutch", "flag": "🇳🇱", "code": "nl-NL"},
    {"name": "Polish", "flag": "🇵🇱", "code": "pl-PL"},
    {"name": "Ukrainian", "flag": "🇺🇦", "code": "uk-UA"},
    {"name": "Indonesian", "flag": "🇮🇩", "code": "id-ID"},
    {"name": "Malay", "flag": "🇲🇾", "code": "ms-MY"},
    {"name": "Filipino", "flag": "🇵🇭", "code": "fil-PH"},
    {"name": "Persian", "flag": "🇮🇷", "code": "fa-IR"},
    {"name": "Amharic", "flag": "🇪🇹", "code": "am-ET"},
    {"name": "Somali", "flag": "🇸🇴", "code": "so-SO"},
    {"name": "Swedish", "flag": "🇸🇪", "code": "sv-SE"},
    {"name": "Norwegian", "flag": "🇳🇴", "code": "nb-NO"},
    {"name": "Danish", "flag": "🇩🇰", "code": "da-DK"},
    {"name": "Finnish", "flag": "🇫🇮", "code": "fi-FI"},
    {"name": "Greek", "flag": "🇬🇷", "code": "el-GR"},
    {"name": "Hebrew", "flag": "🇮🇱", "code": "he-IL"},
    {"name": "Czech", "flag": "🇨🇿", "code": "cs-CZ"},
    {"name": "Hungarian", "flag": "🇭🇺", "code": "hu-HU"},
    {"name": "Romanian", "flag": "🇷🇴", "code": "ro-RO"},
    {"name": "Nepali", "flag": "🇳🇵", "code": "ne-NP"},
    {"name": "Sinhala", "flag": "🇱🇰", "code": "si-LK"},
    {"name": "Tamil", "flag": "🇮🇳", "code": "ta-IN"},
    {"name": "Telugu", "flag": "🇮🇳", "code": "te-IN"},
    {"name": "Kannada", "flag": "🇮🇳", "code": "kn-IN"},
    {"name": "Malayalam", "flag": "🇮🇳", "code": "ml-IN"},
    {"name": "Gujarati", "flag": "🇮🇳", "code": "gu-IN"},
    {"name": "Punjabi", "flag": "🇮🇳", "code": "pa-IN"},
    {"name": "Marathi", "flag": "🇮🇳", "code": "mr-IN"},
    {"name": "Oriya", "flag": "🇮🇳", "code": "or-IN"},
    {"name": "Assamese", "flag": "🇮🇳", "code": "as-IN"},
    {"name": "Khmer", "flag": "🇰🇭", "code": "km-KH"},
    {"name": "Lao", "flag": "🇱🇦", "code": "lo-LA"},
    {"name": "Burmese", "flag": "🇲🇲", "code": "my-MM"},
    {"name": "Georgian", "flag": "🇬🇪", "code": "ka-GE"},
    {"name": "Armenian", "flag": "🇦🇲", "code": "hy-AM"},
    {"name": "Azerbaijani", "flag": "🇦🇿", "code": "az-AZ"},
    {"name": "Kazakh", "flag": "🇰🇿", "code": "kk-KZ"},
    {"name": "Uzbek", "flag": "🇺🇿", "code": "uz-UZ"},
    {"name": "Kyrgyz", "flag": "🇰🇬", "code": "ky-KG"},
    {"name": "Tajik", "flag": "🇹🇯", "code": "tg-TJ"},
    {"name": "Turkmen", "flag": "🇹🇲", "code": "tk-TM"},
    {"name": "Mongolian", "flag": "🇲🇳", "code": "mn-MN"},
    {"name": "Estonian", "flag": "🇪🇪", "code": "et-EE"},
    {"name": "Latvian", "flag": "🇱🇻", "code": "lv-LV"},
    {"name": "Lithuanian", "flag": "🇱🇹", "code": "lt-LT"},
    {"name": "Afrikaans", "flag": "🇿🇦", "code": "af-ZA"},
    {"name": "Albanian", "flag": "🇦🇱", "code": "sq-AL"},
    {"name": "Bosnian", "flag": "🇧🇦", "code": "bs-BA"},
    {"name": "Bulgarian", "flag": "🇧🇬", "code": "bg-BG"},
    {"name": "Catalan", "flag": "🇪🇸", "code": "ca-ES"},
    {"name": "Croatian", "flag": "🇭🇷", "code": "hr-HR"},
    {"name": "Estonian", "flag": "🇪🇪", "code": "et-EE"},
    {"name": "Galician", "flag": "🇪🇸", "code": "gl-ES"},
    {"name": "Icelandic", "flag": "🇮🇸", "code": "is-IS"},
    {"name": "Irish", "flag": "🇮🇪", "code": "ga-IE"},
    {"name": "Macedonian", "flag": "🇲🇰", "code": "mk-MK"},
    {"name": "Maltese", "flag": "🇲🇹", "code": "mt-MT"},
    {"name": "Serbian", "flag": "🇷🇸", "code": "sr-RS"},
    {"name": "Slovak", "flag": "🇸🇰", "code": "sk-SK"},
    {"name": "Slovenian", "flag": "🇸🇮", "code": "sl-SI"},
    {"name": "Welsh", "flag": "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "code": "cy-GB"},
    {"name": "Zulu", "flag": "🇿🇦", "code": "zu-ZA"},
]

def get_lang_code(lang_name):
    for lang in LANGUAGES:
        if lang['name'].lower() == lang_name.lower():
            return lang['code']
    return None

def generate_language_keyboard(callback_prefix, message_id=None):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang in LANGUAGES:
        cb_data = f"{callback_prefix}|{lang['name']}"
        if message_id is not None:
            cb_data += f"|{message_id}"
        buttons.append(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb_data))
    markup.add(*buttons)
    return markup

@bot.message_handler(commands=['language'])
def select_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    markup = generate_language_keyboard("set_lang")
    bot.send_message(
        message.chat.id,
        "Please select your preferred language for future **translations and summaries**:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_lang|"))
def callback_set_language(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    _, lang = call.data.split("|", 1)
    user_language_settings[uid] = lang
    save_user_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Your preferred language for translations and summaries has been set to: **{lang}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Language set to {lang}")

@bot.message_handler(commands=['media_language'])
def select_media_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "Please choose the language of the audio files that you need me to transcribe. This helps ensure accurate reading.",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_media_lang|"))
def callback_set_media_language(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    _, lang = call.data.split("|", 1)
    user_media_language_settings[uid] = lang
    save_user_media_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ The transcription language for your media is set to: **{lang}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Media language set to {lang}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        bot.answer_callback_query(call.id, "Translating with your preferred language...")
        do_translate_with_saved_lang(call.message, uid, preferred_lang, message_id)
    else:
        markup = generate_language_keyboard("translate_to", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want to translate into:",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_summarize|"))
def button_summarize_handler(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        bot.answer_callback_query(call.id, "Summarizing with your preferred language...")
        do_summarize_with_saved_lang(call.message, uid, preferred_lang, message_id)
    else:
        markup = generate_language_keyboard("summarize_in", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want the summary in:",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("translate_to|"))
def callback_translate_to(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    user_language_settings[uid] = lang
    save_user_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Translating to **{lang}**...",
        parse_mode="Markdown"
    )
    if message_id:
        do_translate_with_saved_lang(call.message, uid, lang, message_id)
    else:
        # Fallback if message_id is not in callback_data (shouldn't happen with new buttons)
        if call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions.get(uid, {}):
             do_translate_with_saved_lang(call.message, uid, lang, call.message.reply_to_message.message_id)
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to translate. Please use the inline buttons on the transcription.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("summarize_in|"))
def callback_summarize_in(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    user_language_settings[uid] = lang
    save_user_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Summarizing in **{lang}**...",
        parse_mode="Markdown"
    )
    if message_id:
        do_summarize_with_saved_lang(call.message, uid, lang, message_id)
    else:
        # Fallback if message_id is not in callback_data (shouldn't happen with new buttons)
        if call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions.get(uid, {}):
            do_summarize_with_saved_lang(call.message, uid, lang, call.message.reply_to_message.message_id)
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to summarize. Please use the inline buttons on the transcription.")
    bot.answer_callback_query(call.id)

def do_translate_with_saved_lang(message, uid, lang, message_id):
    original_data = user_transcriptions.get(uid, {}).get(message_id, {})
    original_text = original_data.get("text", "")
    
    if not original_text:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to translate.")
        return

    prompt = f"Translate the following text into {lang}. Provide only the translated text, with no additional notes, explanations, or introductory/concluding remarks:\n\n{original_text}"

    bot.send_chat_action(message.chat.id, 'typing')
    translated = ask_gemini(uid, prompt)

    if translated.startswith("Error:"):
        bot.send_message(message.chat.id, f"Error during translation: {translated}")
        return

    if len(translated) > 4000:
        fn = 'translation.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(translated)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Translation to {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, translated, reply_to_message_id=message_id)

def do_summarize_with_saved_lang(message, uid, lang, message_id):
    original_data = user_transcriptions.get(uid, {}).get(message_id, {})
    original_text = original_data.get("text", "")
    
    if not original_text:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to summarize.")
        return

    prompt = f"Summarize the following text in {lang}. Provide only the summarized text, with no additional notes, explanations, or different versions:\n\n{original_text}"

    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(uid, prompt)

    if summary.startswith("Error:"):
        bot.send_message(message.chat.id, f"Error during summarization: {summary}")
        return

    if len(summary) > 4000:
        fn = 'summary.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(summary)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Summary in {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, summary, reply_to_message_id=message_id)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to translate it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        do_translate_with_saved_lang(message, uid, preferred_lang, transcription_message_id)
    else:
        markup = generate_language_keyboard("translate_to", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want to translate into:",
            reply_markup=markup
        )

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to summarize it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        do_summarize_with_saved_lang(message, uid, preferred_lang, transcription_message_id)
    else:
        markup = generate_language_keyboard("summarize_in", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want the summary in:",
            reply_markup=markup
        )

def transcribe_audio_chunks(audio_buffer: io.BytesIO, lang_code: str) -> str | None:
    """Transcribes audio from an in-memory buffer using speech_recognition library."""
    r = sr.Recognizer()
    full_transcription = []

    try:
        audio = AudioSegment.from_wav(audio_buffer)
        total_length_ms = len(audio)
        start_ms = 0

        logging.info(f"Starting in-memory chunking, total length {total_length_ms / 1000} seconds.")

        while start_ms < total_length_ms:
            end_ms = min(start_ms + CHUNK_LENGTH_MS, total_length_ms)
            chunk = audio[start_ms:end_ms]
            
            chunk_buffer = io.BytesIO()
            chunk.export(chunk_buffer, format="wav")
            chunk_buffer.seek(0)

            with sr.AudioFile(chunk_buffer) as source:
                try:
                    audio_listened = r.record(source)
                    text = r.recognize_google(audio_listened, language=lang_code)
                    full_transcription.append(text)
                    logging.info(f"Transcribed chunk from {start_ms/1000}s to {end_ms/1000}s: {text[:50]}...")
                except sr.UnknownValueError:
                    logging.warning(f"Speech Recognition could not understand audio in chunk {start_ms/1000}s - {end_ms/1000}s")
                except sr.RequestError as e:
                    logging.error(f"Could not request results from Google Speech Recognition service; {e} for chunk {start_ms/1000}s - {end_ms/1000}s")
                    # Optionally re-raise or handle more gracefully for the entire process
                    raise # Re-raise to be caught by the main processing error handling
                except Exception as e:
                    logging.error(f"Error processing chunk {start_ms/1000}s - {end_ms/1000}s: {e}")
                    raise # Re-raise
            
            start_ms += CHUNK_LENGTH_MS - OVERLAP_MS

        return " ".join(full_transcription) if full_transcription else None
    except Exception as e:
        logging.error(f"Overall transcription error: {e}")
        raise # Re-raise to be caught by the main processing error handling

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document', 'text'])
def fallback(message):
    update_user_activity(message.from_user.id)
    if message.text and message.text.startswith('/'):
        pass # Ignore unknown commands, or handle with a specific message if desired
    else:
        bot.send_message(message.chat.id, "Please send only voice messages, audio, or video for transcription.")

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    return abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook():
    bot.set_webhook(url=WEBHOOK_URL)
    return f"Webhook set to {WEBHOOK_URL}", 200

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    
    # Start cleanup thread
    cleanup_thread = threading.Timer(60, cleanup_old_data) # Run first cleanup after 60 seconds
    cleanup_thread.daemon = True
    cleanup_thread.start()

    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

