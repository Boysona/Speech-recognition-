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
from msspeech import MSSpeech, MSSpeechError # Ensure msspeech library is installed

# --- CONFIGURATION ---
# Replace with your actual bot token
TOKEN = "7790991731:AAHZks7W-iwp6pcKD56eOeq3wduPjAiwow"
# Replace with your actual Admin ID
ADMIN_ID = 5978150981
# Webhook URL - Replace with your actual Render URL
WEBHOOK_URL = "https://speech-recognition-6i0c.onrender.com"
# Gemini API Key (ensure this is secure in production, e.g., environment variable)
GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("merged-bot")

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- Directories ---
DOWNLOAD_DIR = "downloads"
AUDIO_DIR = "tts_audio_files" # For text-to-speech outputs
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

# --- User Data Files ---
# For general user activity and admin tracking
USERS_FILE = 'users.json'
# For transcription/summarization/translation language preferences
USER_LANGUAGE_SETTINGS_FILE = 'user_language_settings.json'
# For media transcription language preferences
USER_MEDIA_LANGUAGE_SETTINGS_FILE = 'user_media_language_settings.json'
# For text-to-speech voice preferences
TTS_USERS_FILE = 'tts_users.json'

# Load user data
user_activity_data = {}
if os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'r') as f:
        try:
            user_activity_data = json.load(f)
        except json.JSONDecodeError:
            user_activity_data = {}

user_language_settings = {}
if os.path.exists(USER_LANGUAGE_SETTINGS_FILE):
    with open(USER_LANGUAGE_SETTINGS_FILE, 'r') as f:
        try:
            user_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_language_settings = {}

user_media_language_settings = {}
if os.path.exists(USER_MEDIA_LANGUAGE_SETTINGS_FILE):
    with open(USER_MEDIA_LANGUAGE_SETTINGS_FILE, 'r') as f:
        try:
            user_media_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_media_language_settings = {}

tts_users = {} # Stores voice preferences for TTS
if os.path.exists(TTS_USERS_FILE):
    try:
        with open(TTS_USERS_FILE, "r") as f:
            tts_users = json.load(f)
    except json.JSONDecodeError:
        logger.warning(f"Error decoding JSON from {TTS_USERS_FILE}. Starting with empty TTS users.")
        tts_users = {}

def save_user_activity_data():
    with open(USERS_FILE, 'w') as f:
        json.dump(user_activity_data, f, indent=4)

def save_user_language_settings():
    with open(USER_LANGUAGE_SETTINGS_FILE, 'w') as f:
        json.dump(user_language_settings, f, indent=4)

def save_user_media_language_settings():
    with open(USER_MEDIA_LANGUAGE_SETTINGS_FILE, 'w') as f:
        json.dump(user_media_language_settings, f, indent=4)

def save_tts_users():
    with open(TTS_USERS_FILE, "w") as f:
        json.dump(tts_users, f, indent=2)

# In-memory chat history and transcription store
user_memory = {}
user_transcriptions = {}
processing_message_ids = {}  # To keep track of messages for which typing action is active

# Statistics counters (global variables for transcription bot)
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()  # To prevent race conditions

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB
admin_state = {} # For broadcast functionality

# --- Gemini API for Summarization/Translation ---
def ask_gemini(user_id, user_message):
    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    # Keep last 10 messages for context
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        resp = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": parts}]})
        resp.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        result = resp.json()
        if "candidates" in result:
            reply = result['candidates'][0]['content']['parts'][0]['text']
            user_memory[user_id].append({"role": "model", "text": reply})
            return reply
        return "Error: " + json.dumps(result)
    except requests.exceptions.RequestException as e:
        logger.error(f"Gemini API request failed: {e}")
        return f"Error: Failed to connect to AI service. Please try again later."
    except Exception as e:
        logger.error(f"Error processing Gemini response: {e}")
        return f"Error: An unexpected error occurred with the AI service."

# --- Unified Bot Commands and Descriptions ---
def set_bot_info():
    commands = [
        # General
        telebot.types.BotCommand("start", "👋Get a welcome message and info"),
        telebot.types.BotCommand("help", "❓Get information on how to use the bot"),
        telebot.types.BotCommand("privacy", "👮Privacy Notice"),
        # Media Transcriber
        telebot.types.BotCommand("language", "🌐Change preferred language for translate/summarize"),
        telebot.types.BotCommand("media_language", "📝Set language for media transcription"),
        telebot.types.BotCommand("translate", "Translate a replied transcription"),
        telebot.types.BotCommand("summarize", "Summarize a replied transcription"),
        # Text-to-Speech
        telebot.types.BotCommand("change_voice", "🎤Change your voice for Text-to-Speech"),
        telebot.types.BotCommand("tts_help", "🎧Get help for Text-to-Speech"),
        # Admin Commands (visible to admin only)
        telebot.types.BotCommand("status", "📊View Bot statistics"),
        telebot.types.BotCommand("broadcast", "📢Send a message to all users"),
    ]
    bot.set_my_commands(commands)

    bot.set_my_short_description(
        "Your all-in-one media assistant! Transcribe, summarize, translate, and convert text to speech."
    )

    bot.set_my_description(
        """I'm your versatile Telegram bot, combining powerful features to enhance your communication:

    **📝 Media Transcriber:**
    Quickly transcribe, summarize, and translate voice messages, audio files, and videos for free! Before sending media, use /media_language to set the audio's language for best accuracy.

    **🎤 Text-to-Speech (TTS):**
    Convert any text into natural-sounding speech using various voices and languages. Use /change_voice to customize your preferred voice.

    **🛡️ Anti-Spam (for Groups):**
    Add me to your group to keep it clean and focused! I can automatically remove spam, excessively long messages, and messages with links or mentions.

    Enjoy a seamless experience with all these features in one place!
    """
    )

