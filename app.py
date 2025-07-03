import uuid
from flask import Flask, request, jsonify
import threading
from telethon import TelegramClient, events
from dotenv import load_dotenv
import os
import json
import re
import asyncio
import time
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

SESSION_TELEGRAM = 'server_session';

# === Configuración ===
api_id = int(os.getenv("TELEGRAM_API"))
api_hash = os.getenv("TELEGRAM_API_HASH")

latest_signal_jorge_xau = None
latest_signal_jorge_btc = None
latest_signal_jorge_forex = None
latest_signal_jorge_weltrade = None
latest_signal_jorge_deriv = None

# Canales que vamos a escuchar
TELEGRAM_CHANNEL_JORGE_SINTETICOS = int(os.getenv("TELEGRAM_CHANNEL_JORGE_SINTETICOS"))
TELEGRAM_CHANNEL_JORGE_FOREX = int(os.getenv("TELEGRAM_CHANNEL_JORGE_FOREX"))
TELEGRAM_CHANNEL_JORGE_XAU = int(os.getenv("TELEGRAM_CHANNEL_JORGE_XAU"))
TELEGRAM_CHANNEL_JORGE_BTC = int(os.getenv("TELEGRAM_CHANNEL_JORGE_BTC"))

TELEGRAM_CHANNEL_PRUEBA_XAU = int(os.getenv("TELEGRAM_CHANNEL_PRUEBA_XAU"))

TIME_TO_EXPIRE_SIGNAL = int(os.getenv("TIME_TO_EXPIRE_SIGNAL"))

WATCHED_CHANNELS = [TELEGRAM_CHANNEL_JORGE_SINTETICOS, TELEGRAM_CHANNEL_JORGE_FOREX, TELEGRAM_CHANNEL_JORGE_XAU, TELEGRAM_CHANNEL_JORGE_BTC, TELEGRAM_CHANNEL_PRUEBA_XAU]

RAW_JSON = os.getenv("GOOGLE_CREDENTIALS")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # ID del Google Sheet desde la URL
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME")

# Variables para caché de Google Sheets
authorized_users_cache = None
authorized_users_cache_time = 0
AUTHORIZED_CACHE_TTL = 300  # en segundos (5 minutos)

required_vars = ["SPREADSHEET_ID", "WORKSHEET_NAME", "GOOGLE_CREDENTIALS", "TELEGRAM_API", "TELEGRAM_API_HASH", "TELEGRAM_CHANNEL_PRUEBA_XAU","TIME_TO_EXPIRE_SIGNAL","TELEGRAM_CHANNEL_JORGE_SINTETICOS","TELEGRAM_CHANNEL_JORGE_FOREX","TELEGRAM_CHANNEL_JORGE_XAU","TELEGRAM_CHANNEL_JORGE_BTC"]
for var in required_vars:
    if not os.getenv(var):
        raise ValueError(f"❌ Variable de entorno faltante: {var}")    

#----------------------- Configuración de spreadsheet y funciones de Spreadsheet -------------------------

if not RAW_JSON:
    raise ValueError("❌ Variable de entorno GOOGLE_CREDENTIALS no está definida")

creds_dict = json.loads(RAW_JSON)
creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)

def get_authorized_users():
    global authorized_users_cache, authorized_users_cache_time

    now = time.time()
    if authorized_users_cache and now - authorized_users_cache_time < AUTHORIZED_CACHE_TTL:
        return authorized_users_cache

    # Si expiró el caché o nunca se cargó, hacemos la lectura
    try:
        authorized_users_cache = sheet.get_all_records()

        authorized_users_cache = [
            {k: str(v).strip() for k, v in row.items()}
            for row in sheet.get_all_records()
        ]

        authorized_users_cache_time = now
        return authorized_users_cache
    except Exception as e:
        print("Error al obtener datos de Google Sheets:", e)
        return []

def is_valid_request(account_number, license_key, server_key):
    authorized_users = get_authorized_users()
    for user in authorized_users:
        if (
            str(user["account_number"]).strip() == str(account_number).strip() and
            str(user["license_key"]).strip() == str(license_key).strip() and
            str(user["server_key"]).strip() == str(server_key).strip() and
            str(user["enabled"]).strip().lower() == "true"
        ):
            return True
    return False

