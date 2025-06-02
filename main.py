import os
import re
import uuid
import shutil
import logging
import requests
import telebot
import json
import asyncio
import threading
import time
import subprocess
import io

from flask import Flask, request, abort
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import speech_recognition as sr
import imageio_ffmpeg as ffmpeg
from pydub import AudioSegment

from msspeech import MSSpeech, MSSpeechError

# -------------------- CONFIGURATION --------------------

# Replace with your actual combined bot token (use the media-transcriber bot token)
TOKEN = "7790991731:AAHZks7W-iEwp6pcKD56eOeq3wduPjAiwow"
ADMIN_ID = 5978150981

# Webhook URL (replace with your actual Render or hosting URL)
WEBHOOK_URL = "https://speech-recognition-6i0c.onrender.com"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Directory for temporary downloads and generated audio
DOWNLOAD_DIR = "downloads"
AUDIO_DIR = "audio_files"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

# -------------------- USER DATA FILES --------------------

users_file = 'users.json'  # Tracks last activity timestamps
user_language_settings_file = 'user_language_settings.json'  # For translation/summarization
user_media_language_settings_file = 'user_media_language_settings.json'  # For transcription
users_tts_file = 'users_tts.json'  # For storing user-selected TTS voices

user_data = {}
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        try:
            user_data = json.load(f)
        except json.JSONDecodeError:
            user_data = {}

user_language_settings = {}
if os.path.exists(user_language_settings_file):
    with open(user_language_settings_file, 'r') as f:
        try:
            user_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_language_settings = {}

user_media_language_settings = {}
if os.path.exists(user_media_language_settings_file):
    with open(user_media_language_settings_file, 'r') as f:
        try:
            user_media_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_media_language_settings = {}

users_tts = {}
if os.path.exists(users_tts_file):
    try:
        with open(users_tts_file, 'r') as f:
            users_tts = json.load(f)
    except json.JSONDecodeError:
        users_tts = {}

def save_user_data():
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)

def save_user_language_settings():
    with open(user_language_settings_file, 'w') as f:
        json.dump(user_language_settings, f, indent=4)

def save_user_media_language_settings():
    with open(user_media_language_settings_file, 'w') as f:
        json.dump(user_media_language_settings, f, indent=4)

def save_users_tts():
    with open(users_tts_file, 'w') as f:
        json.dump(users_tts, f, indent=4)

# -------------------- IN-MEMORY STORAGE --------------------

user_memory = {}               # For Gemini chat history
user_transcriptions = {}       # Map: user_id -> { message_id: transcription_text }
processing_message_ids = {}    # Tracks typing indicators

# Statistics
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0
bot_start_time = datetime.now()

admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

# -------------------- GEMINI CONFIG --------------------

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"  # Replace with your Gemini key

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

# -------------------- TTS CONFIG --------------------

# Voices grouped by language
VOICES_BY_LANGUAGE = {
    "English 🇬🇧": [
        "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-DavisNeural",
        "en-GB-LibbyNeural", "en-GB-RyanNeural", "en-GB-MiaNeural", "en-GB-ThomasNeural",
        "en-AU-NatashaNeural", "en-AU-WilliamNeural", "en-CA-LindaNeural", "en-CA-ClaraNeural",
        "en-IE-EmilyNeural", "en-IE-ConnorNeural", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural"
    ],
    "Arabic 🇸🇦": [
        "ar-SA-HamedNeural", "ar-SA-ZariyahNeural", "ar-EG-SalmaNeural", "ar-EG-ShakirNeural",
        "ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural", "ar-BH-LailaNeural", "ar-BH-AliNeural",
        "ar-IQ-RanaNeural", "ar-IQ-BasselNeural", "ar-KW-FahedNeural", "ar-KW-NouraNeural",
        "ar-OM-AishaNeural", "ar-OM-SamirNeural", "ar-QA-MoazNeural", "ar-QA-ZainabNeural",
        "ar-SY-AmiraNeural", "ar-SY-LaithNeural", "ar-AE-FatimaNeural", "ar-AE-HamdanNeural",
        "ar-YE-HamdanNeural", "ar-YE-SarimNeural"
    ],
    "Spanish 🇪🇸": [
        "es-ES-AlvaroNeural", "es-ES-ElviraNeural", "es-MX-DaliaNeural", "es-MX-JorgeNeural",
        "es-AR-ElenaNeural", "es-AR-TomasNeural", "es-CO-SalomeNeural", "es-CO-GonzaloNeural",
        "es-US-PalomaNeural", "es-US-JuanNeural", "es-CL-LorenzoNeural", "es-CL-CatalinaNeural",
        "es-PE-CamilaNeural", "es-PE-DiegoNeural", "es-VE-PaolaNeural", "es-VE-SebastianNeural",
        "es-CR-MariaNeural", "es-CR-JuanNeural", "es-DO-RamonaNeural", "es-DO-AntonioNeural"
    ],
    # (Add other languages if needed)
}

