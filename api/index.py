"""GPA-5 Issue Validation Check - CX lookup portal.

A CX agent signs in with their @shikho.com Google account, types a roll /
registration / mobile / name (or pastes a whole list), and sees whether each
student's profile actually exists in the admin panel - with the exact board,
roll and registration the panel holds, not what anyone typed into a sheet.

Runs as a Vercel serverless function (`handler`) and locally via serve.py.
"""
import html
import json
import os
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth                                            # noqa: E402
from store import DEGRADED, BusyError, store           # noqa: E402
from upstream import Upstream                          # noqa: E402

AUDIT_KEY = 'gpa5:audit'
E = html.escape
_up = None


def admin():
    global _up
    if _up is None:
        _up = Upstream()
    return _up


def audit(user, action, detail, ip=''):
    rec = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'user': user,
           'action': action, 'detail': detail, 'ip': ip}
    print('AUDIT ' + json.dumps(rec, ensure_ascii=False))
    try:
        store.push(AUDIT_KEY, rec)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
CSS = """
*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',system-ui,'Noto Sans Bengali',sans-serif;background:#f4f6fa;color:#182230}
header{background:#1e3a6d;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
header b{font-size:17px}header .who{font-size:13px;opacity:.9}
header a{color:#cfe0ff;font-size:13px;margin-left:14px;text-decoration:none}
header a:hover{text-decoration:underline}
.wrap{max-width:1000px;margin:22px auto;padding:0 16px}
.card{background:#fff;border:1px solid #e2e7f0;border-radius:10px;padding:20px;margin-bottom:16px}
label{display:block;font-size:12px;font-weight:600;color:#54637a;margin-bottom:5px}
input[type=text],input[type=password],textarea{width:100%;padding:11px 12px;border:1px solid #cbd4e2;border-radius:7px;font-size:15px;font-family:inherit}
textarea{min-height:150px;resize:vertical}
button{background:#1e3a6d;color:#fff;border:0;border-radius:7px;padding:11px 22px;font-size:15px;cursor:pointer}
button:hover{background:#294d8f}button:disabled{background:#9aa7bd;cursor:not-allowed}
button.alt{background:#fff;color:#1e3a6d;border:1px solid #1e3a6d}
.gbtn{display:inline-flex;align-items:center;gap:10px;background:#fff;color:#3c4043;border:1px solid #dadce0;border-radius:7px;padding:12px 20px;font-size:15px;font-weight:600;text-decoration:none}
.gbtn:hover{background:#f7f8f8}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>div{flex:1;min-width:150px}
.hint{font-size:12.5px;color:#6b7a90;margin-top:10px;line-height:1.6}
.verdict{border-radius:9px;padding:14px 16px;font-size:17px;font-weight:600;margin-bottom:14px}
.yes{background:#e7f7ed;border:1px solid #9fd8b4;color:#14602f}
.no{background:#fdeaea;border:1px solid #f0b4b4;color:#8c1c1c}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:12px;color:#54637a;padding:8px 6px;border-bottom:2px solid #e2e7f0;white-space:nowrap}
td{padding:8px 6px;border-bottom:1px solid #eef1f6;vertical-align:top}
td.k{color:#6b7a90;width:190px;font-size:13px}
.scroll{overflow-x:auto}
.flags{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.flag{font-size:12.5px;padding:5px 11px;border-radius:20px;font-weight:600}
.ok{background:#e7f7ed;color:#14602f}.bad{background:#fdeaea;color:#8c1c1c}
.err{background:#fff6e5;border:1px solid #f0d09a;color:#7a4b00;padding:13px;border-radius:8px}
.warn{background:#fff6e5;border:1px solid #f0d09a;color:#7a4b00;padding:11px 14px;border-radius:8px;font-size:13px;margin-bottom:16px}
.meta{font-size:12px;color:#8a97a8;margin-top:12px}
h2{font-size:16px;margin:0 0 14px}
.pill{font-size:11.5px;padding:3px 9px;border-radius:20px;font-weight:600;white-space:nowrap}
#bar{height:7px;background:#e2e7f0;border-radius:20px;overflow:hidden;margin:14px 0}
#fill{height:100%;width:0;background:#1e3a6d;transition:width .3s}
"""

