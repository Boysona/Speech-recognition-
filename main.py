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
from pydub import AudioSegment
import threading
import time
import io # For in-memory audio conversion
import subprocess # For improved system command execution

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BOT CONFIGURATION ---
TOKEN = "7790991731:AAHZks7W-iEwp6pcKD56eOeq3wduPjAiwow" # Replace with your actual bot token
ADMIN_ID = 5978150981 # Replace with your actual Admin ID
# Webhook URL - Make sure this is your actual Render URL
WEBHOOK_URL = "https://speech-recognition-6i0c.onrender.com"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Download directory (still needed for initial download before in-memory conversion)
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
processing_message_ids = {} # To store message_ids for progress updates

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

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA" # Replace with your actual Gemini API Key

def ask_gemini(user_id, user_message):
    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    resp = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": parts}]})
    result = resp.json()
    if "candidates" in result:
        reply = result['candidates'][0]['content']['parts'][0]['text']
        user_memory[user_id].append({"role": "model", "text": reply})
        return reply
    return "Error: " + json.dumps(result)

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

# Function to update uptime message (Updated code)
def update_uptime_message(chat_id, message_id):
    """
    Live-update the admin uptime message every second, showing days, hours, minutes and seconds.
    """
    while True:
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
            # Ignore "message is not modified" errors, log others
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
                break # Exit thread on persistent error
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break # Exit thread on unexpected error

# --- Memory Clearing ---
def clear_old_memory():
    """
    Clears old user_transcriptions and user_memory entries.
    Adjust the timedelta as needed (e.g., 7 days for `user_transcriptions`, 1 day for `user_memory`).
    """
    logging.info("Starting memory cleanup...")
    now = datetime.now()

    # Clear old transcriptions (e.g., older than 7 days)
    transcription_retention_days = 7
    keys_to_delete_transcriptions = []
    for user_id, transcripts in user_transcriptions.items():
        keys_to_delete_user_transcripts = []
        for msg_id, data in transcripts.items():
            # If you stored a timestamp with transcription, use that. Otherwise,
            # we'd need to rely on clearing the whole user's transcriptions if they haven't used bot in a while.
            # For simplicity, if a user hasn't interacted for a while, their entire transcription history might be cleared.
            # A more robust solution would timestamp each transcription.
            pass # Currently, no timestamp per transcription, so relying on general user activity.

    # Clear user_transcriptions for users inactive for a certain period
    inactive_user_threshold = timedelta(days=7) # Clear all transcriptions for users inactive for 7 days
    for user_id in list(user_transcriptions.keys()):
        last_activity_str = user_data.get(user_id)
        if last_activity_str:
            last_activity = datetime.fromisoformat(last_activity_str)
            if now - last_activity > inactive_user_threshold:
                del user_transcriptions[user_id]
                logging.info(f"Cleared all transcriptions for inactive user {user_id}")

    # Clear user_memory (e.g., older than 1 day)
    memory_retention_days = 1
    keys_to_delete_memory = []
    for user_id in list(user_memory.keys()):
        last_activity_str = user_data.get(user_id)
        if last_activity_str:
            last_activity = datetime.fromisoformat(last_activity_str)
            if now - last_activity > timedelta(days=memory_retention_days):
                del user_memory[user_id]
                logging.info(f"Cleared memory for inactive user {user_id}")

    # Clear processing_message_ids (e.g., older than 1 hour - just to be safe)
    processing_id_retention_hours = 1
    keys_to_delete_processing_ids = []
    for chat_id in list(processing_message_ids.keys()):
        # Assuming processing_message_ids[chat_id] contains a dict like {message_id: timestamp}
        # If it just contains message_id, we need to adapt this logic.
        # For simplicity, if we don't track timestamp here, we might need a different approach.
        # Let's assume for now, it's cleaned up when a new file starts for that user.
        # Or, just clear it periodically if not linked to a specific completion.
        pass # This needs a timestamp if it's to be cleared after N hours.
            # For now, it will be cleared when processing completes or errors.
    logging.info("Memory cleanup complete.")

    # Schedule next cleanup
    threading.Timer(timedelta(hours=1).total_seconds(), clear_old_memory).start()

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
            # This is tricky with daemon threads. A better way is to pass a stop_event to the thread.
            # For now, if the bot restarts, previous daemon threads are killed anyway.
            # If the user issues /start multiple times, it will spawn multiple threads, which is not ideal.
            # To fix this, we need to track the thread and its stop event in admin_uptime_message.
            if ADMIN_ID in admin_uptime_message and 'stop_event' in admin_uptime_message[ADMIN_ID] and not admin_uptime_message[ADMIN_ID]['stop_event'].is_set():
                admin_uptime_message[ADMIN_ID]['stop_event'].set()
                if admin_uptime_message[ADMIN_ID]['thread'].is_alive():
                    logging.info(f"Signaled old uptime thread for {ADMIN_ID} to stop.")
                    # A small delay to allow thread to finish if it's in time.sleep
                    time.sleep(1.1)

            stop_event = threading.Event()
            admin_uptime_message[ADMIN_ID] = {'message_id': sent_message.message_id, 'chat_id': message.chat.id, 'stop_event': stop_event}
            uptime_thread = threading.Thread(target=update_uptime_message, args=(message.chat.id, sent_message.message_id), daemon=True)
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
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'await_broadcast',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    for uid_key in user_data:
        uid = uid_key
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
    """Keeps sending 'typing' chat action until stop_event is set."""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error in keep_typing thread: {e}")
            break

