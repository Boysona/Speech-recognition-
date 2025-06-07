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

# --- NEW: Import FasterWhisper for faster speech-to-text ---
from faster_whisper import WhisperModel

# --- NEW: Import MSSpeech for Text-to-Speech ---
from msspeech import MSSpeech, MSSpeechError

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BOT CONFIGURATION (Using Media Transcriber Bot's Token and Webhook) ---
TOKEN = "7790991731:AAH4rt8He_PABDa28xgcY3dIQwmtuQD-qiM"  # Bedel halkan haddii lagaa siiyo token kale
ADMIN_ID = 5978150981  # Bedel halkan haddii lagaa siiyo Admin ID kale
# Webhook URL - Bedel halkan haddii lagaa siiyo URL kale
WEBHOOK_URL = "https://speech-recognition-9cyh.onrender.com"

# --- REQUIRED CHANNEL CONFIGURATION ---
REQUIRED_CHANNEL = "@transcriberbo"  # Halkan gali channel-kaaga

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

# --- TTS User settings and Voices ---
tts_users_db = 'tts_users.json'  # Separate DB for TTS user preferences
tts_users = {}
if os.path.exists(tts_users_db):
    try:
        with open(tts_users_db, "r") as f:
            tts_users = json.load(f)
    except json.JSONDecodeError:
        tts_users = {}

# --- User state for Text-to-Speech input mode ---
# {user_id: "en-US-AriaNeural" (chosen voice) or None (not in TTS mode)}
user_tts_mode = {}

# Group voices by language for better organization
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
        "es-DO-RamonaNeural", "es-DO-AntonioNeural"
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
    "Somali 🇸🇴": [  # Somali voices
        "so-SO-UbaxNeural", "so-SO-MuuseNeural"
    ],
}

# --- Load the FasterWhisper model once at startup ---
try:
    WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    logging.info("FasterWhisper 'small' model loaded successfully.")
except Exception as e:
    logging.error(f"Failed to load FasterWhisper model: {e}")
    WHISPER_MODEL = None  # Set to None if loading fails

def save_user_data():
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)

def save_user_language_settings():
    with open(user_language_settings_file, 'w') as f:
        json.dump(user_language_settings, f, indent=4)

# --- NEW: Save TTS user settings ---
def save_tts_users():
    with open(tts_users_db, "w") as f:
        json.dump(tts_users, f, indent=2)

def get_tts_user_voice(uid):
    return tts_users.get(str(uid), "en-US-AriaNeural")

# In-memory chat history and transcription store
user_memory = {}
user_transcriptions = {}
processing_message_ids = {}  # For typing action tracking

# Statistics counters (global variables)
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"  # Bedel halkan haddii lagaa siiyo Gemini API key kale

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

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # Nigerian: 20MB
admin_state = {}

def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "👋Helitaanka fariin soo dhawayn iyo xog"),
        telebot.types.BotCommand("status", "📊Eeg istatistikada bot-ka"),
        telebot.types.BotCommand("language", "🌐Beddel luqadda tarjumaada/kooban"),
        # /media_language baabi'iyey sababtoo ah AAD ayaa hadda la adeegsanayaa
        telebot.types.BotCommand("text_to_speech", "🗣️Beddel qoraal ku hadal"),
    ]
    bot.set_my_commands(commands)

    bot.set_my_short_description(
        "Miyaad haysaa warbaahin? Bot-kan wuxuu si toos ah u turjumayaa, koobãnayaa, lana taliyayaa luqadda!"
    )

    bot.set_my_description(
        """Bot-kan wuxuu si dhaqso leh u qorayaa, koobanayaa, turjumayaa wicitaanno cod ah, audio iyo video, isla markaana wuxuu u beddelayaa qoraalka cod!
     🔥Ku raaxayso adeeg lacag la’aan ah oo bilow hadda!👌🏻"""
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
    Si joogto ah u cusboonaysii fariinta fahantaynta waqti-badheedh, tusaya maalmood, saacado, daqiiqado iyo ilbiriqsiyo.
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
                f"{days} maalmood, {hours:02d} saacadood, {minutes:02d} daqiiqado, {seconds:02d} ilbiriqsi"
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
        return True  # Haddii channel aan la cayimin, markaas wuu ogol yahay
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id} in {REQUIRED_CHANNEL}: {e}")
        return False

