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
import re
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth                                            # noqa: E402
from store import DEGRADED, BusyError, store           # noqa: E402
from upstream import Upstream                          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(os.path.dirname(HERE), 'public', 'logo.svg')
FAVICON = os.path.join(os.path.dirname(HERE), 'public', 'favicon.svg')
AUDIT_KEY = 'gpa5:audit'
E = html.escape
_up = None


def admin():
    global _up
    if _up is None:
        _up = Upstream()
    # Fresh time budget per incoming request. The instance is reused across
    # invocations, so this has to reset here, not in the constructor.
    _up.start_budget()
    return _up


def friendly(e):
    """CX sees what to do next, not a Python exception. The detail still goes
    to the audit log for whoever is debugging."""
    t = str(e)
    if 'timed out' in t.lower() or 'timeout' in t.lower():
        return ('সার্ভার এখন ধীরে সাড়া দিচ্ছে, তাই ফলাফল আসেনি। '
                'কয়েক সেকেন্ড পর আবার খুঁজুন।')
    if 'login' in t.lower() or 'password' in t.lower():
        return ('সার্ভারে সংযোগ করা যাচ্ছে না। '
                'Shikho Tech টিমকে জানান।')
    return ('এখন ফলাফল আনা যাচ্ছে না। কিছুক্ষণ পর আবার চেষ্টা করুন। '
            'বারবার হলে Shikho Tech টিমকে জানান।')


def audit(user, action, detail, ip=''):
    rec = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'user': user,
           'action': action, 'detail': detail, 'ip': ip}
    print('AUDIT ' + json.dumps(rec, ensure_ascii=False))
    try:
        store.push(AUDIT_KEY, rec)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# which field actually matched
#
# The admin panel holds junk identifiers - three separate students share the
# registration 0000000000 - so "found" alone is not proof CX has the right
# person. Show what matched, and say so when one value maps to several.
# ---------------------------------------------------------------------------
BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')


def _digits(v):
    return re.sub(r'\D', '', str(v or '').translate(BN_DIGITS))


def matched_on(q, d):
    qd = _digits(q)
    if qd:
        for label, val in (('রেজিস্ট্রেশন', d.get('registration') or d.get('reg_p')),
                           ('রোল', d.get('roll') or d.get('roll_p')),
                           ('মোবাইল', d.get('phone') or d.get('phone_p')),
                           ('অভিভাবকের মোবাইল', d.get('gurdian_phone'))):
            if val and _digits(val) == qd:
                return label
    qn = re.sub(r'\s+', '', str(q)).lower()
    if qn and qn in re.sub(r'\s+', '', (d.get('name_bn') or '')).lower():
        return 'নাম'
    return 'আংশিক মিল'


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
# Webfont weight is dominated by Bengali glyph coverage, not by how many
# weights are asked for: Hind Siliguri costs 241 KB at one weight and 504 KB at
# two. So one weight of it, one real serif weight for headings where the
# contrast actually shows, and the system monospace for identifiers - 311 KB
# against the 1.02 MB the first cut shipped. display=swap paints text in the
# fallback immediately regardless.
ICON = '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'

FONTS = ('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Fraunces:opsz,wght@9..144,600&'
         'family=Hind+Siliguri:wght@400&display=swap">')

