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
import imageio_ffmpeg as ffmpeg
from pydub import AudioSegment
import threading
import time
import subprocess
import io

# --- NEW: Import FasterWhisper for faster speech-to-text ---
from faster_whisper import WhisperModel

# --- NEW: Import MSSpeech for Text-to-Speech ---
from msspeech import MSSpeech, MSSpeechError

# --- NEW: Imports for Image OCR ---
from PIL import Image
import pytesseract

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BOT CONFIGURATION (Using Media Transcriber Bot's Token and Webhook) ---
TOKEN = "7790991731:AAH4rt8He_PABDa28xgcY3dIQwmtuQD-qiM"  # Replace with your actual bot token
ADMIN_ID = 5978150981  # Replace with your actual Admin ID
# Webhook URL - Replace with your actual Render URL
WEBHOOK_URL = "https://speech-recognition-9cyh.onrender.com"

# --- REQUIRED CHANNEL CONFIGURATION ---
REQUIRED_CHANNEL = "@transcriberbo" # Replace with your actual channel username (e.g., @MyBotOfficialChannel)

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Download directory (still used for intermediate WAV, but aiming for in-memory)
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

# User-specific media language settings for speech recognition and OCR
user_media_language_settings_file = 'user_media_language_settings.json'
user_media_language_settings = {}
if os.path.exists(user_media_language_settings_file):
    with open(user_media_language_settings_file, 'r') as f: # Corrected variable name here
        try:
            user_media_language_settings = json.load(f)
        except json.JSONDecodeError:
            user_media_language_settings = {}

# --- NEW: TTS User settings and Voices ---
tts_users_db = 'tts_users.json' # Separate DB for TTS user preferences
tts_users = {}
if os.path.exists(tts_users_db):
    try:
        with open(tts_users_db, "r") as f:
            tts_users = json.load(f)
    except json.JSONDecodeError:
        tts_users = {}

# --- NEW: User state for Text-to-Speech input mode ---
# This will now store the chosen voice, not just a boolean True/False.
# {user_id: "en-US-AriaNeural" (chosen voice) or None (not in TTS mode)}
user_tts_mode = {} 

# Group voices by language for better organization - Ordered as requested for buttons
TTS_VOICES_BY_LANGUAGE = {
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
        "es-CR-MariaNeural", "es-CR-JuanNeural",
        "es-DO-RamonaNeural", "es-DO-AntonioNeural" # Fixed typo es-DO-JuanNeural and es-DO-MariaNeural
    ],
    "Hindi 🇮🇳": [
        "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"
    ],
    "French 🇫🇷": [
        "fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural", "fr-CA-JeanNeural",
        "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-CH-CharlineNeural", "fr-BE-CamilleNeural"
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
    ],
    "Somali 🇸🇴": [ # Added Somali to the main list for popular languages
        "so-SO-UbaxNeural", "so-SO-MuuseNeural",
    ],
}
# --- End of NEW TTS Voice Config ---

# --- NEW: Load the FasterWhisper model once at startup ---
# Using 'small' model. You can choose 'base', 'medium', 'large-v2', 'large-v3'
# The 'compute_type' can be 'float16' for GPU or 'int8' for CPU for better performance.
# For Render, 'int8' is usually better for CPU-only environments.
# If you have a GPU on Render (unlikely for free tiers), you can use 'float16'.
try:
    WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    logging.info("FasterWhisper 'small' model loaded successfully.")
except Exception as e:
    logging.error(f"Failed to load FasterWhisper model: {e}")
    WHISPER_MODEL = None # Set to None if loading fails

def save_user_data():
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)

def save_user_language_settings():
    with open(user_language_settings_file, 'w') as f:
        json.dump(user_language_settings, f, indent=4)

def save_user_media_language_settings():
    # Corrected variable name here to match the file variable
    with open(user_media_language_settings_file, 'w') as f: 
        json.dump(user_media_language_settings, f, indent=4)

# --- NEW: Save TTS user settings ---
def save_tts_users():
    with open(tts_users_db, "w") as f:
        json.dump(tts_users, f, indent=2)