DEGRADED_BANNER = ('<div class="warn"><b>সতর্কতা:</b> Upstash Redis সেট করা নেই। '
                   'তখন প্রতিটি সার্ভার ইনস্ট্যান্স আলাদাভাবে গতি নিয়ন্ত্রণ করে, '
                   'তাই ১০ জন একসাথে সার্চ করলে admin panel ডাউন হয়ে যেতে পারে। '
                   'UPSTASH_REDIS_REST_URL ও TOKEN যোগ করুন।</div>')


def page(body, user=None, title='Issue Validation Check', script=''):
    nav = ''
    if user:
        nav = (f'<div class="who">{E(user)}<a href="/">একজন</a>'
               f'<a href="/bulk">বাল্ক চেক</a><a href="/logout">লগ আউট</a></div>')
    warn = DEGRADED_BANNER if (DEGRADED and user) else ''
    return (f'<!doctype html><html lang="bn"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{E(title)}</title><style>{CSS}</style></head><body>'
            f'<header><b>GPA-5 Issue Validation Check</b>{nav}</header>'
            f'<div class="wrap">{warn}{body}</div>{script}</body></html>').encode()


GOOGLE_G = ('<svg width="18" height="18" viewBox="0 0 48 48">'
            '<path fill="#4285F4" d="M45 24c0-1.6-.1-2.7-.4-4H24v7.5h12c-.2 2-1.5 5-4.4 7l6.8 5.3C42.4 36 45 30.6 45 24z"/>'
            '<path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.3l-6.8-5.3c-1.9 1.3-4.4 2.2-7.7 2.2-5.9 0-10.9-3.9-12.7-9.2l-7 5.4C7.9 41 15.4 46 24 46z"/>'
            '<path fill="#FBBC05" d="M11.3 28.4c-.5-1.4-.8-2.9-.8-4.4s.3-3 .8-4.4l-7-5.4C2.9 17.1 2 20.4 2 24s.9 6.9 2.3 9.8l7-5.4z"/>'
            '<path fill="#EA4335" d="M24 10.5c3.3 0 5.5 1.4 6.8 2.6l6-5.9C33.1 3.9 29.9 2 24 2 15.4 2 7.9 7 4.3 14.2l7 5.4C13.1 14.4 18.1 10.5 24 10.5z"/></svg>')


def login_page(msg=''):
    warn = f'<div class="err" style="margin-bottom:14px">{E(msg)}</div>' if msg else ''
    if auth.OAUTH_ON:
        inner = (f'<p class="hint" style="margin:0 0 18px">আপনার '
                 f'<b>@{E(auth.ALLOWED_DOMAIN)}</b> Google অ্যাকাউন্ট দিয়ে লগ ইন করুন।</p>'
                 f'<a class="gbtn" href="/auth/start">{GOOGLE_G} Google দিয়ে সাইন ইন</a>')
    else:
        inner = ('<form method="POST" action="/login">'
                 '<label>ইউজারনেম</label><input type="text" name="user" autofocus required>'
                 '<div style="height:12px"></div>'
                 '<label>পাসওয়ার্ড</label><input type="password" name="pw" required>'
                 '<div style="height:16px"></div><button type="submit">লগ ইন</button></form>'
                 '<p class="hint">Google সাইন ইন কনফিগার করা নেই - লোকাল মোড।</p>')
    return page(f'''{warn}<div class="card" style="max-width:430px;margin:40px auto">
      <h2>লগ ইন</h2>{inner}</div>''')


def flag(label, val):
    on = val == 1
    return f'<span class="flag {"ok" if on else "bad"}">{label}: {"হ্যাঁ" if on else "না"}</span>'


def row(k, v):
    return (f'<tr><td class="k">{E(k)}</td><td>{E(str(v))}</td></tr>'
            if v not in (None, '', 'None') else '')


