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
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# --- REPLACE: Import SpeechRecognition instead of FasterWhisper ---
import speech_recognition as sr

# --- KEEP: MSSpeech for Text-to-Speech ---
from msspeech import MSSpeech, MSSpeechError

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BOT CONFIGURATION (Using Media Transcriber Bot's Token and Webhook) ---
TOKEN = "7790991731:AAGpbz6nqE5f0Dvs6ZSTqRoR1LMrrf4rMqU"  # Replace with your actual bot token
ADMIN_ID = 5978150981  # Replace with your actual Admin ID
WEBHOOK_URL = "https://speech-recognition-9j3f.onrender.com"

# --- REQUIRED CHANNEL CONFIGURATION ---
REQUIRED_CHANNEL = "@transcriberbo"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Download directory (still used for intermediate WAV, but aiming for in-memory)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- MongoDB Configuration ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&appName=Cluster0"
DB_NAME = "telegram_bot_db"
USERS_COLLECTION = "users"
LANGUAGE_SETTINGS_COLLECTION = "user_language_settings"
MEDIA_LANGUAGE_SETTINGS_COLLECTION = "user_media_language_settings"
TTS_USERS_COLLECTION = "tts_users"
STATS_COLLECTION = "bot_stats"  # NEW: collection for counters

mongo_client = None
db = None
users_collection = None
language_settings_collection = None
media_language_settings_collection = None
tts_users_collection = None
stats_collection = None  # NEW reference

def connect_to_mongodb():
    global mongo_client, db, users_collection, language_settings_collection, media_language_settings_collection, tts_users_collection, stats_collection
    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster')
        db = mongo_client[DB_NAME]
        users_collection = db[USERS_COLLECTION]
        language_settings_collection = db[LANGUAGE_SETTINGS_COLLECTION]
        media_language_settings_collection = db[MEDIA_LANGUAGE_SETTINGS_COLLECTION]
        tts_users_collection = db[TTS_USERS_COLLECTION]
        stats_collection = db[STATS_COLLECTION]  # NEW
        logging.info("Successfully connected to MongoDB!")
    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        exit(1)

# --- NEW: Initialize or load stats from DB ---
def load_stats():
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
    doc = stats_collection.find_one({"_id": "counters"})
    if doc:
        total_files_processed = doc.get("total_files_processed", 0)
        total_audio_files = doc.get("total_audio_files", 0)
        total_voice_clips = doc.get("total_voice_clips", 0)
        total_videos = doc.get("total_videos", 0)
        total_processing_time = doc.get("total_processing_time", 0)
    else:
        # Insert initial counters
        stats_collection.insert_one({
            "_id": "counters",
            "total_files_processed": 0,
            "total_audio_files": 0,
            "total_voice_clips": 0,
            "total_videos": 0,
            "total_processing_time": 0
        })
        total_files_processed = total_audio_files = total_voice_clips = total_videos = total_processing_time = 0
    logging.info(f"Loaded stats: files={total_files_processed}, audio={total_audio_files}, voice={total_voice_clips}, videos={total_videos}, time={total_processing_time}")

def update_stats_in_db():
    try:
        stats_collection.update_one(
            {"_id": "counters"},
            {"$set": {
                "total_files_processed": total_files_processed,
                "total_audio_files": total_audio_files,
                "total_voice_clips": total_voice_clips,
                "total_videos": total_videos,
                "total_processing_time": total_processing_time
            }},
            upsert=True
        )
        logging.info("Stats updated in DB")
    except Exception as e:
        logging.error(f"Error updating stats in DB: {e}")

# --- NEW: User state for Text-to-Speech input mode ---
user_tts_mode = {}

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
    "Somali 🇸🇴": ["so-SO-UbaxNeural", "so-SO-MuuseNeural"],
    "Persian 🇮🇷": ["fa-IR-DilaraNeural", "fa-IR-FaridNeural"],
    "Oromo 🇪🇹": ["om-ET-WaqayyooNeural", "om-ET-HawweenaNeural"],
    "Tigrinya 🇪🇹": ["ti-ET-HailuNeural", "ti-ET-SelamawitNeural"],
    "Albanian 🇦🇱": ["sq-AL-AnilaNeural", "sq-AL-IlirNeural"],
    "Bosnian 🇧🇦": ["bs-BA-VesnaNeural", "bs-BA-GoranNeural"],
    "Bulgarian 🇧🇬": ["bg-BG-KalinaNeural", "bg-BG-StefanNeural"],
    "Catalan 🇪🇸": ["ca-ES-JoanaNeural", "ca-ES-EnricNeural"],
    "Galician 🇪🇸": ["gl-ES-SabelaNeural", "gl-ES-RoiNeural"],
    "Icelandic 🇮🇸": ["is-IS-AsgerdurNeural", "is-IS-GunnarNeural"],
    "Irish 🇮🇪": ["ga-IE-OrlaNeural", "ga-IE-ColmNeural"],
    "Macedonian 🇲🇰": ["mk-MK-MariaNeural", "mk-MK-AleksandarNeural"],
    "Maltese 🇲🇹": ["mt-MT-AntoniaNeural", "mt-MT-DanielNeural"],
    "Welsh 🏴󠁧󠁢󠁷󠁬󠁳󠁿": ["cy-GB-NiaNeural", "cy-GB-AledNeural"],
    "Basque 🇪🇸": ["eu-ES-AinhoaNeural", "eu-ES-AsierNeural"],
    "Galician 🇪🇸": ["gl-ES-SabelaNeural", "gl-ES-RoiNeural"],
    "Lao 🇱🇦": ["lo-LA-ChanthavongNeural", "lo-LA-KeomanyNeural"],
    "Mongolian 🇲🇳": ["mn-MN-BatbayarNeural", "mn-MN-AnuNeural"],
    "Tajik 🇹🇯": ["tg-TJ-FiruzaNeural", "tg-TJ-HamidNeural"],
    "Turkmen 🇹🇲": ["tk-TM-AltynNeural", "tk-TM-ResulNeural"],
    "Tatar 🇷🇺": ["tt-RU-AlsuNeural", "tt-RU-RuslanNeural"],
    "Bashkir 🇷🇺": ["ba-RU-AigulNeural", "ba-RU-ZaynullaNeural"],
    "Chuvash 🇷🇺": ["cv-RU-IrinaNeural", "cv-RU-SergeyNeural"],
    "Mari (Eastern) 🇷🇺": ["mhr-RU-ElenaNeural", "mhr-RU-MikhailNeural"],
    "Udmurt 🇷🇺": ["udm-RU-AlinaNeural", "udm-RU-AndreiNeural"],
    "Yakut (Sakha) 🇷🇺": ["sah-RU-SardanaNeural", "sah-RU-AisenNeural"],
    "Komis 🇷🇺": ["kv-RU-OlgaNeural", "kv-RU-ValentinNeural"],
    "Nenets 🇷🇺": ["yrk-RU-NataliaNeural", "yrk-RU-VitalyNeural"],
    "Chukchi 🇷🇺": ["ckt-RU-AnnaNeural", "ckt-RU-PavelNeural"],
    "Ingush 🇷🇺": ["inh-RU-ZaremaNeural", "inh-RU-MagomedNeural"],
    "Chechen 🇷🇺": ["ce-RU-MadinaNeural", "ce-RU-ImranNeural"],
    "Lezgian 🇷🇺": ["lez-RU-MarinaNeural", "lez-RU-RuslanNeural"],
    "Kabardian 🇷🇺": ["kbd-RU-FatimaNeural", "kbd-RU-MuratNeural"],
    "Avar 🇷🇺": ["av-RU-AminaNeural", "av-RU-MagomedNeural"],
    "Dargwa 🇷🇺": ["dar-RU-PatimatNeural", "dar-RU-OmarNeural"],
    "Kumyk 🇷🇺": ["kum-RU-AishatNeural", "kum-RU-AslanNeural"],
    "Lak 🇷🇺": ["lbe-RU-ZairaNeural", "lbe-RU-ShamilNeural"],
    "Tabassaran 🇷🇺": ["tab-RU-GulnaraNeural", "tab-RU-RustamNeural"],
    "Buryat 🇷🇺": ["bua-RU-DolgoraNeural", "bua-RU-BatyrNeural"],
    "Kalmyk 🇷🇺": ["xal-RU-BayarmaNeural", "xal-RU-BatorNeural"],
    "Tuvan 🇷🇺": ["tyv-RU-AldynayNeural", "tyv-RU-MongushNeural"],
    "Altai 🇷🇺": ["alt-RU-AizadaNeural", "alt-RU-AidanNeural"],
    "Khakas 🇷🇺": ["krc-RU-KenzhegulNeural", "krc-RU-MaratNeural"],
    "Shughni 🇹🇯": ["sgh-TJ-MadinaNeural", "sgh-TJ-MirzoNeural"],
    "Wakhi 🇹🇯": ["wbl-TJ-GulnaraNeural", "wbl-TJ-SafarNeural"],
    "Dari 🇦🇫": ["prs-AF-ZarminaNeural", "prs-AF-AhmadNeural"],
    "Pashto 🇦🇫": ["ps-AF-PalwashaNeural", "ps-AF-GulzarNeural"],
    "Balochi 🇵🇰": ["bal-PK-SanaNeural", "bal-PK-KarimNeural"],
    "Kurdish (Sorani) 🇮🇶": ["ku-IQ-TaraNeural", "ku-IQ-AramNeural"],
    "Kurdish (Kurmanji) 🇹🇷": ["kmr-TR-BerivanNeural", "kmr-TR-CemilNeural"],
    "Uyghur 🇨🇳": ["ug-CN-MukaddasNeural", "ug-CN-AbdukerimNeural"],
    "Nepali 🇳🇵": ["ne-NP-SaritaNeural", "ne-NP-AbhisekhNeural"],
    "Dzongkha 🇧🇹": ["dz-BT-ChimiNeural", "dz-BT-SonamNeural"],
    "Maldivian 🇲🇻": ["dv-MV-AishaNeural", "dv-MV-AhmedNeural"],
    "Javanese 🇮🇩": ["jv-ID-SitiNeural", "jv-ID-BudiNeural"],
    "Sundanese 🇮🇩": ["su-ID-DewiNeural", "su-ID-AgusNeural"],
    "Kurdish (Central) 🇮🇶": ["ckb-IQ-RebwarNeural", "ckb-IQ-AzinNeural"],
    "Assamese 🇮🇳": ["as-IN-PriyankaNeural", "as-IN-RajeshNeural"],
    "Maithili 🇮🇳": ["mai-IN-AnjaliNeural", "mai-IN-RohanNeural"],
    "Santali 🇮🇳": ["sat-IN-ParvatiNeural", "sat-IN-RajuNeural"],
    "Sindhi 🇮🇳": ["sd-IN-SaimaNeural", "sd-IN-MohsinNeural"],
    "Dogri 🇮🇳": ["doi-IN-SaritaNeural", "doi-IN-PawanNeural"],
    "Kashmiri 🇮🇳": ["ks-IN-MariyaNeural", "ks-IN-AbrarNeural"],
    "Konkani 🇮🇳": ["kok-IN-AnjaliNeural", "kok-IN-RaghavNeural"],
    "Manipuri 🇮🇳": ["mni-IN-ThoibiNeural", "mni-IN-KhagembaNeural"],
    "Bhojpuri 🇮🇳": ["bho-IN-RadhaNeural", "bho-IN-AmitNeural"],
    "Magahi 🇮🇳": ["mag-IN-PriyaNeural", "mag-IN-RajeshNeural"],
    "Angika 🇮🇳": ["anp-IN-SonamNeural", "anp-IN-VivekNeural"],
    "Awadhi 🇮🇳": ["awa-IN-SitaNeural", "awa-IN-RamNeural"],
    "Haryanvi 🇮🇳": ["har-IN-SweetyNeural", "har-IN-MonuNeural"],
    "Chhattisgarhi 🇮🇳": ["chg-IN-RakhiNeural", "chg-IN-RohanNeural"],
    "Marwari 🇮🇳": ["mwr-IN-GeetaNeural", "mwr-IN-VijayNeural"],
    "Bundeli 🇮🇳": ["bns-IN-LataNeural", "bns-IN-MaheshNeural"],
    "Bagheli 🇮🇳": ["bqe-IN-PoojaNeural", "bqe-IN-SureshNeural"],
    "Nepali 🇳🇵": ["ne-NP-SaritaNeural", "ne-NP-AbhisekhNeural"],
    "Farsi 🇮🇷": ["fa-IR-DilaraNeural", "fa-IR-FaridNeural"],
    "Oromo 🇪🇹": ["om-ET-WaqayyooNeural", "om-ET-HawweenaNeural"],
    "Tigrinya 🇪🇹": ["ti-ET-HailuNeural", "ti-ET-SelamawitNeural"],
    "Twi 🇬🇭": ["tw-GH-AkuaNeural", "tw-GH-KofiNeural"],
    "Yoruba 🇳🇬": ["yo-NG-AdedoyinNeural", "yo-NG-BabatundeNeural"],
    "Hausa 🇳🇬": ["ha-NG-JamilaNeural", "ha-NG-UsmanNeural"],
    "Igbo 🇳🇬": ["ig-NG-ChikaNeural", "ig-NG-EmekaNeural"],
    "Lingala 🇨🇩": ["ln-CD-EstherNeural", "ln-CD-JeanNeural"],
    "Luganda 🇺🇬": ["lg-UG-NalubegaNeural", "lg-UG-KasuleNeural"],
    "Kinyarwanda 🇷🇼": ["rw-RW-IngabireNeural", "rw-RW-KarekeziNeural"],
    "Shona 🇿🇼": ["sn-ZW-TadiwaNeural", "sn-ZW-KudakwasheNeural"],
    "Venda 🇿🇦": ["ve-ZA-FhulufheloNeural", "ve-ZA-ThusoNeural"],
    "Tsonga 🇿🇦": ["ts-ZA-ThandiweNeural", "ts-ZA-NjabuloNeural"],
    "Ndebele 🇿🇦": ["nr-ZA-NokuthulaNeural", "nr-ZA-MthokozisiNeural"],
    "Northern Sotho 🇿🇦": ["nso-ZA-PulengNeural", "nso-ZA-LefaNeural"],
    "Sotho 🇱🇸": ["st-LS-LineoNeural", "st-LS-ThaboNeural"],
    "Swati 🇸🇿": ["ss-SZ-NomsaNeural", "ss-SZ-ThembaNeural"],
    "Tsonga 🇿🇦": ["ts-ZA-ThandiweNeural", "ts-ZA-NjabuloNeural"],
    "Venda 🇿🇦": ["ve-ZA-FhulufheloNeural", "ve-ZA-ThusoNeural"],
    "Akan 🇬🇭": ["ak-GH-AdwoaNeural", "ak-GH-KofiNeural"],
    "Ewe 🇬🇭": ["ee-GH-EsiNeural", "ee-GH-KofiNeural"],
    "Ga 🇬🇭": ["gaa-GH-NaaNeural", "gaa-GH-KofiNeural"],
    "Wolof 🇸🇳": ["wo-SN-FatouNeural", "wo-SN-MoussaNeural"],
    "Bambara 🇲🇱": ["bm-ML-MariamNeural", "bm-ML-MoussaNeural"],
    "Fula 🇸🇳": ["ff-SN-AminataNeural", "ff-SN-DembaNeural"],
    "Mandinka 🇬🇲": ["mnk-GM-FatouNeural", "mnk-GM-OusmanNeural"],
    "Susu 🇬🇳": ["sus-GN-MariamaNeural", "sus-GN-MamadouNeural"],
    "Krio 🇸🇱": ["kri-SL-AminataNeural", "kri-SL-MusaNeural"],
    "Estonian 🇪🇪": ["et-EE-LiisNeural", "et-EE-ErkiNeural"],
    "Latvian 🇱🇻": ["lv-LV-EveritaNeural", "lv-LV-AnsisNeural"],
    "Lithuanian 🇱🇹": ["lt-LT-OnaNeural", "lt-LT-LeonasNeural"],
    "Sami (Northern) 🇳🇴": ["se-NO-SaraNeural", "se-NO-PerNeural"],
    "Breton 🇫🇷": ["br-FR-LenaNeural", "br-FR-StevenNeural"],
    "Luxembourgish 🇱🇺": ["lb-LU-ClaireNeural", "lb-LU-TomNeural"],
    "Macedonian 🇲🇰": ["mk-MK-MariaNeural", "mk-MK-AleksandarNeural"],
    "Mongolian 🇲🇳": ["mn-MN-BatbayarNeural", "mn-MN-AnuNeural"],
    "Tajik 🇹🇯": ["tg-TJ-FiruzaNeural", "tg-TJ-HamidNeural"],
    "Turkmen 🇹🇲": ["tk-TM-AltynNeural", "tk-TM-ResulNeural"],
    "Uzbek 🇺🇿": ["uz-UZ-MadinaNeural", "uz-UZ-SuhrobNeural"],
    "Kyrgyz 🇰🇬": ["ky-KG-AigulNeural", "ky-KG-BekbolotNeural"],
}