def get_tts_user_voice(uid):
    # Default to a specific voice if not found in user settings
    return tts_users.get(str(uid), "en-US-AriaNeural")
# --- End of NEW TTS User settings ---

# In-memory chat history and transcription store
user_memory = {}
user_transcriptions = {} # This will now store both audio/video transcriptions and OCR text
processing_message_ids = {}  # To keep track of messages for which typing action is active

# Statistics counters (global variables)
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_images = 0 # NEW: Counter for images processed
total_processing_time = 0
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()  # To prevent race conditions

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"  # Replace with your actual Gemini API Key

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

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB
admin_state = {}

def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "👋Get a welcome message and info"),
        telebot.types.BotCommand("status", "📊View Bot statistics"),
        telebot.types.BotCommand("language", "🌐Change preferred language for translate/summarize"),
        telebot.types.BotCommand("media_language", "📝Set language for media transcription and OCR"), # MODIFIED: Added OCR
        telebot.types.BotCommand("text_to_speech", "🗣️Convert text to speech"), # NEW COMMAND
        
    ]
    bot.set_my_commands(commands)

    bot.set_my_short_description(
        "Got media files? Let this free bot transcribe, summarize, and translate them in seconds!"
    )

    bot.set_my_description(
        """This bot quickly transcribes, summarizes, and translates voice messages, audio files, and videos for free!
It can also extract text from images and convert your text into speech in various languages!

     🔥Enjoy free usage and start now!👌🏻"""
    )

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

# Function to keep sending 'typing' action
def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

# Function to keep sending 'recording' action
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

# --- NEW: Check Channel Subscription ---
def check_subscription(user_id):
    if not REQUIRED_CHANNEL:
        return True # If no required channel is set, always return True

    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id} in {REQUIRED_CHANNEL}: {e}")
        # If the channel is not found or other API error, assume user is not a member or cannot be checked
        return False