def animate_progress_bar(chat_id, message_id, stop_event, phase_names, file_size=None):
    """Animates a progress bar/message update."""
    total_phases = len(phase_names)
    current_phase_index = 0
    progress_chars = "▓░" # Filled and empty
    animation_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    frame_index = 0

    while not stop_event.is_set():
        try:
            current_phase_name = phase_names[current_phase_index]
            progress_percent = int((current_phase_index / total_phases) * 100)
            
            # Simple progress bar based on phases
            bar_length = 10
            filled_length = int(bar_length * (current_phase_index / total_phases))
            bar = progress_chars[0] * filled_length + progress_chars[1] * (bar_length - filled_length)

            animation_char = animation_frames[frame_index % len(animation_frames)]
            frame_index += 1

            status_text = f"{animation_char} {current_phase_name} [{bar}] {progress_percent}%"
            if file_size:
                status_text += f" (Downloading {file_size / (1024*1024):.2f}MB)" # Example, actual download progress needs more info

            bot.edit_message_text(status_text, chat_id, message_id)
            time.sleep(0.5) # Update every half second
            
            # Advance phase if time allows or some condition is met
            # This needs to be driven by actual progress within the main thread
            # For this simple example, we'll advance phase based on time after a few seconds.
            # In a real scenario, the main processing thread would update current_phase_index.
            # For demonstration, let's just cycle the animation.
            
            # A more robust solution would be for the main thread to set a variable
            # that this thread reads to determine the current phase/progress.
            
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating progress message: {e}")
            pass # Ignore modification errors, continue trying
        except Exception as e:
            logging.error(f"Unexpected error in progress animation thread: {e}")
            break

# Async function for downloading the file
async def download_file_async(file_id, file_path):
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, bot.download_file, file_path)
    return data

# Async function for converting audio
async def convert_audio_async(input_data, output_format="wav", sr_rate=16000, channels=1):
    loop = asyncio.get_event_loop()
    audio_segment = await loop.run_in_executor(None, AudioSegment.from_file, io.BytesIO(input_data))
    
    # Set sample rate and channels
    audio_segment = audio_segment.set_frame_rate(sr_rate).set_channels(channels)
    
    output_buffer = io.BytesIO()
    await loop.run_in_executor(None, audio_segment.export, output_buffer, format=output_format)
    output_buffer.seek(0)
    return output_buffer

