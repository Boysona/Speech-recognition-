import os
import uuid
import logging
import requests
import telebot
import json
from flask import Flask, request, abort
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import threading
import time
import io

# AUDIO PROCESSING
import speech_recognition as sr
import imageio_ffmpeg as ffmpeg
from pydub import AudioSegment
import subprocess

# TEXT-TO-SPEECH (MSSpeech)
from msspeech import MSSpeech, MSSpeechError
import asyncio

# ── FIREBASE ADMIN SDK ───────────────────────────────────────────────────────────
import firebase_admin
from firebase_admin import credentials, db

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ── BOT CONFIGURATION ───────────────────────────────────────────────────────────
# !!! TAXADAR: Furayaashan si toos ah ayaa loo muujiyay. Tan maaha mid ammaan ah.
#             Fadlan beddel furayaashan kuwii dhabta ahaa.
TOKEN = "7790991731:AAH4rt8He_PABDa28xgcY3dIQwmtuQD-qiM" # Beddel Token-kan
ADMIN_ID = 5978150981 # Beddel ID-gan haddii aad leedahay admin ID kale
WEBHOOK_URL = "https://speech-recognition-9cyh.onrender.com" # Beddel URL-kan si uu ula jaanqaado domain-kaaga Render
REQUIRED_CHANNEL = "@transcriberbo" # Beddel magaca channel-kaaga ama ka saar haddii aan loo baahnayn

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# ── DOWNLOAD DIRECTORY ──────────────────────────────────────────────────────────
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── FIREBASE INITIALIZATION (EMBEDDED SERVICE ACCOUNT) ──────────────────────────
# TAXADAR: Private key-gan si toos ah ayaa loo muujiyay. Tan maaha mid ammaan ah.
#          Haddii aad tan ku isticmaasho wax soo saar, waxaad halis galinaysaa furahaaga sirta ah.
# WAXYAABAHA LA SAXAY: Khabiijka Private Key-ga ayaa la sixi si sax ah.
SERVICE_ACCOUNT_JSON_STRING = '''
{
  "type": "service_account",
  "project_id": "tm-bot-data-base",
  "private_key_id": "76a77aa43f4a95250071108c0cdb119c40c7b724",
  "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCzzfBsd5R+oC6q\\nvnQ93Z5rIaFfp0AKQNnNgo4oRYAnhkg5ZkIPgAiPo5tK6zu3OlwmrpvoYDazQc5u\\nopvWQI8vjjalpYd6bOwD0VPd3eNcXxxS2MFBz1ekuug9FHauEjZTxdGv7Os89K3p\\ndLuUSLM0HfgJYYtM3NphLnSNXeytgCGN6Dh0nGsey8QmC5iJb7jlVCdQnbbWzMnl\\nLZXScSlfdLdbzmhRHmN2CfduTgyuSZKzywSQfKhXZnwUOm+LahbkpwkOPlag914W\\nbtP4xhBxWgdb79mdgpZDNlBVXb9i8AcoNDoeBaL3YPB3xgfSnGIrywv8MHitQAWF\\nhHZ8lGRbAgMBAAECggEAEJWXWPHYp6tPp/O5gLakdN4yZm0PSkLRXr7C150Sz9J3\\nbpqSJPGFbfZEYxnPtWaedvpBACMruHIClTrODpTVgCiTtXNPS9qoaZu7qr8GHbra\\nFRwlYH68PJdOx3TP6N4I1nDsa9fO73LkEvXvkqL4AH/JJS2y7baAiq6AappLXnSc\\ngaJmIMkOoAWpesNkHDde4UpBzMyiEwvXFjt8cvF9cJ6cD5IoXNra8szoSmbzi4/w\\nn2/myX8oR+lrm12RzB5IX3ZbbF80xVJHWwKrHg4AzF8sQbtOojjpR/sy43zAb5ap\\n4VEyGilsjBS6YGmrJNW2RtqFOgCDY9180pi/12xF+QKBgQD6UrSnrFmB7CIwJXjH\\nCX6JZ5Be/aqRdSGNjPmGBepOV5COhgXT5dPOEcRPTalhPmKETv7uwm7W2ydRbiZV\\n9Ouo0WD5C3n6EKAVdQKh7rb0nDaRFNISf1AHO0BOBbAizcDYZtP7ZG12PXIc7Nng\\n245zFovSoDDECVTPgum4zg33iQKBgQC34dNHPYkPkFfSKyB4mFYvy4cwjXWeRtf5\\nHEnxKVqeXXPqr4D35jQgBozUWWV6c5LT9ta723s5swENXfmKGrxwKUdsLvQ5s42V\\nVCO4xixvdyaG54A9nqMBZTW+m0ZAjGKnUfhys6hAe4LbM7xO5ORtDQokSDmqnZJJ\\n6xNrpi1fwwKBgQDwGxCpnEmms2b/o5G76MF07t+uHcbUCvQKIGTfRyE90AQakTdZ\\nzyNgj+4q4q2yVS31ID8vnY7qr+b+vA0dT3shuxLFTFzVpMoFHNAxVpWd4ntwcoFj6B\\n+5g5t32w2Qff+le1urwucGAGgF3KnO1IH5D9l2y6tWjRQNoWyU2CNDN1cQKBgEDi\\n6UEcT780Oikpmr2zU8Zt1XimkjiV2yRGfTkiVJ3K2bKY17OiggZDCXLtUMfS/J7Z\\n9Dg6DNOhfN+PPmUjZhdWGaPtNbUezPlCxZAwLgHvU6MLExO+sqOyXIE3wUOv6Hd3Z\\nKQigqOCINPbQ3MQiNRDeJxQCgIbaL4Lx9tpnu18tAoGBAIQhJbu/10hfl5s/lKK/\\nDHrglqZYmVsLtIaCs2UriFtZLalxOxL2gWxGb2HM0Frl2302huWOd3JRPRzzKFU3\\ngCpHfpt9hF9UeRBHR6c1wxhEe/2PuVSkS53eAvMBnGS8e8oMOooovdkN5rGKq24D\\nwW3HZk8oy8Fh96GFIAwiAKBP\\n-----END PRIVATE KEY-----\\n",
  "client_email": "firebase-adminsdk-fbsvc@tm-bot-data-base.iam.gserviceaccount.com",
  "client_id": "101243555308297344046",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40tm-bot-data-base.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}
'''