def send_subscription_message(chat_id):
    if not REQUIRED_CHANNEL:
        return
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Guji halkan si aad u biirto channel-ka", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")
    )
    bot.send_message(
        chat_id,
        "😓Waan ka xunnahay …\n🔰 Marka hore ku soo biir channel-ka @transcriberbo si aad u isticmaasho bot-kan.",
        reply_markup=markup,
        disable_web_page_preview=True
    )

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    # Ku dar user_data marka hore
    if user_id not in user_data:
        user_data[user_id] = datetime.now().isoformat()
        save_user_data()

    # Hubi TTS mode
    user_tts_mode[user_id] = None

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "Admin Panel iyo Uptime (si joogto ah u cusboonaysiinayo)...", reply_markup=keyboard)

        with admin_uptime_lock:
            if admin_uptime_message.get(ADMIN_ID) and admin_uptime_message[ADMIN_ID].get('thread') and admin_uptime_message[ADMIN_ID]['thread'].is_alive():
                pass

            admin_uptime_message[ADMIN_ID] = {'message_id': sent_message.message_id, 'chat_id': message.chat.id}
            uptime_thread = threading.Thread(target=update_uptime_message, args=(message.chat.id, sent_message.message_id))
            uptime_thread.daemon = True
            uptime_thread.start()
            admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread

    else:
        # Hubi subscription-ka user-ka
        if not check_subscription(message.from_user.id):
            send_subscription_message(message.chat.id)
            return

        display_name = message.from_user.first_name or (f"@{message.from_user.username}" if message.from_user.username else "user")
        bot.send_message(
            message.chat.id,
            f"""👋🏻 Waad salaaman tahay! {display_name},
Waxaan ahay Media To Text Bot. Waxaan kaa caawinayaa waqti badbaadin adoo si toos ah u qoraya, koobanaya, turjumaya farriimaha codka, audio, iyo video-ga.
Sidoo kale, waxaan qoraalkaaga u beddeli karaa cod!
Sii fariinta ama u soo dir fariin si aan kuu caawiyo.
"""
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None

    help_text = (
        """ℹ️ Sida loo isticmaalo bot-kan:

Bot-kan wuxuu si toos ah u qorayaa, koobãnayaa, turjumayaa farriimaha codka, audio, iyo video-ga, sidoo kale wuxuu qoraalkaa u beddelayaa cod luqado kala duwan.

1.  **Soo dir File si loo Qoro:**
    * Dir farriin cod ah (voice message), file audio, ama video note, ama file video (e.g. .mp4).
    * Hadda **looma baahna** in aad doorato luqadda ka hor—Automatic Language Detection ayaa shaqaynaya.
    * Bot-ku wuxuu kuu soo celin doonaa qoraalka. Haddii qoraalka aad u dheer yahay, wuxuu u diro doonaa file qoraal (.txt).
    * Marka aad hesho qoraalka, waxaa kuu soo muuqan doona batoonno “Translate” ama “Summarize”.

2.  **Beddel Qoraal ku hadal:**
    * Isticmaal amarka `/text_to_speech` si aad u doorato luqad iyo cod.
    * Kadib, dir qoraalkaaga, bot-ku wuxuu kuu soo celin doonaa file cod ah.

3.  **Amarka:**
    * `/start`: Soo hel fariin soo dhawayn iyo macluumaad ku saabsan bot-ka. (Admin-ku wuxuu arkaa panel-ka uptime ee soconaya).
    * `/status`: Daawo istatistikada bot-ka oo faahfaahsan.
    * `/help`: Tus tilmaamaha isticmaalka bot-ka.
    * `/language`: Beddel luqadda aad rabto in lagu turjumo ama lagu koobo qoraalka.
    * (Amarkan `/media_language` waan ka saarnay, AAD ayaa hadda qabanaya xulashada luqadda warbaahinta).
    * `/text_to_speech`: Door luqad iyo cod si loo beddelo qoraal ku cod.

Ku raaxayso qorista, turjumida, koobida, iyo cod-beddelida qoraalka si dhakhso ah!👌🏻
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

    user_tts_mode[user_id] = None

    privacy_text = (
        """**Ogeysiis Asturnaanta**

Asturnaantaada waa muhiim. Waxaan kuu sharxeynaa sida bot-ku u maareeyo xogtaada si faahfaahsan:

1.  **Xogta aan ka shaqeyno & Nolosheeda:**
    * **Warbaahinta (Voice, Audio, Video):** Marka aad dirto warbaahin, si kumeel-gaar ah ayaan u soo dejineynaa si aan u turjunno. Si dhakhso ah ayaan uga tirtirnaa server-yada kadib marka turjumaadu dhammaato. Warbaahintaada ma keydinno.
    * **Qoraalka loo bedelayo cod (TTS):** Marka aad dirto qoraal si loo beddelo cod, waxaa loo diraa si kumeel-gaar ah API-yada, kadibna si kumeel-gaar ah ayaannu uga tirnaa server-keena. Codka la soo saaray sidoo kale waa kumeel-gaar oo wuu tirtirmayaa markuu codka kuugu soo baxo.
    * **Qoraalka la qoray (Transcriptions):** Qoraalka waxaa lagu hayaa xusuusta bot-ka muddo kooban. Tani waxay u oggolaanaysaa turjumid iyo koobid danbe. Qoraalkaan lama kaydin doono muddo dheer, waxaana si joogto ah u nadiifineynaa (tusaale, markay 7 maalmood dhaafto ama marka bot-ku dib u bilaabo).
    * **User IDs:** Telegram User ID-gaaga ayaan kaydinaynaa. Tani waxay noo oggolaanaysaa inaan xasuusano doorbidkaaga luqadda iyo isticmaalka guud ee bot-ka. ID-gaaga looma xiriirin doono xog gaar ah oo dheeraad ah.
    * **Doorbidka Luqadda & Codka:** Waxaan kaydinaynaa doorbidkaaga luqadda tarjumaadda/koobidda iyo doorbidka codka TTS. Tani waxay hubinaysaa inaadan mar walba mar kale dooran luqadda ama codka, taasoo ka dhigaysa isticmaalkaaga mid fudud.

2.  **Sida aan xogtaada u adeegsanno:**
    * Si aan kuugu adeegno adeegyada aasaasiga ah: turjumaadda, koobidda warbaahinta, iyo qoraal-ku-cod.
    * Si aan u wanaajino waxqabadka bot-ka iyo inaan helno aragti guud oo ku saabsan isticmaalayaasha (tusaale, wadarta guud ee faylashii la farsameeyey).
    * Si aan u hayno doorbidkaaga luqadda iyo codka across sessions-ka.

3.  **Sida aan u wadaagno xogtaada:**
    * Ma wadaagno xogtaada shakhsi ahaaneed, warbaahintaada, ama qoraalkaaga cid saddexaad la’aan.
    * Turjumaadda, koobidda, iyo qoraalka waxaa gacan ka geysanaya API-yada AI (Google Speech-to-Text, Gemini API, Microsoft Cognitive Services). Xogtaada waxay raacaysaa shuruucda asturnaanta ee adeegyadan, laakiin anaga si toos ah uma kaydinno xogtaada kadib markii ay ka soo noqoto adeegyadaas.

4.  **Haynta Xogta:**
    * **Warbaahinta & Codka la soo saaray:** Isla markiiba waan tirtirnaa ka dib marka la farsameeyo.
    * **Qoraalka la qoray:** Waxaa lagu hayaa xusuusta bot-ka muddo kooban (7 maalmood), kadib la nadiifiyo.
    * **User IDs & Doorbidka Luqadda/Codka:** Waxaa la haynayaa si loo sugo doorbidkaaga iyo istatistikada guud. Haddii aad rabto in doorbidkaaga la tirtiro, wuu isticmaalkaaga jooji ama la xiriir admin-ka bot-ka.

Markaad isticmaalayso bot-kan, waxaad aqbashay inaad ku raacdo shuruucda kor ku xusan. Haddii su’aalo ama walaac kaa hayaan asturnaantaada, fadlan la xiriir admin-ka bot-ka.
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

    user_tts_mode[user_id] = None

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
        "📊 Istatisikada Bot-ka\n\n"
        "🟢 **Bot-ka Wuxuu Shaqaynayaa**\n"
        f"⏱️ Wakhtigii soo dhawaynta: {days} maalmood, {hours} saacadood, {minutes} daqiiqado, {seconds} ilbiriqsi\n\n"
        "👥 Istatisikada Isticmaalayaasha\n"
        f"▫️ Isticmaalayaasha Maanta: {active_today}\n"
        f"▫️ Isticmaalayaasha Dhamaan: {len(user_data)}\n\n"
        "⚙️ Istatisikada Farsamaynta\n"
        f"▫️ Wadarta Faysh-kii la Farsameeyey: {total_files_processed}\n"
        f"▫️ Faylal Audio: {total_audio_files}\n"
        f"▫️ Farsamaynta Codadka (Voice Clips): {total_voice_clips}\n"
        f"▫️ Fiidyowyada: {total_videos}\n"
        f"⏱️ Wadarta Wakhtiga Farsamaynta: {proc_hours} saacadood {proc_minutes} daqiiqado {proc_seconds} ilbiriqsi\n\n"
        "⸻\n\n"
        "Mahadsanid adeegsigaaga! 🙌"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Wadarta isticmaalayaasha: {len(user_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Fadlan u dir fariinta broadcast-ka hadda:")

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
        f"Broadcast dhammeystirmay.\nGuuleystay: {success}\nKhaladaad: {fail}"
    )

# ────────────────────────────────────────────────────────────────────────────────────────────
#   M E D I A   H A N D L I N G  (voice, audio, video, video_note, document as video) AAD
# ────────────────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note', 'document'])
def handle_file(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    # Hubi subscription-ka user-ka
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Go’aami file_obj
    file_obj = None
    is_document_video = False
    if message.voice:
        file_obj = message.voice
    elif message.audio:
        file_obj = message.audio
    elif message.video:
        file_obj = message.video
    elif message.video_note:
        file_obj = message.video_note
    elif message.document:
        mime = message.document.mime_type or ""
        if mime.startswith("video/") or mime.startswith("audio/"):
            file_obj = message.document
            is_document_video = True
        else:
            bot.send_message(
                message.chat.id,
                "❌ File-ga aad dirtay ma aha format audio/video la taageerayo. Fadlan soo dir voice message, audio file, video note, ama video file (e.g. .mp4)."
            )
            return

    if not file_obj:
        bot.send_message(
            message.chat.id,
            "❌ Fadlan soo dir voice message, audio file, video note, ama video file kaliya."
        )
        return

    # Hubi size-ka faylka
    size = file_obj.file_size
    if size and size > FILE_SIZE_LIMIT:
        bot.send_message(message.chat.id, "😓 Waan ka xunnahay, file-ka aad soo dirtay aad buu u weyn yahay (ugu badnaan 20MB).")
        return

    # React “👀”
    try:
        bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[{'type': 'emoji', 'emoji': '👀'}]
        )
    except Exception as e:
        logging.error(f"Error setting reaction: {e}")

    # Start typing indicator
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing

    try:
        # Process the file in a separate thread
        threading.Thread(
            target=process_media_file,
            args=(message, stop_typing, is_document_video)
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
        bot.send_message(message.chat.id, "😓 Waan ka xunnahay, khalad lama filaan ah ayaa dhacay. Fadlan isku day markale.")

def process_media_file(message, stop_typing, is_document_video):
    """
    Download the media (voice/audio/video/document),
    convert it to WAV, run FasterWhisper transcription with Automatic Language Detection,
    and send back the result.
    """
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time

    uid = str(message.from_user.id)

    # Go’aami file_obj
    if message.voice:
        file_obj = message.voice
    elif message.audio:
        file_obj = message.audio
    elif message.video:
        file_obj = message.video
    elif message.video_note:
        file_obj = message.video_note
    else:  # is_document_video == True
        file_obj = message.document

    local_temp_file = None
    wav_audio_path = None

    try:
        info = bot.get_file(file_obj.file_id)

        # Decide extension:
        if message.voice or message.video_note:
            file_extension = ".ogg"
        elif message.document:
            _, ext = os.path.splitext(message.document.file_name or info.file_path)
            file_extension = ext if ext else os.path.splitext(info.file_path)[1]
        else:
            file_extension = os.path.splitext(info.file_path)[1]

        # Download file to temporary location
        local_temp_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{file_extension}")
        data = bot.download_file(info.file_path)
        with open(local_temp_file, 'wb') as f:
            f.write(data)

        processing_start_time = datetime.now()

        # Convert to 16kHz mono WAV
        wav_audio_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.wav")
        try:
            command = [
                ffmpeg.get_ffmpeg_exe(),
                '-i', local_temp_file,
                '-vn',
                '-acodec', 'pcm_s16le',
                '-ar', '16000',
                '-ac', '1',
                wav_audio_path
            ]
            subprocess.run(command, check=True, capture_output=True)
            if not os.path.exists(wav_audio_path) or os.path.getsize(wav_audio_path) == 0:
                raise Exception("FFmpeg conversion failed or resulted in empty file.")
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg conversion failed: {e.stdout.decode()} {e.stderr.decode()}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg error: {remove_e}")
            bot.send_message(
                message.chat.id,
                "😓 Waan ka xunnahay, waxaa dhacay cilad markii la badaleynayey faylka codka ama video-ga. Fadlan isku day mar kale."
            )
            return
        except Exception as e:
            logging.error(f"FFmpeg conversion failed: {e}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg general error: {remove_e}")
            bot.send_message(
                message.chat.id,
                "😓 Waan ka xunnahay, faylkaaga lama beddelin karo qaabka saxda ah ee aqoonsiga codka. Fadlan hubi in uu yahay fayl audio/video standard ah."
            )
            return

        # --- Transcribe using FasterWhisper with Automatic Language Detection ---
        if WHISPER_MODEL is None:
            bot.send_message(message.chat.id, "❌ Moodel-ka Speech Recognition lama soo dejin. Fadlan la xiriir taageerada.")
            return

        # Halkan waxaan u oggolaaneynaa FasterWhisper in uu iskii u ogaado luqadda
        try:
            segments, info = WHISPER_MODEL.transcribe(wav_audio_path, language=None)
        except Exception as e:
            logging.error(f"FasterWhisper transcription error: {e}")
            bot.send_message(message.chat.id, "😓 Waan ka xunnahay, cilad ayaa ka dhacday markii la qorayey codka.")
            return

        # Isku keen qoraalka oo dhan
        full_transcription = " ".join([segment.text for segment in segments])
        detected_lang = info.language  # Tani waa code-ka luqadda la ogaaday (tusaale: "en", "ar", "so", iwm)

        # Save transcription
        user_transcriptions.setdefault(uid, {})[message.message_id] = full_transcription

        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        else:
            total_videos += 1

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        total_processing_time += processing_time

        # Build inline buttons for Translate / Summarize
        buttons = InlineKeyboardMarkup()
        buttons.add(
            InlineKeyboardButton("Translate", callback_data=f"btn_translate|{message.message_id}|{detected_lang}"),
            InlineKeyboardButton("Summarize", callback_data=f"btn_summarize|{message.message_id}|{detected_lang}")
        )

        # Remove the "👀" reaction
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as e:
            logging.error(f"Error removing reaction before sending result: {e}")

        # Send transcription (short ama long)
        if len(full_transcription) > 4000:
            fn = 'transcription.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(full_transcription)
            bot.send_chat_action(message.chat.id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(
                    message.chat.id,
                    doc,
                    reply_to_message_id=message.message_id,
                    reply_markup=buttons,
                    caption=f"Turjumaad la qoray (Luqad la ogaaday: {detected_lang}). Riix batoonka hoose si aad u turjunto ama u koobto."
                )
            os.remove(fn)
        else:
            bot.reply_to(
                message,
                f"{full_transcription}\n\n(Luqad la ogaaday: {detected_lang})",
                reply_markup=buttons
            )

    except Exception as e:
        logging.error(f"Error processing file for user {uid}: {e}")
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on general processing error: {remove_e}")
        bot.send_message(
            message.chat.id,
            """😓𝗪𝗮𝗮𝗻 𝗸𝗮 𝗵𝗲𝘂𝗺𝗮𝗱𝗻𝗮𝗵𝗮𝗒𝗻𝗮, 𝗸𝗵𝗹𝗮𝗱 𝗹𝗲𝗵 𝗲𝗵𝗮 𝗼 𝗲𝘅𝗰𝗲𝗽𝘁𝗶𝗼𝗻 𝗮𝗮 𝗸𝗵𝗮𝗰𝗮𝗱 𝗹𝗮 𝘁𝗿𝗮𝗻𝘀𝗰𝗿𝗶𝗽𝘁𝗶𝗼𝗻.
Codka waxaa laga yaabaa inuu yahay mid qaylo leh ama si dhakhso leh loo hadlay.
Fadlan isku day mar kale ama soo dir file kale.
Hubi in faylka aad dirayso iyo luqadda la ogaaday ay is waafaqsan yihiin — haddii kale, cilad ayaa dhici karta."""
        )
    finally:
        # Jooji typing indicator
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]

        # Nadiifi faylasha kumeel gaar ah
        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)
            logging.info(f"Nadiifiyey {local_temp_file}")
        if wav_audio_path and os.path.exists(wav_audio_path):
            os.remove(wav_audio_path)
            logging.info(f"Nadiifiyey {wav_audio_path}")

# --- Language Selection for Translate/Summarize (Target Language kaliya) ---
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
    {"name": "Somali", "flag": "🇸🇴", "code": "so"},
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
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

# --- NEW: Language and Voice selection for Text-to-Speech ---
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
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    bot.send_message(message.chat.id, "🎙️ Door luqad si loo beddelo qoraalka cod:", reply_markup=make_tts_language_keyboard())

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, lang_name = call.data.split("|", 1)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🎙️ Door codka luqadda: {lang_name}",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, voice = call.data.split("|", 1)
    tts_users[uid] = voice
    save_tts_users()

    user_tts_mode[uid] = voice

    bot.answer_callback_query(call.id, f"✔️ Codka waa la badalay: {voice}")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🔊 Hadda isticmaalaya: *{voice}*. Waxaad bilaabi kartaa inaad qoraal dirto si loo beddelo cod.",
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🎙️ Door luqad si loo beddelo qoraalka cod:",
        reply_markup=make_tts_language_keyboard()
    )
    bot.answer_callback_query(call.id)

async def synth_and_send_tts(chat_id, user_id, text):
    voice = get_tts_user_voice(user_id)
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
            bot.send_message(chat_id, "❌ MP3 lama soo saarin ama waa madhan. Fadlan isku day mar kale.")
            return

        with open(filename, "rb") as f:
            bot.send_audio(chat_id, f, caption=f"🎤 Codka: {voice}")
    except MSSpeechError as e:
        logging.error(f"TTS error: {e}")
        bot.send_message(chat_id, f"❌ Cilad ayaa ka dhacday markuu codka synthesizing-ka: {e}")
    except Exception as e:
        logging.exception("TTS error")
        bot.send_message(chat_id, "❌ Wax khalad ah ayaa ka dhacay text-to-speech. Fadlan isku day mar kale.")
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            os.remove(filename)

@bot.message_handler(commands=['language'])
def select_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None

    markup = generate_language_keyboard("set_lang")
    bot.send_message(
        message.chat.id,
        "Fadlan dooro luqadda aad rabto in qoraalka lagu turjumo ama lagu koobo:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_lang|"))
def callback_set_language(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None

    _, lang = call.data.split("|", 1)
    user_language_settings[uid] = lang
    save_user_language_settings()
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Luqadda tarjumaadda/koobidda waa la cayimay: **{lang}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Luqadda waa la cayimay: {lang}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None

    _, message_id_str, src_lang = call.data.split("|", 2)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ Ma jiro qoraal la turjumayo.")
        return

    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        bot.answer_callback_query(call.id, "Turjumaya luqadda aad dooratay...")
        threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, preferred_lang, message_id, src_lang)).start()
    else:
        markup = generate_language_keyboard("translate_to", message_id)
        # Waxaan la socodsiin doonaa luqadda la ogaaday
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"Luqadda asalka ah la ogaaday: {src_lang}. Fadlan dooro luqadda aad rabto inaad u turjunto:",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_summarize|"))
def button_summarize_handler(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None

    _, message_id_str, src_lang = call.data.split("|", 2)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ Ma jiro qoraal la koobiyeeyo.")
        return

    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        bot.answer_callback_query(call.id, "Koobinayaa luqadda aad dooratay...")
        threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, preferred_lang, message_id, src_lang)).start()
    else:
        markup = generate_language_keyboard("summarize_in", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"Luqadda asalka ah la ogaaday: {src_lang}. Fadlan dooro luqadda koobidda:",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("translate_to|"))
