"""Shared state for the serverless portal.

On Vercel every request may land in a different instance, so nothing that must
be global can live in process memory. Four things must be global:

  * the request pacer   - or each instance paces itself and the admin panel
                          gets hit by all of them at once
  * the admin session   - or every cold start burns a ~6s login
  * the result cache    - or repeat lookups all cost the site a request
  * the audit trail     - or it vanishes with the instance

All four live in Upstash Redis, reached over its REST API so `requests` stays
the only dependency.

If Upstash is not configured the store degrades to per-instance memory and
DEGRADED is True. It still works, but the pacing guarantee is gone, and the
portal renders a warning banner in that state.
"""
import json
import os
import secrets
import threading
import time

import requests

URL = os.environ.get('UPSTASH_REDIS_REST_URL', '').rstrip('/')
TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')

MIN_GAP = 1.5            # seconds between ANY two upstream requests
LOCK_TTL_MS = 20000
LOCK_WAIT = 8.0

LOCK_KEY = 'gpa5:lock'
LAST_KEY = 'gpa5:last_request_at'


class BusyError(RuntimeError):
    pass


class _Memory:
    """Fallback: correct within one instance, no use across many."""
    degraded = True

    def __init__(self):
        self._d = {}
        self._lists = {}
        self._lock = threading.Lock()

    def get(self, k):
        v = self._d.get(k)
        if not v or (v[1] and v[1] < time.time()):
            return None
        return v[0]

    def set(self, k, val, ex=None):
        self._d[k] = (val, time.time() + ex if ex else None)

    def delete(self, k):
        self._d.pop(k, None)

    def set_nx(self, k, val, px):
        with self._lock:
            if self.get(k) is not None:
                return False
            self.set(k, val, ex=px / 1000.0)
            return True

    def push(self, k, val, keep=2000):
        lst = self._lists.setdefault(k, [])
        lst.insert(0, val)
        del lst[keep:]

    def list(self, k, n=100):
        return list(self._lists.get(k, [])[:n])


class _Upstash:
    degraded = False

    def __init__(self, url, token):
        self.url = url
        self.s = requests.Session()
        self.s.headers['Authorization'] = f'Bearer {token}'

    def _cmd(self, *parts):
        r = self.s.post(self.url, json=[str(p) for p in parts], timeout=10)
        r.raise_for_status()
        return r.json().get('result')

    def get(self, k):
        v = self._cmd('GET', k)
        return json.loads(v) if v else None

    def set(self, k, val, ex=None):
        args = ['SET', k, json.dumps(val)]
        if ex:
            args += ['EX', int(ex)]
        self._cmd(*args)

    def delete(self, k):
        self._cmd('DEL', k)

    def set_nx(self, k, val, px):
        return self._cmd('SET', k, json.dumps(val), 'NX', 'PX', int(px)) == 'OK'

    def push(self, k, val, keep=2000):
        self._cmd('LPUSH', k, json.dumps(val))
        self._cmd('LTRIM', k, 0, keep - 1)

    def list(self, k, n=100):
        return [json.loads(x) for x in (self._cmd('LRANGE', k, 0, n - 1) or [])]


store = _Upstash(URL, TOKEN) if URL and TOKEN else _Memory()
DEGRADED = store.degraded


class Pacer:
    """Global serialiser: one upstream request at a time, MIN_GAP apart.

    The lock lives in Redis, so it holds across every warm instance. If it
    cannot be acquired within LOCK_WAIT the caller is told the portal is busy
    rather than being allowed to skip the queue and pile onto the site.
    """

    def __init__(self, min_gap=MIN_GAP, wait=LOCK_WAIT):
        self.min_gap = min_gap
        self.wait = wait
        self._token = None

    def __enter__(self):
        deadline = time.time() + self.wait
        token = secrets.token_hex(8)
        while time.time() < deadline:
            if store.set_nx(LOCK_KEY, token, LOCK_TTL_MS):
                self._token = token
                last = store.get(LAST_KEY) or 0
                gap = time.time() - last
                if gap < self.min_gap:
                    time.sleep(self.min_gap - gap)
                return self
            time.sleep(0.15)
        raise BusyError('সবাই একসাথে সার্চ করছেন। admin panel রক্ষা করতে '
                        'প্রতিটি রিকোয়েস্ট একে একে পাঠানো হয় - আবার চেষ্টা করুন।')

    def __exit__(self, *exc):
        try:
            store.set(LAST_KEY, time.time(), ex=3600)
            if self._token and store.get(LOCK_KEY) == self._token:
                store.delete(LOCK_KEY)
        except Exception:
            pass
        return False