def get_user_voice(uid):
    return users_tts.get(str(uid), "en-US-AriaNeural")

# -------------------- LANGUAGE LIST FOR TRANSCRIPTION --------------------

LANGUAGES = [
    {"name": "English", "flag": "🇬🇧", "code": "en-US"},
    {"name": "Arabic", "flag": "🇸🇦", "code": "ar-SA"},
    {"name": "Spanish", "flag": "🇪🇸", "code": "es-ES"},
    {"name": "Chinese", "flag": "🇨🇳", "code": "zh-CN"},
    {"name": "Hindi", "flag": "🇮🇳", "code": "hi-IN"},
    {"name": "French", "flag": "🇫🇷", "code": "fr-FR"},
    {"name": "Bengali", "flag": "🇧🇩", "code": "bn-BD"},
    {"name": "Russian", "flag": "🇷🇺", "code": "ru-RU"},
    {"name": "Urdu", "flag": "🇵🇰", "code": "ur-PK"},
    {"name": "Portuguese", "flag": "🇵🇹", "code": "pt-PT"},
    {"name": "German", "flag": "🇩🇪", "code": "de-DE"},
    {"name": "Japanese", "flag": "🇯🇵", "code": "ja-JP"},
    {"name": "Korean", "flag": "🇰🇷", "code": "ko-KR"},
    {"name": "Vietnamese", "flag": "🇻🇳", "code": "vi-VN"},
    {"name": "Turkish", "flag": "🇹🇷", "code": "tr-TR"},
    {"name": "Italian", "flag": "🇮🇹", "code": "it-IT"},
    {"name": "Swahili", "flag": "🇰🇪", "code": "sw-KE"},
    {"name": "Dutch", "flag": "🇳🇱", "code": "nl-NL"},
    {"name": "Polish", "flag": "🇵🇱", "code": "pl-PL"},
    {"name": "Ukrainian", "flag": "🇺🇦", "code": "uk-UA"},
    {"name": "Indonesian", "flag": "🇮🇩", "code": "id-ID"},
    {"name": "Malay", "flag": "🇲🇾", "code": "ms-MY"},
    {"name": "Filipino", "flag": "🇵🇭", "code": "fil-PH"},
    {"name": "Persian", "flag": "🇮🇷", "code": "fa-IR"},
    {"name": "Somali", "flag": "🇸🇴", "code": "so-SO"},
    # (Add more as needed)
]

def get_lang_code(lang_name):
    for lang in LANGUAGES:
        if lang['name'].lower() == lang_name.lower():
            return lang['code']
    return None

# -------------------- KEYBOARD GENERATORS --------------------

def generate_language_keyboard(callback_prefix, include_message_id=False):
    """
    Generates an inline keyboard of LANGUAGES in rows of 3.
    callback data: prefix|language_name[|message_id]
    """
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang in LANGUAGES:
        cb_data = f"{callback_prefix}|{lang['name']}"
        if include_message_id:
            cb_data += "|{message_id}"
        buttons.append(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb_data))
    markup.add(*buttons)
    return markup

def generate_tts_language_keyboard():
    """
    Generates an inline keyboard of VOICES_BY_LANGUAGE keys (languages) in rows of 3.
    callback data: tts_lang|language_key
    """
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang_key in VOICES_BY_LANGUAGE.keys():
        buttons.append(InlineKeyboardButton(lang_key, callback_data=f"tts_lang|{lang_key}"))
    markup.add(*buttons)
    return markup