firebase_cert = credentials.Certificate(json.loads(SERVICE_ACCOUNT_JSON_STRING))

firebase_admin.initialize_app(firebase_cert, {
    'databaseURL': 'https://tm-bot-data-base-default-rtdb.firebaseio.com/' # Beddel URL-kan si uu ula jaanqaado database-kaaga
})

# ── RTDB PATH CONSTANTS ──────────────────────────────────────────────────────────
USERS_PATH = "users"
LANGUAGE_SETTINGS_PATH = "user_language_settings"
MEDIA_LANGUAGE_PATH = "user_media_language_settings"
TTS_USERS_PATH = "tts_users"

# ── IN-MEMORY STRUCTURES ─────────────────────────────────────────────────────────
user_memory = {}            # { user_id: [ {role, text}, ... ] }
user_transcriptions = {}    # { user_id: { message_id: transcription } }
processing_message_ids = {} # { chat_id: threading.Event() }

# Statistics (in memory)
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0
bot_start_time = datetime.now()

# TTS mode: whether next text message should be treated as TTS input
user_tts_mode = {}   # { user_id: True/False }

# MSSpeech / Gemini API Key
# !!! TAXADAR: Furahan si toos ah ayaa loo muujiyay. Tan maaha mid ammaan ah.
GEMINI_API_KEY = "AIzaSyAto78yGVZobO8wCE9ZW8Do2R8HA" # Beddel API key-gan

# Admin state for broadcast
admin_state = {}

# ── FIREBASE UTILITY FUNCTIONS ──────────────────────────────────────────────────
def firebase_set(path: str, key: str, value):
    """Set /<path>/<key> = value in RTDB."""
    ref = db.reference(f"/{path}/{key}")
    ref.set(value)

def firebase_get(path: str, key: str):
    """Get value at /<path>/<key> from RTDB. Returns None if absent."""
    ref = db.reference(f"/{path}/{key}")
    return ref.get()

def firebase_delete(path: str, key: str):
    """Delete node /<path>/<key> from RTDB."""
    ref = db.reference(f"/{path}/{key}")
    ref.delete()

def firebase_get_all_keys(path: str):
    """Return dict of all children under /<path>/. If none, return {}."""
    ref = db.reference(f"/{path}")
    data = ref.get()
    return data if isinstance(data, dict) else {}

# ── UPDATE USER ACTIVITY ─────────────────────────────────────────────────────────
def update_user_activity(user_id: str):
    """Write current ISO timestamp to /users/<user_id>."""
    firebase_set(USERS_PATH, user_id, datetime.now().isoformat())

def get_total_registered_users() -> int:
    """Return number of keys under /users/ node."""
    all_users = firebase_get_all_keys(USERS_PATH) or {}
    return len(all_users)