# --- New MongoDB functions for data persistence with logging ---
def get_user_data(user_id):
    try:
        return users_collection.find_one({"_id": str(user_id)})
    except Exception as e:
        logging.error(f"Error fetching user data for {user_id}: {e}")
        return None

def update_user_activity_db(user_id):
    try:
        users_collection.update_one(
            {"_id": str(user_id)},
            {"$set": {'last_active': datetime.now().isoformat()}},
            upsert=True
        )
        logging.info(f"Updated last_active for user {user_id}")
    except Exception as e:
        logging.error(f"Error updating user activity for {user_id}: {e}")

def increment_transcription_count_db(user_id):
    try:
        users_collection.update_one(
            {"_id": str(user_id)},
            {"$inc": {'transcription_count': 1}, "$set": {'last_active': datetime.now().isoformat()}},
            upsert=True
        )
        logging.info(f"Incremented transcription count for user {user_id}")
    except Exception as e:
        logging.error(f"Error incrementing transcription count for {user_id}: {e}")

def get_user_language_setting_db(user_id):
    try:
        doc = language_settings_collection.find_one({"_id": str(user_id)})
        lang = doc.get("language") if doc else None
        logging.info(f"Fetched language setting for user {user_id}: {lang}")
        return lang
    except Exception as e:
        logging.error(f"Error fetching language setting for {user_id}: {e}")
        return None

