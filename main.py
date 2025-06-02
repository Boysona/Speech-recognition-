import os
import re
import uuid
import shutil
import logging
import requests
import json
import asyncio
import threading
import time
import subprocess
import io
from datetime import datetime, timedelta

from flask import Flask, request, abort
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import speech_recognition as sr
import imageio_ffmpeg as ffmpeg
from pydub import AudioSegment
from msspeech import MSSpeech, MSSpeechError # From Bot 2

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- GLOBAL CONFIGURATION (Merged from all bots) ---
TOKEN = "7790991731:AAHZks7W-iWp6pcKD56eOeq3wduPjAiwow"  # Replace with your actual bot token
ADMIN_ID = 5978150981  # Replace with your actual Admin ID
WEBHOOK_URL = "https://speech-recognition-6i0c.onrender.com" # Replace with your actual Render URL
GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA" # Replace with your actual Gemini API Key

# Directories
DOWNLOAD_DIR = "downloads" # For media transcription temp files
AUDIO_DIR = "audio_files" # For TTS temp files
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

# File size limit for media processing (Bot 1)
FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB

# --- Bot Initialization ---
bot = TeleBot(TOKEN, threaded=True) # Use TeleBot directly
app = Flask(__name__)

# --- User Data & Settings (Merged from all bots) ---
users_file = 'users.json' # Used by Bot 1 for last activity, and Bot 3 for total users. Will be extended for TTS voice settings.
user_data = {} # Stores user last activity (from Bot 1 & 3)

user_language_settings_file = 'user_language_settings.json' # For translate/summarize (Bot 1)
user_language_settings = {}

user_media_language_settings_file = 'user_media_language_settings.json' # For speech recognition (Bot 1)
user_media_language_settings = {}

user_tts_voice_settings_file = 'user_tts_voice_settings.json' # New: For TTS preferred voice (from Bot 2's `users` dict)
user_tts_voice_settings = {}


# Load existing user data
for f_path, data_dict in [
    (users_file, user_data),
    (user_language_settings_file, user_language_settings),
    (user_media_language_settings_file, user_media_language_settings),
    (user_tts_voice_settings_file, user_tts_voice_settings)
]:
    if os.path.exists(f_path):
        with open(f_path, 'r') as f:
            try:
                data_dict.update(json.load(f))
            except json.JSONDecodeError:
                logging.warning(f"Error decoding JSON from {f_path}. Initializing empty data.")
                data_dict = {}

def save_user_data_all():
    """Saves all user-related data to their respective JSON files."""
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)
    with open(user_language_settings_file, 'w') as f:
        json.dump(user_language_settings, f, indent=4)
    with open(user_media_language_settings_file, 'w') as f:
        json.dump(user_media_language_settings, f, indent=4)
    with open(user_tts_voice_settings_file, 'w') as f:
        json.dump(user_tts_voice_settings, f, indent=4)


# In-memory chat history and transcription store (Bot 1)
user_memory = {}
user_transcriptions = {}
processing_message_ids = {}  # To keep track of messages for which typing action is active

# Statistics counters (Bot 1)
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0
bot_start_time = datetime.now()

# Admin uptime message storage (Bot 1)
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()  # To prevent race conditions

admin_state = {} # For admin broadcast (Bot 3)

# --- LANGUAGES FOR TRANSCRIPTION/TRANSLATION/SUMMARIZATION (Bot 1) ---
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