# ── LANGUAGES LIST FOR TRANSLATION/SUMMARIZATION ─────────────────────────────────
LANGUAGES = [
    {"name": "English", "flag": "🇬🇧", "code": "en-US"},
    {"name": "Arabic",  "flag": "🇸🇦", "code": "ar-SA"},
    {"name": "Spanish", "flag": "🇪🇸", "code": "es-ES"},
    {"name": "Hindi",   "flag": "🇮🇳", "code": "hi-IN"},
    {"name": "French",  "flag": "🇫🇷", "code": "fr-FR"},
    {"name": "German",  "flag": "🇩🇪", "code": "de-DE"},
    {"name": "Chinese", "flag": "🇨🇳", "code": "zh-CN"},
    {"name": "Japanese","flag": "🇯🇵","code": "ja-JP"},
    {"name": "Portuguese","flag": "🇵🇹","code": "pt-PT"},
    {"name": "Russian","flag": "🇷🇺","code": "ru-RU"},
    {"name": "Turkish","flag": "🇹🇷","code": "tr-TR"},
    {"name": "Korean","flag": "🇰🇷","code": "ko-KR"},
    {"name": "Italian","flag": "🇮🇹","code": "it-IT"},
    {"name": "Indonesian","flag": "🇮🇩","code": "id-ID"},
    {"name": "Vietnamese","flag": "🇻🇳","code": "vi-VN"},
    {"name": "Thai","flag": "🇹🇭","code": "th-TH"},
    {"name": "Dutch","flag": "🇳🇱","code": "nl-NL"},
    {"name": "Polish","flag": "🇵🇱","code": "pl-PL"},
    {"name": "Swedish","flag": "🇸🇪","code": "sv-SE"},
    {"name": "Filipino","flag": "🇵🇭","code": "fil-PH"},
    {"name": "Greek","flag": "🇬🇷","code": "el-GR"},
    {"name": "Hebrew","flag": "🇮🇱","code": "he-IL"},
    {"name": "Hungarian","flag": "🇭🇺","code": "hu-HU"},
    {"name": "Czech","flag": "🇨🇿","code": "cs-CZ"},
    {"name": "Danish","flag": "🇩🇰","code": "da-DK"},
    {"name": "Finnish","flag": "🇫🇮","code": "fi-FI"},
    {"name": "Norwegian","flag": "🇳🇴","code": "nb-NO"},
    {"name": "Romanian","flag": "🇷🇴","code": "ro-RO"},
    {"name": "Slovak","flag": "🇸🇰","code": "sk-SK"},
    {"name": "Ukrainian","flag": "🇺🇦","code": "uk-UA"},
    {"name": "Malay","flag": "🇲🇾","code": "ms-MY"},
    {"name": "Bengali","flag": "🇧🇩","code": "bn-BD"},
    {"name": "Tamil","flag": "🇮🇳","code": "ta-IN"},
    {"name": "Telugu","flag": "🇮🇳","code": "te-IN"},
    {"name": "Kannada","flag": "🇮🇳","code": "kn-IN"},
    {"name": "Malayalam","flag": "🇮🇳","code": "ml-IN"},
    {"name": "Gujarati","flag": "🇮🇳","code": "gu-IN"},
    {"name": "Marathi","flag": "🇮🇳","code": "mr-IN"},
    {"name": "Urdu","flag": "🇵🇰","code": "ur-PK"},
    {"name": "Nepali","flag": "🇳🇵","code": "ne-NP"},
    {"name": "Sinhala","flag": "🇱🇰","code": "si-LK"},
    {"name": "Khmer","flag": "🇰🇭","code": "km-KH"},
    {"name": "Lao","flag": "🇱🇦","code": "lo-LA"},
    {"name": "Burmese","flag": "🇲🇲","code": "my-MM"},
    {"name": "Georgian","flag": "🇬🇪","code": "ka-GE"},
    {"name": "Armenian","flag": "🇦🇲","code": "hy-AM"},
    {"name": "Azerbaijani","flag": "🇦🇿","code": "az-AZ"},
    {"name": "Kazakh","flag": "🇰🇿","code": "kk-KZ"},
    {"name": "Uzbek","flag": "🇺🇿","code": "uz-UZ"},
    {"name": "Kyrgyz","flag": "🇰🇬","code": "ky-KG"},
    {"name": "Tajik","flag": "🇹🇯","code": "tg-TJ"},
    {"name": "Turkmen","flag": "🇹🇲","code": "tk-TM"},
    {"name": "Mongolian","flag": "🇲🇳","code": "mn-MN"},
    {"name": "Estonian","flag": "🇪🇪","code": "et-EE"},
    {"name": "Latvian","flag": "🇱🇻","code": "lv-LV"},
    {"name": "Lithuanian","flag": "🇱🇹","code": "lt-LT"},
    {"name": "Afrikaans", "flag": "🇿🇦","code": "af-ZA"},
    {"name": "Albanian","flag": "🇦🇱","code": "sq-AL"},
    {"name": "Bosnian","flag": "🇧🇦","code": "bs-BA"},
    {"name": "Bulgarian","flag": "🇧🇬","code": "bg-BG"},
    {"name": "Catalan","flag": "🇪🇸","code": "ca-ES"},
    {"name": "Croatian","flag": "🇭🇷","code": "hr-HR"},
    {"name": "Galician","flag": "🇪🇸","code": "gl-ES"},
    {"name": "Icelandic","flag": "🇮🇸","code": "is-IS"},
    {"name": "Irish","flag": "🇮🇪","code": "ga-IE"},
    {"name": "Macedonian","flag": "🇲🇰","code": "mk-MK"},
    {"name": "Maltese","flag": "🇲🇹","code": "mt-MT"},
    {"name": "Serbian","flag": "🇷🇸","code": "sr-RS"},
    {"name": "Slovenian","flag": "🇸🇮","code": "sl-SI"},
    {"name": "Welsh","flag": "🏴","code": "cy-GB"},
    {"name": "Zulu","flag": "🇿🇦","code": "zu-ZA"},
    {"name": "Somali","flag": "🇸🇴","code": "so-SO"},
]