# Fraunces carries the headings - a serif gives the tool some composure without
# costing legibility. Hind Siliguri is a Bengali face, so Bangla labels are set
# properly rather than falling back to whatever the OS offers. Identifiers use a
# mono with a slashed zero, because CX reads registration numbers all day and
# 0/O must never be ambiguous.
CSS = """
:root{
  --yellow:#efad1e; --red:#ee3d5e; --purple:#cf278d; --blue:#354894;
  --ink:#141728; --body:#3f4560; --muted:#727a96; --faint:#9ba2b8;
  --bg:#fbfbfd; --card:#ffffff; --sunken:#f4f5f9;
  --line:#e5e8f0; --hair:#eef0f6;
  --blue-soft:#eef1fa; --red-soft:#fdeef1; --yellow-soft:#fdf6e7;
  --shadow:0 1px 2px rgba(20,23,40,.04), 0 14px 34px rgba(20,23,40,.055);
  --serif:Fraunces,Georgia,"Times New Roman",serif;
  --sans:"Hind Siliguri",-apple-system,"Segoe UI",system-ui,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--body);font-family:var(--sans);
  font-size:15.5px;line-height:1.62;-webkit-font-smoothing:antialiased}
a{color:var(--purple)}
.brandbar{height:3px;background:linear-gradient(90deg,
  var(--yellow) 0%, var(--red) 34%, var(--purple) 67%, var(--blue) 100%)}
header{background:var(--card);border-bottom:1px solid var(--line);
  padding:15px 26px;display:flex;justify-content:space-between;
  align-items:center;flex-wrap:wrap;gap:14px}
header .brand{display:flex;align-items:center;gap:15px;min-width:0}
header img{height:32px;width:auto;display:block}
header .title{font-family:var(--serif);font-size:17px;font-weight:600;
  letter-spacing:-.015em;padding-left:15px;border-left:1px solid var(--line);
  color:var(--ink)}
nav{display:flex;align-items:center;gap:3px;font-size:14px}
nav .who{color:var(--faint);margin-right:10px;font-size:13px;
  font-family:var(--mono)}
nav a{color:var(--muted);text-decoration:none;padding:7px 13px;border-radius:7px;
  transition:background .15s,color .15s}
nav a:hover{background:var(--blue-soft);color:var(--blue)}
.wrap{max-width:1020px;margin:30px auto 72px;padding:0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:26px 28px;margin-bottom:20px;box-shadow:var(--shadow)}
h2{font-family:var(--serif);font-size:20px;font-weight:600;margin:0 0 20px;
  letter-spacing:-.015em;color:var(--ink)}
label{display:block;font-size:11px;font-weight:600;color:var(--muted);
  margin-bottom:8px;letter-spacing:.07em;text-transform:uppercase}
input[type=text],input[type=password],textarea{width:100%;padding:13px 15px;
  border:1px solid #d7dce8;border-radius:9px;font-size:15.5px;font-family:var(--sans);
  background:var(--card);color:var(--ink);transition:border-color .15s,box-shadow .15s}
input[type=text]{font-family:var(--mono);font-size:15px;font-weight:400}
input:focus,textarea:focus{outline:0;border-color:var(--blue);
  box-shadow:0 0 0 3px rgba(53,72,148,.11)}
textarea{min-height:160px;resize:vertical;line-height:1.75;font-family:var(--mono);
  font-size:14px}
button{background:var(--blue);color:#fff;border:0;border-radius:9px;
  padding:13px 26px;font-size:14.5px;font-weight:600;font-family:var(--sans);
  cursor:pointer;transition:background .15s;letter-spacing:.01em}
button:hover{background:#2a3a78}
button:disabled{background:#bcc2d4;cursor:not-allowed}
button.alt{background:var(--card);color:var(--blue);border:1px solid #ccd4e6}
button.alt:hover{background:var(--blue-soft)}
.row{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end}
.row>div{flex:1;min-width:170px}
.hint{font-size:13.5px;color:var(--muted);margin-top:14px;line-height:1.75}
.verdict{border-radius:11px;padding:16px 19px;font-size:16.5px;font-weight:600;
  display:flex;align-items:center;gap:12px;letter-spacing:-.01em}
.verdict .mark{width:25px;height:25px;border-radius:50%;flex:0 0 auto;
  display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px}
.v-yes{background:var(--blue-soft);color:var(--blue);border:1px solid #ccd5ee}
.v-yes .mark{background:var(--blue)}
.v-no{background:var(--red-soft);color:#ac2244;border:1px solid #f6cdd6}
.v-no .mark{background:var(--red)}
.matched{font-size:13px;color:var(--muted);margin:14px 0 0}
.matched b{color:var(--purple);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:14.5px}
th{text-align:left;font-size:10.5px;font-weight:600;color:var(--muted);
  padding:11px 10px;border-bottom:1px solid var(--line);white-space:nowrap;
  text-transform:uppercase;letter-spacing:.08em}
td{padding:11px 10px;border-bottom:1px solid var(--hair);vertical-align:top}
tr:last-child td{border-bottom:0}
td.k{color:var(--muted);width:210px;font-size:13px}
td.v{color:var(--ink)}
td.v.id{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:13.5px}
.scroll{overflow-x:auto;margin-top:8px}
.flags{display:flex;gap:9px;flex-wrap:wrap;margin:20px 0 8px}
.flag{font-size:12px;padding:6px 14px;border-radius:20px;font-weight:600;
  border:1px solid transparent}
.flag.on{background:var(--blue-soft);color:var(--blue);border-color:#ccd5ee}
.flag.off{background:var(--sunken);color:var(--faint);border-color:var(--line)}
.err{background:var(--yellow-soft);border:1px solid #f0ddb0;color:#835500;
  padding:15px 17px;border-radius:10px;font-size:14.5px}
.warn{background:var(--yellow-soft);border:1px solid #f0ddb0;color:#835500;
  padding:13px 17px;border-radius:10px;font-size:13.5px;margin-bottom:20px}
.meta{font-size:12.5px;color:var(--faint);margin-top:18px;
  padding-top:15px;border-top:1px solid var(--hair)}
.meta a{color:var(--purple);text-decoration:none}.meta a:hover{text-decoration:underline}
.pill{font-size:11.5px;padding:4px 11px;border-radius:20px;font-weight:600;white-space:nowrap}
.pill.ok{background:var(--blue-soft);color:var(--blue)}
.pill.bad{background:var(--red-soft);color:#ac2244}
.pill.err{background:var(--yellow-soft);color:#835500}
#bar{height:5px;background:var(--sunken);border-radius:20px;overflow:hidden;margin:20px 0}
#fill{height:100%;width:0;border-radius:20px;transition:width .3s;
  background:linear-gradient(90deg,var(--purple),var(--blue))}
.status{font-size:13.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.empty{color:var(--faint);font-size:14px;padding:10px 0}
:focus-visible{outline:2px solid var(--purple);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media(max-width:560px){.card{padding:20px}header{padding:13px 16px}
  header .title{display:none}td.k{width:140px}.wrap{padding:0 14px}}
"""