# --- General Utility Functions ---
def update_user_activity(user_id):
    user_activity_data[str(user_id)] = datetime.now().isoformat()
    save_user_activity_data()

# Function to keep sending 'typing' action
def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

# Function to update uptime message (for admin)
def update_uptime_message(chat_id, message_id):
    """
    Live-update the admin uptime message every second, showing days, hours, minutes and seconds.
    """
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

def is_active_within(ts_iso, days):
    """Checks if a user was active within a specified number of days."""
    try:
        last = datetime.fromisoformat(ts_iso)
        return (datetime.utcnow() - last).days < days
    except:
        return False

def get_user_counts():
    """Returns total, monthly, and weekly active user counts."""
    total = len(user_activity_data)
    monthly = sum(is_active_within(ts, 30) for ts in user_activity_data.values())
    weekly = sum(is_active_within(ts, 7) for ts in user_activity_data.values())
    return total, monthly, weekly

# --- Transcription Bot Specific Handlers ---
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
    {"name": "Latvian", "flag": "🇱🇻", "code": "lv-LT"},
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
    {"name": "Welsh", "flag": "🏴", "code": "cy-GB"},
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

def transcribe_audio_from_bytes(audio_bytes: bytes, lang_code: str) -> str | None:
    r = sr.Recognizer()
    full_transcription = []
    chunk_length_ms = 10 * 1000  # 10 seconds (for robustness with free APIs)
    overlap_ms = 500

    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")
        total_length_ms = len(audio)

        start_ms = 0
        logging.info(f"Starting chunking for in-memory audio, total length {total_length_ms / 1000} seconds.")

        while start_ms < total_length_ms:
            end_ms = min(start_ms + chunk_length_ms, total_length_ms)
            chunk = audio[start_ms:end_ms]

            chunk_io = io.BytesIO()
            chunk.export(chunk_io, format="wav")
            chunk_io.seek(0)

            with sr.AudioFile(chunk_io) as source:
                try:
                    audio_listened = r.record(source)
                    text = r.recognize_google(audio_listened, language=lang_code)
                    full_transcription.append(text)
                    logging.info(f"Transcribed chunk from {start_ms/1000}s to {end_ms/1000}s: {text[:50]}...")
                except sr.UnknownValueError:
                    logging.warning(f"Speech Recognition could not understand audio in chunk {start_ms/1000}s - {end_ms/1000}s")
                except sr.RequestError as e:
                    logging.error(f"Could not request results from Google Speech Recognition service; {e} for chunk {start_ms/1000}s - {end_ms/1000}s")
                except Exception as e:
                    logging.error(f"Error processing chunk {start_ms/1000}s - {end_ms/1000}s: {e}")
                finally:
                    chunk_io.close()

            start_ms += chunk_length_ms - overlap_ms

        return " ".join(full_transcription) if full_transcription else None

    except Exception as e:
        logging.error(f"Overall transcription error: {e}")
        return None

def do_translate_with_saved_lang(message, uid, lang, message_id):
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to translate.")
        return

    prompt = f"Translate the following text into {lang}. Provide only the translated text, with no additional notes, explanations, or introductory/concluding remarks:\n\n{original}"

    bot.send_chat_action(message.chat.id, 'typing')
    translated = ask_gemini(uid, prompt)

    if translated.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Sorry, an error occurred during translation: {translated}. Please try again later.")
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
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to summarize.")
        return

    prompt = f"Summarize the following text in {lang}. Provide only the summarized text, with no additional notes, explanations, or different versions:\n\n{original}"

    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(uid, prompt)

    if summary.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Sorry, an error occurred during summarization: {summary}. Please try again later.")
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