def get_lang_code(lang_name: str) -> str | None:
    for lang in LANGUAGES:
        if lang["name"].lower() == lang_name.lower():
            return lang["code"]
    return None

def generate_language_keyboard(callback_prefix: str, message_id: int = None) -> InlineKeyboardMarkup:
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

# ── TTS VOICES BY LANGUAGE ──────────────────────────────────────────────────────
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
        "es-CR-MariaNeural", "es-CR-JuanNeural", "es-DO-RamonaNeural", "es-DO-AntonioNeural"
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
    "Somali 🇸🇴": [
        "so-SO-UbaxNeural", "so-SO-MuuseNeural"
    ],
}

def make_tts_language_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang_name in TTS_VOICES_BY_LANGUAGE.keys():
        buttons.append(InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}"))
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

def make_tts_voice_keyboard_for_language(lang_name: str) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    voices = TTS_VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        markup.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    markup.add(InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_to_languages"))
    return markup

# ── TTS USER VOICE GET/SET ─────────────────────────────────────────────────────
def get_tts_user_voice(uid: str) -> str:
    """Fetch saved voice from /tts_users/<uid>, or return default."""
    saved = firebase_get(TTS_USERS_PATH, uid)
    return saved if saved else "en-US-AriaNeural"

def set_tts_user_voice(uid: str, voice: str):
    """Save chosen TTS voice under /tts_users/<uid>."""
    firebase_set(TTS_USERS_PATH, uid, voice)

# ── SUBSCRIPTION CHECK ──────────────────────────────────────────────────────────
def check_subscription(user_id: int) -> bool:
    """Return True if user_id is a member/admin/creator in REQUIRED_CHANNEL."""
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id}: {e}")
        return False

def send_subscription_message(chat_id: int):
    """Prompt user to join REQUIRED_CHANNEL."""
    if not REQUIRED_CHANNEL:
        return
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            "Click here to join the channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
        )
    )
    bot.send_message(
        chat_id,
        "🚫 This bot only works if you’ve joined our official channel. Please join to continue using the bot.",
        reply_markup=markup,
        disable_web_page_preview=True,
    )

# ── SET BOT COMMANDS & DESCRIPTIONS ────────────────────────────────────────────
def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "👋Get a welcome message and info"),
        telebot.types.BotCommand("status", "📊View Bot statistics"),
        telebot.types.BotCommand("language", "🌐Change preferred language for translate/summarize"),
        telebot.types.BotCommand("media_language", "📝Set language for media transcription"),
        telebot.types.BotCommand("text_to_speech", "🗣️Convert text to speech"),
    ]
    bot.set_my_commands(commands)
    bot.set_my_short_description(
        "Got media files? Let this free bot transcribe, summarize, and translate them in seconds!"
    )
    bot.set_my_description(
        """This bot quickly transcribes voice messages, audio files, and videos using advanced AI, and can also convert text into speech!
Also, it can convert your text into speech in various languages!

     🔥Enjoy free usage and start now!👌🏻"""
    )

# ── UPTIME THREAD ───────────────────────────────────────────────────────────────
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
                parse_mode="Markdown",
            )
            time.sleep(1)

        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break

# ── TYPING / RECORDING INDICATORS ──────────────────────────────────────────────
def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, "typing")
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

def keep_recording(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, "record_audio")
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
            break

# ── HANDLERS ────────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(message.chat.id, "Admin Panel and Uptime (updating live)...", reply_markup=keyboard)

        uptime_thread = threading.Thread(target=update_uptime_message, args=(message.chat.id, sent_message.message_id))
        uptime_thread.daemon = True
        uptime_thread.start()
    else:
        if not check_subscription(message.from_user.id):
            send_subscription_message(message.chat.id)
            return

        display_name = message.from_user.first_name or (f"@{message.from_user.username}" if message.from_user.username else "user")
        bot.send_message(
            message.chat.id,
            f"""👋🏻 Salom {display_name}!
I'm Media To Text Bot. I help you save time by transcribing and summarizing voice messages, audio messages, and video notes.
I can also convert your text into speech!
Simply send or forward a message to me.
""",
        )