def set_user_language_setting_db(user_id, lang):
    try:
        logging.info(f"Setting preferred language for user {user_id} to {lang}")
        language_settings_collection.update_one(
            {"_id": str(user_id)},
            {"$set": {"language": lang}},
            upsert=True
        )
        logging.info(f"Preferred language saved for user {user_id}")
    except Exception as e:
        logging.error(f"Error setting preferred language for {user_id}: {e}")

def get_user_media_language_setting_db(user_id):
    try:
        doc = media_language_settings_collection.find_one({"_id": str(user_id)})
        media_lang = doc.get("media_language") if doc else None
        logging.info(f"Fetched media language setting for user {user_id}: {media_lang}")
        return media_lang
    except Exception as e:
        logging.error(f"Error fetching media language for {user_id}: {e}")
        return None

def set_user_media_language_setting_db(user_id, lang):
    try:
        logging.info(f"Setting media language for user {user_id} to {lang}")
        media_language_settings_collection.update_one(
            {"_id": str(user_id)},
            {"$set": {"media_language": lang}},
            upsert=True
        )
        logging.info(f"Media language saved for user {user_id}")
    except Exception as e:
        logging.error(f"Error setting media language for {user_id}: {e}")

def get_tts_user_voice_db(uid):
    try:
        doc = tts_users_collection.find_one({"_id": str(uid)})
        voice = doc.get("voice", "en-US-AriaNeural") if doc else "en-US-AriaNeural"
        logging.info(f"Fetched TTS voice for user {uid}: {voice}")
        return voice
    except Exception as e:
        logging.error(f"Error fetching TTS voice for {uid}: {e}")
        return "en-US-AriaNeural"