# Async function for transcribing audio
async def transcribe_audio_async(audio_buffer, lang_code, chunk_length_s=20):
    loop = asyncio.get_event_loop()
    r = sr.Recognizer()
    full_transcription = []

    try:
        audio = AudioSegment.from_wav(audio_buffer)
        total_length_ms = len(audio)
        chunk_length_ms = chunk_length_s * 1000 # 20 seconds
        overlap_ms = 1000 # 1 second overlap for smoother transitions

        start_ms = 0
        while start_ms < total_length_ms:
            end_ms = min(start_ms + chunk_length_ms, total_length_ms)
            chunk = audio[start_ms:end_ms]

            chunk_buffer = io.BytesIO()
            chunk.export(chunk_buffer, format="wav")
            chunk_buffer.seek(0)

            with sr.AudioFile(chunk_buffer) as source:
                try:
                    audio_listened = await loop.run_in_executor(None, r.record, source)
                    text = await loop.run_in_executor(None, r.recognize_google, audio_listened, language=lang_code)
                    full_transcription.append(text)
                    logging.info(f"Transcribed chunk from {start_ms/1000}s to {end_ms/1000}s: {text[:50]}...")
                except sr.UnknownValueError:
                    logging.warning(f"Speech Recognition could not understand audio in chunk {start_ms/1000}s - {end_ms/1000}s")
                except sr.RequestError as e:
                    logging.error(f"Could not request results from Google Speech Recognition service; {e} for chunk {start_ms/1000}s - {end_ms/1000}s")
                except Exception as e:
                    logging.error(f"Error processing chunk {start_ms/1000}s - {end_ms/1000}s: {e}")

            start_ms += chunk_length_ms - overlap_ms

        return " ".join(full_transcription) if full_transcription else None
    except Exception as e:
        logging.error(f"Overall transcription error: {e}")
        return None

