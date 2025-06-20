import os
import uuid
import logging
import requests
import telebot
import json
from flask import Flask, request, abort
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import imageio_ffmpeg as ffmpeg
from pydub import AudioSegment
import threading
import time
import subprocess
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure

# --- REPLACE: Import SpeechRecognition instead of FasterWhisper ---
import speech_recognition as sr

# --- KEEP: MSSpeech for Text-to-Speech ---
from msspeech import MSSpeech, MSSpeechError

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BOT CONFIGURATION ---
TOKEN = "7790991731:AAGpbz6nqE5f0Dvs6ZSTdRoR1LMrrf4rMqU"  # Replace with your actual bot token
ADMIN_ID = 5978150981  # Replace with your actual Admin ID
WEBHOOK_URL = "https://speech-recognition-9j3f.onrender.com"  # Replace with your actual webhook URL
REQUIRED_CHANNEL = "@transcriberbo"  # Replace with your actual channel username

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Download directory (still used for intermediate WAV)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- MongoDB Configuration ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"
USERS_COLLECTION = "users"
LANGUAGE_SETTINGS_COLLECTION = "user_language_settings"
MEDIA_LANGUAGE_SETTINGS_COLLECTION = "user_media_language_settings"
TTS_USERS_COLLECTION = "tts_users"
PROCESSING_STATS_COLLECTION = "file_processing_stats"

mongo_client: MongoClient = None
db = None
users_collection = None
language_settings_collection = None
media_language_settings_collection = None
tts_users_collection = None
processing_stats_collection = None

def connect_to_mongodb():
    """
    Connect to MongoDB once at startup, set up collections and indexes.
    """
    global mongo_client, db, users_collection, language_settings_collection, media_language_settings_collection, tts_users_collection, processing_stats_collection
    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster')
        db = mongo_client[DB_NAME]
        users_collection = db[USERS_COLLECTION]
        language_settings_collection = db[LANGUAGE_SETTINGS_COLLECTION]
        media_language_settings_collection = db[MEDIA_LANGUAGE_SETTINGS_COLLECTION]
        tts_users_collection = db[TTS_USERS_COLLECTION]
        processing_stats_collection = db[PROCESSING_STATS_COLLECTION]

        # NOTE:  _id is automatically indexed by MongoDB.  Do NOT create a unique index on _id again.
        # Create other indexes for faster queries:
        users_collection.create_index([("last_active", ASCENDING)])
        language_settings_collection.create_index([("_id", ASCENDING)])            # Index on user_id field
        media_language_settings_collection.create_index([("_id", ASCENDING)])      # Index on user_id field
        tts_users_collection.create_index([("_id", ASCENDING)])                    # Index on user_id field
        processing_stats_collection.create_index([("user_id", ASCENDING)])
        processing_stats_collection.create_index([("type", ASCENDING)])
        processing_stats_collection.create_index([("timestamp", ASCENDING)])

        logging.info("Successfully connected to MongoDB and created indexes!")
    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        exit(1)

# --- In-memory cache for language settings to reduce DB reads ---
# {user_id: language_name}
_user_language_cache = {}
_media_language_cache = {}
_tts_voice_cache = {}

# --- User state for Text-to-Speech input mode ---
# {user_id: "voice_name" or None}
user_tts_mode = {}

# TTS voices by language
TTS_VOICES_BY_LANGUAGE = {
    "English 🇬🇧": ["en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-DavisNeural",
                    "en-GB-LibbyNeural", "en-GB-RyanNeural", "en-GB-MiaNeural", "en-GB-ThomasNeural",
                    "en-AU-NatashaNeural", "en-AU-WilliamNeural", "en-CA-LindaNeural", "en-CA-ClaraNeural",
                    "en-IE-EmilyNeural", "en-IE-ConnorNeural", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural"],
    "Arabic 🇸🇦": ["ar-SA-HamedNeural", "ar-SA-ZariyahNeural", "ar-EG-SalmaNeural", "ar-EG-ShakirNeural",
                 "ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural", "ar-BH-LailaNeural", "ar-BH-AliNeural",
                 "ar-IQ-RanaNeural", "ar-IQ-BasselNeural", "ar-KW-FahedNeural", "ar-KW-NouraNeural",
                 "ar-OM-AishaNeural", "ar-OM-SamirNeural", "ar-QA-MoazNeural", "ar-QA-ZainabNeural",
                 "ar-SY-AmiraNeural", "ar-SY-LaithNeural", "ar-AE-FatimaNeural", "ar-AE-HamdanNeural",
                 "ar-YE-HamdanNeural", "ar-YE-SarimNeural"],
    "Spanish 🇪🇸": ["es-ES-AlvaroNeural", "es-ES-ElviraNeural", "es-MX-DaliaNeural", "es-MX-JorgeNeural",
                   "es-AR-ElenaNeural", "es-AR-TomasNeural", "es-CO-SalomeNeural", "es-CO-GonzaloNeural",
                   "es-US-PalomaNeural", "es-US-JuanNeural", "es-CL-LorenzoNeural", "es-CL-CatalinaNeural",
                   "es-PE-CamilaNeural", "es-PE-DiegoNeural", "es-VE-PaolaNeural", "es-VE-SebastianNeural",
                   "es-CR-MariaNeural", "es-CR-JuanNeural", "es-DO-RamonaNeural", "es-DO-AntonioNeural"],
    "Hindi 🇮🇳": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"],
    "French 🇫🇷": ["fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural", "fr-CA-JeanNeural",
                  "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-CH-CharlineNeural", "fr-BE-CamilleNeural"],
    "German 🇩🇪": ["de-DE-KatjaNeural", "de-DE-ConradNeural", "de-CH-LeniNeural", "de-CH-JanNeural",
                  "de-AT-IngridNeural", "de-AT-JonasNeural"],
    "Chinese 🇨🇳": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural", "zh-CN-YunjianNeural", "zh-CN-XiaoyunNeural",
                  "zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural",
                  "zh-SG-XiaoMinNeural", "zh-SG-YunJianNeural"],
    "Japanese 🇯🇵": ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "ja-JP-MayuNeural", "ja-JP-DaichiNeural"],
    "Portuguese 🇧🇷": ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-PT-RaquelNeural", "pt-PT-DuarteNeural"],
    "Russian 🇷🇺": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-LarisaNeural", "ru-RU-MaximNeural"],
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
    "Somali 🇸🇴": ["so-SO-UbaxNeural", "so-SO-MuuseNeural"]
}

