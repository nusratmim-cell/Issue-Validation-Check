"""Read-only, globally-paced client for the gpa5reception admin panel.

Two rules are enforced here and nowhere else, so no caller can bypass them:

  1. READ ONLY. Only /students?... and /student-data/<digits> may ever be
     requested. No POST, no /add-student, no /log-out, no /dashboard.
  2. PACED. Every upstream request goes through store.Pacer, a Redis-backed
     global lock that keeps a minimum gap between requests across every
     serverless instance. The admin panel 504s under burst - it fell over
     twice during the 2 Sep data pull - so CX traffic must never become one.
"""
import json
import os
import re
import time
from urllib.parse import quote

import requests

from store import Pacer, store

BASE = 'https://www.gpa5reception.com'
TIMEOUT = 45
SESSION_KEY = 'gpa5:admin_cookies'
SESSION_TTL = 3600

_READ_OK = (
    re.compile(r'^/students(\?[^#]*)?$'),
    re.compile(r'^/student-data/\d+$'),
)

# --- detail page parser (same one the 2 Sep harvest used) -------------------
FIELDS = ['registration', 'roll', 'gender', 'study_group', 'event_district',
          'gpa5_check_status', 'is_active', 'is_password_set',
          'is_update_profile', 'has_result', 'gurdian_phone', 'phone',
          'registration_number', 'institute_name', 'upazila', 'updated_at',
          'district', 'board']

LABELS = [('Name', 'name_bn'), ('District', 'district_bn'),
          ('Venue District', 'venue_district_bn'), ('Upazila', 'upazila_bn'),
          ('Board', 'board_bn'), ('Roll', 'roll_p'), ('Registration', 'reg_p'),
          ('Phone', 'phone_p'), ('Reg ID', 'regid_p')]


def parse_detail(h):
    d = {}
    for f in FIELDS:
        m = re.search(r'&quot;' + f + r'&quot;:\s*(?:&quot;(.*?)&quot;|(-?\d+)|(null|true|false))', h)
        if not m:
            continue
        if m.group(1) is not None:
            try:
                d[f] = json.loads('"' + m.group(1).replace('"', '\\"') + '"')
            except Exception:
                d[f] = m.group(1)
        elif m.group(2) is not None:
            d[f] = int(m.group(2))
        else:
            d[f] = {'null': None, 'true': 1, 'false': 0}[m.group(3)]
    for label, key in LABELS:
        m = re.search(r'<p>' + label + r':\s*(.*?)</p>', h)
        if m:
            d[key] = re.sub(r'\s*\(\d+\)\s*$', '', m.group(1).strip())
    return d


class AdminError(RuntimeError):
    pass


class Upstream:
    def __init__(self):
        self.email = os.environ.get('ADMIN_EMAIL', '')
        self.password = os.environ.get('ADMIN_PASSWORD', '')
        self.s = requests.Session()
        self.s.headers['User-Agent'] = 'Mozilla/5.0 Chrome/128'
        self._restore()

    # -- session shared across instances -------------------------------------
    def _restore(self):
        try:
            jar = store.get(SESSION_KEY)
        except Exception:
            jar = None
        if jar:
            self.s.cookies.update(jar)
        return bool(jar)

    def _remember(self):
        try:
            store.set(SESSION_KEY, self.s.cookies.get_dict(), ex=SESSION_TTL)
        except Exception:
            pass

    # -- internals -----------------------------------------------------------
    @staticmethod
    def _check_path(path):
        if not any(p.match(path) for p in _READ_OK):
            raise ValueError(f'refused: {path!r} is not a read-only path')

    def login(self):
        if not self.email or not self.password:
            raise AdminError('ADMIN_EMAIL / ADMIN_PASSWORD are not set on the server')
        with Pacer():
            r = self.s.get(BASE + '/login', timeout=TIMEOUT)
        m = re.search(r'name="_token" value="([^"]+)"', r.text)
        if not m:
            raise AdminError('login page has no CSRF token')
        with Pacer():
            self.s.post(BASE + '/login', timeout=TIMEOUT, data={
                '_token': m.group(1), 'email': self.email,
                'password': self.password})
        self._remember()

    def _get(self, path):
        self._check_path(path)
        for attempt in (1, 2):
            if not self.s.cookies:
                self.login()
            with Pacer():
                r = self.s.get(BASE + path, timeout=TIMEOUT)
            if r.status_code == 200 and 'Please Sign In' not in r.text:
                return r
            if attempt == 1:
                self.s.cookies.clear()
                self.login()
                continue
            if r.status_code != 200:
                raise AdminError(f'admin panel returned {r.status_code}')
            raise AdminError('admin login rejected - check ADMIN_PASSWORD')
        raise AdminError('unreachable')

    # -- public --------------------------------------------------------------
    def search_ids(self, query):
        """Admin student ids matching a free-text query.

        The admin search matches roll, registration, phone AND name, so an
        agent can type whichever identifier the student gave them.
        """
        q = quote(str(query).strip())
        r = self._get(f'/students?query={q}&district=&status=&date=')
        return list(dict.fromkeys(re.findall(r'/student-data/(\d+)', r.text)))

    def detail(self, sid):
        sid = str(int(sid))                       # hard numeric guard
        r = self._get(f'/student-data/{sid}')
        d = parse_detail(r.text)
        d['sid'] = sid
        return d

    def lookup(self, query, limit=5, cache_ttl=120):
        """Search then fetch details, using the shared cache where possible."""
        key = f'gpa5:q:{query.strip().lower()}'
        try:
            ids = store.get(key)
        except Exception:
            ids = None
        if ids is None:
            ids = self.search_ids(query)
            try:
                store.set(key, ids, ex=60)
            except Exception:
                pass

        out = []
        for sid in ids[:limit]:
            dkey = f'gpa5:d:{sid}'
            try:
                d = store.get(dkey)
            except Exception:
                d = None
            cached = d is not None
            if d is None:
                d = self.detail(sid)
                try:
                    store.set(dkey, d, ex=cache_ttl)
                except Exception:
                    pass
            out.append((d, cached))
        return out