# Main processing function now uses asyncio for concurrency
async def process_media_file(message):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
    uid = str(message.from_user.id)
    chat_id = message.chat.id
    update_user_activity(uid)

    if uid not in user_media_language_settings:
        await asyncio.to_thread(bot.send_message, chat_id, "⚠️ Please first select the language of the audio file using /media_language before sending the file.")
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        await asyncio.to_thread(bot.send_message, chat_id, "The file size you uploaded is too large (max allowed is 20MB).")
        return

    try:
        await asyncio.to_thread(bot.set_message_reaction, chat_id=chat_id, message_id=message.message_id, reaction=["👀"])
    except Exception as e:
        logging.error(f"Error setting reaction: {e}")

    # Start typing indicator
    stop_typing_event = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing_event), daemon=True)
    typing_thread.start()

    # Initial processing message with progress bar
    progress_message = await asyncio.to_thread(bot.send_message, chat_id, "🚀 Starting file processing...")
    processing_message_ids[chat_id] = progress_message.message_id # Store for updates

    stop_progress_event = threading.Event()
    progress_phase_names = [
        "Downloading file...",
        "Converting audio format...",
        "Transcribing audio...",
        "Finalizing results..."
    ]
    progress_thread = threading.Thread(
        target=animate_progress_bar, 
        args=(chat_id, progress_message.message_id, stop_progress_event, progress_phase_names), 
        daemon=True
    )
    progress_thread.start()
    
    try:
        info = await asyncio.to_thread(bot.get_file, file_obj.file_id)
        
        # --- Download Phase ---
        # Update progress message to show download status
        await asyncio.to_thread(bot.edit_message_text, "Downloading file...", chat_id, progress_message.message_id)
        
        file_content_bytes = await download_file_async(file_obj.file_id, info.file_path)
        
        # --- Convert Phase ---
        # Update progress message
        await asyncio.to_thread(bot.edit_message_text, "Converting audio format...", chat_id, progress_message.message_id)
        
        audio_buffer = await convert_audio_async(file_content_bytes) # In-memory conversion
        
        media_lang_code = get_lang_code(user_media_language_settings[uid])
        if not media_lang_code:
            await asyncio.to_thread(bot.edit_message_text, "❌ The language specified does not have a valid code. Please re-select the language.", chat_id, progress_message.message_id)
            return

        # --- Transcribe Phase ---
        # Update progress message
        await asyncio.to_thread(bot.edit_message_text, "Transcribing audio...", chat_id, progress_message.message_id)
        
        transcription = await transcribe_audio_async(audio_buffer, media_lang_code, chunk_length_s=20) or ""
        user_transcriptions.setdefault(uid, {})[message.message_id] = transcription

        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        elif message.video or message.video_note:
            total_videos += 1

        # --- Finalizing Phase ---
        await asyncio.to_thread(bot.edit_message_text, "Finalizing results...", chat_id, progress_message.message_id)

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        total_processing_time += processing_time

        buttons = InlineKeyboardMarkup()
        buttons.add(
            InlineKeyboardButton("Translate ", callback_data=f"btn_translate|{message.message_id}"),
            InlineKeyboardButton("Summarize ", callback_data=f"btn_summarize|{message.message_id}")
        )
        
        # Clear the processing message after transcription
        try:
            await asyncio.to_thread(bot.delete_message, chat_id, progress_message.message_id)
        except Exception as e:
            logging.warning(f"Could not delete progress message: {e}")

        if len(transcription) > 4000:
            fn = 'transcription.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(transcription)
            await asyncio.to_thread(bot.send_chat_action, chat_id, 'upload_document')
            with open(fn, 'rb') as doc:
                await asyncio.to_thread(
                    bot.send_document,
                    chat_id,
                    doc,
                    reply_to_message_id=message.message_id,
                    reply_markup=buttons,
                    caption="Here’s your transcription. Tap a button below for more options."
                )
            os.remove(fn)
        else:
            await asyncio.to_thread(
                bot.reply_to,
                message,
                transcription,
                reply_markup=buttons
            )
            
    except telebot.apihelper.ApiTelegramException as e:
        error_msg = f"Telegram API error: {e}"
        logging.error(error_msg)
        await asyncio.to_thread(bot.send_message, chat_id, f"😓 Sorry, there was an issue communicating with Telegram. Please try again.")
    except Exception as e:
        error_type = "an unexpected error"
        if "FFmpeg conversion failed" in str(e):
            error_type = "file conversion issue. The audio might be in an unsupported format or corrupted."
        elif "Could not request results from Google Speech Recognition service" in str(e):
            error_type = "transcription service error. This might be a temporary issue or a problem with the audio quality."
        elif "Speech Recognition could not understand audio" in str(e):
            error_type = "transcription difficulty. The audio might be unclear, too quiet, or in a language not supported by the model."
        
        logging.error(f"Error processing file: {e}")
        await asyncio.to_thread(bot.send_message, chat_id, f"😓 Sorry, there was {error_type}. Please try again later, or ensure your audio is clear.")
    finally:
        stop_typing_event.set()
        stop_progress_event.set()
        # Ensure any temporary files are cleaned up immediately if they were created before in-memory conversion
        if os.path.exists(os.path.join(DOWNLOAD_DIR, f"{file_obj.file_id}{file_extension}")): # This won't exist if using in-memory
             os.remove(os.path.join(DOWNLOAD_DIR, f"{file_obj.file_id}{file_extension}"))
        if chat_id in processing_message_ids:
            # Try to delete the progress message if it still exists
            try:
                await asyncio.to_thread(bot.delete_message, chat_id, processing_message_ids[chat_id])
            except telebot.apihelper.ApiTelegramException as e:
                if "message to delete not found" not in str(e) and "message is not modified" not in str(e):
                    logging.warning(f"Could not delete final progress message: {e}")
            del processing_message_ids[chat_id]