# In-memory chat history and transcription store
user_memory = {}
user_transcriptions = {}
processing_message_ids = {}  # To keep track of messages with typing status

# Statistics counters (in-memory for quick access)
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"  # Replace with your actual Gemini API Key

def ask_gemini(user_id, user_message):
    """
    Query Gemini API with conversation history in-memory.
    """
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

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB
admin_state = {}

def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "👋 Get a welcome message and info"),
        telebot.types.BotCommand("status", "📊 View Bot statistics"),
        telebot.types.BotCommand("language", "🌐 Change preferred language for translate/summarize"),
        telebot.types.BotCommand("media_language", "📝 Set language for media transcription"),
        telebot.types.BotCommand("text_to_speech", "🗣️ Convert text to speech"),
    ]
    bot.set_my_commands(commands)

def update_user_activity_db(user_id):
    """
    Update last_active timestamp in MongoDB (upsert).
    """
    try:
        users_collection.update_one(
            {"_id": str(user_id)},
            {"$set": {"last_active": datetime.now().isoformat()}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error updating user activity for {user_id}: {e}")

def get_user_data(user_id):
    """
    Fetch user document from MongoDB.
    """
    try:
        return users_collection.find_one({"_id": str(user_id)})
    except Exception as e:
        logging.error(f"Error fetching user data for {user_id}: {e}")
        return None

def increment_transcription_count_db(user_id):
    """
    Increment transcription_count and update last_active.
    """
    try:
        users_collection.update_one(
            {"_id": str(user_id)},
            {"$inc": {"transcription_count": 1}, "$set": {"last_active": datetime.now().isoformat()}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error incrementing transcription count for {user_id}: {e}")

def get_user_language_setting_db(user_id):
    """
    Get preferred language for translations/summaries from cache or DB.
    """
    if user_id in _user_language_cache:
        return _user_language_cache[user_id]
    try:
        doc = language_settings_collection.find_one({"_id": str(user_id)})
        lang = doc.get("language") if doc else None
        if lang:
            _user_language_cache[user_id] = lang
        return lang
    except Exception as e:
        logging.error(f"Error fetching language setting for {user_id}: {e}")
        return None

def set_user_language_setting_db(user_id, lang):
    """
    Save preferred language in DB and update cache.
    """
    try:
        language_settings_collection.update_one(
            {"_id": str(user_id)},
            {"$set": {"language": lang}},
            upsert=True
        )
        _user_language_cache[user_id] = lang
    except Exception as e:
        logging.error(f"Error setting preferred language for {user_id}: {e}")

def get_user_media_language_setting_db(user_id):
    """
    Get media transcription language from cache or DB.
    """
    if user_id in _media_language_cache:
        return _media_language_cache[user_id]
    try:
        doc = media_language_settings_collection.find_one({"_id": str(user_id)})
        media_lang = doc.get("media_language") if doc else None
        if media_lang:
            _media_language_cache[user_id] = media_lang
        return media_lang
    except Exception as e:
        logging.error(f"Error fetching media language for {user_id}: {e}")
        return None

def set_user_media_language_setting_db(user_id, lang):
    """
    Save media transcription language in DB and update cache.
    """
    try:
        media_language_settings_collection.update_one(
            {"_id": str(user_id)},
            {"$set": {"media_language": lang}},
            upsert=True
        )
        _media_language_cache[user_id] = lang
    except Exception as e:
        logging.error(f"Error setting media language for {user_id}: {e}")

def get_tts_user_voice_db(user_id):
    """
    Get TTS voice from cache or DB, default to en-US-AriaNeural.
    """
    if user_id in _tts_voice_cache:
        return _tts_voice_cache[user_id]
    try:
        doc = tts_users_collection.find_one({"_id": str(user_id)})
        voice = doc.get("voice", "en-US-AriaNeural") if doc else "en-US-AriaNeural"
        _tts_voice_cache[user_id] = voice
        return voice
    except Exception as e:
        logging.error(f"Error fetching TTS voice for {user_id}: {e}")
        return "en-US-AriaNeural"

def set_tts_user_voice_db(user_id, voice):
    """
    Save TTS voice in DB and update cache.
    """
    try:
        tts_users_collection.update_one(
            {"_id": str(user_id)},
            {"$set": {"voice": voice}},
            upsert=True
        )
        _tts_voice_cache[user_id] = voice
    except Exception as e:
        logging.error(f"Error setting TTS voice for {user_id}: {e}")

# Function to keep sending 'typing' action
def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

# Function to keep sending 'record_audio' action for TTS
def keep_recording(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
            break

# Function to update uptime message
def update_uptime_message(chat_id, message_id):
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
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break

def check_subscription(user_id):
    """
    Verify that user is member of REQUIRED_CHANNEL if set.
    """
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id} in {REQUIRED_CHANNEL}: {e}")
        return False

def send_subscription_message(chat_id):
    """
    Prompt user to join required channel.
    """
    if not REQUIRED_CHANNEL:
        return
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Click here to join the channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")
    )
    bot.send_message(
        chat_id,
        "😓 Sorry …\n🔰 To continue using this bot you must first join the channel @transcriberbo ‼️ After joining, come back to continue using the bot.",
        reply_markup=markup,
        disable_web_page_preview=True
    )

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    # Insert new user if not exists, initialize transcription_count
    existing_user = get_user_data(user_id)
    if not existing_user:
        try:
            users_collection.insert_one({
                "_id": user_id,
                "last_active": datetime.now().isoformat(),
                "transcription_count": 0
            })
        except Exception as e:
            logging.error(f"Error inserting new user {user_id}: {e}")
    elif 'transcription_count' not in existing_user:
        try:
            users_collection.update_one(
                {"_id": user_id},
                {"$set": {"transcription_count": 0}}
            )
        except Exception as e:
            logging.error(f"Error initializing transcription_count for {user_id}: {e}")

    user_tts_mode[user_id] = None

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "Admin Panel and Uptime (updating live)...", reply_markup=keyboard)
        with admin_uptime_lock:
            admin_uptime_message[ADMIN_ID] = {'message_id': sent_message.message_id, 'chat_id': message.chat.id}
            uptime_thread = threading.Thread(target=update_uptime_message, args=(message.chat.id, sent_message.message_id))
            uptime_thread.daemon = True
            uptime_thread.start()
            admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread
    else:
        markup = generate_language_keyboard("set_media_lang")
        bot.send_message(
            message.chat.id,
            "Please choose the language of the audio files using the buttons below.",
            reply_markup=markup
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(user_id)
    user_doc = get_user_data(user_id)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None

    help_text = (
        """ℹ️ How to use this bot:

This bot transcribes voice messages, audio files, and videos using advanced AI, and can also convert text to speech!

1.  **Send a File for Transcription:**
    * Send a voice message, audio file, video note, or a video file (e.g. .mp4) as a document/attachment.
    * **Crucially**, before sending your media, use the `/media_language` command to tell the bot the language of the audio. This ensures the most accurate transcription possible.
    * The bot will then process your media and send back the transcribed text. If the transcription is very long, it will be sent as a text file for easier reading.
    * After receiving the transcription, you'll see inline buttons with options to **Translate** or **Summarize** the text.

2.  **Convert Text to Speech:**
    * Use the command `/text_to_speech` to choose a language and voice.
    * After selecting your preferred voice, simply send any text message, and the bot will convert it into an audio file for you.

3.  **Commands:**
    * `/start`: Get a welcome message and info about the bot. (Admins see a live uptime panel).
    * `/status`: View detailed statistics about the bot's performance and usage.
    * `/help`: Display these instructions on how to use the bot.
    * `/language`: Change your preferred language for translations and summaries. This setting applies to text outputs, not the original media.
    * `/media_language`: Set the language of the audio in your media files for transcription. This is vital for accuracy.
    * `/text_to_speech`: Choose a language and voice for the text-to-speech feature.
    * `/privacy`: Read the bot's privacy notice to understand how your data is handled.

Enjoy transcribing, translating, summarizing, and converting text to speech quickly and easily!
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(user_id)
    user_doc = get_user_data(user_id)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None

    privacy_text = (
        """**Privacy Notice**

Your privacy is paramount. Here's a transparent look at how this bot handles your data in real-time:

1.  **Data We Process & Its Lifecycle:**
    * **Media Files (Voice, Audio, Video):** When you send a media file (voice, audio, video note, or a video file as a document), it's temporarily downloaded for **immediate transcription**. Crucially, these files are **deleted instantly** from our servers once the transcription is complete. We do not store your media content.
    * **Text for Speech Synthesis:** When you send text for conversion to speech, it is processed to generate the audio and then **not stored**. The generated audio file is also temporary and deleted after sending.
    * **Transcriptions:** The text generated from your media is held **temporarily in the bot's memory** for a limited period. This allows for follow-up actions like translation or summarization. This data is not permanently stored on our servers and is cleared regularly (e.g., when new media is processed or the bot restarts, or after 7 days as per cleanup).
    * **User IDs:** Your Telegram User ID is stored in MongoDB. This helps us remember your language preferences and track basic, aggregated activity (like when you last used the bot) to improve service and understand overall usage patterns. This ID is not linked to any personal identifying information outside of Telegram.
    * **Language Preferences:** Your chosen languages for translations/summaries and media transcription are saved in MongoDB. Your chosen voice for text-to-speech is also saved. This ensures you don't need to re-select them for every interaction, making your experience smoother.

2.  **How Your Data is Used:**
    * To deliver the bot's core services: transcribing, translating, summarizing your media, and converting text to speech.
    * To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total files processed).
    * To maintain your personalized language settings and voice preferences across sessions.

3.  **Data Sharing Policy:**
    * We **do not share** your personal data, media files, or transcriptions with any third parties.
    * Transcription, translation, and summarization are facilitated by integrating with advanced AI models (specifically, the Google Speech-to-Text API for transcription and the Gemini API for translation/summarization). Text-to-speech uses the Microsoft Cognitive Services Speech API. Your input sent to these models is governed by their respective privacy policies, but we ensure that your data is **not stored by us** after processing by these services.

4.  **Data Retention:**
    * **Media files and generated audio files:** Deleted immediately post-processing.
    * **Transcriptions:** Held temporarily in the bot's active memory for immediate use and cleared after 7 days or when superseded.
    * **User IDs and language/voice preferences:** Retained in MongoDB to support your settings and for anonymous usage statistics. If you wish to have your stored preferences removed, you can cease using the bot or contact the bot administrator for explicit data deletion.

By using this bot, you acknowledge and agree to the data practices outlined in this Privacy Notice.

Should you have any questions or concerns regarding your privacy, please feel free to contact the bot administrator.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(user_id)
    user_doc = get_user_data(user_id)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    try:
        total_registered_users = users_collection.count_documents({})
    except Exception as e:
        logging.error(f"Error counting registered users: {e}")
        total_registered_users = 0

    today_iso = datetime.now().date().isoformat()
    try:
        active_today = users_collection.count_documents({
            "last_active": {"$gte": today_iso}
        })
    except Exception as e:
        logging.error(f"Error counting active users today: {e}")
        active_today = 0

    try:
        total_processed = processing_stats_collection.count_documents({})
        voice_count = processing_stats_collection.count_documents({"type": "voice"})
        audio_count = processing_stats_collection.count_documents({"type": "audio"})
        video_count = processing_stats_collection.count_documents({"type": "video"})
        pipeline = [
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result = list(processing_stats_collection.aggregate(pipeline))
        total_proc_seconds = agg_result[0]["total_time"] if agg_result else 0
    except Exception as e:
        logging.error(f"Error fetching processing stats: {e}")
        total_processed = voice_count = audio_count = video_count = 0
        total_proc_seconds = 0

    proc_hours = int(total_proc_seconds) // 3600
    proc_minutes = (int(total_proc_seconds) % 3600) // 60
    proc_seconds = int(total_proc_seconds) % 60

    text = (
        "📊 Bot Statistics\n\n"
        "🟢 **Bot Status: Online**\n"
        f"⏱️ The bot has been running for: {days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds\n\n"
        "👥 User Statistics\n"
        f"▫️ Total Users Today: {active_today}\n"
        f"▫️ Total Registered Users: {total_registered_users}\n\n"
        "⚙️ Processing Statistics (from Database)\n"
        f"▫️ Total Files Processed: {total_processed}\n"
        f"▫️ Voice Clips: {voice_count}\n"
        f"▫️ Audio Files: {audio_count}\n"
        f"▫️ Videos: {video_count}\n"
        f"⏱️ Total Processing Time: {proc_hours} hours {proc_minutes} minutes {proc_seconds} seconds\n\n"
        "⸻\n\n"
        "Thanks for using our service! 🙌"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    try:
        total_registered_users = users_collection.count_documents({})
    except Exception as e:
        logging.error(f"Error counting registered users for admin: {e}")
        total_registered_users = 0
    bot.send_message(message.chat.id, f"Total registered users: {total_registered_users}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = "awaiting_broadcast"
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == "awaiting_broadcast",
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    for user_doc in users_collection.find({}, {"_id": 1}):
        uid = user_doc["_id"]
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

# ────────────────────────────────────────────────────────────────────────────────────────────
#   M E D I A   H A N D L I N G  (voice, audio, video, video_note, document as media)
# ────────────────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note', 'document'])
def handle_file(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    media_lang_setting = get_user_media_language_setting_db(uid)
    if not media_lang_setting:
        bot.send_message(
            message.chat.id,
            "⚠️ Please first select the language of the audio/video file using /media_language before sending the file."
        )
        return

    file_obj = None
    is_document_video = False
    if message.voice:
        file_obj = message.voice
        type_str = "voice"
    elif message.audio:
        file_obj = message.audio
        type_str = "audio"
    elif message.video:
        file_obj = message.video
        type_str = "video"
    elif message.video_note:
        file_obj = message.video_note
        type_str = "video"
    elif message.document:
        mime = message.document.mime_type or ""
        if mime.startswith("video/") or mime.startswith("audio/"):
            file_obj = message.document
            is_document_video = True
            if mime.startswith("audio/"):
                type_str = "audio"
            else:
                type_str = "video"
        else:
            bot.send_message(
                message.chat.id,
                "❌ The file you sent is not a supported audio/video format. "
                "Please send a voice message, audio file, video note, or video file (e.g. .mp4)."
            )
            return

    if not file_obj:
        bot.send_message(
            message.chat.id,
            "❌ Please send only voice messages, audio files, video notes, or video files."
        )
        return

    size = file_obj.file_size
    if size and size > FILE_SIZE_LIMIT:
        bot.send_message(message.chat.id, "😓 Sorry, the file size you uploaded is too large (max allowed is 20MB).")
        return

    try:
        bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[{"type": "emoji", "emoji": "👀"}]
        )
    except Exception as e:
        logging.error(f"Error setting reaction: {e}")

    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing

    try:
        threading.Thread(
            target=process_media_file,
            args=(message, stop_typing, is_document_video, type_str)
        ).start()
    except Exception as e:
        logging.error(f"Error initiating file processing: {e}")
        stop_typing.set()
        try:
            bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[]
            )
        except Exception as remove_e:
            logging.error(f"Error removing reaction on early error: {remove_e}")
        bot.send_message(message.chat.id, "😓 Sorry, an unexpected error occurred. Please try again.")

def process_media_file(message, stop_typing, is_document_video, type_str):
    """
    Download media, convert to WAV, run SpeechRecognition in chunks,
    store stats in MongoDB, send transcription.
    """
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time

    uid = str(message.from_user.id)

    if message.voice:
        file_obj = message.voice
    elif message.audio:
        file_obj = message.audio
    elif message.video:
        file_obj = message.video
    elif message.video_note:
        file_obj = message.video_note
    else:
        file_obj = message.document

    local_temp_file = None
    wav_audio_path = None
    processing_time = 0
    status_str = "success"

    try:
        info = bot.get_file(file_obj.file_id)
        if message.voice or message.video_note:
            file_extension = ".ogg"
        elif message.document:
            _, ext = os.path.splitext(message.document.file_name or info.file_path)
            file_extension = ext if ext else os.path.splitext(info.file_path)[1]
        else:
            file_extension = os.path.splitext(info.file_path)[1]

        local_temp_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{file_extension}")
        data = bot.download_file(info.file_path)
        with open(local_temp_file, "wb") as f:
            f.write(data)

        processing_start_time = datetime.now()

        wav_audio_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.wav")
        try:
            command = [
                ffmpeg.get_ffmpeg_exe(),
                "-i", local_temp_file,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                wav_audio_path
            ]
            subprocess.run(command, check=True, capture_output=True)
            if not os.path.exists(wav_audio_path) or os.path.getsize(wav_audio_path) == 0:
                raise Exception("FFmpeg conversion failed or resulted in an empty file.")
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg conversion failed: {e.stdout.decode()} {e.stderr.decode()}")
            status_str = "fail"
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg error: {remove_e}")
            bot.send_message(
                message.chat.id,
                "😓 Sorry, there was an issue converting your audio/video to the correct format. "
                "Please try again with a different file."
            )
            processing_time = (datetime.now() - processing_start_time).total_seconds()
            try:
                processing_stats_collection.insert_one({
                    "user_id": uid,
                    "message_id": message.message_id,
                    "type": type_str,
                    "processing_time": processing_time,
                    "timestamp": datetime.now().isoformat(),
                    "status": status_str
                })
            except Exception as e:
                logging.error(f"Error inserting processing stat (FFmpeg fail): {e}")
            return
        except Exception as e:
            logging.error(f"FFmpeg conversion failed: {e}")
            status_str = "fail"
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg general error: {remove_e}")
            bot.send_message(
                message.chat.id,
                "😓 Sorry, your file cannot be converted to the correct voice recognition format. "
                "Please ensure it's a standard audio/video file."
            )
            processing_time = (datetime.now() - processing_start_time).total_seconds()
            try:
                processing_stats_collection.insert_one({
                    "user_id": uid,
                    "message_id": message.message_id,
                    "type": type_str,
                    "processing_time": processing_time,
                    "timestamp": datetime.now().isoformat(),
                    "status": status_str
                })
            except Exception as e:
                logging.error(f"Error inserting processing stat (conversion fail): {e}")
            return

        # Transcribe in 20-second chunks
        media_lang_name = get_user_media_language_setting_db(uid)
        if not media_lang_name:
            status_str = "fail"
            bot.send_message(message.chat.id, "⚠️ No media language set. Please use /media_language first.")
            processing_time = (datetime.now() - processing_start_time).total_seconds()
            try:
                processing_stats_collection.insert_one({
                    "user_id": uid,
                    "message_id": message.message_id,
                    "type": type_str,
                    "processing_time": processing_time,
                    "timestamp": datetime.now().isoformat(),
                    "status": status_str
                })
            except Exception as e:
                logging.error(f"Error inserting processing stat (no media lang): {e}")
            return

        media_lang_code = get_lang_code(media_lang_name)
        if not media_lang_code:
            status_str = "fail"
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on language code error: {remove_e}")
            bot.send_message(
                message.chat.id,
                f"❌ The language *{media_lang_name}* does not have a valid code for transcription. "
                "Please re-select the language using /media_language."
            )
            processing_time = (datetime.now() - processing_start_time).total_seconds()
            try:
                processing_stats_collection.insert_one({
                    "user_id": uid,
                    "message_id": message.message_id,
                    "type": type_str,
                    "processing_time": processing_time,
                    "timestamp": datetime.now().isoformat(),
                    "status": status_str
                })
            except Exception as e:
                logging.error(f"Error inserting processing stat (invalid lang code): {e}")
            return

        transcription = transcribe_audio_with_chunks(wav_audio_path, media_lang_code) or ""
        user_transcriptions.setdefault(uid, {})[message.message_id] = transcription

        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        else:
            total_videos += 1

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        total_processing_time += processing_time

        increment_transcription_count_db(uid)

        # Buttons for Translate / Summarize
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
            fn = "transcription.txt"
            with open(fn, "w", encoding="utf-8") as f:
                f.write(transcription)
            bot.send_chat_action(message.chat.id, "upload_document")
            with open(fn, "rb") as doc:
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

        try:
            processing_stats_collection.insert_one({
                "user_id": uid,
                "message_id": message.message_id,
                "type": type_str,
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": status_str
            })
        except Exception as e:
            logging.error(f"Error inserting processing stat (success): {e}")

        user_doc_after_inc = get_user_data(uid)
        user_transcription_count_after_inc = user_doc_after_inc.get("transcription_count", 0) if user_doc_after_inc else 0
        if user_transcription_count_after_inc == 5 and not check_subscription(message.from_user.id):
            send_subscription_message(message.chat.id)

    except Exception as e:
        logging.error(f"Error processing file for user {uid}: {e}")
        status_str = "fail"
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on general processing error: {remove_e}")
        bot.send_message(
            message.chat.id,
            "😓𝗪𝗲’𝗿𝗲 𝘀𝗼𝗿𝗿𝘆, 𝗮𝗻 𝗲𝗿𝗿𝗼𝗿 𝗼𝗰𝗰𝘂𝗿𝗿𝗲𝗱 𝗱𝘂𝗿𝗶𝗻𝗴 𝘁𝗿𝗮𝗻𝘀𝗰𝗿𝗶𝗽𝘁𝗶𝗼𝗻.\n"
            "The audio might be noisy or spoken too quickly.\n"
            "Please try again or upload a different file.\n"
            "Make sure the file you’re sending and the selected language match — otherwise, an error may occur."
        )
        processing_time = (datetime.now() - datetime.fromtimestamp(message.date)).total_seconds() if message.date else 0
        try:
            processing_stats_collection.insert_one({
                "user_id": uid,
                "message_id": message.message_id,
                "type": type_str,
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": status_str
            })
        except Exception as e:
            logging.error(f"Error inserting processing stat (exception): {e}")
    finally:
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]

        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)
        if wav_audio_path and os.path.exists(wav_audio_path):
            os.remove(wav_audio_path)

def transcribe_audio_with_chunks(audio_path: str, lang_code: str) -> str | None:
    """
    Split WAV into 20-second chunks and run SpeechRecognition on each.
    """
    recognizer = sr.Recognizer()
    text = ""
    try:
        sound = AudioSegment.from_wav(audio_path)
        chunk_length_ms = 20_000  # 20 seconds in milliseconds

        for i in range(0, len(sound), chunk_length_ms):
            chunk = sound[i:i + chunk_length_ms]
            chunk_filename = os.path.join(
                DOWNLOAD_DIR,
                f"{uuid.uuid4()}_{i // 1000}_{(i + chunk_length_ms) // 1000}.wav"
            )
            chunk.export(chunk_filename, format="wav")

            with sr.AudioFile(chunk_filename) as source:
                audio_data = recognizer.record(source)

            try:
                part = recognizer.recognize_google(audio_data, language=lang_code)
            except sr.UnknownValueError:
                part = ""
            except sr.RequestError as e:
                logging.error(f"Could not request results from Speech Recognition service; {e}")
                os.remove(chunk_filename)
                return None
            except Exception as e:
                logging.error(f"Speech Recognition error: {e}")
                os.remove(chunk_filename)
                return None

            text += part + " "
            os.remove(chunk_filename)

        return text.strip()

    except Exception as e:
        logging.error(f"Error during chunked transcription: {e}")
        return None

# --- Language Selection and Saving ---
LANGUAGES = [
    {"name": "English", "flag": "🇬🇧", "code": "en"},
    {"name": "Arabic", "flag": "🇸🇦", "code": "ar"},
    {"name": "Spanish", "flag": "🇪🇸", "code": "es"},
    {"name": "Hindi", "flag": "🇮🇳", "code": "hi"},
    {"name": "French", "flag": "🇫🇷", "code": "fr"},
    {"name": "German", "flag": "🇩🇪", "code": "de"},
    {"name": "Chinese", "flag": "🇨🇳", "code": "zh"},
    {"name": "Japanese", "flag": "🇯🇵", "code": "ja"},
    {"name": "Portuguese", "flag": "🇵🇹", "code": "pt"},
    {"name": "Russian", "flag": "🇷🇺", "code": "ru"},
    {"name": "Turkish", "flag": "🇹🇷", "code": "tr"},
    {"name": "Korean", "flag": "🇰🇷", "code": "ko"},
    {"name": "Italian", "flag": "🇮🇹", "code": "it"},
    {"name": "Indonesian", "flag": "🇮🇩", "code": "id"},
    {"name": "Vietnamese", "flag": "🇻🇳", "code": "vi"},
    {"name": "Thai", "flag": "🇹🇭", "code": "th"},
    {"name": "Dutch", "flag": "🇳🇱", "code": "nl"},
    {"name": "Polish", "flag": "🇵🇱", "code": "pl"},
    {"name": "Swedish", "flag": "🇸🇪", "code": "sv"},
    {"name": "Filipino", "flag": "🇵🇭", "code": "tl"},
    {"name": "Greek", "flag": "🇬🇷", "code": "el"},
    {"name": "Hebrew", "flag": "🇮🇱", "code": "he"},
    {"name": "Hungarian", "flag": "🇭🇺", "code": "hu"},
    {"name": "Czech", "flag": "🇨🇿", "code": "cs"},
    {"name": "Danish", "flag": "🇩🇰", "code": "da"},
    {"name": "Finnish", "flag": "🇫🇮", "code": "fi"},
    {"name": "Norwegian", "flag": "🇳🇴", "code": "no"},
    {"name": "Romanian", "flag": "🇷🇴", "code": "ro"},
    {"name": "Slovak", "flag": "🇸🇰", "code": "sk"},
    {"name": "Ukrainian", "flag": "🇺🇦", "code": "uk"},
    {"name": "Malay", "flag": "🇲🇾", "code": "ms"},
    {"name": "Bengali", "flag": "🇧🇩", "code": "bn"},
    {"name": "Tamil", "flag": "🇮🇳", "code": "ta"},
    {"name": "Telugu", "flag": "🇮🇳", "code": "te"},
    {"name": "Kannada", "flag": "🇮🇳", "code": "kn"},
    {"name": "Malayalam", "flag": "🇮🇳", "code": "ml"},
    {"name": "Gujarati", "flag": "🇮🇳", "code": "gu"},
    {"name": "Marathi", "flag": "🇮🇳", "code": "mr"},
    {"name": "Urdu", "flag": "🇵🇰", "code": "ur"},
    {"name": "Nepali", "flag": "🇳🇵", "code": "ne"},
    {"name": "Sinhala", "flag": "🇱🇰", "code": "si"},
    {"name": "Khmer", "flag": "🇰🇭", "code": "km"},
    {"name": "Lao", "flag": "🇱🇦", "code": "lo"},
    {"name": "Burmese", "flag": "🇲🇲", "code": "my"},
    {"name": "Georgian", "flag": "🇬🇪", "code": "ka"},
    {"name": "Armenian", "flag": "🇦🇲", "code": "hy"},
    {"name": "Azerbaijani", "flag": "🇦🇿", "code": "az"},
    {"name": "Kazakh", "flag": "🇰🇿", "code": "kk"},
    {"name": "Uzbek", "flag": "🇺🇿", "code": "uz"},
    {"name": "Kyrgyz", "flag": "🇰🇬", "code": "ky"},
    {"name": "Tajik", "flag": "🇹🇯", "code": "tg"},
    {"name": "Turkmen", "flag": "🇹🇲", "code": "tk"},
    {"name": "Mongolian", "flag": "🇲🇳", "code": "mn"},
    {"name": "Estonian", "flag": "🇪🇪", "code": "et"},
    {"name": "Latvian", "flag": "🇱🇻", "code": "lv"},
    {"name": "Lithuanian", "flag": "🇱🇹", "code": "lt"},
    {"name": "Afrikaans", "flag": "🇿🇦", "code": "af"},
    {"name": "Albanian", "flag": "🇦🇱", "code": "sq"},
    {"name": "Bosnian", "flag": "🇧🇦", "code": "bs"},
    {"name": "Bulgarian", "flag": "🇧🇬", "code": "bg"},
    {"name": "Catalan", "flag": "🇪🇸", "code": "ca"},
    {"name": "Croatian", "flag": "🇭🇷", "code": "hr"},
    {"name": "Galician", "flag": "🇪🇸", "code": "gl"},
    {"name": "Icelandic", "flag": "🇮🇸", "code": "is"},
    {"name": "Irish", "flag": "🇮🇪", "code": "ga"},
    {"name": "Macedonian", "flag": "🇲🇰", "code": "mk"},
    {"name": "Maltese", "flag": "🇲🇹", "code": "mt"},
    {"name": "Serbian", "flag": "🇷🇸", "code": "sr"},
    {"name": "Slovenian", "flag": "🇸🇮", "code": "sl"},
    {"name": "Welsh", "flag": "🏴", "code": "cy"},
    {"name": "Zulu", "flag": "🇿🇦", "code": "zu"},
    {"name": "Somali", "flag": "🇸🇴", "code": "so"}
]

def get_lang_code(lang_name):
    """
    Return ISO code for a given language name.
    """
    for lang in LANGUAGES:
        if lang['name'].lower() == lang_name.lower():
            return lang['code']
    return None

def generate_language_keyboard(callback_prefix, message_id=None):
    """
    Generate inline keyboard of LANGUAGES for callbacks.
    """
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang in LANGUAGES:
        cb_data = f"{callback_prefix}|{lang['name']}"
        if message_id is not None:
            cb_data += f"|{message_id}"
        buttons.append(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb_data))
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

# --- TTS language/voice selection keyboards ---
def make_tts_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang_name in TTS_VOICES_BY_LANGUAGE.keys():
        buttons.append(InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}"))
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

def make_tts_voice_keyboard_for_language(lang_name):
    markup = InlineKeyboardMarkup(row_width=2)
    voices = TTS_VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        markup.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    markup.add(InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_to_languages"))
    return markup

@bot.message_handler(commands=['text_to_speech'])
def cmd_text_to_speech(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(user_id)
    user_doc = get_user_data(user_id)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    bot.send_message(message.chat.id, "🎙️ Choose a language for text-to-speech:", reply_markup=make_tts_language_keyboard())

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, lang_name = call.data.split("|", 1)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🎙️ Choose a voice for {lang_name}:",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(call):
    uid = str(call.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, voice = call.data.split("|", 1)
    set_tts_user_voice_db(uid, voice)
    user_tts_mode[uid] = voice

    bot.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🔊 Now using: *{voice}*. You can start sending text messages to convert them to speech.",
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🎙️ Choose a language for text-to-speech:",
        reply_markup=make_tts_language_keyboard()
    )
    bot.answer_callback_query(call.id)

async def synth_and_send_tts(chat_id, user_id, text):
    """
    Use MSSpeech to synthesize text -> mp3, send and delete file.
    """
    voice = get_tts_user_voice_db(user_id)
    filename = os.path.join(DOWNLOAD_DIR, f"tts_{user_id}_{uuid.uuid4()}.mp3")

    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(chat_id, stop_recording))
    recording_thread.daemon = True
    recording_thread.start()

    try:
        mss = MSSpeech()
        await mss.set_voice(voice)
        await mss.set_rate(0)
        await mss.set_pitch(0)
        await mss.set_volume(1.0)

        await mss.synthesize(text, filename)

        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            bot.send_message(chat_id, "❌ MP3 file not generated or empty. Please try again.")
            return

        with open(filename, "rb") as f:
            bot.send_audio(chat_id, f, caption=f"🎤 Voice: {voice}")
    except MSSpeechError as e:
        logging.error(f"TTS error: {e}")
        bot.send_message(chat_id, f"❌ An error occurred with the voice synthesis: {e}")
    except Exception as e:
        logging.exception("TTS error")
        bot.send_message(chat_id, "❌ An unexpected error occurred during text-to-speech conversion. Please try again.")
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            os.remove(filename)

@bot.message_handler(commands=['language'])
def select_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    markup = generate_language_keyboard("set_lang")
    bot.send_message(
        message.chat.id,
        "Please select your preferred language for future **translations and summaries**:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_lang|"))
def callback_set_language(call):
    uid = str(call.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    _, lang = call.data.split("|", 1)
    set_user_language_setting_db(uid, lang)
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
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "Please choose the language of the audio files that you need me to transcribe. This helps ensure accurate reading.",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_media_lang|"))
def callback_set_media_language(call):
    uid = str(call.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    _, lang = call.data.split("|", 1)
    set_user_media_language_setting_db(uid, lang)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ The transcription language for your media is set to: **{lang}**\n\n"
             "Now, please send your voice message, audio file, video note, or video file "
             "for me to transcribe. I support media files up to 20MB in size.",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Media language set to {lang}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call):
    uid = str(call.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = get_user_language_setting_db(uid)
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
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = get_user_language_setting_db(uid)
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
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    set_user_language_setting_db(uid, lang)
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
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    set_user_language_setting_db(uid, lang)
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
    """
    Use Gemini to translate saved transcription into lang.
    """
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to translate.")
        return

    prompt = f"Translate the following text into {lang}. Provide only the translated text, with no additional notes, explanations, or introductory/concluding remarks:\n\n{original}"
    bot.send_chat_action(message.chat.id, "typing")
    translated = ask_gemini(uid, prompt)

    if translated.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Sorry, an error occurred during translation: {translated}. Please try again later.")
        return

    if len(translated) > 4000:
        fn = "translation.txt"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(translated)
        bot.send_chat_action(message.chat.id, "upload_document")
        with open(fn, "rb") as doc:
            bot.send_document(message.chat.id, doc, caption=f"Translation to {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, translated, reply_to_message_id=message_id)

def do_summarize_with_saved_lang(message, uid, lang, message_id):
    """
    Use Gemini to summarize saved transcription into lang.
    """
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to summarize.")
        return

    prompt = f"Summarize the following text in {lang}. Provide only the summarized text, with no additional notes, explanations, or different versions:\n\n{original}"
    bot.send_chat_action(message.chat.id, "typing")
    summary = ask_gemini(uid, prompt)

    if summary.startswith("Error:"):
        bot.send_message(chat_id=message.chat.id, text=f"😓 Sorry, an error occurred during summarization: {summary}. Please try again later.")
        return

    if len(summary) > 4000:
        fn = "summary.txt"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(summary)
        bot.send_chat_action(message.chat.id, "upload_document")
        with open(fn, "rb") as doc:
            bot.send_document(message.chat.id, doc, caption=f"Summary in {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, summary, reply_to_message_id=message_id)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = str(message.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to translate it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = get_user_language_setting_db(uid)
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
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to summarize it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = get_user_language_setting_db(uid)
    if preferred_lang:
        threading.Thread(target=do_summarize_with_saved_lang, args=(message, uid, preferred_lang, transcription_message_id)).start()
    else:
        markup = generate_language_keyboard("summarize_in", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want the summary in:",
            reply_markup=markup
        )

@bot.message_handler(func=lambda message: message.content_type == 'text' and not message.text.startswith('/'))
def handle_text_for_tts_or_fallback(message):
    uid = str(message.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    if user_tts_mode.get(uid):
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))
        ).start()
    elif get_tts_user_voice_db(uid) != "en-US-AriaNeural":
        user_tts_mode[uid] = get_tts_user_voice_db(uid)
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))
        ).start()
    else:
        bot.send_message(
            message.chat.id,
            "I only transcribe voice messages, audio, video, or video files. "
            "To convert text to speech, use the /text_to_speech command first."
        )

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document'])
def fallback_non_text_or_media(message):
    uid = str(message.from_user.id)
    update_user_activity_db(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get("transcription_count", 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    bot.send_message(
        message.chat.id,
        "Please send only voice messages, audio files, video notes, or video files for transcription, "
        "or use `/text_to_speech` for text to speech."
    )

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    if request.method in ("GET", "HEAD"):
        return "OK", 200
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if content_type and content_type.startswith("application/json"):
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
            return "", 200
    return abort(403)

@app.route("/set_webhook", methods=["GET", "POST"])
def set_webhook_route():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        return f"Webhook set to {WEBHOOK_URL}", 200
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")
        return f"Failed to set webhook: {e}", 500

@app.route("/delete_webhook", methods=["GET", "POST"])
def delete_webhook_route():
    try:
        bot.delete_webhook()
        return "Webhook deleted.", 200
    except Exception as e:
        logging.error(f"Failed to delete webhook: {e}")
        return f"Failed to delete webhook: {e}", 500

def set_webhook_on_startup():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set successfully to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook on startup: {e}")

def cleanup_old_data():
    """
    Clean up in-memory and DB data older than thresholds.
    """
    seven_days_ago_iso = (datetime.now() - timedelta(days=7)).isoformat()

    # Cleanup in-memory transcriptions older than seven days
    to_delete_transcriptions = []
    for user_id in user_transcriptions:
        try:
            user_doc = users_collection.find_one({"_id": user_id})
            if not user_doc or user_doc.get("last_active") < seven_days_ago_iso:
                to_delete_transcriptions.append(user_id)
        except Exception as e:
            logging.error(f"Error checking last_active for in-memory cleanup for user {user_id}: {e}")
            to_delete_transcriptions.append(user_id)
    for user_id in to_delete_transcriptions:
        del user_transcriptions[user_id]

    # Cleanup in-memory chat history older than seven days
    to_delete_memory = []
    for user_id in user_memory:
        try:
            user_doc = users_collection.find_one({"_id": user_id})
            if not user_doc or user_doc.get("last_active") < seven_days_ago_iso:
                to_delete_memory.append(user_id)
        except Exception as e:
            logging.error(f"Error checking last_active for chat memory cleanup for user {user_id}: {e}")
            to_delete_memory.append(user_id)
    for user_id in to_delete_memory:
        del user_memory[user_id]

    thirty_days_ago_iso = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        users_collection.update_many(
            {"last_active": {"$lt": thirty_days_ago_iso}},
            {"$set": {"transcription_count": 0}}
        )
    except Exception as e:
        logging.error(f"Error resetting transcription counts: {e}")

    try:
        existing_user_ids = [doc["_id"] for doc in users_collection.find({}, {"_id": 1})]
        language_settings_collection.delete_many({"_id": {"$nin": existing_user_ids}})
        media_language_settings_collection.delete_many({"_id": {"$nin": existing_user_ids}})
        tts_users_collection.delete_many({"_id": {"$nin": existing_user_ids}})
        processing_stats_collection.delete_many({"user_id": {"$nin": existing_user_ids}})
    except Exception as e:
        logging.error(f"Error cleaning up orphaned settings: {e}")

    threading.Timer(24 * 60 * 60, cleanup_old_data).start()

def set_bot_info_and_startup():
    connect_to_mongodb()
    set_bot_info()
    cleanup_old_data()
    set_webhook_on_startup()

if __name__ == "__main__":
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
