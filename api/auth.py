"""Who is allowed in.

Primary: Google sign-in restricted to the company domain. Anyone with a
@shikho.com Google account can use the portal; nobody else can, and no
passwords have to be issued or rotated.

Typing an address proves nothing, so the email is never taken from the form.
It comes from an id_token that this server fetched directly from Google's
token endpoint over TLS using the client secret - which is what makes the
claim trustworthy - and it must additionally be marked verified by Google and
either sit in ALLOWED_DOMAIN or be named in ALLOWED_EMAILS.

ALLOWED_EMAILS is the guest list: named individuals outside the company domain
(agency and partner staff on their own Google accounts) who need the portal.
It lives in an environment variable rather than in this file because the repo
is public and those are personal addresses.

Fallback: if GOOGLE_CLIENT_ID is unset (local runs), the password users in
CX_USERS are used instead so the portal still starts.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import urlencode

import requests

CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
ALLOWED_DOMAIN = os.environ.get('ALLOWED_DOMAIN', 'shikho.com').lower().lstrip('@')

# Comma-, space- or newline-separated; a pasted list survives either way.
ALLOWED_EMAILS = {e.lower() for e in
                  re.split(r'[,\s]+', os.environ.get('ALLOWED_EMAILS', '')) if e}
SECRET = os.environ.get('PORTAL_SECRET', '')
SESSION_HOURS = 12

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'

OAUTH_ON = bool(CLIENT_ID and CLIENT_SECRET)

try:
    USERS = json.loads(os.environ.get('CX_USERS', '{}'))
except json.JSONDecodeError:
    USERS = {}


class AuthError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# signed values (session cookie, oauth state)
# --------------------------------------------------------------------------
def _sig(body):
    return hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]


def _sign(subject, seconds):
    """A signed, expiring string. int() wraps the whole sum - the state token's
    lifetime is fractional minutes, and a float expiry would serialise as
    '...911.0' and fail to parse on the way back in."""
    body = f'{subject}|{int(time.time() + seconds)}'
    return f'{body}|{_sig(body)}'


def _unsign(tok):
    """Subject of a token whose signature and expiry both check out, else None.
    Says nothing about whether that subject may log in - see read_token."""
    if not SECRET or not tok:
        return None
    try:
        subject, exp, sig = tok.split('|')
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(sig, _sig(f'{subject}|{exp}')):
        return None
    try:
        if float(exp) < time.time():
            return None
    except ValueError:
        return None
    return subject


# Session cookies and OAuth state are both signed strings, but they are not
# interchangeable: a session names a person and must pass the domain gate,
# while the state token names no one. Running state through that gate rejected
# every sign-in, so the two have separate entry points.
STATE_SUBJECT = 'oauth-state'
STATE_TTL = 300


def make_token(email, hours=SESSION_HOURS):
    return _sign(email, hours * 3600)


def read_token(tok):
    email = _unsign(tok)
    if email is None or email == STATE_SUBJECT:
        return None
    if OAUTH_ON:
        return email if allowed(email) else None
    return email if email in USERS else None


def make_state():
    return _sign(STATE_SUBJECT, STATE_TTL)


def check_state(tok):
    return _unsign(tok) == STATE_SUBJECT


def allowed(email):
    if not email:
        return False
    email = email.lower()
    return email.endswith('@' + ALLOWED_DOMAIN) or email in ALLOWED_EMAILS


# --------------------------------------------------------------------------
# password fallback
# --------------------------------------------------------------------------
def hash_pw(pw, salt):
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000)
    return f'{salt}${h.hex()}'


def check_pw(pw, stored):
    try:
        salt, _ = stored.split('$', 1)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(hash_pw(pw, salt), stored)


# --------------------------------------------------------------------------
# Google OAuth
# --------------------------------------------------------------------------
def redirect_uri(host, scheme='https'):
    override = os.environ.get('OAUTH_REDIRECT_URI')
    if override:
        return override
    if host.startswith('localhost') or host.startswith('127.0.0.1'):
        scheme = 'http'
    return f'{scheme}://{host}/auth/callback'


def start_url(host):
    state = make_state()
    q = {
        'client_id': CLIENT_ID,
        'redirect_uri': redirect_uri(host),
        'response_type': 'code',
        'scope': 'openid email profile',
        'prompt': 'select_account',
        'state': state,
    }
    # 'hd' is a hint, but Google honours it by hiding every account outside the
    # domain - which would leave the guest list unable to reach the chooser.
    # Drop it when there is a guest list; the real check is server side either way.
    if not ALLOWED_EMAILS:
        q['hd'] = ALLOWED_DOMAIN
    q = urlencode(q)
    return f'{AUTH_URL}?{q}'


def _decode_id_token(id_token):
    """Read the payload of a token that came straight from Google's token
    endpoint. No signature check is needed because it was not supplied by the
    browser - this server fetched it over TLS using the client secret."""
    payload = id_token.split('.')[1]
    payload += '=' * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def finish(code, state, host):
    if not check_state(state):
        raise AuthError('লগ ইন লিঙ্কটি মেয়াদোত্তীর্ণ। আবার চেষ্টা করুন।')
    r = requests.post(TOKEN_URL, timeout=20, data={
        'code': code, 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'redirect_uri': redirect_uri(host), 'grant_type': 'authorization_code'})
    if r.status_code != 200:
        raise AuthError('Google লগ ইন সম্পন্ন হয়নি। আবার চেষ্টা করুন।')
    claims = _decode_id_token(r.json()['id_token'])

    email = (claims.get('email') or '').lower()
    if not claims.get('email_verified'):
        raise AuthError('Google এই ইমেইলটি ভেরিফাই করেনি।')
    if not allowed(email):
        raise AuthError(f'এই ইমেইলটির অনুমতি নেই — @{ALLOWED_DOMAIN} ইমেইল অথবা '
                        f'অনুমোদিত ঠিকানা দিয়ে লগ ইন করুন। '
                        f'আপনি {email} দিয়ে চেষ্টা করেছেন।')
    return email