def callback_translate_to(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None

    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    user_language_settings[uid] = lang
    save_user_language_settings()

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Turjumayaa luqadda: **{lang}**...",
        parse_mode="Markdown"
    )

    # Halkan waxaan ka soo qaadaneynaa luqadda asalka (waa lama huraan in aan u gudbino), laakiin 
    # bot-ku si toos ah ayuu u aqoonsan yahay luqadda asalka marka hore. Waxaan ku qasbaynaa turquoise call string.
    # Haddaba, looma baahna src_lang oo kale, maxaa yeelay full transcription-ka iyo luqadda la ogaaday way kaydsan yihiin 
    # user_transcriptions iyo callback data-da, laakiin annagaa qaadanay.
    # Ku shubo luqadda asalka si loogu isticmaalo Gemini: (marka loo baahdo, Gemini wuu ogan karaa isha luqad kale).
    if message_id:
        # Qoraalka asalka ah iyo luqadda asalka aan u gudbino Gemini
        threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, message_id, None)).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id, None)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ Ma jiro qoraal la turjumayo. Fadlan isticmaal button-ka inline ee qoraalka.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("summarize_in|"))
def callback_summarize_in(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None

    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    user_language_settings[uid] = lang
    save_user_language_settings()

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Koobinayaa luqadda: **{lang}**...",
        parse_mode="Markdown"
    )

    if message_id:
        threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, message_id, None)).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id, None)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ Ma jiro qoraal la koobiyeeyo. Fadlan isticmaal button-ka inline ee qoraalka.")
    bot.answer_callback_query(call.id)