def generate_tts_voice_keyboard(lang_key):
    """
    Generates an inline keyboard of voice names for the chosen language.
    callback data: tts_voice|voice_name
    """
    markup = InlineKeyboardMarkup(row_width=2)
    voices = VOICES_BY_LANGUAGE.get(lang_key, [])
    for voice in voices:
        markup.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    # Back button
    markup.add(InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_langs"))
    return markup

# -------------------- ADMIN AND UPTIME --------------------

def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "👋 Get welcome message and info"),
        telebot.types.BotCommand("status", "📊 View bot statistics"),
        telebot.types.BotCommand("help", "❓ How to use the bot"),
        telebot.types.BotCommand("language", "🌐 Change preferred language for translate/summarize"),
        telebot.types.BotCommand("media_language", "📝 Set language for media transcription"),
        telebot.types.BotCommand("change_voice", "🔊 Change TTS voice"),
        telebot.types.BotCommand("privacy", "👮 Privacy notice"),
    ]
    bot.set_my_commands(commands)

    bot.set_my_short_description(
        "🤖 Transcribe, Summarize, Translate, and Convert Text ↔️ Speech in one bot!"
    )

    bot.set_my_description(
        """This unified bot can transcribe voice messages, audio, and video; summarize or translate transcriptions; and convert text to speech in multiple voices.

Features:
• 🗣️ Speech-to-Text: Send voice/audio/video after setting /media_language.
• 📄 Summarize/Translate: Inline buttons after transcription.
• 🔊 Text-to-Speech: Use /change_voice to pick language & voice, then send any text.

Enjoy free AI-powered media and text processing!"""
    )

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

def update_uptime_message(chat_id, message_id):
    while True:
        try:
            elapsed = datetime.now() - bot_start_time
            total_seconds = int(elapsed.total_seconds())
            days, rem = divmod(total_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)

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
            time.sleep(1)

        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break

# -------------------- START, HELP, PRIVACY, STATUS --------------------