# --- Text-to-Speech Bot Specific Handlers ---
VOICES_BY_LANGUAGE = {
    "English 🇬🇧": [
        "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-DavisNeural",
        "en-GB-LibbyNeural", "en-GB-RyanNeural", "en-GB-MiaNeural", "en-GB-ThomasNeural",
        "en-AU-NatashaNeural", "en-AU-WilliamNeural", "en-CA-LindaNeural", "en-CA-ClaraNeural",
        "en-IE-EmilyNeural", "en-IE-ConnorNeural", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural"
    ],
    "Somali 🇸🇴": [
        "so-SO-UbaxNeural", "so-SO-MuuseNeural",
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
    "French 🇫🇷": [
        "fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural", "fr-CA-JeanNeural",
        "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-CH-CamilleNeural", "fr-BE-CharlineNeural"
    ],
    "German 🇩🇪": [
        "de-DE-KatjaNeural", "de-DE-ConradNeural", "de-CH-LeniNeural", "de-CH-JanNeural",
        "de-AT-IngridNeural", "de-AT-JonasNeural"
    ],
    "Chinese 🇨🇳": [
        "zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural", "zh-CN-YunjianNeural", "zh-CN-XiaoyunNeural",
        "zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural",
        "zh-SG-XiaoMinNeural", "zh-SG-YunJianNeural"
    ],
    "Japanese 🇯🇵": [
        "ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "ja-JP-MayuNeural", "ja-JP-DaichiNeural"
    ],
    "Portuguese 🇧🇷": [
        "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-PT-RaquelNeural", "pt-PT-DuarteNeural"
    ],
    "Russian 🇷🇺": [
        "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-LarisaNeural", "ru-RU-MaximNeural"
    ],
    "Hindi 🇮🇳": [
        "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"
    ],
    "Turkish 🇹🇷": [
        "tr-TR-EmelNeural", "tr-TR-AhmetNeural"
    ],
    "Korean 🇰🇷": [
        "ko-KR-SunHiNeural", "ko-KR-InJoonNeural"
    ],
    "Italian 🇮🇹": [
        "it-IT-ElsaNeural", "it-IT-DiegoNeural"
    ],
    "Indonesian 🇮🇩": [
        "id-ID-GadisNeural", "id-ID-ArdiNeural"
    ],
    "Vietnamese 🇻🇳": [
        "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"
    ],
    "Thai 🇹🇭": [
        "th-TH-PremwadeeNeural", "th-TH-NiwatNeural"
    ],
    "Dutch 🇳🇱": [
        "nl-NL-ColetteNeural", "nl-NL-MaartenNeural"
    ],
    "Polish 🇵🇱": [
        "pl-PL-ZofiaNeural", "pl-PL-MarekNeural"
    ],
    "Swedish 🇸🇪": [
        "sv-SE-SofieNeural", "sv-SE-MattiasNeural"
    ],
    "Filipino 🇵🇭": [
        "fil-PH-BlessicaNeural", "fil-PH-AngeloNeural"
    ],
    "Greek 🇬🇷": [
        "el-GR-AthinaNeural", "el-GR-NestorasNeural"
    ],
    "Hebrew 🇮🇱": [
        "he-IL-AvriNeural", "he-IL-HilaNeural"
    ],
    "Hungarian 🇭🇺": [
        "hu-HU-NoemiNeural", "hu-HU-AndrasNeural"
    ],
    "Czech 🇨🇿": [
        "cs-CZ-VlastaNeural", "cs-CZ-AntoninNeural"
    ],
    "Danish 🇩🇰": [
        "da-DK-ChristelNeural", "da-DK-JeppeNeural"
    ],
    "Finnish 🇫🇮": [
        "fi-FI-SelmaNeural", "fi-FI-HarriNeural"
    ],
    "Norwegian 🇳🇴": [
        "nb-NO-PernilleNeural", "nb-NO-FinnNeural"
    ],
    "Romanian 🇷🇴": [
        "ro-RO-AlinaNeural", "ro-RO-EmilNeural"
    ],
    "Slovak 🇸🇰": [
        "sk-SK-LukasNeural", "sk-SK-ViktoriaNeural"
    ],
    "Ukrainian 🇺🇦": [
        "uk-UA-PolinaNeural", "uk-UA-OstapNeural"
    ],
    "Malay 🇲🇾": [
        "ms-MY-YasminNeural", "ms-MY-OsmanNeural"
    ],
    "Bengali 🇧🇩": [
        "bn-BD-NabanitaNeural", "bn-BD-BasharNeural"
    ],
    "Tamil 🇮🇳": [
        "ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"
    ],
    "Telugu 🇮🇳": [
        "te-IN-ShrutiNeural", "te-IN-RagavNeural"
    ],
    "Kannada 🇮🇳": [
        "kn-IN-SapnaNeural", "kn-IN-GaneshNeural"
    ],
    "Malayalam 🇮🇳": [
        "ml-IN-SobhanaNeural", "ml-IN-MidhunNeural"
    ],
    "Gujarati 🇮🇳": [
        "gu-IN-DhwaniNeural", "gu-IN-AvinashNeural"
    ],
    "Marathi 🇮🇳": [
        "mr-IN-AarohiNeural", "mr-IN-ManoharNeural"
    ],
    "Urdu 🇵🇰": [
        "ur-PK-AsmaNeural", "ur-PK-FaizanNeural"
    ],
    "Nepali 🇳🇵": [
        "ne-NP-SaritaNeural", "ne-NP-AbhisekhNeural"
    ],
    "Sinhala 🇱🇰": [
        "si-LK-SameeraNeural", "si-LK-ThiliniNeural"
    ],
    "Khmer 🇰🇭": [
        "km-KH-SreymomNeural", "km-KH-PannNeural"
    ],
    "Lao 🇱🇦": [
        "lo-LA-ChanthavongNeural", "lo-LA-KeomanyNeural"
    ],
    "Myanmar 🇲🇲": [
        "my-MM-NilarNeural", "my-MM-ThihaNeural"
    ],
    "Georgian 🇬🇪": [
        "ka-GE-EkaNeural", "ka-GE-GiorgiNeural"
    ],
    "Armenian 🇦🇲": [
        "hy-AM-AnahitNeural", "hy-AM-AraratNeural"
    ],
    {"name": "Azerbaijani", "flag": "🇦🇿", "code": "az-AZ"},
    {"name": "Kazakh", "flag": "🇰🇿", "code": "kk-KZ"},
    {"name": "Uzbek", "flag": "🇺🇿", "code": "uz-UZ"},
    {"name": "Serbian", "flag": "🇷🇸", "code": "sr-RS"},
    {"name": "Croatian", "flag": "🇭🇷", "code": "hr-HR"},
    {"name": "Slovenian", "flag": "🇸🇮", "code": "sl-SI"},
    {"name": "Latvian", "flag": "🇱🇻", "code": "lv-LV"},
    {"name": "Lithuanian", "flag": "🇱🇹", "code": "lt-LT"},
    {"name": "Estonian", "flag": "🇪🇪", "code": "et-EE"},
    {"name": "Amharic", "flag": "🇪🇹", "code": "am-ET"},
    {"name": "Swahili", "flag": "🇰🇪", "code": "sw-KE"},
    {"name": "Zulu", "flag": "🇿🇦", "code": "zu-ZA"},
    {"name": "Xhosa", "flag": "🇿🇦", "code": "xh-ZA"},
    {"name": "Afrikaans", "flag": "🇿🇦", "code": "af-ZA"}
}

def get_user_voice(uid):
    return tts_users.get(str(uid), "en-US-AriaNeural") # Default voice

def make_tts_language_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    for lang_name in VOICES_BY_LANGUAGE.keys():
        kb.add(InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}"))
    return kb

def make_voice_keyboard_for_language(lang_name):
    kb = InlineKeyboardMarkup(row_width=2)
    voices = VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        kb.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    kb.add(InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_to_languages"))
    return kb

async def synth_and_send(chat_id, user_id, text):
    voice = get_user_voice(user_id)
    filename = os.path.join(AUDIO_DIR, f"{user_id}.mp3")

    try:
        bot.send_chat_action(chat_id, "record_audio")
        mss = MSSpeech()
        await mss.set_voice(voice)
        # You can add rate, pitch, volume adjustments here if needed, e.g.:
        # await mss.set_rate(0)
        # await mss.set_pitch(0)
        # await mss.set_volume(1.0)
        await mss.synthesize(text, filename)

        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            bot.send_message(chat_id, "❌ MP3 file not generated or empty. Please try again.")
            return

        with open(filename, "rb") as f:
            bot.send_audio(chat_id, f, caption=f"🎤 Voice: {voice}")
    except MSSpeechError as e:
        bot.send_message(chat_id, f"❌ An error occurred during voice synthesis: {e}")
    except Exception as e:
        logger.exception("TTS error")
        bot.send_message(chat_id, "❌ An unexpected error occurred during Text-to-Speech. Please try again.")
    finally:
        if os.path.exists(filename):
            os.remove(filename) # Clean up the audio file

# --- General Command Handlers ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if user_id not in user_activity_data:
        user_activity_data[user_id] = datetime.now().isoformat()
        save_user_activity_data()

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "Admin Panel and Uptime (updating live)...", reply_markup=keyboard)

        with admin_uptime_lock:
            if admin_uptime_message.get(ADMIN_ID) and admin_uptime_message[ADMIN_ID].get('thread') and admin_uptime_message[ADMIN_ID]['thread'].is_alive():
                pass # Uptime thread is already running
            else:
                admin_uptime_message[ADMIN_ID] = {'message_id': sent_message.message_id, 'chat_id': message.chat.id}
                uptime_thread = threading.Thread(target=update_uptime_message, args=(message.chat.id, sent_message.message_id))
                uptime_thread.daemon = True
                uptime_thread.start()
                admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread

    else:
        display_name = message.from_user.first_name or (f"@{message.from_user.username}" if message.from_user.username else "user")
        bot.send_message(
            message.chat.id,
            f"""👋🏻 Salom {display_name}!
I'm your all-in-one media assistant. I can help you:
    **📝 Transcribe, summarize, and translate voice/audio/video.**
    **🎤 Convert text to speech.**
    **🛡️ Keep your groups spam-free.**

Send /help for more information on how to use all my features!
"""
            , parse_mode="Markdown"
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    update_user_activity(message.from_user.id)
    help_text = (
        """ℹ️ How to use this bot:

This bot combines several powerful features:

1.  **Media Transcription, Summarization, and Translation:**
    * Send a **voice message, audio file, or video** to the bot.
    * **Important:** Before sending your media, use the `/media_language` command to specify the language of the audio for accurate transcription.
    * After transcription, you'll see inline buttons to **Translate** or **Summarize** the text. You can set your preferred language for these outputs using `/language`.

2.  **Text-to-Speech (TTS):**
    * Simply send any **text message** to the bot.
    * It will convert your text into natural-sounding speech.
    * Use `/change_voice` to select your preferred language and voice for TTS.
    * Use `/tts_help` for more specific TTS instructions.

3.  **Anti-Spam for Groups:**
    * **Add this bot to your group as an administrator.**
    * It will automatically delete overly long messages, messages with links, and messages containing user mentions to keep your chat clean and focused.

**Available Commands:**
    * `/start`: Get a welcome message and general info.
    * `/help`: Display these instructions.
    * `/language`: Set your preferred language for text translation and summarization.
    * `/media_language`: Set the language of the audio in your media files for transcription.
    * `/translate`: Reply to a transcription to translate it.
    * `/summarize`: Reply to a transcription to summarize it.
    * `/change_voice`: Choose a voice for Text-to-Speech.
    * `/tts_help`: Get specific help for Text-to-Speech.
    * `/privacy`: Read the bot's privacy notice.

Enjoy transcribing, translating, summarizing, and generating speech with ease!
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    update_user_activity(message.from_user.id)
    privacy_text = (
        """**Privacy Notice**

Your privacy is paramount. Here's a transparent look at how this bot handles your data in real-time:

1.  **Data We Process & Its Lifecycle:**
    * **Media Files (Voice, Audio, Video):** When you send a media file, it's temporarily downloaded for **immediate transcription**. Crucially, these files are **deleted instantly** from our servers once the transcription is complete. We do not store your media content.
    * **Text-to-Speech Audio:** Generated audio files are temporarily stored to be sent to you, then **deleted instantly** from our servers.
    * **Transcriptions & Text for TTS:** The text you send for TTS or generated from your media is held **temporarily in the bot's memory** for a limited period (e.g., for follow-up actions like translation/summarization or for TTS processing). This data is not permanently stored on our servers and is cleared regularly (e.g., when new media is processed, when the bot restarts, or after 7 days as per cleanup).
    * **User IDs:** Your Telegram User ID is stored. This helps us remember your language and voice preferences and track basic, aggregated activity (like when you last used the bot) to improve service and understand overall usage patterns. This ID is not linked to any personal identifying information outside of Telegram.
    * **Language & Voice Preferences:** Your chosen languages for translations/summaries, media transcription, and TTS voice preferences are saved. This ensures you don't need to re-select them for every interaction, making your experience smoother.

2.  **How Your Data is Used:**
    * To deliver the bot's core services: transcribing, translating, summarizing your media, and converting text to speech.
    * To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total files processed).
    * To maintain your personalized language and voice settings across sessions.
    * For the anti-spam feature, message content is analyzed in real-time within your group to determine if it should be deleted. This content is not stored.

3.  **Data Sharing Policy:**
    * We **do not share** your personal data, media files, or transcriptions with any third parties.
    * Transcription, translation, and summarization are facilitated by integrating with advanced AI models (Google Speech-to-Text API for transcription and the Gemini API for translation/summarization). Text-to-speech uses Microsoft's Cognitive Services. Your input sent to these models is governed by their respective privacy policies, but we ensure that your data is **not stored by us** after processing by these services.

4.  **Data Retention:**
    * **Media files and generated audio:** Deleted immediately post-processing.
    * **Transcriptions and TTS text:** Held temporarily in the bot's active memory for immediate use and cleared after 7 days or when superseded.
    * **User IDs and preferences:** Retained to support your settings and for anonymous usage statistics. If you wish to have your stored preferences removed, you can cease using the bot or contact the bot administrator for explicit data deletion.

By using this bot, you acknowledge and agree to the data practices outlined in this Privacy Notice.

Should you have any questions or concerns regarding your privacy, please feel free to contact the bot administrator.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    update_user_activity(message.from_user.id)
    if message.from_user.id != ADMIN_ID:
        return bot.send_message(message.chat.id, "🔒 This command is for admins only.")

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    today = datetime.now().date()
    active_today = sum(
        1 for timestamp in user_activity_data.values()
        if datetime.fromisoformat(timestamp).date() == today
    )

    total_proc_seconds = int(total_processing_time)
    proc_hours = total_proc_seconds // 3600
    proc_minutes = (total_proc_seconds % 3600) // 60
    proc_seconds = total_proc_seconds % 60

    total_users_count, monthly_active, weekly_active = get_user_counts()

    text = (
        "📊 **Bot Statistics**\n\n"
        "🟢 **Bot Status: Online**\n"
        f"⏳ Uptime: {days} days, {hours} hours, {minutes} minutes, {seconds} seconds\n\n"
        "👥 **User Statistics**\n"
        f"▫️ Total Users Today: {active_today}\n"
        f"▫️ Total Registered Users: {total_users_count}\n"
        f"▫️ Active Last 7 Days: {weekly_active}\n"
        f"▫️ Active Last 30 Days: {monthly_active}\n\n"
        "⚙️ **Media Processing Statistics**\n"
        f"▫️ Total Files Processed: {total_files_processed}\n"
        f"▫️ Audio Files: {total_audio_files}\n"
        f"▫️ Voice Clips: {total_voice_clips}\n"
        f"▫️ Videos: {total_videos}\n"
        f"⏱️ Total Media Processing Time: {proc_hours} hours {proc_minutes} minutes {proc_seconds} seconds\n\n"
        "⸻\n\n"
        "Thanks for using our service! 🙌"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users_admin(message):
    update_user_activity(message.from_user.id)
    bot.send_message(message.chat.id, f"Total registered users: {len(user_activity_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast_admin(message):
    update_user_activity(message.from_user.id)
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message_handler(message):
    update_user_activity(message.from_user.id)
    admin_state[message.from_user.id] = None # Reset state
    success = fail = 0
    # Iterate through all user IDs ever recorded
    for uid_key in user_activity_data:
        uid = int(uid_key) # Ensure user ID is an integer
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid}: {e}")
            fail += 1
    bot.send_message(
        message.chat.id,
        f"📣 Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

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

@bot.message_handler(commands=['translate'])
def handle_translate_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to translate it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        threading.Thread(target=do_translate_with_saved_lang, args=(message, uid, preferred_lang, transcription_message_id)).start()
    else:
        markup = generate_language_keyboard("translate_to", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want to translate into:",
            reply_markup=markup
        )

@bot.message_handler(commands=['summarize'])
def handle_summarize_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to summarize it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        threading.Thread(target=do_summarize_with_saved_lang, args=(message, uid, preferred_lang, transcription_message_id)).start()
    else:
        markup = generate_language_keyboard("summarize_in", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want the summary in:",
            reply_markup=markup
        )

@bot.message_handler(commands=["change_voice"])
def cmd_change_voice(message):
    update_user_activity(message.from_user.id)
    bot.send_message(message.chat.id, "🎙️ Choose a language for Text-to-Speech:", reply_markup=make_tts_language_keyboard())

@bot.message_handler(commands=["tts_help"])
def cmd_tts_help(message):
    update_user_activity(message.from_user.id)
    help_text = (
        """🎧 **Text-to-Speech (TTS) Help:**

To use the Text-to-Speech feature:
1.  **Simply send me any text message.** I will convert it into an audio file.
2.  To change the voice or language, use the `/change_voice` command.
    * You'll first select a language, then choose from available voices for that language.

Currently, I support a wide range of languages and neural voices for high-quality speech.

Enjoy converting your texts into natural-sounding audio!
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

# --- Message Content Handlers ---
@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if uid not in user_media_language_settings:
        bot.send_message(message.chat.id,
                         "⚠️ Please first select the language of the audio file using /media_language before sending the file for transcription.")
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "😓 Sorry, the file size you uploaded is too large (max allowed is 20MB).")

    # ─── Directly send "👀" reaction ────────────────────────────────────────────
    try:
        bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[{'type': 'emoji', 'emoji': '👀'}])
    except Exception as e:
        logger.error(f"Error setting reaction: {e}")
    # ──────────────────────────────────────────────────────────────────────────────

    # Start typing indicator
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing  # Store for cleanup

    try:
        # Process the file in a separate thread
        threading.Thread(target=process_media_file, args=(message, stop_typing)).start()

    except Exception as e:
        logger.error(f"Error initiating file processing: {e}")
        stop_typing.set()  # Ensure typing indicator stops if an error occurs early
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[]) # Remove all reactions
        except Exception as remove_e:
            logger.error(f"Error removing reaction on early error: {remove_e}")
        bot.send_message(message.chat.id, "😓 Sorry, an unexpected error occurred. Please try again.")

def process_media_file(message, stop_typing):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
    uid = str(message.from_user.id)
    file_obj = message.voice or message.audio or message.video or message.video_note

    local_temp_file = None
    wav_audio_data = None

    try:
        info = bot.get_file(file_obj.file_id)
        file_extension = ".ogg" if message.voice or message.video_note else os.path.splitext(info.file_path)[1]

        local_temp_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{file_extension}")
        data = bot.download_file(info.file_path)
        with open(local_temp_file, 'wb') as f:
            f.write(data)

        processing_start_time = datetime.now()

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
                raise Exception("FFmpeg conversion failed or resulted in empty file.")

            with open(temp_wav_file, 'rb') as f:
                wav_audio_data = f.read()

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg conversion failed: {e.stdout.decode()} {e.stderr.decode()}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logger.error(f"Error removing reaction on FFmpeg error: {remove_e}")
            bot.send_message(message.chat.id,
                             "😓 Sorry, there was an issue converting your audio. The file might be corrupted or in an unsupported format. Please try again with a different file.")
            return

        except Exception as e:
            logger.error(f"FFmpeg conversion failed: {e}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logger.error(f"Error removing reaction on FFmpeg general error: {remove_e}")
            bot.send_message(message.chat.id,
                             "😓 Sorry, your file cannot be converted to the correct voice recognition format. Please ensure it's a standard audio/video file.")
            return

        finally:
            if os.path.exists(temp_wav_file):
                os.remove(temp_wav_file)

        media_lang_code = get_lang_code(user_media_language_settings[uid])
        if not media_lang_code:
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logger.error(f"Error removing reaction on language code error: {remove_e}")
            bot.send_message(message.chat.id,
                             f"❌ The language *{user_media_language_settings[uid]}* does not have a valid code for transcription. Please re-select the language using /media_language.")
            return

        transcription = transcribe_audio_from_bytes(wav_audio_data, media_lang_code) or ""
        user_transcriptions.setdefault(uid, {})[message.message_id] = transcription

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
            InlineKeyboardButton("Translate", callback_data=f"btn_translate|{message.message_id}"),
            InlineKeyboardButton("Summarize", callback_data=f"btn_summarize|{message.message_id}")
        )

        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as e:
            logger.error(f"Error removing reaction before sending result: {e}")

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
                    caption="Here’s your transcription. Tap a button below for more options."
                )
            os.remove(fn)
        else:
            bot.reply_to(
                message,
                transcription,
                reply_markup=buttons
            )

    except Exception as e:
        logger.error(f"Error processing file for user {uid}: {e}")
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logger.error(f"Error removing reaction on general processing error: {remove_e}")
        bot.send_message(message.chat.id,
                         "😓 Sorry, an error occurred during transcription. The audio might be unclear or very short. Please try again or with a different file.")
    finally:
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]

        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)
            logger.info(f"Cleaned up {local_temp_file}")

# --- Anti-Spam for Groups (Prioritized) ---
@bot.message_handler(
    func=lambda m: m.chat.type in ["group", "supergroup"] and m.content_type == 'text'
)
def anti_spam_filter(message):
    update_user_activity(message.from_user.id)
    try:
        # Check if bot is admin
        bot_member = bot.get_chat_member(message.chat.id, bot.get_me().id)
        if bot_member.status not in ['administrator', 'creator']:
            return  # Bot not admin => can't delete messages

        # Allow group admins/creators to send anything
        user_member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if user_member.status in ['administrator', 'creator']:
            return

        text = message.text or ""
        # Spam detection logic
        if (
            len(text) > 120  # Too long message
            or re.search(r"https?://", text)  # Contains HTTP/HTTPS link
            or "t.me/" in text  # Contains Telegram link
            or re.search(r"@\w+", text)  # Contains user mention
        ):
            bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            logger.info(f"Deleted spam message from user {message.from_user.id} in chat {message.chat.id}")
            # Optionally, notify the user or admin
            # bot.send_message(message.chat.id, "Spam detected and removed.", reply_to_message_id=message.message_id)
            return True # Indicate that the message was handled (deleted)
    except Exception as e:
        logger.warning(f"Anti-spam check failed: {e}")
    return False # Indicate that the message was not handled by anti-spam

# --- Generic Text Message Handler (for TTS or Fallback) ---
@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'), content_types=['text'])
def handle_text_messages_for_tts_or_fallback(message):
    update_user_activity(message.from_user.id)
    # Check if anti-spam already handled and deleted the message
    if message.chat.type in ["group", "supergroup"]:
        # The anti_spam_filter is called via its decorator. If it deletes a message,
        # the bot might not receive it further down the handler chain.
        # However, for direct messages or if not deleted, this handler will run.
        # For simplicity, we can assume anti_spam_filter handles the deletion.
        # If the message made it here, it wasn't spam.
        pass

    # Process for TTS
    # Run in a separate thread to avoid blocking the main bot thread for TTS synthesis
    threading.Thread(target=lambda: asyncio.run(synth_and_send(message.chat.id, message.from_user.id, message.text))).start()

# --- Fallback for other unhandled content types ---
@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document', 'animation', 'contact', 'location', 'venue', 'dice', 'game', 'poll', 'web_app_data', 'invoice', 'successful_payment', 'connected_website', 'passport_data', 'proximity_alert_triggered', 'forum_topic_created', 'forum_topic_closed', 'forum_topic_reopened', 'video_chat_started', 'video_chat_ended', 'video_chat_participants_invited', 'message_auto_delete_timer_changed', 'new_chat_members', 'left_chat_member', 'new_chat_title', 'new_chat_photo', 'delete_chat_photo', 'group_chat_created', 'supergroup_chat_created', 'channel_chat_created', 'migrate_to_chat_id', 'migrate_from_chat_id', 'pinned_message', 'invoice', 'successful_payment', 'user_shared', 'chat_shared'])
def fallback(message):
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private':
        bot.send_message(message.chat.id, "I only process text for Text-to-Speech, and voice messages, audio, or video for transcription. Please send one of those types.")
    else:
        # In groups, simply acknowledge (or ignore) other content types if not handled by anti-spam
        pass

# --- Unified Callback Query Handler ---
@bot.callback_query_handler(func=lambda call: True)
def handle_all_callbacks(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)

    if call.data.startswith("set_lang|"):
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

    elif call.data.startswith("set_media_lang|"):
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

    elif call.data.startswith("btn_translate|"):
        _, message_id_str = call.data.split("|", 1)
        message_id = int(message_id_str)

        if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
            bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
            return

        preferred_lang = user_language_settings.get(uid)
        if preferred_lang:
            bot.answer_callback_query(call.id, "Translating with your preferred language...")
            threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, preferred_lang, message_id)).start()
        else:
            markup = generate_language_keyboard("translate_to", message_id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Please select the language you want to translate into:",
                reply_markup=markup
            )
        bot.answer_callback_query(call.id)

    elif call.data.startswith("btn_summarize|"):
        _, message_id_str = call.data.split("|", 1)
        message_id = int(message_id_str)

        if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
            bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
            return

        preferred_lang = user_language_settings.get(uid)
        if preferred_lang:
            bot.answer_callback_query(call.id, "Summarizing with your preferred language...")
            threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, preferred_lang, message_id)).start()
        else:
            markup = generate_language_keyboard("summarize_in", message_id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Please select the language you want the summary in:",
                reply_markup=markup
            )
        bot.answer_callback_query(call.id)

    elif call.data.startswith("translate_to|"):
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
            threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, message_id)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to translate. Please use the inline buttons on the transcription.")
        bot.answer_callback_query(call.id)

    elif call.data.startswith("summarize_in|"):
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
            threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, message_id)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to summarize. Please use the inline buttons on the transcription.")
        bot.answer_callback_query(call.id)

    elif call.data.startswith("tts_lang|"):
        _, lang_name = call.data.split("|", 1)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🎙️ Choose a voice for {lang_name}:",
            reply_markup=make_voice_keyboard_for_language(lang_name)
        )
        bot.answer_callback_query(call.id)

    elif call.data.startswith("tts_voice|"):
        _, voice = call.data.split("|", 1)
        tts_users[str(call.from_user.id)] = voice
        save_tts_users()
        bot.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Now using: *{voice}*. You can start typing text to convert to speech.",
            parse_mode="Markdown"
        )

    elif call.data == "tts_back_to_languages":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="🎙️ Choose a language for Text-to-Speech:",
            reply_markup=make_tts_language_keyboard()
        )
        bot.answer_callback_query(call.id)

# --- Memory Cleanup Function ---
def cleanup_old_data():
    """Cleans up user_transcriptions and user_memory older than 7 days, and old user activity data."""
    seven_days_ago = datetime.now() - timedelta(days=7)

    # Clean up user_transcriptions
    keys_to_delete_transcriptions = []
    for user_id, transcriptions in user_transcriptions.items():
        if user_id in user_activity_data:
            last_activity = datetime.fromisoformat(user_activity_data[user_id])
            if last_activity < seven_days_ago:
                keys_to_delete_transcriptions.append(user_id)
        else:
            # If user is not in activity data, consider them inactive for cleanup
            keys_to_delete_transcriptions.append(user_id)

    for user_id in keys_to_delete_transcriptions:
        if user_id in user_transcriptions:
            del user_transcriptions[user_id]
            logger.info(f"Cleaned up old transcriptions for user {user_id}")

    # Clean up user_memory (for Gemini chat history)
    keys_to_delete_memory = []
    for user_id in user_memory:
        if user_id in user_activity_data:
            last_activity = datetime.fromisoformat(user_activity_data[user_id])
            if last_activity < seven_days_ago:
                keys_to_delete_memory.append(user_id)
        else:
            keys_to_delete_memory.append(user_id)

    for user_id in keys_to_delete_memory:
        if user_id in user_memory:
            del user_memory[user_id]
            logger.info(f"Cleaned up old chat memory for user {user_id}")

    # Clean up user_activity_data for truly inactive users (e.g., inactive for 90 days)
    ninety_days_ago = datetime.now() - timedelta(days=90)
    keys_to_delete_activity = [
        uid for uid, timestamp_iso in user_activity_data.items()
        if datetime.fromisoformat(timestamp_iso) < ninety_days_ago
    ]
    for user_id in keys_to_delete_activity:
        if user_id in user_activity_data:
            del user_activity_data[user_id]
            logger.info(f"Cleaned up very old activity data for user {user_id}")
    save_user_activity_data()


    threading.Timer(24 * 60 * 60, cleanup_old_data).start()  # Run every 24 hours

# --- Webhook and Flask Setup ---
@app.route('/', methods=['GET', 'POST', 'HEAD'])
def webhook():
    # 1) Health‐check (GET or HEAD) → return 200 OK
    if request.method in ('GET', 'HEAD'):
        return "OK", 200

    # 2) Telegram webhook (POST with JSON)
    if request.method == 'POST' and request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200

    return abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook_route():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        return f"Webhook set to {WEBHOOK_URL}", 200
    except Exception as e:
        return f"Failed to set webhook: {e}", 500

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook_route():
    try:
        bot.delete_webhook()
        return 'Webhook deleted.', 200
    except Exception as e:
        return f"Failed to delete webhook: {e}", 500

if __name__ == "__main__":
    # Set bot commands and descriptions at startup
    set_bot_info()
    # Start cleanup thread
    cleanup_old_data()
    # Run Flask app for webhook
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