# --- VOICES FOR TEXT-TO-SPEECH (Bot 2) ---
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
        "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-BE-CharlineNeural", "fr-BE-CamilleNeural"
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
    "Azerbaijani 🇦🇿": [
        "az-AZ-BabekNeural", "az-AZ-BanuNeural"
    ],
    "Kazakh 🇰🇿": [
        "kk-KZ-AigulNeural", "kk-KZ-NurzhanNeural"
    ],
    "Uzbek 🇺🇿": [
        "uz-UZ-MadinaNeural", "uz-UZ-SuhrobNeural"
    ],
    "Serbian 🇷🇸": [
        "sr-RS-NikolaNeural", "sr-RS-SophieNeural"
    ],
    "Croatian 🇭🇷": [
        "hr-HR-GabrijelaNeural", "hr-HR-SreckoNeural"
    ],
    "Slovenian 🇸🇮": [
        "sl-SI-PetraNeural", "sl-SI-RokNeural"
    ],
    "Latvian 🇱🇻": [
        "lv-LV-EveritaNeural", "lv-LV-AnsisNeural"
    ],
    "Lithuanian 🇱🇹": [
        "lt-LT-OnaNeural", "lt-LT-LeonasNeural"
    ],
    "Estonian 🇪🇪": [
        "et-EE-LiisNeural", "et-EE-ErkiNeural"
    ],
    "Amharic 🇪🇹": [
        "am-ET-MekdesNeural", "am-ET-AbebeNeural"
    ],
    "Swahili 🇰🇪": [
        "sw-KE-ZuriNeural", "sw-KE-RafikiNeural"
    ],
    "Zulu 🇿🇦": [
        "zu-ZA-ThandoNeural", "zu-ZA-ThembaNeural"
    ],
    "Xhosa 🇿🇦": [
        "xh-ZA-NomusaNeural", "xh-ZA-DumisaNeural"
    ],
    "Afrikaans 🇿🇦": [
        "af-ZA-AdriNeural", "af-ZA-WillemNeural"
    ]
}

# --- Shared Utility Functions ---
def update_user_activity(user_id):
    """Updates the last activity timestamp for a user and saves all user data."""
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data_all()

def ask_gemini(user_id, user_message):
    """Interacts with the Gemini API for translation/summarization."""
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

def get_lang_code(lang_name):
    """Returns the Google Speech Recognition language code for a given language name."""
    for lang in LANGUAGES:
        if lang['name'].lower() == lang_name.lower():
            return lang['code']
    return None

def generate_language_keyboard(callback_prefix, message_id=None):
    """Generates an inline keyboard for language selection (Bot 1)."""
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang in LANGUAGES:
        cb_data = f"{callback_prefix}|{lang['name']}"
        if message_id is not None:
            cb_data += f"|{message_id}"
        buttons.append(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb_data))
    markup.add(*buttons)
    return markup

# Function to keep sending 'typing' action (Bot 1)
def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

# Function to update uptime message (Bot 1)
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
    """Checks if a user was active within a given number of days (Bot 3)."""
    try:
        last = datetime.fromisoformat(ts_iso)
        return (datetime.utcnow() - last).days < days
    except:
        return False

def get_user_counts():
    """Gets total, monthly, and weekly active user counts (Bot 3)."""
    total = len(user_data)
    monthly = sum(is_active_within(ts, 30) for ts in user_data.values())
    weekly  = sum(is_active_within(ts, 7) for ts in user_data.values())
    return total, monthly, weekly

# --- Bot Commands and Descriptions Setup (Merged) ---
def set_bot_info():
    commands = [
        types.BotCommand("start", "👋Get a welcome message and info"),
        types.BotCommand("status", "📊View Bot statistics"),
        types.BotCommand("help", "❓Get information on how to use the bot"),
        types.BotCommand("language", "🌐Change preferred language for translate/summarize"),
        types.BotCommand("media_language", "📝Set language for media transcription"),
        types.BotCommand("change_voice", "🎤Change text-to-speech voice"), # From Bot 2
        types.BotCommand("privacy", "👮Privacy Notice"),
    ]
    bot.set_my_commands(commands)

    bot.set_my_short_description(
        "I can transcribe, summarize, translate media, convert text to speech, and keep groups clean!"
    )

    bot.set_my_description(
        """I'm an all-in-one bot!
    🗣️ **Media to Text:** Quickly transcribe, summarize, and translate voice messages, audio files, and videos for free!
    🎤 **Text to Speech:** Convert any text you send into natural-sounding audio in various languages.
    🛡️ **Group Anti-Spam:** Add me to your group to keep it clean, focused, and free from spam or excessively long messages.

    🔥Enjoy free usage and start now!👌🏻"""
    )