@bot.message_handler(commands=['start'])
def start_handler(message):
    uid = message.from_user.id
    update_user_activity(uid)

    if uid == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "👑 Admin Panel: Live Uptime", reply_markup=keyboard)

        with admin_uptime_lock:
            admin_uptime_message[ADMIN_ID] = {
                'message_id': sent_message.message_id,
                'chat_id': message.chat.id
            }
            uptime_thread = threading.Thread(target=update_uptime_message,
                                             args=(message.chat.id, sent_message.message_id))
            uptime_thread.daemon = True
            uptime_thread.start()
            admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread
    else:
        display_name = message.from_user.first_name or (f"@{message.from_user.username}" if message.from_user.username else "user")
        bot.send_message(
            message.chat.id,
            f"""👋🏻 Salam {display_name}!
I’m your all-in-one AI bot:
• 🗣️ Send a voice message, audio, or video after setting /media_language to transcribe.
• 📄 Tap “Translate” or “Summarize” on transcriptions.
• 🔊 Use /change_voice to pick a voice, then send any text to get speech audio.

Send /help for detailed instructions.
"""
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = (
        """ℹ️ **How to use this Unified Bot**

**1. Speech-to-Text (Transcription):**
• Use `/media_language` to set the audio language first.
• Then send a voice message, audio, or video (≤20MB).
• The bot transcribes it and shows inline buttons:
  – **Translate**: Translate the transcription into your preferred language.
  – **Summarize**: Summarize the transcription in your preferred language.

**2. Translate/Summarize:**
• After transcription, tap “Translate” or “Summarize.” 
• If you set a preferred language via `/language`, it uses that automatically. Otherwise, you’ll choose inline.

**3. Text-to-Speech (TTS):**
• Use `/change_voice` to pick a language and then a neural voice.
• Once selected, send any text message; the bot will reply with an audio file spoken in that voice.

**4. Commands:**
• `/start`: Welcome and info. (Admins see live uptime.)
• `/help`: This guide.
• `/status`: View bot stats (uptime, users, files processed).
• `/language`: Change preferred language for Translate/Summarize.
• `/media_language`: Set audio language for transcription.
• `/change_voice`: Set TTS language & voice.
• `/privacy`: See privacy notice.

Enjoy fast, free AI-powered media and text processing!"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    privacy_text = (
        """**Privacy Notice**

1. **Media Files:** Temporarily downloaded for transcription, then **deleted immediately** after processing.
2. **Transcriptions:** Stored in memory short-term (up to 7 days) for follow-up Translate/Summarize, then cleared.
3. **Text for TTS:** Not stored long-term. Generated audio files are deleted after sending.
4. **User IDs & Preferences:** Stored (language settings, voice choices) to personalize your experience. Not linked to personal info outside Telegram.
5. **AI Integrations:** We use Google’s Speech-to-Text API and Gemini API for transcription, translation, summarization; and Microsoft’s Neural TTS (via `msspeech`) for voice synthesis. Your data stays private; we do not share your files or text with third parties.

By using this bot, you agree to these terms. For questions, contact the administrator."""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    uid = message.from_user.id
    update_user_activity(uid)

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    today = datetime.now().date()
    active_today = sum(
        1 for ts in user_data.values()
        if datetime.fromisoformat(ts).date() == today
    )

    total_proc_seconds = int(total_processing_time)
    proc_hours = total_proc_seconds // 3600
    proc_minutes = (total_proc_seconds % 3600) // 60
    proc_seconds = total_proc_seconds % 60

    text = (
        "📊 **Bot Statistics**\n\n"
        "🟢 **Status:** Online\n"
        f"⏳ **Uptime:** {days}d {hours}h {minutes}m {seconds}s\n\n"
        "👥 **User Statistics**\n"
        f"▫️ Active Today: {active_today}\n"
        f"▫️ Total Registered: {len(user_data)}\n\n"
        "⚙️ **Processing Statistics**\n"
        f"▫️ Total Files Processed: {total_files_processed}\n"
        f"▫️ Voice Clips: {total_voice_clips}\n"
        f"▫️ Audio Files: {total_audio_files}\n"
        f"▫️ Videos: {total_videos}\n"
        f"⏱️ Total Processing Time: {proc_hours}h {proc_minutes}m {proc_seconds}s\n\n"
        "Thank you for using our service! 🙌"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Total registered users: {len(user_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Please send the broadcast message now (text, photo, video, or document):")

admin_state = {}
@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    for uid_str in user_data:
        try:
            bot.copy_message(uid_str, message.chat.id, message.message_id)
            success += 1
        except Exception as e:
            logging.error(f"Broadcast failed for {uid_str}: {e}")
            fail += 1
    bot.send_message(message.chat.id, f"Broadcast complete.\n✅ Success: {success}\n❌ Failed: {fail}")

# -------------------- MEDIA TRANSCRIPTION HANDLERS --------------------

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB limit

@bot.message_handler(commands=['media_language'])
def select_media_language_command(message):
    uid = message.from_user.id
    update_user_activity(uid)

    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "🌐 Please choose the language of the audio to transcribe:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_media_lang|"))
def callback_set_media_language(call):
    uid = call.from_user.id
    update_user_activity(uid)

    _, lang_name = call.data.split("|", 1)
    user_media_language_settings[str(uid)] = lang_name
    save_user_media_language_settings()

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Transcription language set to: **{lang_name}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Media language: {lang_name}")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    uid = message.from_user.id
    update_user_activity(uid)

    if str(uid) not in user_media_language_settings:
        bot.send_message(message.chat.id,
                         "⚠️ Please set /media_language before sending media.")
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        bot.send_message(message.chat.id, "😓 File too large (max 20MB).")
        return

    # Add "👀" reaction
    try:
        bot.set_message_reaction(chat_id=message.chat.id,
                                 message_id=message.message_id,
                                 reaction=[{'type': 'emoji', 'emoji': '👀'}])
    except Exception:
        pass

    # Start typing indicator
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing

    try:
        threading.Thread(target=process_media_file, args=(message, stop_typing)).start()
    except Exception as e:
        logging.error(f"Failed to start processing thread: {e}")
        stop_typing.set()
        try:
            bot.set_message_reaction(chat_id=message.chat.id,
                                     message_id=message.message_id,
                                     reaction=[])
        except Exception:
            pass
        bot.send_message(message.chat.id, "😓 Unexpected error. Please try again.")

def process_media_file(message, stop_typing):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time

    uid_str = str(message.from_user.id)
    file_obj = message.voice or message.audio or message.video or message.video_note

    local_temp_file = None
    wav_audio_data = None

    try:
        info = bot.get_file(file_obj.file_id)
        ext = ".ogg" if (message.voice or message.video_note) else os.path.splitext(info.file_path)[1]
        local_temp_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{ext}")
        data = bot.download_file(info.file_path)
        with open(local_temp_file, 'wb') as f:
            f.write(data)

        start_time = datetime.now()

        temp_wav_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.wav")
        try:
            command = [
                ffmpeg.get_ffmpeg_exe(),
                '-i', local_temp_file,
                '-vn',
                '-acodec', 'pcm_s16le',
                '-ar', '16000',
                '-ac', '1',
                temp_wav_file
            ]
            subprocess.run(command, check=True, capture_output=True)

            if not os.path.exists(temp_wav_file) or os.path.getsize(temp_wav_file) == 0:
                raise Exception("FFmpeg conversion failed or empty output.")

            with open(temp_wav_file, 'rb') as f:
                wav_audio_data = f.read()

        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg error: {e.stderr.decode()}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id,
                                         message_id=message.message_id,
                                         reaction=[])
            except Exception:
                pass
            bot.send_message(message.chat.id,
                             "😓 Could not convert audio. Please try a different file.")
            return
        except Exception as e:
            logging.error(f"FFmpeg general error: {e}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id,
                                         message_id=message.message_id,
                                         reaction=[])
            except Exception:
                pass
            bot.send_message(message.chat.id,
                             "😓 Audio conversion failed. Please ensure it’s a standard format.")
            return
        finally:
            if os.path.exists(temp_wav_file):
                os.remove(temp_wav_file)

        media_lang = user_media_language_settings[uid_str]
        media_lang_code = get_lang_code(media_lang)
        if not media_lang_code:
            try:
                bot.set_message_reaction(chat_id=message.chat.id,
                                         message_id=message.message_id,
                                         reaction=[])
            except Exception:
                pass
            bot.send_message(message.chat.id,
                             f"❌ Invalid language code for **{media_lang}**. Please /media_language again.")
            return

        transcription = transcribe_audio_from_bytes(wav_audio_data, media_lang_code) or ""
        user_transcriptions.setdefault(uid_str, {})[message.message_id] = transcription

        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        else:
            total_videos += 1

        elapsed = (datetime.now() - start_time).total_seconds()
        total_processing_time += elapsed

        buttons = InlineKeyboardMarkup()
        buttons.add(
            InlineKeyboardButton("Translate", callback_data=f"btn_translate|{message.message_id}"),
            InlineKeyboardButton("Summarize", callback_data=f"btn_summarize|{message.message_id}")
        )

        try:
            bot.set_message_reaction(chat_id=message.chat.id,
                                     message_id=message.message_id,
                                     reaction=[])
        except Exception:
            pass

        if len(transcription) > 4000:
            fn = 'transcription.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(transcription)
            bot.send_chat_action(message.chat.id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(
                    message.chat.id,
                    doc,
                    reply_to_message_id=message.message_id,
                    reply_markup=buttons,
                    caption="Here’s your transcription. Tap below for options."
                )
            os.remove(fn)
        else:
            bot.reply_to(
                message,
                transcription,
                reply_markup=buttons
            )

    except Exception as e:
        logging.error(f"Error processing media for {uid_str}: {e}")
        try:
            bot.set_message_reaction(chat_id=message.chat.id,
                                     message_id=message.message_id,
                                     reaction=[])
        except Exception:
            pass
        bot.send_message(message.chat.id,
                         "😓 Transcription error. Audio might be unclear or short. Try again.")
    finally:
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]
        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)

# --- Transcription Chunking ---

def transcribe_audio_from_bytes(audio_bytes: bytes, lang_code: str) -> str | None:
    r = sr.Recognizer()
    full_transcription = []
    chunk_length_ms = 10 * 1000
    overlap_ms = 500

    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")
        total_length_ms = len(audio)

        start_ms = 0
        logging.info(f"Transcription: audio length = {total_length_ms / 1000}s")

        while start_ms < total_length_ms:
            end_ms = min(start_ms + chunk_length_ms, total_length_ms)
            chunk = audio[start_ms:end_ms]

            chunk_io = io.BytesIO()
            chunk.export(chunk_io, format="wav")
            chunk_io.seek(0)

            with sr.AudioFile(chunk_io) as source:
                try:
                    audio_data = r.record(source)
                    text = r.recognize_google(audio_data, language=lang_code)
                    full_transcription.append(text)
                    logging.info(f"Chunk {start_ms/1000}-{end_ms/1000}s: {text[:30]}...")
                except sr.UnknownValueError:
                    logging.warning(f"Unrecognized audio in chunk {start_ms/1000}-{end_ms/1000}s")
                except sr.RequestError as e:
                    logging.error(f"Google SR API error: {e} (chunk {start_ms/1000}-{end_ms/1000}s)")
                except Exception as e:
                    logging.error(f"Chunk processing error: {e}")
                finally:
                    chunk_io.close()

            start_ms += chunk_length_ms - overlap_ms

        if full_transcription:
            return " ".join(full_transcription)
        else:
            return None

    except Exception as e:
        logging.error(f"Overall transcription failed: {e}")
        return None

# -------------------- TRANSLATE & SUMMARIZE CALLBACKS --------------------

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call):
    uid = call.from_user.id
    update_user_activity(uid)

    _, msg_id_str = call.data.split("|", 1)
    msg_id = int(msg_id_str)
    uid_str = str(uid)

    if uid_str not in user_transcriptions or msg_id not in user_transcriptions[uid_str]:
        bot.answer_callback_query(call.id, "❌ No transcription found.")
        return

    preferred = user_language_settings.get(uid_str)
    if preferred:
        bot.answer_callback_query(call.id, "🌐 Translating...")
        threading.Thread(target=do_translate_with_saved_lang,
                         args=(call.message.chat.id, uid, preferred, msg_id)).start()
    else:
        markup = generate_language_keyboard("translate_to", include_message_id=True)
        # We embed msg_id manually into callback data so call handler can parse it
        # To simplify, edit buttons so that callback carries message_id
        kb = InlineKeyboardMarkup(row_width=3)
        for lang in LANGUAGES:
            cb = f"translate_to|{lang['name']}|{msg_id}"
            kb.add(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb))
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="🌐 Select translation language:",
            reply_markup=kb
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_summarize|"))
def button_summarize_handler(call):
    uid = call.from_user.id
    update_user_activity(uid)

    _, msg_id_str = call.data.split("|", 1)
    msg_id = int(msg_id_str)
    uid_str = str(uid)

    if uid_str not in user_transcriptions or msg_id not in user_transcriptions[uid_str]:
        bot.answer_callback_query(call.id, "❌ No transcription found.")
        return

    preferred = user_language_settings.get(uid_str)
    if preferred:
        bot.answer_callback_query(call.id, "✍️ Summarizing...")
        threading.Thread(target=do_summarize_with_saved_lang,
                         args=(call.message.chat.id, uid, preferred, msg_id)).start()
    else:
        kb = InlineKeyboardMarkup(row_width=3)
        for lang in LANGUAGES:
            cb = f"summarize_in|{lang['name']}|{msg_id}"
            kb.add(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb))
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="✍️ Select summary language:",
            reply_markup=kb
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("translate_to|"))
def callback_translate_to(call):
    uid = call.from_user.id
    update_user_activity(uid)

    parts = call.data.split("|")
    _, lang, msg_id_str = parts
    msg_id = int(msg_id_str)

    user_language_settings[str(uid)] = lang
    save_user_language_settings()

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🌐 Translating to **{lang}**...",
        parse_mode="Markdown"
    )
    threading.Thread(target=do_translate_with_saved_lang,
                     args=(call.message.chat.id, uid, lang, msg_id)).start()
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("summarize_in|"))
def callback_summarize_in(call):
    uid = call.from_user.id
    update_user_activity(uid)

    parts = call.data.split("|")
    _, lang, msg_id_str = parts
    msg_id = int(msg_id_str)

    user_language_settings[str(uid)] = lang
    save_user_language_settings()

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✍️ Summarizing in **{lang}**...",
        parse_mode="Markdown"
    )
    threading.Thread(target=do_summarize_with_saved_lang,
                     args=(call.message.chat.id, uid, lang, msg_id)).start()
    bot.answer_callback_query(call.id)

def do_translate_with_saved_lang(chat_id, uid, lang, msg_id):
    uid_str = str(uid)
    original = user_transcriptions.get(uid_str, {}).get(msg_id, "")
    if not original:
        bot.send_message(chat_id, "❌ No transcription to translate.")
        return

    prompt = (
        f"Translate the following text into {lang}. Provide only the translated text, with no extra notes:\n\n{original}"
    )
    bot.send_chat_action(chat_id, 'typing')
    translated = ask_gemini(str(uid), prompt)

    if translated.startswith("Error:"):
        bot.send_message(chat_id, f"😓 Translation error: {translated}")
        return

    if len(translated) > 4000:
        fn = 'translation.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(translated)
        bot.send_chat_action(chat_id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(chat_id, doc, caption=f"Translation to {lang}", reply_to_message_id=msg_id)
        os.remove(fn)
    else:
        bot.send_message(chat_id, translated, reply_to_message_id=msg_id)

def do_summarize_with_saved_lang(chat_id, uid, lang, msg_id):
    uid_str = str(uid)
    original = user_transcriptions.get(uid_str, {}).get(msg_id, "")
    if not original:
        bot.send_message(chat_id, "❌ No transcription to summarize.")
        return

    prompt = (
        f"Summarize the following text in {lang}. Provide only the summary, no extra notes:\n\n{original}"
    )
    bot.send_chat_action(chat_id, 'typing')
    summary = ask_gemini(str(uid), prompt)

    if summary.startswith("Error:"):
        bot.send_message(chat_id, f"😓 Summarization error: {summary}")
        return

    if len(summary) > 4000:
        fn = 'summary.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(summary)
        bot.send_chat_action(chat_id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(chat_id, doc, caption=f"Summary in {lang}", reply_to_message_id=msg_id)
        os.remove(fn)
    else:
        bot.send_message(chat_id, summary, reply_to_message_id=msg_id)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = message.from_user.id
    update_user_activity(uid)

    if not message.reply_to_message:
        return bot.send_message(message.chat.id, "❌ Reply to a transcription to translate it.")
    msg_id = message.reply_to_message.message_id
    uid_str = str(uid)

    if uid_str not in user_transcriptions or msg_id not in user_transcriptions[uid_str]:
        return bot.send_message(message.chat.id, "❌ No transcription found to translate.")

    preferred = user_language_settings.get(uid_str)
    if preferred:
        threading.Thread(target=do_translate_with_saved_lang,
                         args=(message.chat.id, uid, preferred, msg_id)).start()
    else:
        kb = InlineKeyboardMarkup(row_width=3)
        for lang in LANGUAGES:
            cb = f"translate_to|{lang['name']}|{msg_id}"
            kb.add(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb))
        bot.send_message(
            message.chat.id,
            "🌐 Select translation language:",
            reply_markup=kb
        )

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    uid = message.from_user.id
    update_user_activity(uid)

    if not message.reply_to_message:
        return bot.send_message(message.chat.id, "❌ Reply to a transcription to summarize it.")
    msg_id = message.reply_to_message.message_id
    uid_str = str(uid)

    if uid_str not in user_transcriptions or msg_id not in user_transcriptions[uid_str]:
        return bot.send_message(message.chat.id, "❌ No transcription found to summarize.")

    preferred = user_language_settings.get(uid_str)
    if preferred:
        threading.Thread(target=do_summarize_with_saved_lang,
                         args=(message.chat.id, uid, preferred, msg_id)).start()
    else:
        kb = InlineKeyboardMarkup(row_width=3)
        for lang in LANGUAGES:
            cb = f"summarize_in|{lang['name']}|{msg_id}"
            kb.add(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb))
        bot.send_message(
            message.chat.id,
            "✍️ Select summary language:",
            reply_markup=kb
        )

# -------------------- TEXT-TO-SPEECH HANDLERS --------------------

@bot.message_handler(commands=["change_voice"])
def cmd_change_voice(m):
    uid = m.from_user.id
    update_user_activity(uid)

    kb = generate_tts_language_keyboard()
    bot.send_message(m.chat.id, "🎙️ Choose a TTS language:", reply_markup=kb)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(c):
    uid = c.from_user.id
    update_user_activity(uid)

    _, lang_key = c.data.split("|", 1)
    kb = generate_tts_voice_keyboard(lang_key)
    bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        text=f"🎙️ Choose a voice for **{lang_key}**:",
        parse_mode="Markdown",
        reply_markup=kb
    )
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(c):
    uid = c.from_user.id
    update_user_activity(uid)

    _, voice = c.data.split("|", 1)
    users_tts[str(uid)] = voice
    save_users_tts()

    bot.answer_callback_query(c.id, f"✔️ Voice changed to {voice}")
    bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        text=f"🔊 Now using voice: *{voice}*\n\nSend any text message, and I'll reply with speech.",
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_langs")
def on_tts_back_to_languages(c):
    uid = c.from_user.id
    update_user_activity(uid)

    kb = generate_tts_language_keyboard()
    bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        text="🎙️ Choose a TTS language:",
        reply_markup=kb
    )
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text_messages(m):
    uid = m.from_user.id
    update_user_activity(uid)

    # If text starts with a command, ignore here
    if m.text.startswith('/'):
        return

    # Check if this user has selected a TTS voice
    voice = get_user_voice(uid)
    if voice:
        # Perform TTS
        asyncio.run(synth_and_send(m.chat.id, uid, m.text))
    else:
        bot.send_message(m.chat.id,
                         "🔊 You haven't set a TTS voice yet. Use /change_voice to pick one.")

async def a_main(voice, text, filename, rate=0, pitch=0, volume=1.0):
    mss = MSSpeech()
    await mss.set_voice(voice)
    await mss.set_rate(rate)
    await mss.set_pitch(pitch)
    await mss.set_volume(volume)
    return await mss.synthesize(text, filename)

async def synth_and_send(chat_id, user_id, text):
    voice = get_user_voice(user_id)
    filename = os.path.join(AUDIO_DIR, f"{user_id}.mp3")
    try:
        bot.send_chat_action(chat_id, "record_audio")
        await a_main(voice, text, filename)
        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            bot.send_message(chat_id, "❌ Failed to generate audio. Please try again.")
            return

        with open(filename, "rb") as f:
            bot.send_audio(chat_id, f, caption=f"🎤 Voice: {voice}")
    except MSSpeechError as e:
        bot.send_message(chat_id, f"❌ TTS error: {e}")
    except Exception as e:
        logging.exception(f"TTS error for user {user_id}: {e}")
        bot.send_message(chat_id, "❌ Unexpected error during TTS. Try again.")
    finally:
        if os.path.exists(filename):
            os.remove(filename)

# -------------------- FALLBACK HANDLER --------------------

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document'])
def fallback_non_supported(message):
    update_user_activity(message.from_user.id)
    bot.send_message(message.chat.id, "❌ Please send voice, audio, video (for transcription) or text (for TTS).")

# -------------------- CLEANUP OLD DATA --------------------

def cleanup_old_data():
    seven_days_ago = datetime.now() - timedelta(days=7)

    # Clean old transcriptions
    to_delete = []
    for uid_str in user_transcriptions:
        if uid_str in user_data:
            last_act = datetime.fromisoformat(user_data[uid_str])
            if last_act < seven_days_ago:
                to_delete.append(uid_str)
        else:
            to_delete.append(uid_str)
    for uid_str in to_delete:
        del user_transcriptions[uid_str]

    # Clean old chat history
    to_delete_mem = []
    for uid_str in user_memory:
        if uid_str in user_data:
            last_act = datetime.fromisoformat(user_data[uid_str])
            if last_act < seven_days_ago:
                to_delete_mem.append(uid_str)
        else:
            to_delete_mem.append(uid_str)
    for uid_str in to_delete_mem:
        del user_memory[uid_str]

    threading.Timer(24*60*60, cleanup_old_data).start()

# -------------------- WEBHOOK ROUTES --------------------

@app.route('/', methods=['GET', 'POST', 'HEAD'])
def webhook():
    if request.method in ('GET', 'HEAD'):
        return "OK", 200
    if request.method == 'POST' and request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    return abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook_route():
    bot.set_webhook(url=WEBHOOK_URL)
    return f"Webhook set to {WEBHOOK_URL}", 200

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook_route():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

# -------------------- MAIN --------------------

if __name__ == "__main__":
    set_bot_info()
    cleanup_old_data()
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