def do_translate_with_saved_lang(message, uid, target_lang, message_id, src_lang=None):
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ Ma jiro qoraal lasoo qorey si loo turjumo.")
        return

    # Haddii Gemini isha luqadda u baahan yahay, wuxuu u diri karaa 
    if src_lang:
        prompt = f"Translate the following text from {src_lang} into {target_lang}. Provide only the translated text, with no additional notes:\n\n{original}"
    else:
        prompt = f"Translate the following text into {target_lang}. Provide only the translated text, with no additional notes:\n\n{original}"

    bot.send_chat_action(message.chat.id, 'typing')
    translated = ask_gemini(uid, prompt)

    if translated.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Waan ka xunnahay, cilad ayaa ka dhacday turjumaadda: {translated}. Fadlan isku day markale.")
        return

    if len(translated) > 4000:
        fn = 'translation.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(translated)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Tarjumid: {target_lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, translated, reply_to_message_id=message_id)

def do_summarize_with_saved_lang(message, uid, target_lang, message_id, src_lang=None):
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ Ma jiro qoraal lasoo qorey si loo koobo.")
        return

    if src_lang:
        prompt = f"Summarize the following text from {src_lang} in {target_lang}. Provide only the summarized text, with no additional notes:\n\n{original}"
    else:
        prompt = f"Summarize the following text in {target_lang}. Provide only the summarized text, with no additional notes:\n\n{original}"

    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(uid, prompt)

    if summary.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Waan ka xunnahay, cilad ayaa ka dhacday koobidda: {summary}. Fadlan isku day markale.")
        return

    if len(summary) > 4000:
        fn = 'summary.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(summary)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Koobid: {target_lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, summary, reply_to_message_id=message_id)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Fadlan ku jawaab fariin qoraal ah si loo turjumo.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        threading.Thread(target=do_translate_with_saved_lang, args=(message, uid, preferred_lang, transcription_message_id, None)).start()
    else:
        markup = generate_language_keyboard("translate_to", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Fadlan dooro luqadda aad rabto inaad u turjunto:",
            reply_markup=markup
        )

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Fadlan ku jawaab fariin qoraal ah si loo koobo.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = user_language_settings.get(uid)
    if preferred_lang:
        threading.Thread(target=do_summarize_with_saved_lang, args=(message, uid, preferred_lang, transcription_message_id, None)).start()
    else:
        markup = generate_language_keyboard("summarize_in", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Fadlan dooro luqadda koobidda:",
            reply_markup=markup
        )