def send_subscription_message(chat_id):
    if not REQUIRED_CHANNEL:
        return # Do nothing if no required channel is set

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Click here to join the channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")
    )
    bot.send_message(
        chat_id,
        "🚫 This bot only works if you’ve joined our official channel. Please join to continue using the bot.",
        reply_markup=markup,
        disable_web_page_preview=True # Prevent showing a preview of the channel link
    )

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    # Always add user to user_data on start
    if user_id not in user_data:
        user_data[user_id] = datetime.now().isoformat()
        save_user_data()
    
    # --- MODIFIED: Ensure TTS mode is OFF on start ---
    user_tts_mode[user_id] = None # Set to None, not False, to indicate not in TTS text input mode
    # --- END MODIFIED ---

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "Admin Panel and Uptime (updating live)...", reply_markup=keyboard)

        with admin_uptime_lock:
            if admin_uptime_message.get(ADMIN_ID) and admin_uptime_message[ADMIN_ID].get('thread') and admin_uptime_message[ADMIN_ID]['thread'].is_alive():
                pass

            admin_uptime_message[ADMIN_ID] = {'message_id': sent_message.message_id, 'chat_id': message.chat.id}
            uptime_thread = threading.Thread(target=update_uptime_message, args=(message.chat.id, sent_message.message_id))
            uptime_thread.daemon = True
            uptime_thread.start()
            admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread

    else:
        # --- NEW: Check subscription for regular users on start ---
        if not check_subscription(message.from_user.id):
            send_subscription_message(message.chat.id)
            return
        # --- End NEW check ---

        display_name = message.from_user.first_name or (f"@{message.from_user.username}" if message.from_user.username else "user")
        bot.send_message(
            message.chat.id,
            f"""👋🏻 Salaam {display_name}!
I'm Media To text Bot. I help you save time by transcribing and summarizing voice messages, audio messages, and video notes.
I can also extract text from images and convert your text into speech!
Simply send or forward a message to me.
"""
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # --- MODIFIED: Ensure TTS mode is OFF on help command ---
    user_tts_mode[user_id] = None
    # --- END MODIFIED ---

    help_text = (
        """ℹ️ How to use this bot:

This bot transcribes voice messages, audio files, and videos using advanced AI. It can also extract text from images and convert text to speech!

1.  **Send a File for Transcription/OCR:**
    * Send a voice message, audio file, video, or an **image** to the bot.
    * **Crucially**, before sending your media, use the `/media_language` command to tell the bot the language of the audio/text in the image. This ensures the most accurate transcription/OCR possible.
    * The bot will then process your media and send back the transcribed text (for audio/video) or extracted text (for images). If the output is very long, it will be sent as a text file for easier reading.
    * After receiving the output, you'll see inline buttons with options to **Translate** or **Summarize** the text.

2.  **Convert Text to Speech:**
    * Use the command `/text_to_speech` to choose a language and voice.
    * After selecting your preferred voice, simply send any text message, and the bot will convert it into an audio file for you.

3.  **Commands:**
    * `/start`: Get a welcome message and info about the bot. (Admins see a live uptime panel).
    * `/status`: View detailed statistics about the bot's performance and usage.
    * `/help`: Display these instructions on how to use the bot.
    * `/language`: Change your preferred language for translations and summaries. This setting applies to text outputs, not the original media.
    * `/media_language`: Set the language of the audio in your media files for transcription or the text in images for OCR. This is vital for accuracy.
    * `/text_to_speech`: Choose a language and voice for the text-to-speech feature.
    * `/privacy`: Read the bot's privacy notice to understand how your data is handled.

Enjoy transcribing, translating, summarizing, converting text to speech, and extracting text from images quickly and easily!
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # --- MODIFIED: Ensure TTS mode is OFF on privacy command ---
    user_tts_mode[user_id] = None
    # --- END MODIFIED ---

    privacy_text = (
        """**Privacy Notice**

Your privacy is paramount. Here's a transparent look at how this bot handles your data in real-time:

1.  **Data We Process & Its Lifecycle:**
    * **Media Files (Voice, Audio, Video):** When you send a media file, it's temporarily downloaded for **immediate transcription**. Crucially, these files are **deleted instantly** from our servers once the transcription is complete. We do not store your media content.
    * **Image Files:** When you send an image, it's temporarily downloaded for **immediate text extraction (OCR)**. These files are **deleted instantly** from our servers once the OCR is complete. We do not store your image content.
    * **Text for Speech Synthesis:** When you send text for conversion to speech, it is processed to generate the audio and then **not stored**. The generated audio file is also temporary and deleted after sending.
    * **Transcriptions/Extracted Text:** The text generated from your media or images is held **temporarily in the bot's memory** for a limited period. This allows for follow-up actions like translation or summarization. This data is not permanently stored on our servers and is cleared regularly (e.g., when new media is processed or the bot restarts, or after 7 days as per cleanup).
    * **User IDs:** Your Telegram User ID is stored. This helps us remember your language preferences and track basic, aggregated activity (like when you last used the bot) to improve service and understand overall usage patterns. This ID is not linked to any personal identifying information outside of Telegram.
    * **Language Preferences:** Your chosen languages for translations/summaries and media transcription/OCR are saved. Your chosen voice for text-to-speech is also saved. This ensures you don't need to re-select them for every interaction, making your experience smoother.

2.  **How Your Data is Used:**
    * To deliver the bot's core services: transcribing, translating, summarizing your media, extracting text from images, and converting text to speech.
    * To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total files processed).
    * To maintain your personalized language settings and voice preferences across sessions.

3.  **Data Sharing Policy:**
    * We **do not share** your personal data, media files, or transcriptions with any third parties.
    * Transcription, translation, and summarization are facilitated by integrating with advanced AI models (specifically, FasterWhisper for transcription and the Gemini API for translation/summarization). Text-to-speech uses the Microsoft Cognitive Services Speech API. Image OCR uses Tesseract. Your input sent to these models is governed by their respective privacy policies, but we ensure that your data is **not stored by us** after processing by these services.

4.  **Data Retention:**
    * **Media files, image files, and generated audio files:** Deleted immediately post-processing.
    * **Transcriptions/Extracted Text:** Held temporarily in the bot's active memory for immediate use and cleared after 7 days or when superseded.
    * **User IDs and language/voice preferences:** Retained to support your settings and for anonymous usage statistics. If you wish to have your stored preferences removed, you can cease using the bot or contact the bot administrator for explicit data deletion.

By using this bot, you acknowledge and agree to the data practices outlined in this Privacy Notice.

Should you have any questions or concerns regarding your privacy, please feel free to contact the bot administrator.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # --- MODIFIED: Ensure TTS mode is OFF on status command ---
    user_tts_mode[user_id] = None
    # --- END MODIFIED ---

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
        f"▫️ Images (OCR): {total_images}\n" # NEW: Image counter
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

# --- NEW: Handler for Photo messages (for OCR) ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None # Reset TTS mode when new media is sent

    if uid not in user_media_language_settings:
        bot.send_message(message.chat.id,
                         "⚠️ Please first select the language of the text in the image using /media_language before sending the image.")
        return

    # Telegram sends multiple photo sizes; pick the largest one (last in the list)
    file_obj = message.photo[-1]
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "😓 Sorry, the image file size you uploaded is too large (max allowed is 20MB).")

    try:
        bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[{'type': 'emoji', 'emoji': '👀'}])
    except Exception as e:
        logging.error(f"Error setting reaction for photo: {e}")

    # Start typing indicator
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing

    try:
        threading.Thread(target=process_image_file, args=(message, stop_typing)).start()
    except Exception as e:
        logging.error(f"Error initiating image processing: {e}")
        stop_typing.set()
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on early image error: {remove_e}")
        bot.send_message(message.chat.id, "😓 Sorry, an unexpected error occurred while processing the image. Please try again.")