def update_account_fields(sheet, account_number, server_key, new_balance, new_last_trade, trade_mode, account_server, broker_company, risk_per_group):
    """
    Actualiza las columnas 6 y 7 si el account_number y server_key coinciden en la misma fila habilitada.
    """
    records = sheet.get_all_records()

    for idx, row in enumerate(records, start=2):  # Asume que la fila 1 es encabezado
        if str(row["account_number"]) == str(account_number):
            if str(row["enabled"]).lower() != "true":
                return False, "Cuenta no habilitada"
            if str(row["server_key"]) != str(server_key):
                return False, "Server key inválida"
            
            # Columnas F (6) y G (7)
            sheet.update_cell(idx, 6, new_balance)
            sheet.update_cell(idx, 7, new_last_trade)
            sheet.update_cell(idx, 8, trade_mode)
            sheet.update_cell(idx, 9, account_server)
            sheet.update_cell(idx, 10, broker_company)
            sheet.update_cell(idx, 11, risk_per_group)
            return True, "Actualización exitosa"

    return False, "Cuenta no encontrada"


def update_ea_status(sheet, account_number, server_key, ea_status):
    """
    Actualiza las columnas 12 si el account_number y server_key coinciden en la misma fila habilitada.
    """
    records = sheet.get_all_records()

    for idx, row in enumerate(records, start=2):  # Asume que la fila 1 es encabezado
        if str(row["account_number"]) == str(account_number):
            if str(row["enabled"]).lower() != "true":
                return False, "Cuenta no habilitada"
            if str(row["server_key"]) != str(server_key):
                return False, "Server key inválida"
            
            # Columnas L (12)
            sheet.update_cell(idx, 12, ea_status)
            return True, "Actualización exitosa"

    return False, "Cuenta no encontrada"

#------------------------------------------ Fin Configuración de spreadsheet -------------------------------------


# Inicializar cliente de Telethon
client_telegram = TelegramClient(SESSION_TELEGRAM, api_id, api_hash)
telethon_event_loop = None

app = Flask(__name__)

# JORGE BTC SIGNALS

def is_jorge_btc_signal(text):
    """
    Detecta señales de tipo JanhTraders SOLO para el par BTCUSD (con o sin sufijo 'm').
    """
    if not text or not isinstance(text, str):
        return False

    text = text.upper()

    # Aceptar BTCUSD o BTCUSDm (la 'm' puede estar en minúscula en el original)
    if not re.search(r'\bBTCUSD(M)?\b', text):
        return False

    # Buscar dirección y entrada
    if not re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Buscar SL
    if not re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Buscar al menos un TP
    if not re.search(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text):
        return False

    return True

def parse_jorge_btc_signal(text):
    """
    Parsea señales de tipo JanhTraders solo para BTCUSD.

    Retorna un diccionario con:
    - symbol
    - side
    - entry
    - sl
    - tps
    """
    if not text or not isinstance(text, str):
        return None

    text = text.upper().strip()

    # Validar que sea BTCUSD
    if "BTCUSD" not in text:
        return None
    symbol = "BTCUSD"

    # Dirección y entrada
    entry_match = re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text)
    if not entry_match:
        return None

    side = "BUY" if entry_match.group(1) == "COMPRA" else "SELL"
    try:
        entry = float(entry_match.group(2))
    except ValueError:
        return None

    # SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return None
    try:
        sl = float(sl_match.group(1))
    except ValueError:
        return None

    # TPs
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    try:
        tps = [float(tp) for tp in tp_matches]
    except ValueError:
        return None

    if not tps:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tps": tps
    }

# JORGE XAU SIGNALS

def is_jorge_gold_signal(text):
    """
    Detecta señales de tipo JanhTraders SOLO para el par BTCUSD.
    """
    if not text or not isinstance(text, str):
        return False

    text = text.upper()

    # Solo aceptar BTCUSD
    if not re.search(r'\bXAUUSD\b', text):
        return False

    # Buscar dirección y entrada
    if not re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Buscar SL
    if not re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Buscar al menos un TP
    if not re.search(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text):
        return False

    return True