@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file_wrapper(message):
    # This wraps the async processing function to be called from the sync handler
    threading.Thread(target=lambda: asyncio.run(process_media_file(message))).start()

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
        # Run translation in a separate thread/async to keep bot responsive
        threading.Thread(target=lambda: asyncio.run(do_translate_with_saved_lang_async(call.message, uid, preferred_lang, message_id))).start()
    else:
        markup = generate_language_keyboard("translate_to", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want to translate into:",
            reply_markup=markup
        )
    # No answer_callback_query here, it will be answered by the translation function or the language selection.

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
        # Run summarization in a separate thread/async to keep bot responsive
        threading.Thread(target=lambda: asyncio.run(do_summarize_with_saved_lang_async(call.message, uid, preferred_lang, message_id))).start()
    else:
        markup = generate_language_keyboard("summarize_in", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want the summary in:",
            reply_markup=markup
        )
    # No answer_callback_query here, it will be answered by the summarization function or the language selection.

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
        threading.Thread(target=lambda: asyncio.run(do_translate_with_saved_lang_async(call.message, uid, lang, message_id))).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
             threading.Thread(target=lambda: asyncio.run(do_translate_with_saved_lang_async(call.message, uid, lang, call.message.reply_to_message.message_id))).start()
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
        threading.Thread(target=lambda: asyncio.run(do_summarize_with_saved_lang_async(call.message, uid, lang, message_id))).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=lambda: asyncio.run(do_summarize_with_saved_lang_async(call.message, uid, lang, call.message.reply_to_message.message_id))).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to summarize. Please use the inline buttons on the transcription.")
    bot.answer_callback_query(call.id)

async def do_translate_with_saved_lang_async(message, uid, lang, message_id):
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        await asyncio.to_thread(bot.send_message, message.chat.id, "❌ No transcription available for this specific message to translate.")
        return

    prompt = f"Translate the following text into {lang}. Provide only the translated text, with no additional notes, explanations, or introductory/concluding remarks:\n\n{original}"

    await asyncio.to_thread(bot.send_chat_action, message.chat.id, 'typing')
    translated = await asyncio.to_thread(ask_gemini, uid, prompt)

    if translated.startswith("Error:"):
        await asyncio.to_thread(bot.send_message, message.chat.id, f"Error during translation: {translated}")
        return

    if len(translated) > 4000:
        fn = f'translation_{uuid.uuid4()}.txt' # Unique filename
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(translated)
        await asyncio.to_thread(bot.send_chat_action, message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            await asyncio.to_thread(bot.send_document, message.chat.id, doc, caption=f"Translation to {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        await asyncio.to_thread(bot.send_message, message.chat.id, translated, reply_to_message_id=message_id)

async def do_summarize_with_saved_lang_async(message, uid, lang, message_id):
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        await asyncio.to_thread(bot.send_message, message.chat.id, "❌ No transcription available for this specific message to summarize.")
        return

    prompt = f"Summarize the following text in {lang}. Provide only the summarized text, with no additional notes, explanations, or different versions:\n\n{original}"

    await asyncio.to_thread(bot.send_chat_action, message.chat.id, 'typing')
    summary = await asyncio.to_thread(ask_gemini, uid, prompt)

    if summary.startswith("Error:"):
        await asyncio.to_thread(bot.send_message, message.chat.id, f"Error during summarization: {summary}")
        return

    if len(summary) > 4000:
        fn = f'summary_{uuid.uuid4()}.txt' # Unique filename
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(summary)
        await asyncio.to_thread(bot.send_chat_action, message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            await asyncio.to_thread(bot.send_document, message.chat.id, doc, caption=f"Summary in {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        await asyncio.to_thread(bot.send_message, message.chat.id, summary, reply_to_message_id=message_id)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to translate it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        threading.Thread(target=lambda: asyncio.run(do_translate_with_saved_lang_async(message, uid, preferred_lang, transcription_message_id))).start()
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
        threading.Thread(target=lambda: asyncio.run(do_summarize_with_saved_lang_async(message, uid, preferred_lang, transcription_message_id))).start()
    else:
        markup = generate_language_keyboard("summarize_in", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want the summary in:",
            reply_markup=markup
        )

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document', 'text'])
def fallback(message):
    update_user_activity(message.from_user.id)
    if message.text and message.text.startswith('/'):
        # It's a command not explicitly handled, or a new command.
        # Do nothing or send a generic "unknown command" message if preferred.
        pass
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
def set_webhook_route(): # Renamed to avoid clash with telebot's set_webhook
    bot.set_webhook(url=WEBHOOK_URL)
    return f"Webhook set to {WEBHOOK_URL}", 200

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook_route(): # Renamed to avoid clash
    bot.delete_webhook()
    return 'Webhook deleted.', 200

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    
    # Start the memory clearing background task
    clear_old_memory() 
    
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