DEGRADED_BANNER = ('<div class="warn"><b>সতর্কতা:</b> সার্ভারের একটি সেটিং '
                   'অসম্পূর্ণ। এই অবস্থায় একসাথে অনেকজন খুঁজলে ফলাফল আসতে '
                   'সমস্যা হতে পারে। Shikho Tech টিমকে জানান।</div>')


def page(body, user=None, title='Issue Validation Check', script=''):
    nav = ''
    if user:
        nav = (f'<nav><span class="who">{E(user)}</span>'
               f'<a href="/">একজন</a><a href="/bulk">বাল্ক চেক</a>'
               f'<a href="/logout">লগ আউট</a></nav>')
    warn = DEGRADED_BANNER if (DEGRADED and user) else ''
    return (f'<!doctype html><html lang="bn"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{E(title)}</title>{ICON}{FONTS}<style>{CSS}</style></head><body>'
            f'<div class="brandbar"></div>'
            f'<header><div class="brand">'
            f'<img src="/logo.svg" alt="GPA-5 সংবর্ধনা">'
            f'<span class="title">Issue Validation Check</span></div>{nav}</header>'
            f'<div class="wrap">{warn}{body}</div>{script}</body></html>').encode()


GOOGLE_G = ('<svg width="18" height="18" viewBox="0 0 48 48">'
            '<path fill="#4285F4" d="M45 24c0-1.6-.1-2.7-.4-4H24v7.5h12c-.2 2-1.5 5-4.4 7l6.8 5.3C42.4 36 45 30.6 45 24z"/>'
            '<path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.3l-6.8-5.3c-1.9 1.3-4.4 2.2-7.7 2.2-5.9 0-10.9-3.9-12.7-9.2l-7 5.4C7.9 41 15.4 46 24 46z"/>'
            '<path fill="#FBBC05" d="M11.3 28.4c-.5-1.4-.8-2.9-.8-4.4s.3-3 .8-4.4l-7-5.4C2.9 17.1 2 20.4 2 24s.9 6.9 2.3 9.8l7-5.4z"/>'
            '<path fill="#EA4335" d="M24 10.5c3.3 0 5.5 1.4 6.8 2.6l6-5.9C33.1 3.9 29.9 2 24 2 15.4 2 7.9 7 4.3 14.2l7 5.4C13.1 14.4 18.1 10.5 24 10.5z"/></svg>')