@bot.message_handler(commands=["help"])
def help_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    help_text = (
        """ℹ️ How to use this bot:

This bot transcribes voice messages, audio files, and videos using advanced AI, and can also convert text to speech!

1.  **Send a File for Transcription:**
    * Send a voice message, audio file, or video to the bot.
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

@bot.message_handler(commands=["privacy"])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    privacy_text = (
        """**Privacy Notice**

Your privacy is paramount. Here's a transparent look at how this bot handles your data in real-time:

1.  **Data We Process & Its Lifecycle:**
    * **Media Files (Voice, Audio, Video):** When you send a media file, it's temporarily downloaded for ** immediate transcription**. Crucially, these files are **deleted instantly** from our servers once the transcription is complete. We do not store your media content.
    * **Text for Speech Synthesis:** When you send text for conversion to speech, it is processed to generate the audio and then **not stored**. The generated audio file is also temporary and deleted after sending.
    * **Transcriptions:** The text generated from your media is held **temporarily in the bot's memory** for a limited period. This allows for follow-up actions like translation or summarization. This data is not permanently stored on our servers and is cleared regularly (e.g., when new media is processed or the bot restarts, or after 7 days as per cleanup).
    * **User IDs:** Your Telegram User ID is stored. This helps us remember your language preferences and track basic, aggregated activity (like when you last used the bot) to improve service and understand overall usage patterns. This ID is not linked to any personal identifying information outside of Telegram.
    * **Language Preferences:** Your chosen languages for translations/summaries and media transcription are saved. Your chosen voice for text-to-speech is also saved. This ensures you don't need to re-select them for every interaction, making your experience smoother.

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
    * **User IDs and language/voice preferences:** Retained to support your settings and for anonymous usage statistics. If you wish to have your stored preferences removed, you can cease using the bot or contact the bot administrator for explicit data deletion.

By using this bot, you acknowledge and agree to the data practices outlined in this Privacy Notice.

Should you have any questions or concerns regarding your privacy, please feel free to contact the bot administrator.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=["status"])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    today = datetime.now().date()
    all_users = firebase_get_all_keys(USERS_PATH) or {}
    active_today = 0
    for uid, iso_ts in all_users.items():
        try:
            if datetime.fromisoformat(iso_ts).date() == today:
                active_today += 1
        except:
            pass

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
        f"▫️ Total Registered Users: {get_total_registered_users()}\n\n"
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
    bot.send_message(message.chat.id, f"Total registered users: {get_total_registered_users()}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = "awaiting_broadcast"
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == "awaiting_broadcast",
    content_types=["text", "photo", "video", "audio", "document"],
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    all_users = firebase_get_all_keys(USERS_PATH) or {}
    for uid in all_users.keys():
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid}: {e}")
            fail += 1
    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}",
    )

# ── MEDIA HANDLER ───────────────────────────────────────────────────────────────
FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB

def handle_file(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = False  # reset TTS mode

    chosen_media_lang = firebase_get(MEDIA_LANGUAGE_PATH, uid)
    if not chosen_media_lang:
        bot.send_message(
            message.chat.id,
            "⚠️ Please first select the language of the audio file using /media_language before sending the file.",
        )
        return

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(
            message.chat.id, "😓 Sorry, the file size you uploaded is too large (max allowed is 20MB)."
        )

    try:
        bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[{"type": "emoji", "emoji": "👀"}],
        )
    except:
        pass

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
        except:
            pass
        bot.send_message(message.chat.id, "😓 Sorry, an unexpected error occurred. Please try again.")