def render_student(d, cached):
    """Everything below is what the admin panel holds - board, roll and
    registration as stored there, never as CX or the student typed them."""
    rows = ''.join([
        row('নাম (admin)', d.get('name_bn')),
        row('বোর্ড (admin)', d.get('board_bn') or d.get('board')),
        row('রোল (admin)', d.get('roll') or d.get('roll_p')),
        row('রেজিস্ট্রেশন (admin)', d.get('registration') or d.get('reg_p')),
        row('মোবাইল', d.get('phone') or d.get('phone_p')),
        row('অভিভাবকের মোবাইল', d.get('gurdian_phone')),
        row('গ্রুপ', d.get('study_group')),
        row('জেন্ডার', d.get('gender')),
        row('প্রতিষ্ঠান', d.get('institute_name')),
        row('উপজেলা', d.get('upazila_bn') or d.get('upazila')),
        row('নিজ জেলা', d.get('district_bn')),
        row('সংবর্ধনার জেলা', d.get('venue_district_bn')),
        row('Reg ID', d.get('regid_p')),
        row('শেষ আপডেট', (d.get('updated_at') or '')[:10]),
        row('Admin ID', d.get('sid')),
    ])
    flags = (flag('প্রোফাইল সম্পূর্ণ', d.get('is_update_profile'))
             + flag('পাসওয়ার্ড সেট', d.get('is_password_set'))
             + flag('অ্যাকাউন্ট Active', d.get('is_active'))
             + flag('রেজাল্ট আছে', d.get('has_result')))
    src = 'ক্যাশ থেকে' if cached else 'এইমাত্র admin panel থেকে'
    link = f'https://www.gpa5reception.com/student-data/{E(str(d.get("sid")))}'
    return f'''<div class="card">
      <div class="verdict yes">✓ প্রোফাইল তৈরি আছে - অ্যাকাউন্টও আছে</div>
      <div class="flags">{flags}</div>
      <div class="scroll"><table>{rows}</table></div>
      <div class="meta">ডেটা: {src} · <a href="{link}" target="_blank" rel="noopener">admin panel এ খুলুন</a></div>
    </div>'''


NOT_FOUND = '''<div class="card">
  <div class="verdict no">✗ প্রোফাইল পাওয়া যায়নি - এই তথ্যে কোনো অ্যাকাউন্ট নেই</div>
  <div class="hint">রেজিস্ট্রেশন নম্বর দিয়ে আরেকবার চেষ্টা করুন। তাতেও না পেলে
  শিক্ষার্থী এখনো রেজিস্ট্রেশন সম্পন্ন করেনি - নতুন করে রেজিস্ট্রেশন করতে বলুন।</div>
</div>'''


def search_page(user, q='', body=''):
    return page(f'''<div class="card">
      <h2>একজন শিক্ষার্থী খুঁজুন</h2>
      <form method="GET" action="/">
        <div class="row">
          <div><label>রোল / রেজিস্ট্রেশন / মোবাইল / নাম</label>
               <input type="text" name="q" value="{E(q)}" autofocus required></div>
          <div style="flex:0 0 auto;align-self:flex-end"><button type="submit">খুঁজুন</button></div>
        </div>
      </form>
      <div class="hint">নাম দিয়েও খোঁজা যায়, তবে একই নামে একাধিক শিক্ষার্থী থাকতে পারে -
      নিশ্চিত হতে <b>রেজিস্ট্রেশন নম্বর</b> ব্যবহার করুন।<br>
      অনেকজন একসাথে দেখতে <a href="/bulk">বাল্ক চেক</a> ব্যবহার করুন।</div>
    </div>{body}''', user)


