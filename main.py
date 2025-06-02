import os
import re
import uuid
import json
import shutil
import logging
import requests
import threading
import subprocess
import asyncio
import time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from telebot import TeleBot, types
import speech_recognition as sr
import imageio_ffmpeg as ffmpeg
from pydub import AudioSegment
from msspeech import MSSpeech, MSSpeechError

# =========================
# === CONFIGURATION KEYS ===
# =========================

# --- Use the media‐transcriber bot’s token and admin ID ---
TOKEN = "7790991731:AAHZks7W-iEwp6pcKD56eOeq3wduPjAiwow"   # Bot #1’s token
ADMIN_ID = 5978150981                                       # Bot #1’s admin ID

# Webhook URL (must point to your deployed endpoint)
WEBHOOK_URL = "https://speech-recognition-6i0c.onrender.com"  # Bot #1’s Render URL

# =======================
# === GLOBAL SETTINGS ===
# =======================

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask app and TeleBot initialization
app = Flask(__name__)
bot = TeleBot(TOKEN, threaded=True)

# Directories
DOWNLOAD_DIR = "downloads"
AUDIO_DIR = "audio_files"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

# ================
# === STORAGE ====
# ================

# 1) User tracking & language preferences (from Bot #1 & Bot #3)
USERS_FILE = 'users.json'                        # tracks last activity & basic settings
user_data = {}
if os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'r') as f:
        try:
            user_data = json.load(f)
        except json.JSONDecodeError:
            user_data = {}

def save_user_data():
    with open(USERS_FILE, 'w') as f:
        json.dump(user_data, f, indent=4)

# 2) Media‐language preferences (Bot #1)
USER_MEDIA_LANG_FILE = 'user_media_language_settings.json'
user_media_language_settings = {}
if os.path.exists(USER_MEDIA_LANG_FILE):
    with open(USER_MEDIA_LANG_FILE, 'r') as f:
        try:
            user_media_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_media_language_settings = {}

def save_user_media_language_settings():
    with open(USER_MEDIA_LANG_FILE, 'w') as f:
        json.dump(user_media_language_settings, f, indent=4)

# 3) Translation/summarization language preferences (Bot #1)
USER_LANG_SETTINGS_FILE = 'user_language_settings.json'
user_language_settings = {}
if os.path.exists(USER_LANG_SETTINGS_FILE):
    with open(USER_LANG_SETTINGS_FILE, 'r') as f:
        try:
            user_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_language_settings = {}

def save_user_language_settings():
    with open(USER_LANG_SETTINGS_FILE, 'w') as f:
        json.dump(user_language_settings, f, indent=4)

# 4) TTS voice selections (Bot #2)
TTS_USERS_FILE = 'tts_users.json'
tts_users = {}
if os.path.exists(TTS_USERS_FILE):
    with open(TTS_USERS_FILE, 'r') as f:
        try:
            tts_users = json.load(f)
        except json.JSONDecodeError:
            tts_users = {}

def save_tts_users():
    with open(TTS_USERS_FILE, 'w') as f:
        json.dump(tts_users, f, indent=2)

# ================================
# === IN‐MEMORY TRANSCRIPTION ====
# ================================

# For Bot #1: store recent transcriptions & chat history
user_memory = {}          # for Gemini chat history
user_transcriptions = {}  # { user_id: { message_id: transcription_text } }

# ===============================
# === STATISTICS & UPTIME (1) ===
# ===============================

total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0.0
bot_start_time = datetime.now()

# Store ongoing typing‐indicator threads for media requests
processing_message_ids = {}

# Gem­­ini API key for translations/summaries
GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"  # Replace with your Gemini API key