def process_image_file(message, stop_typing):
    global total_files_processed, total_images
    uid = str(message.from_user.id)
    file_obj = message.photo[-1] # Get the largest photo size

    local_image_path = None
    processing_start_time = datetime.now()

    try:
        info = bot.get_file(file_obj.file_id)
        file_extension = os.path.splitext(info.file_path)[1] if info.file_path else ".jpg" # Default to .jpg
        
        local_image_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{file_extension}")
        data = bot.download_file(info.file_path)
        with open(local_image_path, 'wb') as f:
            f.write(data)

        media_lang_name = user_media_language_settings[uid]
        # Tesseract expects language codes like 'eng', 'ara', 'som' (ISO 639-2/3) or 'en', 'ar' (ISO 639-1).
        # FasterWhisper uses ISO 639-1, so we need to map for Tesseract if different.
        # For simplicity, we'll try to use the same 'code' from LANGUAGES, and if Tesseract needs 'eng', it'll likely auto-map.
        # Or you can create a specific Tesseract language map. For now, we'll pass the Whisper code.
        ocr_lang_code = get_lang_code(media_lang_name) 
        if not ocr_lang_code:
             bot.send_message(message.chat.id,
                             f"❌ The language *{media_lang_name}* does not have a valid code for OCR. Please re-select the language using /media_language.")
             return

        # Perform OCR
        try:
            # You might need to specify the path to tesseract if it's not in your PATH
            # pytesseract.pytesseract.tesseract_cmd = r'/path/to/tesseract' 
            extracted_text = pytesseract.image_to_string(Image.open(local_image_path), lang=ocr_lang_code)
            
            if not extracted_text.strip():
                extracted_text = "No readable text found in the image or text is too faint/unclear."

        except pytesseract.TesseractNotFoundError:
            logging.error("Tesseract is not installed or not in your PATH.")
            bot.send_message(message.chat.id, "❌ OCR engine (Tesseract) not found. Please contact support.")
            return
        except Exception as e:
            logging.error(f"OCR error: {e}")
            bot.send_message(message.chat.id, "😓 Sorry, an error occurred during text extraction from the image. Please try again.")
            return

        user_transcriptions.setdefault(uid, {})[message.message_id] = extracted_text

        total_files_processed += 1
        total_images += 1

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
            logging.error(f"Error removing reaction before sending image OCR result: {e}")

        # Format output for Telegram
        formatted_text = f"📝 **Extracted Text ({media_lang_name}):**\n\n"
        if extracted_text == "No readable text found in the image or text is too faint/unclear.":
            formatted_text += extracted_text
        else:
            # Split into paragraphs and add extra newlines for better readability
            paragraphs = [p.strip() for p in extracted_text.split('\n\n') if p.strip()]
            for p in paragraphs:
                formatted_text += f"{p}\n\n"
            
        # Send formatted text
        if len(formatted_text) > 4000:
            fn = 'extracted_text.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(formatted_text)
            bot.send_chat_action(message.chat.id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(
                    message.chat.id,
                    doc,
                    reply_to_message_id=message.message_id,
                    reply_markup=buttons,
                    caption="Here’s the extracted text. Tap a button below for more options."
                )
            os.remove(fn)
        else:
            bot.reply_to(
                message,
                formatted_text,
                reply_markup=buttons,
                parse_mode="Markdown"
            )

    except Exception as e:
        logging.error(f"Error processing image for user {uid}: {e}")
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on general image error: {remove_e}")
        bot.send_message(message.chat.id,
                         "😓 Sorry, an error occurred during text extraction. The image might be corrupted or unreadable. Please try again or with a different image.")
    finally:
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]
        if local_image_path and os.path.exists(local_image_path):
            os.remove(local_image_path)
            logging.info(f"Cleaned up {local_image_path}")