LOGIN_CSS = """
:root{--bg:#fbfbfd;--card:#fff;--line:#e5e8f0;--ink:#141728;--body:#3f4560;
  --muted:#727a96;--faint:#9ba2b8;--blue:#354894;--purple:#cf278d;
  --serif:Fraunces,Georgia,"Times New Roman",serif;
  --sans:"Hind Siliguri",-apple-system,"Segoe UI",system-ui,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--body);font-family:var(--sans);
  -webkit-font-smoothing:antialiased}
.rule{height:3px;background:linear-gradient(90deg,#efad1e,#ee3d5e 34%,#cf278d 67%,#354894)}
.shell{min-height:calc(100% - 3px);display:flex;align-items:center;
  justify-content:center;padding:30px}
.box{width:100%;max-width:400px;background:var(--card);border:1px solid var(--line);
  border-radius:16px;padding:40px 38px 32px;text-align:center;
  box-shadow:0 1px 2px rgba(20,23,40,.04), 0 18px 44px rgba(20,23,40,.07)}
.box img{height:46px;width:auto;margin:0 auto 24px;display:block}
.box h1{font-family:var(--serif);font-size:25px;font-weight:600;margin:0 0 9px;
  letter-spacing:-.02em;color:var(--ink)}
.box .sub{font-size:14.5px;color:var(--muted);margin:0 0 28px;line-height:1.6}
.signin{display:flex;align-items:center;justify-content:center;gap:12px;
  width:100%;background:var(--card);color:#3c4043;border:1px solid #d6dae5;
  border-radius:10px;padding:14px 18px;font-size:15px;font-weight:600;
  font-family:var(--sans);text-decoration:none;cursor:pointer;
  transition:box-shadow .15s,border-color .15s}
.signin:hover{box-shadow:0 2px 10px rgba(20,23,40,.10);border-color:#c3cadb}
.only{font-size:13px;color:var(--muted);margin:20px 0 0}
.only b{color:var(--ink);font-weight:600}
.alert{background:#fdf6e7;border:1px solid #f0ddb0;color:#835500;
  border-radius:9px;padding:12px 15px;font-size:13.5px;margin:0 0 22px;
  text-align:left;line-height:1.6}
.pwform{text-align:left;display:flex;flex-direction:column;gap:14px}
.pwform label{font-size:10.5px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);display:block;margin-bottom:7px}
.pwform input{width:100%;background:var(--card);border:1px solid #d7dce8;
  border-radius:9px;padding:12px 14px;color:var(--ink);font-size:15px;
  font-family:var(--mono)}
.pwform input:focus{outline:0;border-color:var(--blue);
  box-shadow:0 0 0 3px rgba(53,72,148,.11)}
.pwform button{width:100%;background:var(--blue);color:#fff;border:0;
  border-radius:9px;padding:13px;font-size:15px;font-weight:600;
  font-family:var(--sans);cursor:pointer;margin-top:5px}
.pwform button:hover{background:#2a3a78}
:focus-visible{outline:2px solid var(--purple);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def login_page(msg=''):
    alert = f'<div class="alert">{E(msg)}</div>' if msg else ''
    if auth.OAUTH_ON:
        body = (f'<p class="sub">Sign in with your Shikho Google account to continue</p>'
                f'{alert}'
                f'<a class="signin" href="/auth/start">{GOOGLE_G} Sign in with Google</a>'
                f'<p class="only">Only <b>@{E(auth.ALLOWED_DOMAIN)}</b> emails are allowed</p>')
    else:
        # Google is not configured on this deployment, so the portal falls back
        # to the CX_USERS password list. Say so plainly rather than silently
        # showing a different login than the one people were told to expect.
        body = (f'<p class="sub">Google সাইন ইন এখানে চালু নেই — '
                f'পাসওয়ার্ড দিয়ে লগ ইন করুন।</p>{alert}'
                f'<form class="pwform" method="POST" action="/login">'
                f'<div><label>ইউজারনেম</label>'
                f'<input type="text" name="user" autofocus required></div>'
                f'<div><label>পাসওয়ার্ড</label>'
                f'<input type="password" name="pw" required></div>'
                f'<button type="submit">লগ ইন</button></form>'
                f'<p class="only">GOOGLE_CLIENT_ID সেট করলে Google সাইন ইন চালু হবে</p>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Issue Validation Check</title>{ICON}{FONTS}'
            f'<style>{LOGIN_CSS}</style></head>'
            f'<body><div class="rule"></div><div class="shell"><div class="box">'
            f'<img src="/logo.svg" alt="GPA-5 সংবর্ধনা">'
            f'<h1>Issue Validation Check</h1>{body}</div></div></body></html>').encode()


def flag(label, val):
    on = val == 1
    return f'<span class="flag {"on" if on else "off"}">{E(label)} · {"হ্যাঁ" if on else "না"}</span>'


IDENT = {'রোল', 'রেজিস্ট্রেশন', 'মোবাইল', 'অভিভাবকের মোবাইল',
         'রেজিস্ট্রেশন আইডি', 'প্যানেল আইডি', 'শেষ আপডেট'}


def row(k, v):
    """Identifiers get the mono, tabular-numeral column - CX compares these
    digit by digit, so they must align and 0 must not read as O."""
    if v in (None, '', 'None'):
        return ''
    cls = 'v id' if k in IDENT else 'v'
    return f'<tr><td class="k">{E(k)}</td><td class="{cls}">{E(str(v))}</td></tr>' 


def render_student(d, cached, q='', ambiguous=False):
    """Everything below is what the admin panel holds - board, roll and
    registration as stored there, never as CX or the student typed them."""
    rows = ''.join([
        row('নাম', d.get('name_bn')),
        row('বোর্ড', d.get('board_bn') or d.get('board')),
        row('রোল', d.get('roll') or d.get('roll_p')),
        row('রেজিস্ট্রেশন', d.get('registration') or d.get('reg_p')),
        row('মোবাইল', d.get('phone') or d.get('phone_p')),
        row('অভিভাবকের মোবাইল', d.get('gurdian_phone')),
        row('গ্রুপ', d.get('study_group')),
        row('লিঙ্গ', d.get('gender')),
        row('প্রতিষ্ঠান', d.get('institute_name')),
        row('উপজেলা', d.get('upazila_bn') or d.get('upazila')),
        row('নিজ জেলা', d.get('district_bn')),
        row('সংবর্ধনার জেলা', d.get('venue_district_bn')),
        row('রেজিস্ট্রেশন আইডি', d.get('regid_p')),
        row('শেষ আপডেট', (d.get('updated_at') or '')[:10]),
        row('প্যানেল আইডি', d.get('sid')),
        # The panel's own verification status. It is the last field CX used to
        # open the panel for, so it belongs here instead. Shown as হ্যাঁ/না when
        # it is a flag, as text when the panel spells it out.
        ('' if d.get('gpa5_check_status') in (None, '')
         else row('GPA-5 যাচাই',
                  {1: 'হ্যাঁ', 0: 'না'}.get(d.get('gpa5_check_status'),
                                            d.get('gpa5_check_status')))),
    ])
    flags = (flag('প্রোফাইল সম্পূর্ণ', d.get('is_update_profile'))
             + flag('পাসওয়ার্ড সেট', d.get('is_password_set'))
             + flag('অ্যাকাউন্ট সক্রিয়', d.get('is_active'))
             + flag('রেজাল্ট আছে', d.get('has_result')))
    match = (f'<p class="matched">মিলেছে <b>{E(matched_on(q, d))}</b> দিয়ে।'
             + (' একই তথ্যে একাধিক শিক্ষার্থী আছে — নিচের সবগুলো দেখে নিশ্চিত হোন।'
                if ambiguous else '') + '</p>') if q else ''
    src = 'সংরক্ষিত তথ্য' if cached else 'এইমাত্র নেওয়া'
    return f'''<div class="card">
      <div class="verdict v-yes"><span class="mark">✓</span>
        প্রোফাইল পাওয়া গেছে — অ্যাকাউন্ট আছে</div>
      {match}
      <div class="flags">{flags}</div>
      <div class="scroll"><table>{rows}</table></div>
      <div class="meta">উপরের তথ্য প্যানেলে যেভাবে সংরক্ষিত আছে · {src}</div>
    </div>'''


NOT_FOUND = '''<div class="card">
  <div class="verdict v-no"><span class="mark">✕</span>
    প্রোফাইল পাওয়া যায়নি — এই তথ্যে কোনো অ্যাকাউন্ট নেই</div>
  <p class="hint">রেজিস্ট্রেশন নম্বর দিয়ে আরেকবার চেষ্টা করুন। তাতেও না পাওয়া গেলে
  শিক্ষার্থীর রেজিস্ট্রেশন সম্পন্ন হয়নি — নতুন করে রেজিস্ট্রেশন করতে বলুন।</p>
</div>'''


def search_page(user, q='', body=''):
    return page(f'''<div class="card">
      <h2>একজন শিক্ষার্থী খুঁজুন</h2>
      <form method="GET" action="/">
        <div class="row">
          <div><label>রোল / রেজিস্ট্রেশন / মোবাইল / নাম</label>
               <input type="text" name="q" value="{E(q)}" autofocus required
                      placeholder="যেমন 2310929381"></div>
          <div style="flex:0 0 auto"><button type="submit">খুঁজুন</button></div>
        </div>
      </form>
      <p class="hint">নাম দিয়েও খোঁজা যায়, তবে একই নামে একাধিক শিক্ষার্থী থাকতে পারে।
      নিশ্চিত হতে <b>রেজিস্ট্রেশন নম্বর</b> ব্যবহার করুন।
      একসাথে অনেকজন দেখতে <a href="/bulk">বাল্ক চেক</a> ব্যবহার করুন।</p>
    </div>{body}''', user)


BULK_JS = """<script>
const COLS = ['খোঁজার তথ্য','প্রোফাইল','মিলেছে','নাম','বোর্ড',
'রোল','রেজিস্ট্রেশন','মোবাইল','গ্রুপ','লিঙ্গ','প্রতিষ্ঠান',
'নিজ জেলা','সংবর্ধনার জেলা','প্রোফাইল সম্পূর্ণ','পাসওয়ার্ড সেট','অ্যাকাউন্ট সক্রিয়',
'রেজাল্ট আছে','প্যানেল আইডি'];
let rows = [], halted = false;