def parse_jorge_gold_signal(text):
    """
    Parsea señales de tipo JanhTraders solo para XAUUSD.

    Retorna un diccionario con:
    - symbol
    - side
    - entry
    - sl
    - tps
    """
    if not text or not isinstance(text, str):
        return None

    text = text.upper().strip()

    # Validar que sea XAUUSD
    if "XAUUSD" not in text:
        return None
    symbol = "XAUUSD"

    # Dirección y entrada
    entry_match = re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text)
    if not entry_match:
        return None

    side = "BUY" if entry_match.group(1) == "COMPRA" else "SELL"
    try:
        entry = float(entry_match.group(2))
    except ValueError:
        return None

    # SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return None
    try:
        sl = float(sl_match.group(1))
    except ValueError:
        return None

    # TPs
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    try:
        tps = [float(tp) for tp in tp_matches]
    except ValueError:
        return None

    if not tps:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tps": tps
    }

# JORGE WELTRADE SIGNALS

def is_jorge_weltrade_signal(text):
    """
    Detecta señales de índices de Weltrade: GainX, PainX, FX Vol, SFX Vol.
    Ejemplos válidos:
    🔔 GainX 1200 Index 🔔
    VENTA: 9284.131
    TP1... SL...
    """
    if not text or not isinstance(text, str):
        return False

    text = text.upper()

    # Buscar símbolo válido del broker Weltrade
    weltrade_match = re.search(
        r'\b(GAINX|PAINX|FX VOL|SFX VOL)[\s\-]?\d{2,5}', text
    )
    if not weltrade_match:
        return False

    # Buscar dirección y entrada
    if not re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Buscar al menos un TP
    if not re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Buscar SL
    if not re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text):
        return False

    return True

def parse_jorge_weltrade_signal(text):
    """
    Parsea señales para GainX, PainX, FX Vol, SFX Vol.

    Retorna un diccionario con:
    - symbol: str
    - side: BUY o SELL
    - entry: float
    - sl: float
    - tps: list[float]
    """
    if not text or not isinstance(text, str):
        return None

    text = text.upper()

    # Buscar símbolo
    symbol_match = re.search(
        r'\b(GAINX|PAINX|FX VOL|SFX VOL)[\s\-]?(\d{2,5})', text
    )
    if not symbol_match:
        return None

    symbol = symbol_match.group(1).replace(" ", "") + symbol_match.group(2)

    # Buscar dirección y entrada
    entry_match = re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text)
    if not entry_match:
        return None

    side = "BUY" if entry_match.group(1) == "COMPRA" else "SELL"
    try:
        entry = float(entry_match.group(2))
    except ValueError:
        return None

    # Buscar SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return None
    try:
        sl = float(sl_match.group(1))
    except ValueError:
        return None

    # Buscar TPs
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    try:
        tps = [float(tp) for tp in tp_matches]
    except ValueError:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tps": tps
    }

# JORGE DERIV SIGNALS

def is_jorge_deriv_signal(text):
    """
    Detecta señales de índices Deriv como Boom, Crash, Volatility, Step, Jump.
    Formato aceptado:
    🔔 Boom 1000 Index 🔔
    VENTA 3126.00
    TP...
    SL...
    """
    if not text or not isinstance(text, str):
        return False

    text = text.upper().strip()

    # Verificar símbolo válido
    allowed_prefixes = ["BOOM", "CRASH", "VOLATILITY", "STEP", "JUMP"]
    symbol_match = re.search(r'\b(BOOM|CRASH|VOLATILITY|STEP|JUMP)[\s\-]?\d{3,5}', text)
    if not symbol_match:
        return False

    # Confirmar dirección y entrada
    if not re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Confirmar SL
    if not re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text):
        return False

    # Confirmar al menos un TP
    if not re.search(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text):
        return False

    return True

def parse_jorge_deriv_signal(text):
    """
    Parsea señales Deriv de Boom, Crash, Volatility, Step, Jump.

    Retorna:
    - symbol: str (formato sin espacios, ej: BOOM1000)
    - side: BUY o SELL
    - entry: float
    - sl: float
    - tps: list[float]
    """
    if not text or not isinstance(text, str):
        return None

    text = text.upper().strip()

    # Símbolo Deriv
    symbol_match = re.search(r'\b(BOOM|CRASH|VOLATILITY|STEP|JUMP)[\s\-]?(\d{3,5})', text)
    if not symbol_match:
        return None

    symbol = symbol_match.group(1) + symbol_match.group(2)

    # Dirección y entrada
    entry_match = re.search(r'\b(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', text)
    if not entry_match:
        return None

    side = "BUY" if entry_match.group(1) == "COMPRA" else "SELL"
    try:
        entry = float(entry_match.group(2))
    except ValueError:
        return None

    # SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return None
    try:
        sl = float(sl_match.group(1))
    except ValueError:
        return None

    # TPs
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    try:
        tps = [float(tp) for tp in tp_matches]
    except ValueError:
        return None

    if not tps:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tps": tps
    }