# --- Existing Handler for Voice, Audio, Video, Video_Note ---
@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None # Reset TTS mode when new media is sent

    if uid not in user_media_language_settings:
        bot.send_message(message.chat.id,
                         "⚠️ Please first select the language of the audio file using /media_language before sending the file.")
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "😓 Sorry, the file size you uploaded is too large (max allowed is 20MB).")

    # ─── Directly send "👀" reaction ────────────────────────────────────────────
    try:
        # Use bot.set_message_reaction to add an emoji reaction
        bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[{'type': 'emoji', 'emoji': '👀'}])
    except Exception as e:
        logging.error(f"Error setting reaction: {e}")
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
        logging.error(f"Error initiating file processing: {e}")
        stop_typing.set()  # Ensure typing indicator stops if an error occurs early
        # If an error occurs, you might want to remove the reaction here if it was set
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[]) # Remove all reactions
        except Exception as remove_e:
            logging.error(f"Error removing reaction on early error: {remove_e}")
        bot.send_message(message.chat.id, "😓 Sorry, an unexpected error occurred. Please try again.")

def process_media_file(message, stop_typing):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
    uid = str(message.from_user.id)
    file_obj = message.voice or message.audio or message.video or message.video_note

    local_temp_file = None
    wav_audio_path = None  # To hold the path to the WAV file

    try:
        info = bot.get_file(file_obj.file_id)
        file_extension = ".ogg" if message.voice or message.video_note else os.path.splitext(info.file_path)[1]
        
        # Download file to a temporary location for ffmpeg processing
        local_temp_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{file_extension}")
        data = bot.download_file(info.file_path)
        with open(local_temp_file, 'wb') as f:
            f.write(data)

        processing_start_time = datetime.now()

        # Convert to WAV using subprocess
        wav_audio_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.wav")
        try:
            command = [
                ffmpeg.get_ffmpeg_exe(),
                '-i', local_temp_file,
                '-vn',  # No video
                '-acodec', 'pcm_s16le',  # PCM 16-bit little-endian
                '-ar', '16000',  # 16 KHz sample rate
                '-ac', '1',  # Mono audio
                wav_audio_path
            ]
            subprocess.run(command, check=True, capture_output=True)
            if not os.path.exists(wav_audio_path) or os.path.getsize(wav_audio_path) == 0:
                raise Exception("FFmpeg conversion failed or resulted in empty file.")
            
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg conversion failed: {e.stdout.decode()} {e.stderr.decode()}")
            # Remove the "👀" reaction before sending the error
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[]) # Remove all reactions
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg error: {remove_e}")
            bot.send_message(message.chat.id,
                             "😓 Sorry, there was an issue converting your audio. The file might be corrupted or in an unsupported format. Please try again with a different file.")
            return

        except Exception as e:
            logging.error(f"FFmpeg conversion failed: {e}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[]) # Remove all reactions
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg general error: {remove_e}")
            bot.send_message(message.chat.id,
                             "😓 Sorry, your file cannot be converted to the correct voice recognition format. Please ensure it's a standard audio/video file.")
            return

        # NEW: Transcribe using FasterWhisper
        if WHISPER_MODEL is None:
            bot.send_message(message.chat.id, "❌ Speech recognition model is not loaded. Please contact support.")
            return

        media_lang_name = user_media_language_settings[uid] # Get the language name
        media_lang_code = get_lang_code(media_lang_name) # Convert to language code
        
        if not media_lang_code:
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[]) # Remove all reactions
            except Exception as remove_e:
                logging.error(f"Error removing reaction on language code error: {remove_e}")
            bot.send_message(message.chat.id,
                             f"❌ The language *{media_lang_name}* does not have a valid code for transcription. Please re-select the language using /media_language.")
            return

        # Pass the language code to the transcribe function
        transcription = transcribe_audio_with_faster_whisper(wav_audio_path, media_lang_code) or ""
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

        # ─── Remove the "👀" reaction before sending the result ──────────────────────────
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[]) # Remove all reactions
        except Exception as e:
            logging.error(f"Error removing reaction before sending result: {e}")

        # Send transcription result
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
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[]) # Remove all reactions
        except Exception as remove_e:
            logging.error(f"Error removing reaction on general processing error: {remove_e}")
        bot.send_message(message.chat.id,
                         "😓 Sorry, an error occurred during transcription. The audio might be unclear or very short. Please try again or with a different file.")
    finally:
        # Stop the typing indicator
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]

        # Clean up the initial downloaded file and WAV file
        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)
            logging.info(f"Cleaned up {local_temp_file}")
        if wav_audio_path and os.path.exists(wav_audio_path):
            os.remove(wav_audio_path)
            logging.info(f"Cleaned up {wav_audio_path}")