BULK_JS = """<script>
const COLS = ['যা দিয়ে খোঁজা','প্রোফাইল আছে?','নাম (admin)','বোর্ড (admin)',
'রোল (admin)','রেজিস্ট্রেশন (admin)','মোবাইল','গ্রুপ','জেন্ডার','প্রতিষ্ঠান',
'নিজ জেলা','সংবর্ধনার জেলা','প্রোফাইল সম্পূর্ণ','পাসওয়ার্ড সেট','Active',
'রেজাল্ট আছে','Admin ID'];
let rows = [], halted = false;

function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function yn(v){return v===1?'হ্যাঁ':'না';}

function draw(){
  document.getElementById('out').innerHTML =
    '<div class="scroll"><table><tr>'+COLS.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+
    rows.map(r=>'<tr>'+r.map(function(c,i){
      if(i===1){var ok=(c==='হ্যাঁ');
        return '<td><span class="pill '+(ok?'ok':'bad')+'">'+esc(c)+'</span></td>';}
      return '<td>'+esc(c)+'</td>';}).join('')+'</tr>').join('')+'</table></div>';
}

async function run(){
  const raw = document.getElementById('list').value.split(/[\\n,;\\t]+/)
              .map(s=>s.trim()).filter(Boolean);
  if(!raw.length){alert('আগে তালিকা দিন');return;}
  rows=[];halted=false;
  document.getElementById('go').disabled=true;
  document.getElementById('halt').style.display='inline-block';
  document.getElementById('dl').style.display='none';
  for(let i=0;i<raw.length;i++){
    if(halted) break;
    document.getElementById('status').textContent=(i+1)+' / '+raw.length+' - '+raw[i];
    document.getElementById('fill').style.width=(i/raw.length*100)+'%';
    let r;
    try{ r = await (await fetch('/check?q='+encodeURIComponent(raw[i]))).json(); }
    catch(e){ r = {error:'নেটওয়ার্ক সমস্যা'}; }
    const blank = new Array(15).fill('');
    if(r.error){ rows.push([raw[i],'ত্রুটি',r.error].concat(blank.slice(1))); }
    else if(!r.students || !r.students.length){ rows.push([raw[i],'না'].concat(blank)); }
    else{
      for(const d of r.students){
        rows.push([raw[i],'হ্যাঁ',d.name_bn||'',d.board_bn||d.board||'',
          d.roll||d.roll_p||'',d.registration||d.reg_p||'',d.phone||d.phone_p||'',
          d.study_group||'',d.gender||'',d.institute_name||'',d.district_bn||'',
          d.venue_district_bn||'',yn(d.is_update_profile),yn(d.is_password_set),
          yn(d.is_active),yn(d.has_result),d.sid||'']);
      }
    }
    draw();
  }
  document.getElementById('fill').style.width='100%';
  document.getElementById('status').textContent =
    'শেষ - '+rows.length+' টি ফলাফল'+(halted?' (থামানো হয়েছে)':'');
  document.getElementById('go').disabled=false;
  document.getElementById('halt').style.display='none';
  if(rows.length) document.getElementById('dl').style.display='inline-block';
}

function download(){
  const q=s=>'"'+String(s==null?'':s).replace(/"/g,'""')+'"';
  const csv='\\ufeff'+[COLS.map(q).join(',')]
    .concat(rows.map(r=>r.map(q).join(','))).join('\\n');
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  a.download='validation-check-'+new Date().toISOString().slice(0,10)+'.csv';
  a.click();
}
</script>"""