# JORGE FOREX SIGNALS

def is_jorge_forex_signal(text):
    """
    Detecta señales tipo JanhTraders EXCLUYENDO BTCUSD y XAUUSD.
    Formato esperado:
    🔔 GBPUSDm 🔔
    💹 VENTA: 1.36987
    TP1: ...
    SL: ...
    """
    if not text or not isinstance(text, str):
        return False

    text = text.upper().strip()

    # Buscar el símbolo entre 🔔 ... 🔔
    symbol_match = re.search(r'🔔\s*([A-Z]{6,7}M?)\s*🔔', text)
    if not symbol_match:
        return False

    symbol = symbol_match.group(1)
    if symbol in {"BTCUSD", "XAUUSD"}:
        return False

    # Confirmar dirección (💹 opcional) y precio de entrada
    if not re.search(r'(💹)?\s*(COMPRA|VENTA)\s*[:=]?\s*[\d\.]+', text):
        return False

    # Confirmar al menos un TP
    if not re.search(r'TP\d*\s*[:=]?\s*[\d\.]+', text):
        return False

    # Confirmar SL
    if not re.search(r'SL\s*[:=]?\s*[\d\.]+', text):
        return False

    return True

def parse_jorge_forex_signal(text):
    """
    Parsea señales tipo JanhTraders EXCLUYENDO BTCUSD y XAUUSD.

    Mantiene la 'm' final en minúscula si el símbolo lo incluye.

    Retorna:
    - symbol: str
    - side: "BUY" o "SELL"
    - entry: float
    - sl: float
    - tps: list[float]
    """
    if not text or not isinstance(text, str):
        return None

    raw_text = text.strip()

    # Buscar símbolo entre 🔔 ... 🔔 tal como viene en el texto
    symbol_match = re.search(r'🔔\s*([A-Za-z]{6,7})\s*🔔', raw_text)
    if not symbol_match:
        return None

    symbol = symbol_match.group(1)  # conserva la 'm' en minúscula si viene así

    if symbol.upper().startswith("BTCUSD") or symbol.upper().startswith("XAUUSD"):
        return None

    # Texto para parsing: mayúsculas para el resto
    upper_text = raw_text.upper()

    # Dirección y entrada
    entry_match = re.search(r'(💹)?\s*(COMPRA|VENTA)\s*[:=]?\s*([\d\.]+)', upper_text)
    if not entry_match:
        return None

    side = "BUY" if entry_match.group(2) == "COMPRA" else "SELL"
    try:
        entry = float(entry_match.group(3))
    except ValueError:
        return None

    # SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', upper_text)
    if not sl_match:
        return None
    try:
        sl = float(sl_match.group(1))
    except ValueError:
        return None

    # TPs
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', upper_text)
    try:
        tps = [float(tp) for tp in tp_matches]
    except ValueError:
        return None

    if not tps:
        return None

    return {
        "symbol": symbol,  # mantiene la 'm' tal cual viene
        "side": side,
        "entry": entry,
        "sl": sl,
        "tps": tps
    }

# READY PARSED SIGNALS

def send_order_to_mt5(order_data):
    global latest_signal_jorge_xau, latest_signal_jorge_btc, latest_signal_jorge_forex, latest_signal_jorge_weltrade, latest_signal_jorge_deriv

    vendor = order_data.get("vendor", "").lower()

    if vendor == "jorge_btc":
        latest_signal_jorge_btc = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de JORGE BTC almacenada: {order_data['symbol']} [{order_data['side']}]")

    elif vendor == "jorge_xau":
        latest_signal_jorge_xau = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de JORGE XAU almacenada: {order_data['symbol']} [{order_data['side']}]")

    elif vendor == "jorge_forex":
        latest_signal_jorge_forex = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de JORGE FOREX almacenada: {order_data['symbol']} [{order_data['side']}]")

    elif vendor == "jorge_weltrade":
        latest_signal_jorge_weltrade = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de JORGE WELTRADE almacenada: {order_data['symbol']} [{order_data['side']}]")

    elif vendor == "jorge_deriv":
        latest_signal_jorge_deriv = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de JORGE DERIV almacenada: {order_data['symbol']} [{order_data['side']}]")

    else:
        print("❌ Vendor desconocido en la señal:", vendor)