# --- Language Selection and Saving ---
# This LANGUAGES list is for Media Transcriber Bot's language selection
LANGUAGES = [
    {"name": "English", "flag": "🇬🇧", "code": "en"}, # Changed to 'en' for Whisper
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
    {"name": "Filipino", "flag": "🇵🇭", "code": "tl"}, # Filipino code is 'tl' for Whisper/Tesseract
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
    {"name": "Somali", "flag": "🇸🇴", "code": "so"}, # Added Somali for consistency
]


def get_lang_code(lang_name):
    for lang in LANGUAGES:
        if lang['name'].lower() == lang_name.lower():
            return lang['code']
    return None

def generate_language_keyboard(callback_prefix, message_id=None):
    markup = InlineKeyboardMarkup(row_width=3) # Changed to row_width=3 for 3 buttons per row
    buttons = []
    for lang in LANGUAGES: # Use the main LANGUAGES list for transcription/translation settings
        cb_data = f"{callback_prefix}|{lang['name']}"
        if message_id is not None:
            cb_data += f"|{message_id}"
        buttons.append(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb_data))
    
    # Sort the buttons to ensure popular languages come first
    # This assumes LANGUAGES is already ordered by popularity. If not, you'd need a custom sort.
    # For now, relying on the manually ordered list.
    
    # Add buttons in chunks of 3
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    
    return markup

# --- NEW: Language and Voice selection for Text-to-Speech ---
def make_tts_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3) # 3 buttons per row
    buttons = []
    for lang_name in TTS_VOICES_BY_LANGUAGE.keys():
        buttons.append(InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}"))
    
    # Add buttons in chunks of 3
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
    update_user_activity(user_id)
    # --- NEW: Check subscription for TTS command ---
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    # --- End NEW check ---

    # --- MODIFIED: On TTS command, set TTS mode to active but don't set a voice yet ---
    user_tts_mode[user_id] = None # User has initiated TTS, but no voice chosen yet
    # --- END MODIFIED ---

    bot.send_message(message.chat.id, "🎙️ Choose a language for text-to-speech:", reply_markup=make_tts_language_keyboard())

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    # --- NEW: Check subscription for callback queries related to TTS ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

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
    update_user_activity(uid)
    # --- NEW: Check subscription for callback queries related to TTS ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    _, voice = call.data.split("|", 1)
    tts_users[uid] = voice
    save_tts_users()
    
    # --- MODIFIED: Store the chosen voice in user_tts_mode to indicate readiness ---
    user_tts_mode[uid] = voice 
    # --- END MODIFIED ---

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
    update_user_activity(uid)
    # --- NEW: Check subscription for callback queries related to TTS ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    # --- MODIFIED: When going back, reset user_tts_mode as voice is no longer selected ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🎙️ Choose a language for text-to-speech:",
        reply_markup=make_tts_language_keyboard()
    )
    bot.answer_callback_query(call.id)