# --- Memory Cleanup Function ---
def cleanup_old_data():
    """Nadiifi user_transcriptions iyo user_memory ka weyn 7 maalmood."""
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
            logging.info(f"Nadiifiyey qoraalladii duugoonaa ee user {user_id}")

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
            logging.info(f"Nadiifiyey xusuustii duugoonaa ee user {user_id}")

    # --- Nadiifi TTS user preferences haddii user-ka hurdaa ---
    keys_to_delete_tts_users = []
    for user_id in tts_users:
        if user_id in user_data:
            last_activity = datetime.fromisoformat(user_data[user_id])
            if last_activity < seven_days_ago:
                keys_to_delete_tts_users.append(user_id)
        else:
            keys_to_delete_tts_users.append(user_id)

    for user_id in keys_to_delete_tts_users:
        if user_id in tts_users:
            del tts_users[user_id]
            if user_id in user_tts_mode:
                del user_tts_mode[user_id]
            logging.info(f"Nadiifiyey doorbidka TTS duugoonaa ee user {user_id}")
    save_tts_users()
    threading.Timer(24 * 60 * 60, cleanup_old_data).start()  # Mar walba 24 saacadood ku
    # Dhammaad nadiifinta

# --- Handle all text messages for TTS after command selection ---
@bot.message_handler(func=lambda message: message.content_type == 'text' and not message.text.startswith('/'))
def handle_text_for_tts_or_fallback(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    if user_tts_mode.get(uid):  # Haddii user-ku doortay cod TTS
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))
        ).start()
    elif uid in tts_users:  # User wuu lahaa cod la keydiyey
        user_tts_mode[uid] = tts_users[uid]
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))
        ).start()
    else:
        bot.send_message(
            message.chat.id,
            "Kaliya waxaan qoraal ka qorayaa warbaahinta. Haddii aad rabto qoraal ku hadal, adeegsado /text_to_speech marka hore."
        )

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document'])
def fallback_non_text_or_media(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    bot.send_message(
        message.chat.id,
        "Fadlan soo dir voice message, audio file, video note, ama video file si aan u qoro, ama adeegsado `/text_to_speech` qoraal ku hadal ah."
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
        return f"Webhook waxaa loo dejiyey: {WEBHOOK_URL}", 200
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")
        return f"Failed to set webhook: {e}", 500

@app.route("/delete_webhook", methods=["GET", "POST"])
def delete_webhook_route():
    try:
        bot.delete_webhook()
        return "Webhook waa la tirtiray.", 200
    except Exception as e:
        logging.error(f"Failed to delete webhook: {e}")
        return f"Failed to delete webhook: {e}", 500

def set_webhook_on_startup():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook si guul leh ayaa loogu dejiyey: {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook on startup: {e}")

if __name__ == "__main__":
    set_bot_info()
    cleanup_old_data()  # Bilow jadwal nadiifinta
    set_webhook_on_startup()  # Dej webhook markay app-ku bilaabato
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