def bulk_page(user):
    return page('''<div class="card">
      <h2>বাল্ক চেক</h2>
      <label>প্রতি লাইনে একটি - রোল / রেজিস্ট্রেশন / মোবাইল / নাম</label>
      <textarea id="list" placeholder="2310929381&#10;2310937742&#10;01752770779"></textarea>
      <div class="hint">Excel এর একটি কলাম কপি করে সরাসরি পেস্ট করতে পারেন।
      admin panel রক্ষা করতে একেকটি একে একে পাঠানো হয় (১.৫ সেকেন্ড বিরতি),
      তাই ৫০ জনে প্রায় দেড় মিনিট লাগবে - পেজটি খোলা রাখুন।</div>
      <div id="bar"><div id="fill"></div></div>
      <div class="row" style="align-items:center">
        <div style="flex:0 0 auto"><button id="go" onclick="run()">চেক শুরু করুন</button></div>
        <div style="flex:0 0 auto"><button id="halt" class="alt" style="display:none"
             onclick="halted=true">থামান</button></div>
        <div style="flex:0 0 auto"><button id="dl" class="alt" style="display:none"
             onclick="download()">CSV ডাউনলোড</button></div>
        <div style="font-size:13px;color:#6b7a90" id="status"></div>
      </div>
    </div>
    <div class="card"><h2>ফলাফল</h2><div id="out"></div></div>''',
                user, script=BULK_JS)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):
    server_version = 'gpa5cx'

    def log_message(self, fmt, *a):
        pass

    def _send(self, body, code=200, cookie=None, ctype='text/html; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), code,
                   ctype='application/json; charset=utf-8')

    def _redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header('Location', to)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()

    def _host(self):
        return self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or ''

    def _secure(self):
        return (self.headers.get('X-Forwarded-Proto') or '').startswith('https')

    def _cookie(self, val, max_age):
        sec = '; Secure' if self._secure() else ''
        return f'cxs={val}; Path=/; Max-Age={max_age}; HttpOnly{sec}; SameSite=Lax'

    def _user(self):
        raw = self.headers.get('Cookie')
        if not raw:
            return None
        c = SimpleCookie(raw)
        return auth.read_token(c['cxs'].value) if 'cxs' in c else None

    def _ip(self):
        return (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip('/') or '/'
        qs = parse_qs(u.query)

        if path == '/health':
            return self._json({'ok': True, 'shared_state': not DEGRADED,
                               'google_login': auth.OAUTH_ON,
                               'domain': auth.ALLOWED_DOMAIN})

        if path == '/auth/start':
            if not auth.OAUTH_ON:
                return self._send(login_page('Google সাইন ইন কনফিগার করা নেই।'), 500)
            return self._redirect(auth.start_url(self._host()))

        if path == '/auth/callback':
            if qs.get('error'):
                return self._send(login_page('Google লগ ইন বাতিল হয়েছে।'), 401)
            try:
                email = auth.finish((qs.get('code') or [''])[0],
                                    (qs.get('state') or [''])[0], self._host())
            except auth.AuthError as e:
                audit('?', 'login_denied', str(e), self._ip())
                return self._send(login_page(str(e)), 403)
            except Exception as e:
                audit('?', 'login_error', str(e), self._ip())
                return self._send(login_page('লগ ইন সম্পন্ন হয়নি। আবার চেষ্টা করুন।'), 500)
            audit(email, 'login', 'google', self._ip())
            return self._redirect('/', self._cookie(auth.make_token(email),
                                                    auth.SESSION_HOURS * 3600))

        user = self._user()
        if not user:
            if path == '/check':
                return self._json({'error': 'সেশন শেষ - আবার লগ ইন করুন'}, 401)
            return self._send(login_page())

        if path == '/logout':
            audit(user, 'logout', '', self._ip())
            return self._redirect('/', self._cookie('', 0))

        if path == '/bulk':
            return self._send(bulk_page(user))

        q = (qs.get('q') or [''])[0].strip()

        if path == '/check':                        # JSON, drives the bulk page
            if not q:
                return self._json({'error': 'খালি'}, 400)
            try:
                res = admin().lookup(q)
            except BusyError as e:
                return self._json({'error': str(e)}, 429)
            except Exception as e:
                audit(user, 'check_error', {'q': q, 'err': str(e)}, self._ip())
                return self._json({'error': str(e)}, 502)
            audit(user, 'check', {'q': q, 'found': len(res)}, self._ip())
            return self._json({'students': [d for d, _ in res]})

        if path != '/':
            return self._send(page('<div class="card">পেজ পাওয়া যায়নি</div>', user), 404)

        if not q:
            return self._send(search_page(user))
        try:
            results = admin().lookup(q)
        except BusyError as e:
            body = f'<div class="card"><div class="err">{E(str(e))}</div></div>'
            return self._send(search_page(user, q, body))
        except Exception as e:
            audit(user, 'search_error', {'q': q, 'err': str(e)}, self._ip())
            body = (f'<div class="card"><div class="err"><b>admin panel এ পৌঁছানো যায়নি।</b>'
                    f'<br>{E(str(e))}<br><br>কিছুক্ষণ পর আবার চেষ্টা করুন।</div></div>')
            return self._send(search_page(user, q, body))
        audit(user, 'search', {'q': q, 'found': len(results)}, self._ip())
        body = ''.join(render_student(d, c) for d, c in results) or NOT_FOUND
        self._send(search_page(user, q, body))

    def do_POST(self):
        """Password fallback, only when Google sign-in is not configured."""
        if urlparse(self.path).path.rstrip('/') != '/login' or auth.OAUTH_ON:
            return self._send(b'', 404)
        n = int(self.headers.get('Content-Length') or 0)
        f = parse_qs(self.rfile.read(n).decode('utf-8', 'replace'))
        user = (f.get('user') or [''])[0].strip()
        stored = auth.USERS.get(user)
        if not stored or not auth.check_pw((f.get('pw') or [''])[0], stored):
            audit(user or '?', 'login_failed', '', self._ip())
            time.sleep(1)
            return self._send(login_page('ভুল ইউজারনেম বা পাসওয়ার্ড।'), 401)
        audit(user, 'login', 'password', self._ip())
        self._redirect('/', self._cookie(auth.make_token(user),
                                         auth.SESSION_HOURS * 3600))