# ====== TEXT-TO-SPEECH SYNTHESIS FUNCTION ======
async def synth_and_send_tts(chat_id, user_id, text):
    voice = get_tts_user_voice(user_id) # This will get the saved voice or default
    filename = os.path.join(DOWNLOAD_DIR, f"tts_{user_id}_{uuid.uuid4()}.mp3") # Use DOWNLOAD_DIR for consistency

    # Start recording indicator for TTS
    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(chat_id, stop_recording))
    recording_thread.daemon = True
    recording_thread.start()
    
    try:
        mss = MSSpeech()
        await mss.set_voice(voice)
        await mss.set_rate(0) # Default rate
        await mss.set_pitch(0) # Default pitch
        await mss.set_volume(1.0) # Default volume
        
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
        stop_recording.set() # Stop recording indicator
        if os.path.exists(filename):
            os.remove(filename) # Clean up the audio file
# --- End of NEW TTS functions ---


@bot.message_handler(commands=['language'])
def select_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    # --- NEW: Check subscription for language command ---
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF on language command ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

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
    # --- NEW: Check subscription for callback queries related to language settings ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF when setting language ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

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
    # --- NEW: Check subscription for media language command ---
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF on media_language command ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "Please choose the language of the audio files that you need me to transcribe, or the text in images for OCR. This helps ensure accurate results.",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_media_lang|"))
def callback_set_media_language(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    # --- NEW: Check subscription for callback queries related to media language settings ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF when setting media language ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

    _, lang = call.data.split("|", 1)
    user_media_language_settings[uid] = lang
    save_user_media_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ The transcription/OCR language for your media is set to: **{lang}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Media language set to {lang}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    # --- NEW: Check subscription for translate button ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF when using translate button ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription or extracted text found for this message.")
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
    # --- NEW: Check subscription for summarize button ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF when using summarize button ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription or extracted text found for this message.")
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
    # --- NEW: Check subscription for translate_to callback ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF when using translate_to callback ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

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
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription or extracted text found for this message to translate. Please use the inline buttons on the output.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("summarize_in|"))
def callback_summarize_in(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    # --- NEW: Check subscription for summarize_in callback ---
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id) # Answer the callback to remove the loading state
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF when using summarize_in callback ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

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
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription or extracted text found for this message to summarize. Please use the inline buttons on the output.")
    bot.answer_callback_query(call.id)

def do_translate_with_saved_lang(message, uid, lang, message_id):
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No text available for this specific message to translate.")
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
        bot.send_message(message.chat.id, "❌ No text available for this specific message to summarize.")
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
    # --- NEW: Check subscription for translate command ---
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF on translate command ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription/extracted text message to translate it.")

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
    # --- NEW: Check subscription for summarize command ---
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF on summarize command ---
    user_tts_mode[uid] = None
    # --- END MODIFIED ---

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription/extracted text message to summarize it.")

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

# --- NEW: Function to transcribe audio using FasterWhisper ---
def transcribe_audio_with_faster_whisper(audio_path: str, lang_code: str) -> str | None:
    if WHISPER_MODEL is None:
        logging.error("Whisper model is not loaded. Cannot transcribe.")
        return None

    try:
        # FasterWhisper processes audio in chunks internally.
        # We pass the WAV file path directly and specify the language.
        segments, info = WHISPER_MODEL.transcribe(audio_path, language=lang_code)
        
        full_transcription = []
        for segment in segments:
            full_transcription.append(segment.text)
        
        # Log detected language if different from specified, or for debugging
        if info.language != lang_code:
            logging.info(f"Whisper detected language: {info.language} (specified: {lang_code})")

        return " ".join(full_transcription)

    except Exception as e:
        logging.error(f"FasterWhisper transcription error: {e}")
        return None