def format_signal_for_telegram(order_data):
    global latest_signal_jorge_xau, latest_signal_jorge_btc, latest_signal_jorge_forex, latest_signal_jorge_weltrade, latest_signal_jorge_deriv
    
    """
    Formatea una señal de trading para enviar como mensaje de Telegram (Markdown),
    soportando distintos formatos de `order_data`.
    """

    # Extraer campos con respaldo alternativo
    symbol = order_data.get("symbol", "🆔 ACTIVO NO DEFINIDO")
    direction = order_data.get("direction") or order_data.get("side") or "🧐"
    sl = order_data.get("sl")
    tps = order_data.get("tps")
    entry = order_data.get("entry", "⏳ Esperando ejecución")
    vendor = order_data.get("vendor")

    # Armar líneas condicionalmente
    if vendor == "jorge_btc":
        lines = ["📢 Nueva Señal de JORGE VIP CHANNEL - BTC\n"]
    elif vendor == "jorge_xau":
        lines = ["📢 Nueva Señal de JORGE VIP CHANNEL - XAU\n"]
    elif vendor == "jorge_forex":
        lines = ["📢 Nueva Señal de JORGE VIP CHANNEL - FOREX\n"]
    elif vendor == "jorge_weltrade":
        lines = ["📢 Nueva Señal de JORGE VIP CHANNEL - WELTRADE\n"]
    elif vendor == "jorge_deriv":
        lines = ["📢 Nueva Señal de JORGE VIP CHANNEL - DERIV\n"]

    if direction and symbol:
        lines.append(f"📈 {direction} - `{symbol}`\n")
    
    # lines.append(f"🎯 Entry: `{entry}`")

    if isinstance(tps, list) and len(tps) > 0:
        for i, tp in enumerate(tps):
            lines.append(f"🎯 TP{i+1}: `{tp}`")

    if sl:
        lines.append(f"🛑 SL: `{sl}`")

    return "\n".join(lines)

# === Handler principal ===