# --- COMMAND HANDLERS (Merged and Prioritized) ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.from_user.id == ADMIN_ID:
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "Admin Panel and Uptime (updating live)...", reply_markup=keyboard)

        with admin_uptime_lock:
            if admin_uptime_message.get(ADMIN_ID) and admin_uptime_message[ADMIN_ID].get('thread') and admin_uptime_message[ADMIN_ID]['thread'].is_alive():
                pass # Already running
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
I'm an all-in-one bot:
    🗣️ **Media to Text:** Transcribe and summarize voice/audio/video. Use /media_language first!
    🎤 **Text to Speech:** Send me text, and I'll convert it to audio. Use /change_voice to pick a voice.
    🛡️ **Group Anti-Spam:** Add me to your group to keep it clean.

Send /help for more information.
"""
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = (
        """ℹ️ How to use this bot:

This bot combines several powerful features:

1.  **Media to Text (Transcription, Summarization, Translation):**
    * Send a voice message, audio file, or video.
    * **Crucially**, before sending your media, use the `/media_language` command to tell the bot the language of the audio for accurate transcription.
    * After transcription, you'll see inline buttons to **Translate** or **Summarize** the text.
    * Use `/language` to set your preferred output language for translations/summaries.

2.  **Text to Speech (TTS):**
    * Simply send any text message to the bot.
    * It will convert your text into natural-sounding audio.
    * Use `/change_voice` to select from a variety of languages and voices for the TTS output.

3.  **Group Anti-Spam (Add me to your group):**
    * Add this bot to your Telegram group and grant it **admin privileges** (specifically, "delete messages").
    * It will automatically delete long messages, links, and mentions to help keep your group clean and focused.

**Available Commands:**
* `/start`: Get a welcome message and info. (Admins see a live uptime panel).
* `/status`: View detailed statistics about the bot's performance and usage (admin only).
* `/help`: Display these instructions on how to use the bot.
* `/language`: Change your preferred language for text translations and summaries.
* `/media_language`: Set the language of the audio in your media files for transcription. **Vital for accuracy!**
* `/change_voice`: Select the voice and language for text-to-speech conversions.
* `/privacy`: Read the bot's privacy notice to understand how your data is handled.

Enjoy using the bot!
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
    * **Transcriptions:** The text generated from your media is held **temporarily in the bot's memory** for a limited period. This allows for follow-up actions like translation or summarization. This data is not permanently stored on our servers and is cleared regularly (e.g., when new media is processed or the bot restarts, or after 7 days as per cleanup).
    * **TTS Audio Files:** When you send text for Text-to-Speech, the generated audio file is **temporarily stored** to be sent to you and is **deleted immediately** after sending.
    * **User IDs:** Your Telegram User ID is stored. This helps us remember your language and voice preferences and track basic, aggregated activity (like when you last used the bot) to improve service and understand overall usage patterns. This ID is not linked to any personal identifying information outside of Telegram.
    * **Language & Voice Preferences:** Your chosen languages for translations/summaries and media transcription, and your preferred TTS voice, are saved. This ensures you don't need to re-select them for every interaction, making your experience smoother.

2.  **How Your Data is Used:**
    * To deliver the bot's core services: transcribing, translating, summarizing your media, and converting text to speech.
    * To filter spam and manage groups (if added to one).
    * To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total files processed).
    * To maintain your personalized language and voice settings across sessions.

3.  **Data Sharing Policy:**
    * We **do not share** your personal data, media files, or transcriptions with any third parties beyond what is strictly necessary for the bot's functionality.
    * **Transcription:** Facilitated by integrating with the Google Speech-to-Text API. Your audio input sent to this model is governed by Google's privacy policies, but we ensure your data is **not stored by us** after processing.
    * **Translation/Summarization:** Facilitated by integrating with the Gemini API. Your text input sent to this model is governed by Google's privacy policies, but we ensure your data is **not stored by us** after processing.
    * **Text-to-Speech:** Facilitated by integrating with Microsoft Azure Cognitive Services Speech API. Your text input sent to this service is governed by Microsoft's privacy policies, but we ensure your data is **not stored by us** after processing.