@bot.message_handler(content_types=["voice", "audio", "video", "video_note"])
def on_receive_file(message):
    handle_file(message)

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
        with open(local_temp_file, "wb") as f:
            f.write(data)

        processing_start_time = datetime.now()

        # Convert to WAV
        temp_wav_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.wav")
        try:
            command = [
                ffmpeg.get_ffmpeg_exe(),
                "-i", local_temp_file,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                temp_wav_file,
            ]
            subprocess.run(command, check=True, capture_output=True)
            if not os.path.exists(temp_wav_file) or os.path.getsize(temp_wav_file) == 0:
                raise Exception("FFmpeg conversion failed or empty file.")

            with open(temp_wav_file, "rb") as f:
                wav_audio_data = f.read()

        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg conversion failed: {e.stdout.decode()} {e.stderr.decode()}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except:
                pass
            bot.send_message(
                message.chat.id,
                "😓 Sorry, there was an issue converting your audio. The file might be corrupted or in an unsupported format.",
            )
            return

        except Exception as e:
            logging.error(f"FFmpeg general error: {e}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except:
                pass
            bot.send_message(
                message.chat.id,
                "😓 Sorry, your file cannot be converted to the correct voice recognition format. Please ensure it's a standard audio/video file.",
            )
            return

        finally:
            if os.path.exists(temp_wav_file):
                os.remove(temp_wav_file)

        media_lang_code = get_lang_code(firebase_get(MEDIA_LANGUAGE_PATH, uid))
        if not media_lang_code:
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except:
                pass
            bot.send_message(
                message.chat.id,
                f"❌ The language *{firebase_get(MEDIA_LANGUAGE_PATH, uid)}* does not have a valid code for transcription. Please re-select /media_language.",
            )
            return

        transcription = transcribe_audio_from_bytes(wav_audio_data, media_lang_code) or ""
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

        buttons = InlineKeyboardMarkup()
        buttons.add(
            InlineKeyboardButton("Translate", callback_data=f"btn_translate|{message.message_id}"),
            InlineKeyboardButton("Summarize", callback_data=f"btn_summarize|{message.message_id}"),
        )

        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except:
            pass

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
                    caption="Here’s your transcription. Tap a button below for more options.",
                )
            os.remove(fn)
        else:
            bot.reply_to(message, transcription, reply_markup=buttons)

    except Exception as e:
        logging.error(f"Error processing file for user {uid}: {e}")
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except:
            pass
        bot.send_message(
            message.chat.id,
            "😓 Sorry, an error occurred during transcription. The audio might be unclear or very short. Please try again or with a different file.",
        )
    finally:
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]
        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)
            logging.info(f"Cleaned up {local_temp_file}")

# ── LANGUAGE SELECTION CALLBACKS ────────────────────────────────────────────────
@bot.message_handler(commands=["language"])
def select_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = False
    markup = generate_language_keyboard("set_lang")
    bot.send_message(
        message.chat.id,
        "Please select your preferred language for future **translations and summaries**:",
        reply_markup=markup,
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_lang|"))
def callback_set_language(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, lang = call.data.split("|", 1)
    firebase_set(LANGUAGE_SETTINGS_PATH, uid, lang)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Your preferred language for translations and summaries has been set to: **{lang}**",
        parse_mode="Markdown",
    )
    bot.answer_callback_query(call.id, text=f"Language set to {lang}")

@bot.message_handler(commands=["media_language"])
def select_media_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = False
    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "Please choose the language of the audio files that you need me to transcribe. This helps ensure accurate reading.",
        reply_markup=markup,
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_media_lang|"))
def callback_set_media_language(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, lang = call.data.split("|", 1)
    firebase_set(MEDIA_LANGUAGE_PATH, uid, lang)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ The transcription language for your media is set to: **{lang}**",
        parse_mode="Markdown",
    )
    bot.answer_callback_query(call.id, text=f"Media language set to {lang}")

# ── TRANSLATE / SUMMARIZE BUTTON CALLBACKS ───────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = False
    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = firebase_get(LANGUAGE_SETTINGS_PATH, uid)
    if preferred_lang:
        bot.answer_callback_query(call.id, "Translating with your preferred language...")
        threading.Thread(
            target=do_translate_with_saved_lang,
            args=(call.message, uid, preferred_lang, message_id)
        ).start()
    else:
        markup = generate_language_keyboard("translate_to", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want to translate into:",
            reply_markup=markup,
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

    user_tts_mode[uid] = False
    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = firebase_get(LANGUAGE_SETTINGS_PATH, uid)
    if preferred_lang:
        bot.answer_callback_query(call.id, "Summarizing with your preferred language...")
        threading.Thread(
            target=do_summarize_with_saved_lang,
            args=(call.message, uid, preferred_lang, message_id)
        ).start()
    else:
        markup = generate_language_keyboard("summarize_in", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want the summary in:",
            reply_markup=markup,
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

    user_tts_mode[uid] = False
    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    firebase_set(LANGUAGE_SETTINGS_PATH, uid, lang)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Translating to **{lang}**...",
        parse_mode="Markdown",
    )

    if message_id:
        threading.Thread(
            target=do_translate_with_saved_lang,
            args=(call.message, uid, lang, message_id)
        ).start()
    else:
        if (uid in user_transcriptions
            and call.message.reply_to_message
            and call.message.reply_to_message.message_id in user_transcriptions[uid]):
            threading.Thread(
                target=do_translate_with_saved_lang,
                args=(call.message, uid, lang, call.message.reply_to_message.message_id)
            ).start()
        else:
            bot.send_message(
                call.message.chat.id,
                "❌ No transcription found for this message to translate. Please use the inline buttons on the transcription.",
            )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("summarize_in|"))
def callback_summarize_in(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = False
    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    firebase_set(LANGUAGE_SETTINGS_PATH, uid, lang)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Summarizing in **{lang}**...",
        parse_mode="Markdown",
    )

    if message_id:
        threading.Thread(
            target=do_summarize_with_saved_lang,
            args=(call.message, uid, lang, message_id)
        ).start()
    else:
        if (uid in user_transcriptions
            and call.message.reply_to_message
            and call.message.reply_to_message.message_id in user_transcriptions[uid]):
            threading.Thread(
                target=do_summarize_with_saved_lang,
                args=(call.message, uid, lang, call.message.reply_to_message.message_id)
            ).start()
        else:
            bot.send_message(
                call.message.chat.id,
                "❌ No transcription found for this message to summarize. Please use the inline buttons on the transcription.",
            )
    bot.answer_callback_query(call.id)

# ── TRANSLATE / SUMMARIZE ROUTINES ───────────────────────────────────────────────
def do_translate_with_saved_lang(message, uid, lang, message_id):
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to translate.")
        return

    prompt = f"Translate the following text into {lang}. Provide only the translated text:\n\n{original}"
    bot.send_chat_action(message.chat.id, "typing")
    translated = ask_gemini(uid, prompt)

    if translated.startswith("Error:"):
        bot.send_message(
            message.chat.id,
            f"😓 Sorry, an error occurred during translation: {translated}. Please try again later.",
        )
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
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to summarize.")
        return

    prompt = f"Summarize the following text in {lang}. Provide only the summarized text:\n\n{original}"
    bot.send_chat_action(message.chat.id, "typing")
    summary = ask_gemini(uid, prompt)

    if summary.startswith("Error:"):
        bot.send_message(
            message.chat.id,
            f"😓 Sorry, an error occurred during summarization: {summary}. Please try again later.",
        )
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

# ── ASK GEMINI (TRANSLATION / SUMMARIZATION) ────────────────────────────────────
def ask_gemini(user_id, user_message):
    if not GEMINI_API_KEY:
        return "Error: Gemini API Key is not set." # Tani ma dhici doonto maadaama aad si toos ah u qortay

    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": parts}]}
        )
        resp.raise_for_status()
        result = resp.json()
        if "candidates" in result:
            reply = result["candidates"][0]["content"]["parts"][0]["text"]
            user_memory[user_id].append({"role": "model", "text": reply})
            return reply
        return "Error: Unexpected response from Gemini API: " + json.dumps(result)
    except requests.exceptions.RequestException as e:
        logging.error(f"Error communicating with Gemini API: {e}")
        return f"Error: Failed to connect to Gemini API. {e}"
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON response from Gemini API: {resp.text}")
        return "Error: Invalid response from Gemini API."