function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function yn(v){return v===1?'হ্যাঁ':'না';}

function draw(){
  const el = document.getElementById('out');
  if(!rows.length){el.innerHTML='<div class="empty">এখনো কোনো যাচাই করা হয়নি।</div>';return;}
  el.innerHTML='<div class="scroll"><table><tr>'+COLS.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+
    rows.map(r=>'<tr>'+r.map(function(c,i){
      if(i===1){var k=c==='হ্যাঁ'?'ok':(c==='না'?'bad':'err');
        return '<td><span class="pill '+k+'">'+esc(c)+'</span></td>';}
      return '<td>'+esc(c)+'</td>';}).join('')+'</tr>').join('')+'</table></div>';
}

async function run(){
  const raw = document.getElementById('list').value.split(/[\\n,;\\t]+/)
              .map(s=>s.trim()).filter(Boolean);
  if(!raw.length){alert('আগে তালিকাটি পেস্ট করুন।');return;}
  rows=[];halted=false;
  document.getElementById('go').disabled=true;
  document.getElementById('halt').style.display='inline-block';
  document.getElementById('dl').style.display='none';
  for(let i=0;i<raw.length;i++){
    if(halted) break;
    document.getElementById('status').textContent=(i+1)+' / '+raw.length+' — '+raw[i];
    document.getElementById('fill').style.width=(i/raw.length*100)+'%';
    let r;
    try{ r = await (await fetch('/check?q='+encodeURIComponent(raw[i]))).json(); }
    catch(e){ r = {error:'ইন্টারনেট সংযোগে সমস্যা'}; }
    const blank = new Array(15).fill('');
    if(r.error){ rows.push([raw[i],'সমস্যা',r.error].concat(blank)); }
    else if(!r.students || !r.students.length){ rows.push([raw[i],'না',''].concat(blank)); }
    else{
      for(const d of r.students){
        rows.push([raw[i],'হ্যাঁ',d._matched||'',d.name_bn||'',d.board_bn||d.board||'',
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
    'যাচাই শেষ — '+rows.length+'টি ফলাফল'+(halted?' (থামানো হয়েছে)':'');
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
window.addEventListener('DOMContentLoaded',draw);
</script>"""


def bulk_page(user):
    return page('''<div class="card">
      <h2>বাল্ক চেক</h2>
      <label>প্রতি লাইনে একটি — রোল / রেজিস্ট্রেশন / মোবাইল / নাম</label>
      <textarea id="list" placeholder="2310929381&#10;2310937742&#10;01752770779"></textarea>
      <p class="hint">Excel থেকে একটি কলাম কপি করে সরাসরি পেস্ট করতে পারেন।
      একজন করে যাচাই হয়, তাই ৫০ জনে প্রায় ৩ মিনিট সময় লাগে —
      যাচাই শেষ হওয়া পর্যন্ত পেজটি খোলা রাখুন।</p>
      <div id="bar"><div id="fill"></div></div>
      <div class="row">
        <div style="flex:0 0 auto"><button id="go" onclick="run()">চেক শুরু করুন</button></div>
        <div style="flex:0 0 auto"><button id="halt" class="alt" style="display:none"
             onclick="halted=true">থামান</button></div>
        <div style="flex:0 0 auto"><button id="dl" class="alt" style="display:none"
             onclick="download()">CSV ডাউনলোড</button></div>
        <div class="status" id="status"></div>
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

    def _send(self, body, code=200, cookie=None, ctype='text/html; charset=utf-8',
              cache='no-store'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', cache)
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

        if path in ('/logo.svg', '/favicon.svg'):  # Vercel serves public/
            try:                                   # directly; this covers the
                f = LOGO if path == '/logo.svg' else FAVICON   # local runner
                with open(f, 'rb') as fh:
                    return self._send(fh.read(), ctype='image/svg+xml',
                                      cache='public, max-age=86400')
            except OSError:
                return self._send(b'', 404)

        if path == '/health':
            # Reports which variables are SET, never their values. Without this
            # a half-configured deployment just says "google_login: false" and
            # leaves you guessing which of the two is missing.
            need = ('ADMIN_EMAIL', 'ADMIN_PASSWORD', 'PORTAL_SECRET',
                    'ALLOWED_DOMAIN', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET',
                    'UPSTASH_REDIS_REST_URL', 'UPSTASH_REDIS_REST_TOKEN')
            env = {k: bool(os.environ.get(k)) for k in need}
            missing = [k for k, v in env.items() if not v]
            return self._json({
                'ok': not missing,
                'shared_state': not DEGRADED,
                'google_login': auth.OAUTH_ON,
                'domain': auth.ALLOWED_DOMAIN,
                'env_set': env,
                'missing': missing,
                'hint': ('all variables present' if not missing else
                         f'{len(missing)} not set on this deployment - add them, '
                         f'then REDEPLOY (variables only apply to a new build)'),
            })

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
                return self._json({'error': 'সেশন শেষ হয়ে গেছে। আবার লগ ইন করুন।'}, 401)
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
                # CX reads this straight out of the বাল্ক চেক table, so it must
                # be the same plain-Bangla message the single search shows. The
                # exception text stays in the audit log.
                audit(user, 'check_error', {'q': q, 'err': str(e)}, self._ip())
                return self._json({'error': friendly(e)}, 502)
            audit(user, 'check', {'q': q, 'found': len(res)}, self._ip())
            out = []
            for d, _ in res:
                d = dict(d)
                d['_matched'] = matched_on(q, d)
                out.append(d)
            return self._json({'students': out})

        if path != '/':
            return self._send(page('<div class="card">পেজটি পাওয়া যায়নি।</div>', user), 404)

        if not q:
            return self._send(search_page(user))
        try:
            results = admin().lookup(q)
        except BusyError as e:
            body = f'<div class="card"><div class="err">{E(str(e))}</div></div>'
            return self._send(search_page(user, q, body))
        except Exception as e:
            audit(user, 'search_error', {'q': q, 'err': str(e)}, self._ip())
            body = (f'<div class="card"><div class="err">{E(friendly(e))}</div></div>')
            return self._send(search_page(user, q, body))
        audit(user, 'search', {'q': q, 'found': len(results)}, self._ip())
        many = len(results) > 1
        body = ''.join(render_student(d, c, q, many) for d, c in results) or NOT_FOUND
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