4.  **Data Retention:**
    * **Media files & TTS audio files:** Deleted immediately post-processing/sending.
    * **Transcriptions:** Held temporarily in the bot's active memory for immediate use and cleared after 7 days or when superseded.
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
        bot.send_message(message.chat.id, "🔒 This command is for admins only.")
        return

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    today = datetime.now().date()
    active_today = sum(
        1 for timestamp in user_data.values()
        if datetime.fromisoformat(timestamp).date() == today
    )
    total_registered_users = len(user_data) # This is the total number of unique users who have interacted with the bot.

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
        f"▫️ Total Unique Users: {total_registered_users}\n\n"
        "⚙️ Processing Statistics\n"
        f"▫️ Total Media Files Processed: {total_files_processed}\n"
        f"▫️ Audio Files: {total_audio_files}\n"
        f"▫️ Voice Clips: {total_voice_clips}\n"
        f"▫️ Videos: {total_videos}\n"
        f"⏱️ Total Media Processing Time: {proc_hours} hours {proc_minutes} minutes {proc_seconds} seconds\n\n"
        "⸻\n\n"
        "Thanks for using our service! 🙌"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users_command(message): # From Bot 3
    total, monthly, weekly = get_user_counts()
    bot.send_message(
        message.chat.id,
        f"📊 User Counts (Based on unique interactions):\n"
        f"• Total Unique Users: {total}\n"
        f"• Active Last 30 Days: {monthly}\n"
        f"• Active Last 7 Days: {weekly}"
    )

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
@bot.message_handler(commands=['broadcast']) # Also capture /broadcast command directly
def send_broadcast_command(message): # From Bot 3
    if message.from_user.id == ADMIN_ID:
        admin_state[message.from_user.id] = 'awaiting_broadcast'
        bot.send_message(message.chat.id, "📢 Send the broadcast message now:")
    else:
        bot.send_message(message.chat.id, "🔒 This command is for admins only.")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message): # From Bot 3
    admin_state[message.from_user.id] = None
    success = fail = 0
    # Iterate through all unique users who have ever interacted with the bot
    for uid_key in list(user_data.keys()):
        try:
            uid = int(uid_key)
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid_key}: {e}")
            fail += 1
    bot.send_message(
        message.chat.id,
        f"📣 Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# --- Media Transcription Handlers (Bot 1) ---

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if uid not in user_media_language_settings:
        bot.send_message(message.chat.id,
                         "⚠️ Please first select the language of the audio file using /media_language before sending the file.")
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "😓 Sorry, the file size you uploaded is too large (max allowed is 20MB).")

    try:
        bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[{'type': 'emoji', 'emoji': '👀'}])
    except Exception as e:
        logging.error(f"Error setting reaction: {e}")

    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing

    try:
        threading.Thread(target=process_media_file, args=(message, stop_typing)).start()
    except Exception as e:
        logging.error(f"Error initiating file processing: {e}")
        stop_typing.set()
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on early error: {remove_e}")
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
            logging.error(f"FFmpeg conversion failed: {e.stdout.decode()} {e.stderr.decode()}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg error: {remove_e}")
            bot.send_message(message.chat.id,
                             "😓 Sorry, there was an issue converting your audio. The file might be corrupted or in an unsupported format. Please try again with a different file.")
            return

        except Exception as e:
            logging.error(f"FFmpeg conversion failed: {e}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg general error: {remove_e}")
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
                logging.error(f"Error removing reaction on language code error: {remove_e}")
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
            logging.error(f"Error removing reaction before sending result: {e}")

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
        logging.error(f"Error processing file for user {uid}: {e}")
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on general processing error: {remove_e}")
        bot.send_message(message.chat.id,
                         "😓 Sorry, an error occurred during transcription. The audio might be unclear or very short. Please try again or with a different file.")
    finally:
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]

        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)
            logging.info(f"Cleaned up {local_temp_file}")