@client_telegram.on(events.NewMessage(chats=WATCHED_CHANNELS))
async def handler(event):
    global signal_id_mrpip
    sender_id = int(event.chat_id)
    message = event.message.message
    header = ''

    print(f"sender: {sender_id}")
    print(f"message: {message}")

    #JORGE BTC
    if sender_id in [TELEGRAM_CHANNEL_JORGE_BTC, TELEGRAM_CHANNEL_PRUEBA_XAU] and is_jorge_btc_signal(message):
        header = "📡 Señal BTC de JORGE VIP Recibida con SL y TP"

        print(f"\n🪙 Señal BTC de JORGE VIP CHANNEL detectada:\n{message}\n{'='*60}")

        signal_data = parse_jorge_btc_signal(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "jorge_btc"
            }
            signal_id_jorge_btc = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_jorge_btc

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA_XAU, message=f"{format_signal_for_telegram(order_data)}")
            return
        
    #JORGE XAU
    if sender_id in [TELEGRAM_CHANNEL_JORGE_XAU, TELEGRAM_CHANNEL_PRUEBA_XAU] and is_jorge_gold_signal(message):
        header = "📡 Señal XAU de JORGE VIP Recibida con SL y TP"

        print(f"\n🪙 Señal XAU JORGE VIP CHANNEL detectada:\n{message}\n{'='*60}")

        signal_data = parse_jorge_gold_signal(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "jorge_xau"
            }
            signal_id_jorge_xau = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_jorge_xau

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA_XAU, message=f"{format_signal_for_telegram(order_data)}")
            return
    
    #JORGE WELTRADE
    if sender_id in [TELEGRAM_CHANNEL_JORGE_SINTETICOS, TELEGRAM_CHANNEL_PRUEBA_XAU] and is_jorge_weltrade_signal(message):
        header = "📡 Señal WELTRADE de JORGE VIP Recibida con SL y TP"

        print(f"\n🪙 Señal WELTRADE JORGE VIP CHANNEL detectada:\n{message}\n{'='*60}")

        signal_data = parse_jorge_weltrade_signal(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "jorge_weltrade"
            }
            signal_id_jorge_weltrade = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_jorge_weltrade

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA_XAU, message=f"{format_signal_for_telegram(order_data)}")
            return
        
    #JORGE DERIV
    if sender_id in [TELEGRAM_CHANNEL_JORGE_SINTETICOS, TELEGRAM_CHANNEL_PRUEBA_XAU] and is_jorge_deriv_signal(message):
        header = "📡 Señal DERIV de JORGE VIP Recibida con SL y TP"

        print(f"\n🪙 Señal DERIV JORGE VIP CHANNEL detectada:\n{message}\n{'='*60}")

        signal_data = parse_jorge_deriv_signal(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "jorge_deriv"
            }
            signal_id_jorge_deriv = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_jorge_deriv

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA_XAU, message=f"{format_signal_for_telegram(order_data)}")
            return

    #JORGE FOREX
    if sender_id in [TELEGRAM_CHANNEL_JORGE_FOREX, TELEGRAM_CHANNEL_PRUEBA_XAU] and is_jorge_forex_signal(message):
        header = "📡 Señal FOREX de JORGE VIP Recibida con SL y TP"

        print(f"\n🪙 Señal FOREX JORGE VIP CHANNEL detectada:\n{message}\n{'='*60}")

        signal_data = parse_jorge_forex_signal(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "jorge_forex"
            }
            signal_id_jorge_forex = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_jorge_forex

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA_XAU, message=f"{format_signal_for_telegram(order_data)}")
            return
      
    else:
        if sender_id  == TELEGRAM_CHANNEL_JORGE_SINTETICOS:
            header = "⚠️ Se recibió un mensaje del grupo Jorge VIP Sinteticos, pero no es una señal"
        elif sender_id  == TELEGRAM_CHANNEL_JORGE_FOREX:
            header = "⚠️ Se recibió un mensaje del grupo Jorge VIP Forex, pero no es una señal"
        elif sender_id  == TELEGRAM_CHANNEL_JORGE_XAU:
            header = "⚠️ Se recibió un mensaje del grupo Jorge VIP XAU, pero no es una señal"
        elif sender_id  == TELEGRAM_CHANNEL_JORGE_BTC:
            header = "⚠️ Se recibió un mensaje del grupo Jorge VIP BTC, pero no es una señal"
        elif sender_id  == TELEGRAM_CHANNEL_PRUEBA_XAU:
            header = "⚠️ Se recibió un mensaje del grupo de prueba, pero no es una señal"
        else:
            # header = "⚠️ Se recibió un mensaje, pero no es de otro canal"
            print(f"\n📭 Mensaje ignorado de canal {sender_id}.\n{'='*60}")
    # Enviar mensaje al canal
    try:
        # await client_telegram.send_message(entity=target_channel, message=f"{header}\n\n{message}")
        await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA_XAU, message=f"{header}\n\n{message}")
        print("✅ Mensaje enviado al canal destino.")
    except Exception as e:
        print(f"❌ Error al enviar mensaje al canal: {e}")

# === Ejecutar cliente ===
def start_flask():
    port = int(os.getenv("PORT", 3000))
    print(f"🌐 Flask escuchando en puerto {port}")
    app.run(host="0.0.0.0", port=port)

def main():
    print("🚀 Bot y backend MT5 iniciando...")
    flask_thread = threading.Thread(target=start_flask)
    flask_thread.start()
    with client_telegram:
        telethon_event_loop = client_telegram.loop  # 🔥 capturamos el loop real
        client_telegram.run_until_disconnected()

@app.route("/")
def index():
    return "Unauthorized", 401

@app.route("/ping")
def ping():
    return {"status": "ok", "message": "working!"}

#========== SIGNALS JORGE =============
#-------------- GOLD ------------------

@app.route("/mt5/xau/execute", methods=["POST"])
def get_jorge_xau_signal():
    global latest_signal_jorge_xau
    try:
        data = request.get_json(force=True)  # fuerza decodificación JSON
    except Exception as e:
        print("❌ Error decoding JSON:", e)
        return "Bad Request", 400

    #print("✅ JSON recibido:", data)

    account_number = str(data.get("account_number"))
    license_key = str(data.get("license_key"))
    server_key =str(data.get("server_key"))

    if not is_valid_request(account_number, license_key, server_key):
        return "Unauthorized", 401

    if not latest_signal_jorge_xau:
        return "", 204

    # TTL y retorno
    now = datetime.utcnow()
    created = latest_signal_jorge_xau["timestamp"]
    ttl = latest_signal_jorge_xau["ttl"]

    if now - created > ttl:
        latest_signal_jorge_xau = None
        return "", 204

    return jsonify(latest_signal_jorge_xau["data"])