# --- Memory Cleanup Function ---
def cleanup_old_data():
    """Cleans up user_transcriptions and user_memory older than 7 days."""
    seven_days_ago = datetime.now() - timedelta(days=7)

    keys_to_delete_transcriptions = []
    for user_id, transcriptions in user_transcriptions.items():
        if user_id in user_data:
            last_activity = datetime.fromisoformat(user_data[user_id])
            if last_activity < seven_days_ago:
                keys_to_delete_transcriptions.append(user_id)
        else:
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
    
    # --- NEW: Also clean up TTS user preferences if user is inactive ---
    keys_to_delete_tts_users = []
    for user_id in tts_users:
        if user_id in user_data:
            last_activity = datetime.fromisoformat(user_data[user_id])
            if last_activity < seven_days_ago:
                keys_to_delete_tts_users.append(user_id)
        else: # If user_id is not in main user_data, they are inactive
            keys_to_delete_tts_users.append(user_id)
            
    for user_id in keys_to_delete_tts_users:
        if user_id in tts_users:
            del tts_users[user_id]
            # Also clear the TTS mode for this user if they were in it
            # This logic might need refinement if you want to keep TTS preferences indefinitely
            # but for now, it cleans up if the user is generally inactive.
            if user_id in user_tts_mode:
                del user_tts_mode[user_id] 
            logging.info(f"Cleaned up old TTS preferences for user {user_id}")
    save_tts_users() # Save updated TTS user data
    # --- End of NEW cleanup ---

    threading.Timer(24 * 60 * 60, cleanup_old_data).start()  # Run every 24 hours


# --- NEW: Handle all text messages for TTS after command selection ---
@bot.message_handler(func=lambda message: message.content_type == 'text' and not message.text.startswith('/'))
def handle_text_for_tts_or_fallback(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    # --- NEW: Check subscription for all text messages ---
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    # --- End NEW check ---

    # --- MODIFIED: Check if a voice is set in user_tts_mode (it will be the voice name if set) ---
    if user_tts_mode.get(uid): # If user has a voice selected for TTS
        threading.Thread(target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))).start()
        # Do NOT set user_tts_mode to False here. Keep the chosen voice active.
    elif uid in tts_users: # User has a voice saved, but maybe didn't start with /text_to_speech
        user_tts_mode[uid] = tts_users[uid] # Reactivate TTS mode with the saved voice
        threading.Thread(target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))).start()
    else: # User hasn't selected a voice yet for TTS or sent a command to initiate TTS
        bot.send_message(message.chat.id, "I only transcribe voice messages, audio, video, or extract text from images. To convert text to speech, use the /text_to_speech command first.")
    # --- END MODIFIED ---

@bot.message_handler(func=lambda m: True, content_types=['sticker', 'document'])
def fallback_non_text_or_media(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    # --- NEW: Check subscription for other content types ---
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    # --- End NEW check ---

    # --- MODIFIED: Ensure TTS mode is OFF when a non-text/non-media message is sent ---
    user_tts_mode[uid] = None # Reset to None
    # --- END MODIFIED ---
    bot.send_message(message.chat.id, "Please send only voice messages, audio, video, or images for transcription/OCR, or use `/text_to_speech` for text to speech.")

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    # 1) Health-check (GET or HEAD) → return 200 OK
    if request.method in ("GET", "HEAD"):
        return "OK", 200

    # 2) Telegram webhook (POST with JSON)
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

if __name__ == "__main__":
    set_bot_info()
    cleanup_old_data()  # Start the cleanup timer
    set_webhook_on_startup()  # Set webhook when the application starts
    # Ensure Flask app runs on the port specified by Render (usually 8080)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