# --- Language Selection and Saving for Transcription/Translation (Bot 1) ---

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
    save_user_data_all()
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
    save_user_data_all()
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

@bot.callback_query_handler(func=lambda c: c.data.startswith("translate_to|"))
def callback_translate_to(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    user_language_settings[uid] = lang
    save_user_data_all()
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Translating to **{lang}**...",
        parse_mode="Markdown"
    )
    
    if message_id:
        threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, message_id)).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id)).start()
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
    save_user_data_all()
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Summarizing in **{lang}**...",
        parse_mode="Markdown"
    )
    
    if message_id:
        threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, message_id)).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to summarize. Please use the inline buttons on the transcription.")
    bot.answer_callback_query(call.id)

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

@bot.message_handler(commands=['translate'])
def handle_translate(message):
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
def handle_summarize(message):
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

# Function to transcribe audio from bytes (in-memory) (Bot 1)
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

# --- Text-to-Speech Functions & Handlers (Bot 2) ---

def get_user_voice(uid):
    """Retrieves the user's preferred TTS voice, or a default."""
    return user_tts_voice_settings.get(str(uid), "en-US-AriaNeural") # Default voice

def make_tts_language_keyboard():
    """Generates an inline keyboard for TTS language selection."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    for lang_name in VOICES_BY_LANGUAGE.keys():
        kb.add(types.InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}"))
    return kb

def make_voice_keyboard_for_language(lang_name):
    """Generates an inline keyboard for TTS voice selection within a language."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    voices = VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        kb.add(types.InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    kb.add(types.InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_to_languages"))
    return kb

async def async_synth_and_send(chat_id, user_id, text):
    """Synthesizes text to speech and sends the audio file asynchronously."""
    voice = get_user_voice(user_id)
    filename = os.path.join(AUDIO_DIR, f"{user_id}_{uuid.uuid4()}.mp3") # Ensure unique filename

    try:
        bot.send_chat_action(chat_id, "record_audio")
        mss = MSSpeech()
        await mss.set_voice(voice)
        await mss.synthesize(text, filename) # Assuming default rate/pitch/volume is fine

        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            bot.send_message(chat_id, "❌ MP3 file not generated or empty. Please try again.")
            return

        with open(filename, "rb") as f:
            bot.send_audio(chat_id, f, caption=f"🎤 Voice: {voice}")
    except MSSpeechError as e:
        bot.send_message(chat_id, f"❌ Wuu jiraa khalad dhinaca codka ah: {e}")
    except Exception as e:
        logging.exception("TTS error")
        bot.send_message(chat_id, "❌ Wuxuu dhacay khalad aan la filayn. Fadlan isku day mar kale.")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
            logging.info(f"Cleaned up {filename}")


@bot.message_handler(commands=["change_voice"])
def cmd_change_voice(m):
    update_user_activity(m.from_user.id)
    bot.send_message(m.chat.id, "🎙️ Choose a language for Text-to-Speech:", reply_markup=make_tts_language_keyboard())

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(c):
    update_user_activity(c.from_user.id)
    _, lang_name = c.data.split("|", 1)
    bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        text=f"🎙️ Choose a voice for {lang_name}:",
        reply_markup=make_voice_keyboard_for_language(lang_name)
    )
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(c):
    update_user_activity(c.from_user.id)
    _, voice = c.data.split("|", 1)
    user_tts_voice_settings[str(c.from_user.id)] = voice
    save_user_data_all()
    bot.answer_callback_query(c.id, f"✔️ Voice changed to {voice}")
    bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        text=f"🔊 Hadda waxaad isticmaalaysaa: *{voice}*. Waxaad bilaabi kartaa inaad qorto qoraalka si aan ugu bedelo cod.",
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(c):
    update_user_activity(c.from_user.id)
    bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        text="🎙️ Choose a language for Text-to-Speech:",
        reply_markup=make_tts_language_keyboard()
    )
    bot.answer_callback_query(c.id)