@app.route("/mt5/xau/update-account", methods=["POST"])
def update_account():
    try:
        data = request.get_json(force=True)  # fuerza decodificación JSON
    except Exception as e:
        print("❌ Error decoding JSON:", e)
        return "Bad Request", 400

    # Validaciones
    account_number = data.get("account")
    account_balance = data.get("balance")
    last_trade = data.get("last_trade")
    server_key = data.get("server_key")
    account_server = data.get("account_server")
    broker_company = data.get("broker_company")
    trade_mode = data.get("trade_mode")
    risk_per_group = data.get("risk_per_group")

    if not all([account_number, account_balance, last_trade, server_key, account_server, broker_company, trade_mode, risk_per_group]):
        return jsonify({"error": "Faltan parámetros"}), 400

    # Validación + actualización
    success, message = update_account_fields(
        sheet,
        account_number,
        server_key,
        account_balance,
        last_trade,
        trade_mode,
        account_server,
        broker_company,
        risk_per_group
    )

    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 401
    
@app.route("/mt5/xau/update-ea-status", methods=["POST"])
def update_ea_status():
    try:
        data = request.get_json(force=True)  # fuerza decodificación JSON
    except Exception as e:
        print("❌ Error decoding JSON:", e)
        return "Bad Request", 400

    # Validaciones
    account_number = str(data.get("account"))
    server_key = str(data.get("server_key"))
    ea_status = str(data.get("server_key"))

    if not all([account_number, server_key, ea_status]):
        return jsonify({"error": "Faltan parámetros"}), 400

    # Validación + actualización
    success, message = update_ea_status(
        sheet,
        account_number,
        server_key,
        ea_status
    )

    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 401

#-------------- GOLD ------------------

# @app.route("/mt5/btc/execute", methods=["GET"])
# def get_jorge_btc_signal():
#     global latest_signal_jorge_btc
#     if not latest_signal_jorge_btc:
#         return "", 204
    
#     now = datetime.utcnow()
#     created = latest_signal_jorge_btc["timestamp"]
#     ttl = latest_signal_jorge_btc["ttl"]

#     if now - created > ttl:
#         latest_signal_jorge_btc = None
#         return "", 204

#     return jsonify(latest_signal_jorge_btc["data"])

# @app.route("/mt5/forex/execute", methods=["GET"])
# def get_jorge_forex_signal():
#     global latest_signal_jorge_forex
#     if not latest_signal_jorge_forex:
#         return "", 204
    
#     now = datetime.utcnow()
#     created = latest_signal_jorge_forex["timestamp"]
#     ttl = latest_signal_jorge_forex["ttl"]

#     if now - created > ttl:
#         latest_signal_jorge_forex = None
#         return "", 204

#     return jsonify(latest_signal_jorge_forex["data"])

# @app.route("/mt5/weltrade/execute", methods=["GET"])
# def get_jorge_weltrade_signal():
#     global latest_signal_jorge_weltrade
#     if not latest_signal_jorge_weltrade:
#         return "", 204
    
#     now = datetime.utcnow()
#     created = latest_signal_jorge_weltrade["timestamp"]
#     ttl = latest_signal_jorge_weltrade["ttl"]

#     if now - created > ttl:
#         latest_signal_jorge_weltrade = None
#         return "", 204

#     return jsonify(latest_signal_jorge_weltrade["data"])

# @app.route("/mt5/deriv/execute", methods=["GET"])
# def get_jorge_deriv_signal():
#     global latest_signal_jorge_deriv
#     if not latest_signal_jorge_deriv:
#         return "", 204
    
#     now = datetime.utcnow()
#     created = latest_signal_jorge_deriv["timestamp"]
#     ttl = latest_signal_jorge_deriv["ttl"]

#     if now - created > ttl:
#         latest_signal_jorge_deriv = None
#         return "", 204

#     return jsonify(latest_signal_jorge_deriv["data"])

if __name__ == "__main__":
    main()