def set_tts_user_voice_db(uid, voice):
    try:
        logging.info(f"Setting TTS voice for user {uid} to {voice}")
        tts_users_collection.update_one(
            {"_id": str(uid)},
            {"$set": {"voice": voice}},
            upsert=True
        )
        logging.info(f"TTS voice saved for user {uid}")
    except Exception as e:
        logging.error(f"Error setting TTS voice for {uid}: {e}")

# In-memory chat history and transcription store
user_memory = {}
user_transcriptions = {}
processing_message_ids = {}

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

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"

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
        telebot.types.BotCommand("media_language", "📝Set language for media transcription"),
        telebot.types.BotCommand("text_to_speech", "🗣️Convert text to speech"),
        telebot.types.BotCommand("translate", "📖Translate a transcription"),
        telebot.types.BotCommand("summary", "📋Summarize a transcription"),
    ]
    bot.set_my_commands(commands)

def update_user_activity(user_id):
    update_user_activity_db(user_id)

def animate_processing_message(chat_id, message_id, stop_event):
    emojis = ["", ".", "..", "..."] # Changed to reflect "Transcribing..." style
    idx = 0
    while not stop_event.is_set():
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"🔍 Transcribing{emojis[idx % len(emojis)]}"
            )
            idx += 1
            time.sleep(1) # Faster update for better animation
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error animating processing message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in animation thread: {e}")
            break