# --- Anti-Spam for Groups (Bot 3) ---
@bot.message_handler(
    func=lambda m: m.chat.type in ["group", "supergroup"] and m.content_type == 'text'
)
def anti_spam_filter(message):
    # Don't track user activity for every group message for efficiency, only relevant ones
    # update_user_activity(message.from_user.id) # Potentially too frequent for large groups

    try:
        # Check if bot is admin in the group
        bot_member = bot.get_chat_member(message.chat.id, bot.get_me().id)
        if bot_member.status not in ['administrator', 'creator'] or not bot_member.can_delete_messages:
            return  # Bot not admin or lacks delete permission => can't delete

        # Check if sender is admin
        user_member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if user_member.status in ['administrator', 'creator']:
            return  # Allow group admins/creators to send anything

        text = message.text or ""
        # Spam detection logic: too long, contains http(s) links, t.me links, or mentions
        if (
            len(text) > 120
            or re.search(r"https?://", text)
            or "t.me/" in text
            or re.search(r"@\w+", text)
        ):
            bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            logging.info(f"Deleted potential spam from user {message.from_user.id} in chat {message.chat.id}")
    except Exception as e:
        logging.warning(f"Anti-spam check failed in chat {message.chat.id}: {e}")

# --- General Text Message Handler (for TTS, after all commands) ---
@bot.message_handler(func=lambda m: m.content_type == 'text' and not m.text.startswith('/'))
def handle_text_messages_for_tts(m):
    uid = str(m.from_user.id)
    update_user_activity(uid)
    # This handler acts as a fallback for any text that isn't a command or part of an admin state
    # Send to async TTS function
    threading.Thread(target=lambda: asyncio.run(async_synth_and_send(m.chat.id, m.from_user.id, m.text))).start()

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document'])
def fallback_unsupported_media(message):
    """Handles unsupported media types, asking the user to send appropriate files."""
    update_user_activity(message.from_user.id)
    bot.send_message(message.chat.id, "Please send only voice messages, audio, or video for transcription, or text for Text-to-Speech. Other media types are not supported.")


# --- Memory Cleanup Function (Bot 1) ---
def cleanup_old_data():
    """Cleans up user_transcriptions and user_memory older than 7 days."""
    seven_days_ago = datetime.now() - timedelta(days=7)

    keys_to_delete_transcriptions = []
    for user_id, transcriptions in user_transcriptions.items():
        if user_id in user_data: # Only check if user_data contains this user
            last_activity = datetime.fromisoformat(user_data[user_id])
            if last_activity < seven_days_ago:
                keys_to_delete_transcriptions.append(user_id)
        else: # If user_id not in user_data (e.g., deleted), also clean up
            keys_to_delete_transcriptions.append(user_id)

    for user_id in keys_to_delete_transcriptions:
        if user_id in user_transcriptions:
            del user_transcriptions[user_id]
            logging.info(f"Cleaned up old transcriptions for user {user_id}")

    keys_to_delete_memory = []
    for user_id in user_memory:
        if user_id in user_data:
            last_activity = datetime.fromisoformat(user_data[user_id])
            if last_activity < seven_days_ago:
                keys_to_delete_memory.append(user_id)
        else:
            keys_to_delete_memory.append(user_id)

    for user_id in keys_to_delete_memory:
        if user_id in user_memory:
            del user_memory[user_id]
            logging.info(f"Cleaned up old chat memory for user {user_id}")
    
    # Schedule next cleanup
    threading.Timer(24 * 60 * 60, cleanup_old_data).start()  # Run every 24 hours


# --- Webhook and Flask App Setup ---
@app.route('/', methods=['GET', 'POST', 'HEAD'])
def webhook():
    if request.method in ('GET', 'HEAD'):
        return "OK", 200

    if request.method == 'POST' and request.headers.get('content-type') == 'application/json':
        update = types.Update.de_json(request.get_data().decode('utf-8'))
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
    
    # Start the cleanup thread for old data
    cleanup_old_data() 

    # Run the Flask app
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