# ============================
# === TTS VOICE SELECTION ====
# ============================

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
    "Japanese 🇯🇵": ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "ja-JP-MayuNeural", "ja-JP-DaichiNeural"],
    "Portuguese 🇧🇷": ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-PT-RaquelNeural", "pt-PT-DuarteNeural"],
    "Russian 🇷🇺": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-LarisaNeural", "ru-RU-MaximNeural"],
    "Hindi 🇮🇳": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"],
    "Turkish 🇹🇷": ["tr-TR-EmelNeural", "tr-TR-AhmetNeural"],
    "Korean 🇰🇷": ["ko-KR-SunHiNeural", "ko-KR-InJoonNeural"],
    "Italian 🇮🇹": ["it-IT-ElsaNeural", "it-IT-DiegoNeural"],
    "Indonesian 🇮🇩": ["id-ID-GadisNeural", "id-ID-ArdiNeural"],
    "Vietnamese 🇻🇳": ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"],
    "Thai 🇹🇭": ["th-TH-PremwadeeNeural", "th-TH-NiwatNeural"],
    "Dutch 🇳🇱": ["nl-NL-ColetteNeural", "nl-NL-MaartenNeural"],
    "Polish 🇵🇱": ["pl-PL-ZofiaNeural", "pl-PL-MarekNeural"],
    "Swedish 🇸🇪": ["sv-SE-SofieNeural", "sv-SE-MattiasNeural"],
    "Filipino 🇵🇭": ["fil-PH-BlessicaNeural", "fil-PH-AngeloNeural"],
    "Greek 🇬🇷": ["el-GR-AthinaNeural", "el-GR-NestorasNeural"],
    "Hebrew 🇮🇱": ["he-IL-AvriNeural", "he-IL-HilaNeural"],
    "Hungarian 🇭🇺": ["hu-HU-NoemiNeural", "hu-HU-AndrasNeural"],
    "Czech 🇨🇿": ["cs-CZ-VlastaNeural", "cs-CZ-AntoninNeural"],
    "Danish 🇩🇰": ["da-DK-ChristelNeural", "da-DK-JeppeNeural"],
    "Finnish 🇫🇮": ["fi-FI-SelmaNeural", "fi-FI-HarriNeural"],
    "Norwegian 🇳🇴": ["nb-NO-PernilleNeural", "nb-NO-FinnNeural"],
    "Romanian 🇷🇴": ["ro-RO-AlinaNeural", "ro-RO-EmilNeural"],
    "Slovak 🇸🇰": ["sk-SK-LukasNeural", "sk-SK-ViktoriaNeural"],
    "Ukrainian 🇺🇦": ["uk-UA-PolinaNeural", "uk-UA-OstapNeural"],
    "Malay 🇲🇾": ["ms-MY-YasminNeural", "ms-MY-OsmanNeural"],
    "Bengali 🇧🇩": ["bn-BD-NabanitaNeural", "bn-BD-BasharNeural"],
    "Tamil 🇮🇳": ["ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"],
    "Telugu 🇮🇳": ["te-IN-ShrutiNeural", "te-IN-RagavNeural"],
    "Kannada 🇮🇳": ["kn-IN-SapnaNeural", "kn-IN-GaneshNeural"],
    "Malayalam 🇮🇳": ["ml-IN-SobhanaNeural", "ml-IN-MidhunNeural"],
    "Gujarati 🇮🇳": ["gu-IN-DhwaniNeural", "gu-IN-AvinashNeural"],
    "Marathi 🇮🇳": ["mr-IN-AarohiNeural", "mr-IN-ManoharNeural"],
    "Urdu 🇵🇰": ["ur-PK-AsmaNeural", "ur-PK-FaizanNeural"],
    "Nepali 🇳🇵": ["ne-NP-SaritaNeural", "ne-NP-AbhisekhNeural"],
    "Sinhala 🇱🇰": ["si-LK-SameeraNeural", "si-LK-ThiliniNeural"],
    "Khmer 🇰🇭": ["km-KH-SreymomNeural", "km-KH-PannNeural"],
    "Lao 🇱🇦": ["lo-LA-ChanthavongNeural", "lo-LA-KeomanyNeural"],
    "Myanmar 🇲🇲": ["my-MM-NilarNeural", "my-MM-ThihaNeural"],
    "Georgian 🇬🇪": ["ka-GE-EkaNeural", "ka-GE-GiorgiNeural"],
    "Armenian 🇦🇲": ["hy-AM-AnahitNeural", "hy-AM-AraratNeural"],
    "Azerbaijani 🇦🇿": ["az-AZ-BabekNeural", "az-AZ-BanuNeural"],
    "Kazakh 🇰🇿": ["kk-KZ-AigulNeural", "kk-KZ-NurzhanNeural"],
    "Uzbek 🇺🇿": ["uz-UZ-MadinaNeural", "uz-UZ-SuhrobNeural"],
    "Serbian 🇷🇸": ["sr-RS-NikolaNeural", "sr-RS-SophieNeural"],
    "Croatian 🇭🇷": ["hr-HR-GabrijelaNeural", "hr-HR-SreckoNeural"],
    "Slovenian 🇸🇮": ["sl-SI-PetraNeural", "sl-SI-RokNeural"],
    "Latvian 🇱🇻": ["lv-LV-EveritaNeural", "lv-LV-AnsisNeural"],
    "Lithuanian 🇱🇹": ["lt-LT-OnaNeural", "lt-LT-LeonasNeural"],
    "Estonian 🇪🇪": ["et-EE-LiisNeural", "et-EE-ErkiNeural"],
    "Amharic 🇪🇹": ["am-ET-MekdesNeural", "am-ET-AbebeNeural"],
    "Swahili 🇰🇪": ["sw-KE-ZuriNeural", "sw-KE-RafikiNeural"],
    "Zulu 🇿🇦": ["zu-ZA-ThandoNeural", "zu-ZA-ThembaNeural"],
    "Xhosa 🇿🇦": ["xh-ZA-NomusaNeural", "xh-ZA-DumisaNeural"],
    "Afrikaans 🇿🇦": ["af-ZA-AdriNeural", "af-ZA-WillemNeural"],
}

def get_user_voice(uid: int) -> str:
    return tts_users.get(str(uid), "en-US-AriaNeural")

# =========================
# === LANGUAGE UTILITIES ==
# =========================

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