def keep_recording(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
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

def check_subscription(user_id):
    if not REQUIRED_CHANNEL:
        return True
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
        telebot.types.InlineKeyboardButton("Click here to join the channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")
    )
    bot.send_message(
        chat_id,
        "😓Sorry …\n🔰 To continue using this bot next time First join the channel @transcriberbo ‼️ After joining, come back to continue using the bot.",
        reply_markup=markup,
        disable_web_page_preview=True
    )

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    existing_user = get_user_data(user_id)
    if not existing_user:
        try:
            users_collection.insert_one({'_id': user_id, 'last_active': datetime.now().isoformat(), 'transcription_count': 0})
            logging.info(f"Inserted new user {user_id} into users collection")
        except Exception as e:
            logging.error(f"Error inserting new user {user_id}: {e}")
    elif 'transcription_count' not in existing_user:
        try:
            users_collection.update_one({"_id": user_id}, {"$set": {'transcription_count': 0}})
            logging.info(f"Initialized transcription_count for existing user {user_id}")
        except Exception as e:
            logging.error(f"Error initializing transcription_count for {user_id}: {e}")

    user_tts_mode[user_id] = None

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
        markup = generate_language_keyboard("set_media_lang")
        bot.send_message(
            message.chat.id,
            "Please choose the language of the audio files using the below buttons.",
            reply_markup=markup
        )

# Removed @bot.message_handler(commands=['help'])
# Removed def help_handler(message):