# ── /translate COMMAND (FALLBACK) ───────────────────────────────────────────────
@bot.message_handler(commands=["translate"])
def handle_translate(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = False
    if (not message.reply_to_message
        or uid not in user_transcriptions
        or message.reply_to_message.message_id not in user_transcriptions[uid]):
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to translate it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = firebase_get(LANGUAGE_SETTINGS_PATH, uid)
    if preferred_lang:
        threading.Thread(
            target=do_translate_with_saved_lang,
            args=(message, uid, preferred_lang, transcription_message_id)
        ).start()
    else:
        markup = generate_language_keyboard("translate_to", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want to translate into:",
            reply_markup=markup,
        )

# ── /summarize COMMAND (FALLBACK) ───────────────────────────────────────────────
@bot.message_handler(commands=["summarize"])
def handle_summarize(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = False
    if (not message.reply_to_message
        or uid not in user_transcriptions
        or message.reply_to_message.message_id not in user_transcriptions[uid]):
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to summarize it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = firebase_get(LANGUAGE_SETTINGS_PATH, uid)
    if preferred_lang:
        threading.Thread(
            target=do_summarize_with_saved_lang,
            args=(message, uid, preferred_lang, transcription_message_id)
        ).start()
    else:
        markup = generate_language_keyboard("summarize_in", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want the summary in:",
            reply_markup=markup,
        )

# ── TRANSCRIBE AUDIO FROM BYTES ─────────────────────────────────────────────────
def transcribe_audio_from_bytes(audio_bytes: bytes, lang_code: str) -> str | None:
    r = sr.Recognizer()
    full_transcription = []
    chunk_length_ms = 10 * 1000  # 10 seconds
    overlap_ms = 500

    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")
        total_length_ms = len(audio)
        start_ms = 0
        logging.info(f"Starting chunking for in-memory audio, total length {total_length_ms/1000:.2f}s.")

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
                    logging.info(f"Transcribed chunk {start_ms/1000:.1f}-{end_ms/1000:.1f}s: {text[:50]}...")
                except sr.UnknownValueError:
                    logging.warning(f"Speech Recognition could not understand chunk {start_ms/1000:.1f}-{end_ms/1000:.1f}s")
                except sr.RequestError as e:
                    logging.error(f"Google SR request error: {e} for chunk {start_ms/1000:.1f}-{end_ms/1000:.1f}s")
                except Exception as e:
                    logging.error(f"Error processing chunk {start_ms/1000:.1f}-{end_ms/1000:.1f}s: {e}")
                finally:
                    chunk_io.close()

            start_ms += chunk_length_ms - overlap_ms

        return " ".join(full_transcription) if full_transcription else None

    except Exception as e:
        logging.error(f"Overall transcription error: {e}")
        return None

# ── CLEANUP OLD IN-MEMORY DATA EVERY 24H ───────────────────────────────────────
def cleanup_old_data():
    seven_days_ago = datetime.now() - timedelta(days=7)

    # Clean up user_transcriptions
    to_delete_t = []
    for user_id, trans in user_transcriptions.items():
        last_activity = firebase_get(USERS_PATH, user_id)
        if not last_activity:
            to_delete_t.append(user_id)
        else:
            try:
                if datetime.fromisoformat(last_activity).date() < seven_days_ago.date(): # Isku xir waxa ay noqon doonaan kaliya taariikhda
                    to_delete_t.append(user_id)
            except:
                to_delete_t.append(user_id)
    for u in to_delete_t:
        user_transcriptions.pop(u, None)
        logging.info(f"Cleaned up old transcriptions for user {u}")

    # Clean up user_memory
    to_delete_m = []
    for user_id, mem in user_memory.items():
        last_activity = firebase_get(USERS_PATH, user_id)
        if not last_activity:
            to_delete_m.append(user_id)
        else:
            try:
                if datetime.fromisoformat(last_activity).date() < seven_days_ago.date():
                    to_delete_m.append(user_id)
            except:
                to_delete_m.append(user_id)
    for u in to_delete_m:
        user_memory.pop(u, None)
        logging.info(f"Cleaned up old chat memory for user {u}")

    # Clean up TTS prefs
    all_tts = firebase_get_all_keys(TTS_USERS_PATH) or {}
    for user_id, voice in all_tts.items():
        last_activity = firebase_get(USERS_PATH, user_id)
        if not last_activity:
            firebase_delete(TTS_USERS_PATH, user_id)
            logging.info(f"Deleted TTS preference for inactive user {user_id}")
        else:
            try:
                if datetime.fromisoformat(last_activity).date() < seven_days_ago.date():
                    firebase_delete(TTS_USERS_PATH, user_id)
                    logging.info(f"Deleted TTS preference for inactive user {user_id}")
            except:
                firebase_delete(TTS_USERS_PATH, user_id)
                logging.info(f"Deleted TTS preference for invalid timestamp user {user_id}")

    threading.Timer(24 * 60 * 60, cleanup_old_data).start()

# ── TEXT-TO-SPEECH HANDLERS ─────────────────────────────────────────────────────
@bot.message_handler(commands=["text_to_speech"])
def cmd_text_to_speech(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = True
    bot.send_message(message.chat.id, "🎙️ Choose a language for text-to-speech:", reply_markup=make_tts_language_keyboard())

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
        text=f"🎙️ Choose a voice for {lang_name}:",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name),
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
    set_tts_user_voice(uid, voice)

    user_tts_mode[uid] = True
    bot.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🔊 Now using: *{voice}*. You can start sending text messages to convert them to speech.",
        parse_mode="Markdown",
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    if not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🎙️ Choose a language for text-to-speech:",
        reply_markup=make_tts_language_keyboard(),
    )
    bot.answer_callback_query(call.id)

async def synth_and_send_tts(chat_id: int, user_id: str, text: str):
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

@bot.message_handler(func=lambda message: message.content_type == "text" and not message.text.startswith("/"))
def handle_text_for_tts_or_fallback(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)

    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    if user_tts_mode.get(uid):
        threading.Thread(target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))).start()
    else:
        bot.send_message(
            message.chat.id,
            "I only transcribe voice messages, audio, or video. To convert text to speech, use the /text_to_speech command first.",
        )

@bot.message_handler(func=lambda m: True, content_types=["photo", "sticker", "document"])
def fallback_non_text_or_media(message):
    uid = str(message.from_user.id)
    update_user_activity(uid)
    if not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    user_tts_mode[uid] = False
    bot.send_message(
        message.chat.id,
        "Please send only voice messages, audio, or video for transcription, or use `/text_to_speech` for text to speech.",
    )

# ── FLASK WEBHOOK ENDPOINTS ────────────────────────────────────────────────────
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

if __name__ == "__main__":
    # Start cleanup timer
    cleanup_old_data()
    # Set bot commands and descriptions
    set_bot_info()
    # Set webhook when the application starts
    set_webhook_on_startup()
    # Run Flask on port from environment or 8080
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