def get_lang_code(lang_name: str) -> str | None:
    for lang in LANGUAGES:
        if lang['name'].lower() == lang_name.lower():
            return lang['code']
    return None

def generate_language_keyboard(callback_prefix: str, message_id: int = None) -> types.InlineKeyboardMarkup:
    """
    Generate an InlineKeyboardMarkup with all available LANGUAGES.
    callback_prefix: either "set_lang", "set_media_lang", "translate_to", or "summarize_in".
    If message_id provided, append '|<message_id>' to callback_data.
    """
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang in LANGUAGES:
        cb_data = f"{callback_prefix}|{lang['name']}"
        if message_id is not None:
            cb_data += f"|{message_id}"
        buttons.append(types.InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb_data))
    markup.add(*buttons)
    return markup

# ==============================
# === UPTIME & ADMIN PANEL (1) ==
# ==============================

admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

def update_uptime_message(chat_id: int, message_id: int):
    """
    Live-update the admin uptime message every second.
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
        except Exception as e:
            if hasattr(e, 'result') and "message is not modified" in str(e):
                # ignore if Telegram complains about no change
                pass
            else:
                logger.error(f"Error updating uptime message: {e}")
            break

# ===========================
# === ANTI-SPAM (Bot #3) ====
# ===========================

@bot.message_handler(
    func=lambda m: m.chat.type in ["group", "supergroup"] and m.content_type == 'text'
)
def anti_spam_filter(message):
    """
    Delete overly long messages or those containing URLs/mentions in groups, unless sender is admin.
    """
    try:
        bot_member = bot.get_chat_member(message.chat.id, bot.get_me().id)
        if bot_member.status not in ['administrator', 'creator']:
            return  # bot can't delete if not admin

        user_member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if user_member.status in ['administrator', 'creator']:
            return  # allow admins to post freely

        text = message.text or ""
        if (
            len(text) > 120
            or re.search(r"https?://", text)
            or "t.me/" in text
            or re.search(r"@\w+", text)
        ):
            bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logger.warning(f"Anti-spam check failed: {e}")

# ================================
# === BOT COMMAND & DESCRIPTION ==
# ================================

def set_bot_commands_and_description():
    """
    Register common commands and set bot description.
    Combines Bot #1 and Bot #3 commands.
    """
    commands = [
        types.BotCommand("start", "👋 Get a welcome message"),
        types.BotCommand("help", "❓ How to use this bot"),
        types.BotCommand("status", "📊 View bot statistics (admin only)"),
        types.BotCommand("language", "🌐 Change preferred language for translate/summarize"),
        types.BotCommand("media_language", "📝 Set language for media transcription"),
        types.BotCommand("privacy", "👮 Privacy Notice"),
        types.BotCommand("change_voice", "🎙️ Change TTS language/voice"),
    ]
    bot.set_my_commands(commands)
    bot.set_my_description(
        "Multi‐Feature Bot: Anti‐Spam, Media Transcriber, TTS, Translations & Summaries."
    )

# ================================
# === USER ACTIVITY TRACKING ====
# ================================

def update_user_activity(user_id: int):
    """
    Record the last‐seen timestamp for a user.
    """
    user_data[str(user_id)] = datetime.utcnow().isoformat()
    save_user_data()

def is_active_within(ts_iso: str, days: int) -> bool:
    """
    Check if timestamp (ISO) is within the last <days> days.
    """
    try:
        last = datetime.fromisoformat(ts_iso)
        return (datetime.utcnow() - last).days < days
    except:
        return False

def get_user_counts() -> tuple[int, int, int]:
    """
    Return (total_users, active_within_30d, active_within_7d).
    """
    total = len(user_data)
    monthly = sum(is_active_within(ts, 30) for ts in user_data.values())
    weekly = sum(is_active_within(ts, 7) for ts in user_data.values())
    return total, monthly, weekly

# ======================================
# === GEMINI INTERFACE (Bot #1) =========
# ======================================

def ask_gemini(user_id: int, user_message: str) -> str:
    """
    Send conversation history + new prompt to Gemini for translation/summarization.
    """
    user_memory.setdefault(str(user_id), []).append({"role": "user", "text": user_message})
    history = user_memory[str(user_id)][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    resp = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": parts}]})
    result = resp.json()
    if "candidates" in result:
        reply = result['candidates'][0]['content']['parts'][0]['text']
        user_memory[str(user_id)].append({"role": "model", "text": reply})
        return reply
    return "Error: " + json.dumps(result)

# =======================================
# === MEDIA‐TO‐TEXT PROCESSING (Bot #1) ===
# =======================================

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB

def keep_typing(chat_id: int, stop_event: threading.Event):
    """
    Continuously send 'typing' action every 4 seconds until stop_event is set.
    """
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logger.error(f"Error sending typing action: {e}")
            break

def transcribe_audio_from_bytes(audio_bytes: bytes, lang_code: str) -> str | None:
    """
    Chunk the in‐memory WAV bytes into 10s segments (500ms overlap),
    send to Google Speech Recognition, return full transcription.
    """
    r = sr.Recognizer()
    full_transcription = []
    chunk_length_ms = 10 * 1000  # 10 seconds
    overlap_ms = 500

    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")
        total_length_ms = len(audio)
        start_ms = 0

        logger.info(f"Starting chunking for in-memory audio, total length {total_length_ms/1000:.2f}s.")
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
                    logger.info(f"Transcribed {start_ms/1000:.1f}s–{end_ms/1000:.1f}s: {text[:50]}...")
                except sr.UnknownValueError:
                    logger.warning(f"Could not understand chunk {start_ms/1000:.1f}s–{end_ms/1000:.1f}s.")
                except sr.RequestError as e:
                    logger.error(f"Google SR request error: {e} at chunk {start_ms/1000:.1f}s.")
                except Exception as e:
                    logger.error(f"Error processing chunk: {e}")
                finally:
                    chunk_io.close()

            start_ms += chunk_length_ms - overlap_ms

        return " ".join(full_transcription) if full_transcription else None

    except Exception as e:
        logger.error(f"Overall transcription error: {e}")
        return None

def process_media_file(message: types.Message, stop_typing: threading.Event):
    """
    Download voice/audio/video, convert to WAV (16kHz mono), run transcription,
    send back text (or text file + inline buttons).
    """
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time

    uid = str(message.from_user.id)
    file_obj = message.voice or message.audio or message.video or message.video_note
    local_temp_file = None
    wav_audio_data = None

    try:
        info = bot.get_file(file_obj.file_id)
        # Choose extension: .ogg for voice/video_note, else preserve
        if message.voice or message.video_note:
            file_extension = ".ogg"
        else:
            _, ext = os.path.splitext(info.file_path)
            file_extension = ext if ext else ".mp3"

        # Download to temporary file
        local_temp_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{file_extension}")
        data = bot.download_file(info.file_path)
        with open(local_temp_file, 'wb') as f:
            f.write(data)

        processing_start = datetime.now()

        # Convert to WAV via ffmpeg subprocess
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
                raise Exception("FFmpeg produced no output.")
            with open(temp_wav_file, 'rb') as f_wav:
                wav_audio_data = f_wav.read()
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg conversion failed: {e.stderr.decode()}")
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])  # remove reaction
            bot.send_message(
                message.chat.id,
                "😓 Sorry, there was an issue converting your audio. Unsupported format or corrupted file."
            )
            return
        except Exception as e:
            logger.error(f"FFmpeg error: {e}")
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            bot.send_message(
                message.chat.id,
                "😓 Unable to convert your file to the correct format. Please send a standard audio/video file."
            )
            return
        finally:
            if os.path.exists(temp_wav_file):
                os.remove(temp_wav_file)

        # Determine user’s chosen media language
        media_lang = user_media_language_settings.get(uid)
        media_lang_code = get_lang_code(media_lang) if media_lang else None
        if not media_lang_code:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            bot.send_message(
                message.chat.id,
                f"❌ The language “{media_lang}” is invalid for transcription. Use /media_language to re-select."
            )
            return

        # Perform transcription
        transcription = transcribe_audio_from_bytes(wav_audio_data, media_lang_code) or ""
        user_transcriptions.setdefault(uid, {})[message.message_id] = transcription

        # Update stats
        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        elif message.video or message.video_note:
            total_videos += 1

        elapsed_secs = (datetime.now() - processing_start).total_seconds()
        total_processing_time += elapsed_secs

        # Prepare inline buttons
        buttons = types.InlineKeyboardMarkup()
        buttons.add(
            types.InlineKeyboardButton("Translate", callback_data=f"btn_translate|{message.message_id}"),
            types.InlineKeyboardButton("Summarize", callback_data=f"btn_summarize|{message.message_id}")
        )

        # Remove “👀” reaction
        bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])

        # Send transcription: if >4000 chars, send as .txt
        if len(transcription) > 4000:
            fn = f"transcription_{message.message_id}.txt"
            with open(fn, 'w', encoding='utf-8') as f_out:
                f_out.write(transcription)
            bot.send_chat_action(message.chat.id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(
                    message.chat.id,
                    doc,
                    reply_to_message_id=message.message_id,
                    reply_markup=buttons,
                    caption="Here’s your transcription. Use the buttons below for more options."
                )
            os.remove(fn)
        else:
            bot.reply_to(message, transcription, reply_markup=buttons)

    except Exception as e:
        logger.error(f"Error processing file for user {uid}: {e}")
        bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        bot.send_message(
            message.chat.id,
            "😓 Sorry, an error occurred during transcription. Try again with a clearer/longer clip."
        )
    finally:
        stop_typing.set()
        processing_message_ids.pop(message.chat.id, None)
        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)

# ========================================
# === HANDLERS: START, HELP, PRIVACY, STATUS ==
# ========================================

@bot.message_handler(commands=['start'])
def start_handler(message: types.Message):
    """
    /start: greet user or show admin panel with live uptime.
    """
    uid = message.from_user.id
    update_user_activity(uid)

    if uid == ADMIN_ID:
        # Admin: show live‐updating uptime panel
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Total Users", "/status")
        sent_message = bot.send_message(
            message.chat.id,
            "Admin Panel: Live Uptime →",
            reply_markup=keyboard
        )
        with admin_uptime_lock:
            if (
                ADMIN_ID in admin_uptime_message
                and admin_uptime_message[ADMIN_ID].get('thread')
                and admin_uptime_message[ADMIN_ID]['thread'].is_alive()
            ):
                # thread already running
                pass
            else:
                admin_uptime_message[ADMIN_ID] = {
                    'message_id': sent_message.message_id,
                    'chat_id': message.chat.id
                }
                uptime_thread = threading.Thread(
                    target=update_uptime_message,
                    args=(message.chat.id, sent_message.message_id),
                    daemon=True
                )
                uptime_thread.start()
                admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread
    else:
        display_name = message.from_user.first_name or (f"@{message.from_user.username}" if message.from_user.username else "user")
        bot.send_message(
            message.chat.id,
            f"👋🏻 Salom {display_name}!\n"
            "I'm your Multi‐Feature Bot:\n"
            "• Anti‐Spam (in groups)\n"
            "• Media Transcriber (send voice/audio/video)\n"
            "• Text‐to‐Speech (send text)\n"
            "• Translate/Summarize (use inline buttons after transcription)\n"
            "Send /help for more info."
        )

@bot.message_handler(commands=['help'])
def help_handler(message: types.Message):
    """
    /help: detailed instructions for using all features.
    """
    uid = message.from_user.id
    update_user_activity(uid)

    help_text = (
        "ℹ️ **How to use this Multi‐Feature Bot:**\n\n"
        "1. **Anti‐Spam (in Groups):**\n"
        "   • When added as admin in a group, the bot auto‐deletes:\n"
        "     – Texts >120 chars\n"
        "     – Messages with URLs (`http://`, `t.me/`)\n"
        "     – Messages with @mentions\n"
        "   • Admins & creators are exempt.\n\n"
        "2. **Media Transcription:**\n"
        "   • Before sending any voice/audio/video, set the language via `/media_language`.\n"
        "   • Supported formats: voice notes, .ogg, .mp3, .wav, .mp4, etc. Max 20MB.\n"
        "   • Bot shows 👀 reaction while processing, then replies with transcription.\n"
        "   • If transcription >4k chars, you’ll get a .txt file. Inline buttons appear below for **Translate** or **Summarize**.\n\n"
        "3. **Translate / Summarize:**\n"
        "   • After transcription, tap **Translate** or **Summarize**.\n"
        "   • If you haven’t set a preferred language, you’ll be prompted.\n"
        "   • Otherwise, translation/summarization happens in your saved language.\n"
        "   • Use `/language` to change your preferred translation/summarization language anytime.\n\n"
        "4. **Text‐to‐Speech (TTS):**\n"
        "   • Use `/change_voice` to pick a language & voice.\n"
        "   • After choosing a voice, send any text (not starting with `/`) → bot replies with audio file (.mp3).\n\n"
        "5. **Other Commands:**\n"
        "   • `/start`: Welcome message & admin panel.\n"
        "   • `/status`: (Admin only) Bot statistics.\n"
        "   • `/privacy`: Read privacy notice.\n"
        "   • `/media_language`: Set transcription language for your media.\n"
        "   • `/language`: Set translation/summarization language.\n"
        "   • `/change_voice`: Set your TTS voice.\n\n"
        "Enjoy! 🚀"
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_handler(message: types.Message):
    """
    /privacy: display bot’s privacy notice.
    """
    uid = message.from_user.id
    update_user_activity(uid)

    privacy_text = (
        "**Privacy Notice**\n\n"
        "1. **Media Files (Voice, Audio, Video):**\n"
        "   • Temporarily downloaded for transcription.\n"
        "   • **Deleted immediately** after transcription.\n\n"
        "2. **Transcriptions:**\n"
        "   • Held in memory for follow‐up actions (translate/summarize).\n"
        "   • Cleared after 7 days or when superseded.\n\n"
        "3. **Text Messages (for TTS):**\n"
        "   • Only used to generate your audio reply.\n"
        "   • Not stored beyond immediate processing.\n\n"
        "4. **User IDs & Preferences:**\n"
        "   • We store your Telegram User ID to keep language/voice settings.\n"
        "   • This does not link to personal info outside of Telegram.\n"
        "   • Preferences (media_lang, translation_lang, TTS voice) are saved.\n\n"
        "5. **No Third‐Party Sharing:**\n"
        "   • We do not share your data with any third parties.\n"
        "   • Transcription & translation use Google & Gemini APIs under their privacy policies.\n\n"
        "6. **Data Retention:**\n"
        "   • Media files: deleted immediately post‐transcription.\n"
        "   • Transcriptions: kept temporarily; cleared after 7 days.\n"
        "   • User IDs & preferences: kept until you delete them or stop using the bot.\n\n"
        "By using this bot, you agree to these practices."
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message: types.Message):
    """
    /status (admin only): show bot statistics.
    """
    if message.from_user.id != ADMIN_ID:
        return

    update_user_activity(message.from_user.id)
    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, rem = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    today = datetime.utcnow().date()
    active_today = sum(
        1 for ts in user_data.values()
        if datetime.fromisoformat(ts).date() == today
    )

    total_proc_seconds = int(total_processing_time)
    proc_h = total_proc_seconds // 3600
    proc_m = (total_proc_seconds % 3600) // 60
    proc_s = total_proc_seconds % 60

    total, monthly, weekly = get_user_counts()

    text = (
        "📊 **Bot Statistics**\n\n"
        "🟢 **Status:** Online\n"
        f"⏳ **Uptime:** {days}d {hours}h {minutes}m {seconds}s\n\n"
        "👥 **User Stats**\n"
        f"• Total Registered Users: {total}\n"
        f"• Active (last 30d): {monthly}\n"
        f"• Active (last 7d): {weekly}\n"
        f"• Users Active Today: {active_today}\n\n"
        "⚙️ **Processing Stats**\n"
        f"• Total Files Processed: {total_files_processed}\n"
        f"• Audio Files: {total_audio_files}\n"
        f"• Voice Clips: {total_voice_clips}\n"
        f"• Videos: {total_videos}\n"
        f"• Total Processing Time: {proc_h}h {proc_m}m {proc_s}s\n\n"
        "Thanks for using the service! 🙌"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# =========================================
# === BROADCAST (Bot #1 & Bot #3 combined) ==
# =========================================

# We will use the Bot #1 approach (admin_state). Bot #3 used next_step; either works.
admin_state = {}

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users_handler(message: types.Message):
    bot.send_message(message.chat.id, f"Total registered users: {len(user_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast_request(message: types.Message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "📢 Please send the broadcast content now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def process_broadcast(message: types.Message):
    admin_state[message.from_user.id] = None
    success = 0
    fail = 0
    for uid_str in user_data:
        try:
            bot.copy_message(uid_str, message.chat.id, message.message_id)
            success += 1
        except Exception as e:
            logger.error(f"Failed to broadcast to {uid_str}: {e}")
            fail += 1
    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# =================================
# === LANGUAGE SELECTION HANDLERS (Bot #1) ===
# =================================

@bot.message_handler(commands=['language'])
def select_language_command(message: types.Message):
    uid = str(message.from_user.id)
    update_user_activity(int(uid))
    markup = generate_language_keyboard("set_lang")
    bot.send_message(
        message.chat.id,
        "Please select your preferred language for future **translations and summaries**:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_lang|"))
def callback_set_language(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    update_user_activity(int(uid))
    _, lang = call.data.split("|", 1)
    user_language_settings[uid] = lang
    save_user_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Preferred translation/summarization language set to: **{lang}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Language set to {lang}")

@bot.message_handler(commands=['media_language'])
def select_media_language_command(message: types.Message):
    uid = str(message.from_user.id)
    update_user_activity(int(uid))
    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "Please choose the language of your media for transcription:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_media_lang|"))
def callback_set_media_language(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    update_user_activity(int(uid))
    _, lang = call.data.split("|", 1)
    user_media_language_settings[uid] = lang
    save_user_media_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Media transcription language set to: **{lang}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Media language set to {lang}")

# ============================================
# === TRANSLATE / SUMMARIZE CALLBACK HANDLERS ==
# ============================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    update_user_activity(int(uid))
    _, msg_id_str = call.data.split("|", 1)
    msg_id = int(msg_id_str)

    if uid not in user_transcriptions or msg_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found.")
        return

    preferred = user_language_settings.get(uid)
    if preferred:
        bot.answer_callback_query(call.id, "Translating with your preferred language…")
        threading.Thread(
            target=do_translate_with_saved_lang,
            args=(call.message, uid, preferred, msg_id),
            daemon=True
        ).start()
    else:
        markup = generate_language_keyboard("translate_to", msg_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select target language for translation:",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_summarize|"))
def button_summarize_handler(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    update_user_activity(int(uid))
    _, msg_id_str = call.data.split("|", 1)
    msg_id = int(msg_id_str)

    if uid not in user_transcriptions or msg_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found.")
        return

    preferred = user_language_settings.get(uid)
    if preferred:
        bot.answer_callback_query(call.id, "Summarizing with your preferred language…")
        threading.Thread(
            target=do_summarize_with_saved_lang,
            args=(call.message, uid, preferred, msg_id),
            daemon=True
        ).start()
    else:
        markup = generate_language_keyboard("summarize_in", msg_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select language for summary:",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("translate_to|"))
def callback_translate_to(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    update_user_activity(int(uid))
    parts = call.data.split("|")
    lang = parts[1]
    msg_id = int(parts[2]) if len(parts) > 2 else None

    user_language_settings[uid] = lang
    save_user_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Translating to **{lang}**…",
        parse_mode="Markdown"
    )

    if msg_id:
        threading.Thread(
            target=do_translate_with_saved_lang,
            args=(call.message, uid, lang, msg_id),
            daemon=True
        ).start()
    else:
        bot.send_message(call.message.chat.id, "❌ No transcription found to translate.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("summarize_in|"))
def callback_summarize_in(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    update_user_activity(int(uid))
    parts = call.data.split("|")
    lang = parts[1]
    msg_id = int(parts[2]) if len(parts) > 2 else None

    user_language_settings[uid] = lang
    save_user_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Summarizing in **{lang}**…",
        parse_mode="Markdown"
    )

    if msg_id:
        threading.Thread(
            target=do_summarize_with_saved_lang,
            args=(call.message, uid, lang, msg_id),
            daemon=True
        ).start()
    else:
        bot.send_message(call.message.chat.id, "❌ No transcription found to summarize.")
    bot.answer_callback_query(call.id)

def do_translate_with_saved_lang(message: types.Message, uid: str, lang: str, msg_id: int):
    """
    Takes a saved transcription, sends to Gemini to translate into `lang`, replies.
    """
    original = user_transcriptions.get(uid, {}).get(msg_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription found to translate.")
        return

    prompt = (
        f"Translate the following text into {lang}. "
        "Provide only the translated text, without notes:\n\n"
        f"{original}"
    )
    bot.send_chat_action(message.chat.id, 'typing')
    translated = ask_gemini(int(uid), prompt)

    if translated.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Translation error: {translated}")
        return

    if len(translated) > 4000:
        fn = f"translation_{msg_id}.txt"
        with open(fn, 'w', encoding='utf-8') as f_out:
            f_out.write(translated)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Translation to {lang}", reply_to_message_id=msg_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, translated, reply_to_message_id=msg_id)

def do_summarize_with_saved_lang(message: types.Message, uid: str, lang: str, msg_id: int):
    """
    Takes a saved transcription, sends to Gemini to summarize into `lang`, replies.
    """
    original = user_transcriptions.get(uid, {}).get(msg_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription found to summarize.")
        return

    prompt = (
        f"Summarize the following text in {lang}. "
        "Provide only the summarized text, without notes:\n\n"
        f"{original}"
    )
    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(int(uid), prompt)

    if summary.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Summarization error: {summary}")
        return

    if len(summary) > 4000:
        fn = f"summary_{msg_id}.txt"
        with open(fn, 'w', encoding='utf-8') as f_out:
            f_out.write(summary)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Summary in {lang}", reply_to_message_id=msg_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, summary, reply_to_message_id=msg_id)

# ======================================
# === MEDIA FILE HANDLER (Bot #1) ======
# ======================================

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_media(message: types.Message):
    """
    When a user sends voice/audio/video:
    • Check if they set /media_language
    • React with 👀
    • Start typing indicator thread
    • Spawn transcription thread
    """
    uid = str(message.from_user.id)
    update_user_activity(int(uid))

    if uid not in user_media_language_settings:
        bot.send_message(
            message.chat.id,
            "⚠️ Use /media_language to set the audio language before sending media."
        )
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        bot.send_message(message.chat.id, "😓 File too large. Max 20MB.")
        return

    # Add 👀 reaction (attempt; catch exceptions)
    try:
        bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[{'type': 'emoji', 'emoji': '👀'}]
        )
    except Exception as e:
        logger.error(f"Error setting reaction: {e}")

    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing), daemon=True)
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing

    # Start transcription in background
    threading.Thread(target=process_media_file, args=(message, stop_typing), daemon=True).start()

# ==================================
# === TTS HANDLERS (Bot #2) ========
# ==================================

def make_language_keyboard_tts() -> types.InlineKeyboardMarkup:
    """
    Returns an InlineKeyboardMarkup listing each language group for TTS.
    """
    kb = types.InlineKeyboardMarkup(row_width=1)
    for lang_name in VOICES_BY_LANGUAGE.keys():
        kb.add(types.InlineKeyboardButton(lang_name, callback_data=f"lang|{lang_name}"))
    return kb

def make_voice_keyboard_for_language(lang_name: str) -> types.InlineKeyboardMarkup:
    """
    Given a language group (e.g. "English 🇬🇧"), return InlineKeyboardMarkup of its voices.
    """
    kb = types.InlineKeyboardMarkup(row_width=2)
    voices = VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        kb.add(types.InlineKeyboardButton(voice, callback_data=f"voice|{voice}"))
    kb.add(types.InlineKeyboardButton("⬅️ Back to Languages", callback_data="back_to_languages"))
    return kb

async def a_main(voice: str, text: str, filename: str, rate: int = 0, pitch: int = 0, volume: float = 1.0):
    """
    Async helper using MSSpeech to generate TTS.
    """
    mss = MSSpeech()
    await mss.set_voice(voice)
    await mss.set_rate(rate)
    await mss.set_pitch(pitch)
    await mss.set_volume(volume)
    return await mss.synthesize(text, filename)

async def synth_and_send(chat_id: int, user_id: int, text: str):
    """
    Generate TTS for `text` using chosen voice, send audio file, then delete it.
    """
    voice = get_user_voice(user_id)
    filename = os.path.join(AUDIO_DIR, f"{user_id}.mp3")
    try:
        bot.send_chat_action(chat_id, "record_audio")
        await a_main(voice, text, filename)
        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            bot.send_message(chat_id, "❌ Failed to generate audio. Try again.")
            return
        with open(filename, "rb") as f_audio:
            bot.send_audio(chat_id, f_audio, caption=f"🎤 Voice: {voice}")
    except MSSpeechError as e:
        bot.send_message(chat_id, f"❌ TTS error: {e}")
    except Exception as e:
        logger.exception("TTS error")
        bot.send_message(chat_id, "❌ Unexpected TTS error. Please try again.")
    finally:
        if os.path.exists(filename):
            os.remove(filename)

@bot.message_handler(commands=['change_voice'])
def cmd_change_voice(message: types.Message):
    """
    /change_voice: show available language groups for TTS.
    """
    uid = message.from_user.id
    update_user_activity(uid)
    bot.send_message(message.chat.id, "🎙️ Choose a language for TTS:", reply_markup=make_language_keyboard_tts())

@bot.callback_query_handler(lambda c: c.data.startswith("lang|"))
def on_tts_language_select(call: types.CallbackQuery):
    """
    User selected a language group (e.g. "English 🇬🇧"). Show available voices.
    """
    _, lang_name = call.data.split("|", 1)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🎙️ Choose a voice for {lang_name}:",
        reply_markup=make_voice_keyboard_for_language(lang_name)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(lambda c: c.data.startswith("voice|"))
def on_tts_voice_change(call: types.CallbackQuery):
    """
    User selected a specific TTS voice. Save and confirm.
    """
    _, voice = call.data.split("|", 1)
    tts_users[str(call.from_user.id)] = voice
    save_tts_users()
    bot.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🔊 You are now using: *{voice}*. Send any text to get audio.",
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data == "back_to_languages")
def on_back_to_languages(call: types.CallbackQuery):
    """
    “⬅️ Back to Languages” button for TTS.
    """
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🎙️ Choose a language for TTS:",
        reply_markup=make_language_keyboard_tts()
    )
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
def handle_text_for_tts(m: types.Message):
    """
    Any plain text (not commands) → treat as TTS request.
    """
    uid = m.from_user.id
    update_user_activity(uid)
    # Run async TTS and send
    asyncio.run(synth_and_send(m.chat.id, uid, m.text))

# =======================================
# === FALLBACK HANDLER FOR OTHER TYPES ===
# =======================================

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document', 'text'])
def fallback_handler(message: types.Message):
    """
    Catch‐all: if text starts with / and not a known command, do nothing;
    otherwise, prompt user to send valid input.
    """
    update_user_activity(message.from_user.id)
    if message.text and message.text.startswith('/'):
        # unknown command: ignore quietly
        return
    # If non-media and not TTS (handled above), remind usage
    bot.send_message(
        message.chat.id,
        "❓ I did not understand. Send voice/audio/video for transcription, or plain text for TTS."
    )

# ============================
# === WEBHOOK ENDPOINTS ======
# ============================

@app.route('/', methods=['GET', 'HEAD', 'POST'])
def webhook():
    """
    Telegram webhook receiver. Process updates when POST with JSON.
    """
    if request.method in ('GET', 'HEAD'):
        return "OK", 200
    if request.method == 'POST' and request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    return abort(403)

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook_route():
    """
    Call this endpoint to register the bot’s webhook with Telegram.
    """
    bot.set_webhook(url=WEBHOOK_URL)
    return f"Webhook set to {WEBHOOK_URL}", 200

@app.route('/delete_webhook', methods=['GET', 'POST'])
def delete_webhook_route():
    """
    Call this to delete the webhook.
    """
    bot.delete_webhook()
    return 'Webhook deleted.', 200

# ======================
# === MEMORY CLEANUP ===
# ======================

def cleanup_old_data():
    """
    Purge user_transcriptions and user_memory older than 7 days.
    """
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    # Transcriptions
    for uid, trans_map in list(user_transcriptions.items()):
        if uid in user_data:
            last_activity = datetime.fromisoformat(user_data[uid])
            if last_activity < seven_days_ago:
                del user_transcriptions[uid]
                logger.info(f"Cleaned old transcriptions for user {uid}")
        else:
            del user_transcriptions[uid]

    # Conversation memory
    for uid in list(user_memory.keys()):
        if uid in user_data:
            last_activity = datetime.fromisoformat(user_data[uid])
            if last_activity < seven_days_ago:
                del user_memory[uid]
                logger.info(f"Cleaned old chat memory for user {uid}")
        else:
            del user_memory[uid]

    threading.Timer(24 * 3600, cleanup_old_data).start()

# ========================
# === BOT STARTUP MAIN ==
# ========================

if __name__ == "__main__":
    # Register commands & description
    set_bot_commands_and_description()

    # Start cleanup timer
    cleanup_old_data()

    # Run Flask app (webhook mode)
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