# Removed @bot.message_handler(commands=['privacy'])
# Removed def privacy_notice_handler(message):

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
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

    today = datetime.now().date()
    try:
        active_today = users_collection.count_documents({
            'last_active': {"$gte": today.isoformat()}
        })
    except Exception as e:
        logging.error(f"Error counting active users today: {e}")
        active_today = 0

    total_proc_seconds = int(total_processing_time)
    proc_hours = total_proc_seconds // 3600
    proc_minutes = (total_proc_seconds % 3600) // 60
    proc_seconds = total_proc_seconds % 60

    text = (
        "📊 Bot Statistics\n\n"
        "🟢 **Bot Status: Online**\n"
        f"⏱️ Uptime: {days} days, {hours} hours, {minutes} minutes, {seconds} seconds ago\n\n"
        "👥 User Statistics\n"
        f"▫️ Total Users Today: {active_today}\n"
        f"▫️ Total Registered Users: {total_registered_users}\n\n"
        "⚙️ Processing Statistics\n"
        f"▫️ Total Files Processed: {total_files_processed}\n"
        f"▫️ Audio Files: {total_audio_files}\n"
        f"▫️ Voice Clips: {total_voice_clips}\n"
        f"▫️ Videos: {total_videos}\n"
        f"⏱️ Total Processing Time: {proc_hours}h {proc_minutes}m {proc_seconds}s\n\n"
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
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
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
#   M E D I A   H A N D L I N G  (voice, audio, video, video_note, document)
# ────────────────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note', 'document'])
def handle_file(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

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

    # Send initial "Transcribing..." message and start animation
    try:
        status_msg = bot.send_message(message.chat.id, "🔍 Transcribing...")
    except Exception as e:
        logging.error(f"Error sending status message: {e}")
        status_msg = None

    stop_animation = threading.Event()
    animation_thread = threading.Thread(target=animate_processing_message, args=(message.chat.id, status_msg.message_id, stop_animation))
    animation_thread.daemon = True
    animation_thread.start()
    processing_message_ids[message.chat.id] = stop_animation # Store event to stop it later

    # Directly send "👀" reaction
    try:
        bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[{'type': 'emoji', 'emoji': '👀'}]
        )
    except Exception as e:
        logging.error(f"Error setting reaction: {e}")

    try:
        threading.Thread(
            target=process_media_file,
            args=(message, stop_animation, is_document_video, status_msg)
        ).start()
    except Exception as e:
        logging.error(f"Error initiating file processing: {e}")
        stop_animation.set()
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on early error: {remove_e}")
        bot.send_message(message.chat.id, "😓 Sorry, an unexpected error occurred. Please try again.")


def process_media_file(message, stop_animation, is_document_video, status_msg):
    """
    Download the media (voice/audio/video/document),
    convert it to WAV, run SpeechRecognition transcription (in 20-second chunks),
    and send back the result. Update status messages and stats.
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
        with open(local_temp_file, 'wb') as f:
            f.write(data)

        processing_start_time = datetime.now()

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
                raise Exception("FFmpeg conversion failed or resulted in an empty file.")
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg conversion failed: {e.stdout.decode()} {e.stderr.decode()}")
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on FFmpeg error: {remove_e}")
            bot.send_message(
                message.chat.id,
                "😓 Sorry, there was an issue converting your audio/video to the correct format. "
                "Please try again with a different file."
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
                "😓 Sorry, your file cannot be converted to the correct voice recognition format. "
                "Please ensure it's a standard audio/video file."
            )
            return

        # Transcribe using SpeechRecognition in 20-second chunks
        media_lang_name = get_user_media_language_setting_db(uid)
        if not media_lang_name:
            bot.send_message(message.chat.id, "⚠️ No media language set. Please use /media_language first.")
            return

        media_lang_code = get_lang_code(media_lang_name)
        if not media_lang_code:
            try:
                bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
            except Exception as remove_e:
                logging.error(f"Error removing reaction on language code error: {remove_e}")
            bot.send_message(
                message.chat.id,
                f"❌ The language *{media_lang_name}* does not have a valid code for transcription. "
                "Please re-select the language using /media_language."
            )
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

        # Update counters in DB
        update_stats_in_db()

        # Increment transcription count for the user via DB
        increment_transcription_count_db(uid)

        buttons = InlineKeyboardMarkup()
        buttons.add(
            InlineKeyboardButton("Translate", callback_data=f"btn_translate|{message.message_id}"),
            InlineKeyboardButton("Summarize", callback_data=f"btn_summarize|{message.message_id}")
        )

        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as e:
            logging.error(f"Error removing reaction before sending result: {e}")

        # Stop the animation thread for the status message
        stop_animation.set()

        # Delete the status message
        if status_msg:
            try:
                bot.delete_message(chat_id=status_msg.chat.id, message_id=status_msg.message_id)
            except Exception as e:
                logging.error(f"Error deleting status message: {e}")

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

        user_doc_after_inc = get_user_data(uid)
        user_transcription_count_after_inc = user_doc_after_inc.get('transcription_count', 0) if user_doc_after_inc else 0
        if user_transcription_count_after_inc == 5 and not check_subscription(message.from_user.id):
            send_subscription_message(message.from_user.id) # Changed to message.from_user.id to send to user
            
    except Exception as e:
        logging.error(f"Error processing file for user {uid}: {e}")
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on general processing error: {remove_e}")
        bot.send_message(
            message.chat.id,
            "😓𝗪𝗲’𝗿𝗲 𝘀𝗈𝗋𝗋𝘆, 𝗮𝗇 𝗲𝗋𝗋𝗈𝗋 𝗼𝗰𝗰𝘂𝗋𝗋𝗂𝗇𝗀 𝗱𝘂𝗋𝗂𝗇𝗀 𝘁𝗋𝗮𝗇𝘀𝗂𝗉𝗍𝗂𝗈𝗇.\n"
            "The audio might be noisy or spoken too quickly.\n"
            "Please try again or upload a different file.\n"
            "Make sure the file you’re sending and the selected language match — otherwise, an error may occur."
        )
    finally:
        stop_animation.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]

        if local_temp_file and os.path.exists(local_temp_file):
            os.remove(local_temp_file)
            logging.info(f"Cleaned up {local_temp_file}")
        if wav_audio_path and os.path.exists(wav_audio_path):
            os.remove(wav_audio_path)
            logging.info(f"Cleaned up {wav_audio_path}")


def transcribe_audio_with_chunks(audio_path: str, lang_code: str) -> str | None:
    recognizer = sr.Recognizer()
    text = ""
    try:
        sound = AudioSegment.from_wav(audio_path)
        chunk_length_ms = 20_000  # 20 seconds

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
    {"name": "Filipino", "flag": "🇵🇭", "code": "fil"}, # Changed code to 'fil' for Filipino
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
    {"name": "Welsh", "flag": "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "code": "cy"}, # Added Welsh flag
    {"name": "Zulu", "flag": "🇿🇦", "code": "zu"},
    {"name": "Somali", "flag": "🇸🇴", "code": "so"},
    {"name": "Oromo", "flag": "🇪🇹", "code": "om"}, # Added Oromo
    {"name": "Tigrinya", "flag": "🇪🇹", "code": "ti"}, # Added Tigrinya
    {"name": "Amharic", "flag": "🇪🇹", "code": "am"}, # Ensure Amharic is present
    {"name": "Persian", "flag": "🇮🇷", "code": "fa"}, # Added Persian
    {"name": "Twi", "flag": "🇬🇭", "code": "tw"}, # Added Twi
    {"name": "Yoruba", "flag": "🇳🇬", "code": "yo"}, # Added Yoruba
    {"name": "Hausa", "flag": "🇳🇬", "code": "ha"}, # Added Hausa
    {"name": "Igbo", "flag": "🇳🇬", "code": "ig"}, # Added Igbo
    {"name": "Lingala", "flag": "🇨🇩", "code": "ln"}, # Added Lingala
    {"name": "Luganda", "flag": "🇺🇬", "code": "lg"}, # Added Luganda
    {"name": "Kinyarwanda", "flag": "🇷🇼", "code": "rw"}, # Added Kinyarwanda
    {"name": "Shona", "flag": "🇿🇼", "code": "sn"}, # Added Shona
    {"name": "Venda", "flag": "🇿🇦", "code": "ve"}, # Added Venda
    {"name": "Tsonga", "flag": "🇿🇦", "code": "ts"}, # Added Tsonga
    {"name": "Ndebele", "flag": "🇿🇦", "code": "nr"}, # Added Ndebele
    {"name": "Northern Sotho", "flag": "🇿🇦", "code": "nso"}, # Added Northern Sotho
    {"name": "Sotho", "flag": "🇱🇸", "code": "st"}, # Added Sotho
    {"name": "Swati", "flag": "🇸🇿", "code": "ss"}, # Added Swati
    {"name": "Akan", "flag": "🇬🇭", "code": "ak"}, # Added Akan
    {"name": "Ewe", "flag": "🇬🇭", "code": "ee"}, # Added Ewe
    {"name": "Ga", "flag": "🇬🇭", "code": "gaa"}, # Added Ga
    {"name": "Wolof", "flag": "🇸🇳", "code": "wo"}, # Added Wolof
    {"name": "Bambara", "flag": "🇲🇱", "code": "bm"}, # Added Bambara
    {"name": "Fula", "flag": "🇸🇳", "code": "ff"}, # Added Fula
    {"name": "Mandinka", "flag": "🇬🇲", "code": "mnk"}, # Added Mandinka
    {"name": "Susu", "flag": "🇬🇳", "code": "sus"}, # Added Susu
    {"name": "Krio", "flag": "🇸🇱", "code": "kri"}, # Added Krio
    {"name": "Basque", "flag": "🇪🇸", "code": "eu"}, # Added Basque
    {"name": "Tatar", "flag": "🇷🇺", "code": "tt"}, # Added Tatar
    {"name": "Bashkir", "flag": "🇷🇺", "code": "ba"}, # Added Bashkir
    {"name": "Chuvash", "flag": "🇷🇺", "code": "cv"}, # Added Chuvash
    {"name": "Mari (Eastern)", "flag": "🇷🇺", "code": "mhr"}, # Added Mari (Eastern)
    {"name": "Udmurt", "flag": "🇷🇺", "code": "udm"}, # Added Udmurt
    {"name": "Yakut (Sakha)", "flag": "🇷🇺", "code": "sah"}, # Added Yakut (Sakha)
    {"name": "Komis", "flag": "🇷🇺", "code": "kv"}, # Added Komis
    {"name": "Nenets", "flag": "🇷🇺", "code": "yrk"}, # Added Nenets
    {"name": "Chukchi", "flag": "🇷🇺", "code": "ckt"}, # Added Chukchi
    {"name": "Ingush", "flag": "🇷🇺", "code": "inh"}, # Added Ingush
    {"name": "Chechen", "flag": "🇷🇺", "code": "ce"}, # Added Chechen
    {"name": "Lezgian", "flag": "🇷🇺", "code": "lez"}, # Added Lezgian
    {"name": "Kabardian", "flag": "🇷🇺", "code": "kbd"}, # Added Kabardian
    {"name": "Avar", "flag": "🇷🇺", "code": "av"}, # Added Avar
    {"name": "Dargwa", "flag": "🇷🇺", "code": "dar"}, # Added Dargwa
    {"name": "Kumyk", "flag": "🇷🇺", "code": "kum"}, # Added Kumyk
    {"name": "Lak", "flag": "🇷🇺", "code": "lbe"}, # Added Lak
    {"name": "Tabassaran", "flag": "🇷🇺", "code": "tab"}, # Added Tabassaran
    {"name": "Buryat", "flag": "🇷🇺", "code": "bua"}, # Added Buryat
    {"name": "Kalmyk", "flag": "🇷🇺", "code": "xal"}, # Added Kalmyk
    {"name": "Tuvan", "flag": "🇷🇺", "code": "tyv"}, # Added Tuvan
    {"name": "Altai", "flag": "🇷🇺", "code": "alt"}, # Added Altai
    {"name": "Khakas", "flag": "🇷🇺", "code": "krc"}, # Added Khakas
    {"name": "Shughni", "flag": "🇹🇯", "code": "sgh"}, # Added Shughni
    {"name": "Wakhi", "flag": "🇹🇯", "code": "wbl"}, # Added Wakhi
    {"name": "Dari", "flag": "🇦🇫", "code": "prs"}, # Added Dari
    {"name": "Pashto", "flag": "🇦🇫", "code": "ps"}, # Added Pashto
    {"name": "Balochi", "flag": "🇵🇰", "code": "bal"}, # Added Balochi
    {"name": "Kurdish (Sorani)", "flag": "🇮🇶", "code": "ku"}, # Added Kurdish (Sorani)
    {"name": "Kurdish (Kurmanji)", "flag": "🇹🇷", "code": "kmr"}, # Added Kurdish (Kurmanji)
    {"name": "Uyghur", "flag": "🇨🇳", "code": "ug"}, # Added Uyghur
    {"name": "Dzongkha", "flag": "🇧🇹", "code": "dz"}, # Added Dzongkha
    {"name": "Maldivian", "flag": "🇲🇻", "code": "dv"}, # Added Maldivian
    {"name": "Javanese", "flag": "🇮🇩", "code": "jv"}, # Added Javanese
    {"name": "Sundanese", "flag": "🇮🇩", "code": "su"}, # Added Sundanese
    {"name": "Kurdish (Central)", "flag": "🇮🇶", "code": "ckb"}, # Added Kurdish (Central)
    {"name": "Assamese", "flag": "🇮🇳", "code": "as"}, # Added Assamese
    {"name": "Maithili", "flag": "🇮🇳", "code": "mai"}, # Added Maithili
    {"name": "Santali", "flag": "🇮🇳", "code": "sat"}, # Added Santali
    {"name": "Sindhi", "flag": "🇮🇳", "code": "sd"}, # Added Sindhi
    {"name": "Dogri", "flag": "🇮🇳", "code": "doi"}, # Added Dogri
    {"name": "Kashmiri", "flag": "🇮🇳", "code": "ks"}, # Added Kashmiri
    {"name": "Konkani", "flag": "🇮🇳", "code": "kok"}, # Added Konkani
    {"name": "Manipuri", "flag": "🇮🇳", "code": "mni"}, # Added Manipuri
    {"name": "Bhojpuri", "flag": "🇮🇳", "code": "bho"}, # Added Bhojpuri
    {"name": "Magahi", "flag": "🇮🇳", "code": "mag"}, # Added Magahi
    {"name": "Angika", "flag": "🇮🇳", "code": "anp"}, # Added Angika
    {"name": "Awadhi", "flag": "🇮🇳", "code": "awa"}, # Added Awadhi
    {"name": "Haryanvi", "flag": "🇮🇳", "code": "har"}, # Added Haryanvi
    {"name": "Chhattisgarhi", "flag": "🇮🇳", "code": "chg"}, # Added Chhattisgarhi
    {"name": "Marwari", "flag": "🇮🇳", "code": "mwr"}, # Added Marwari
    {"name": "Bundeli", "flag": "🇮🇳", "code": "bns"}, # Added Bundeli
    {"name": "Bagheli", "flag": "🇮🇳", "code": "bqe"}, # Added Bagheli
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

def make_tts_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang_name in sorted(TTS_VOICES_BY_LANGUAGE.keys()): # Sort for consistent order
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
    user_doc = get_user_data(user_id)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    bot.send_message(message.chat.id, "🎙️ Choose a language for text-to-speech:", reply_markup=make_tts_language_keyboard())

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
        bot.send_message(chat_id=message.chat.id, text=f"😓 Sorry, an error occurred during summarization: {summary}. Please try again later.")
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
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    update_user_activity(uid)
    user_doc = get_user_data(uid)
    user_transcription_count = user_doc.get('transcription_count', 0) if user_doc else 0
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
    seven_days_ago_iso = (datetime.now() - timedelta(days=7)).isoformat()

    keys_to_delete_transcriptions_in_memory = []
    for user_id in user_transcriptions:
        try:
            user_doc = users_collection.find_one({"_id": user_id})
            if not user_doc or user_doc.get('last_active') < seven_days_ago_iso:
                keys_to_delete_transcriptions_in_memory.append(user_id)
        except Exception as e:
            logging.error(f"Error checking last_active for in-memory cleanup for user {user_id}: {e}")
            keys_to_delete_transcriptions_in_memory.append(user_id)

    for user_id in keys_to_delete_transcriptions_in_memory:
        if user_id in user_transcriptions: # Check before deleting
            del user_transcriptions[user_id]
            logging.info(f"Cleaned up old in-memory transcriptions for user {user_id}")

    keys_to_delete_memory_in_memory = []
    for user_id in user_memory:
        try:
            user_doc = users_collection.find_one({"_id": user_id})
            if not user_doc or user_doc.get('last_active') < seven_days_ago_iso:
                keys_to_delete_memory_in_memory.append(user_id)
        except Exception as e:
            logging.error(f"Error checking last_active for chat memory cleanup for user {user_id}: {e}")
            keys_to_delete_memory_in_memory.append(user_id)

    for user_id in keys_to_delete_memory_in_memory:
        if user_id in user_memory: # Check before deleting
            del user_memory[user_id]
            logging.info(f"Cleaned up old in-memory chat memory for user {user_id}")

    thirty_days_ago_iso = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        users_collection.update_many(
            {'last_active': {'$lt': thirty_days_ago_iso}},
            {'$set': {'transcription_count': 0}}
        )
        logging.info("Reset transcription counts for users inactive for over 30 days.")
    except Exception as e:
        logging.error(f"Error resetting transcription counts: {e}")

    try:
        existing_user_ids = [doc["_id"] for doc in users_collection.find({}, {'_id': 1})]
        language_settings_collection.delete_many({'_id': {'$nin': existing_user_ids}})
        media_language_settings_collection.delete_many({'_id': {'$nin': existing_user_ids}})
        tts_users_collection.delete_many({'_id': {'$nin': existing_user_ids}})
        logging.info("Cleaned up orphaned language and TTS settings.")
    except Exception as e:
        logging.error(f"Error cleaning up orphaned settings: {e}")

    threading.Timer(24 * 60 * 60, cleanup_old_data).start()

def set_bot_info_and_startup():
    connect_to_mongodb()
    load_stats()  # NEW: load counters
    set_bot_info()
    cleanup_old_data()
    set_webhook_on_startup()

if __name__ == "__main__":
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

