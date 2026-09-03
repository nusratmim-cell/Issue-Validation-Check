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
import random
import threading
import time

import requests

URL = os.environ.get('UPSTASH_REDIS_REST_URL', '').rstrip('/')
TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')

MIN_GAP = 1.5            # seconds between ANY two upstream requests
LOCK_WAIT = 30.0         # ceiling only; callers pass what their budget allows

GAP_KEY = 'gpa5:gap'


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

    def pttl(self, k):
        v = self._d.get(k)
        return max(0, int((v[1] - time.time()) * 1000)) if v and v[1] else 0

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

    def pttl(self, k):
        return max(0, int(self._cmd('PTTL', k) or 0))

    def push(self, k, val, keep=2000):
        self._cmd('LPUSH', k, json.dumps(val))
        if random.random() < 0.05:          # amortised trim, not every write
            self._cmd('LTRIM', k, 0, keep - 1)

    def list(self, k, n=100):
        return [json.loads(x) for x in (self._cmd('LRANGE', k, 0, n - 1) or [])]


store = _Upstash(URL, TOKEN) if URL and TOKEN else _Memory()
DEGRADED = store.degraded


class Pacer:
    """Global rate gate: at most one upstream request per MIN_GAP, fleet-wide.

    Implemented as a single expiring key. `SET gap NX PX 1500` succeeds only
    when no one has taken a turn in the last 1.5s, so successful acquisitions
    are inherently >=MIN_GAP apart no matter how many instances are running.

    That is one Redis command per turn, against five for the lock-plus-clock
    version this replaces - which matters on Upstash's 500K/month free tier.
    When a turn is refused, PTTL says exactly how long to wait, so a queued
    caller sleeps once rather than polling.

    Note this caps the *rate*, not concurrency: two requests may briefly be in
    flight. Rate is what protects the admin panel; overlap does not hurt it.
    """

    def __init__(self, min_gap=MIN_GAP, wait=LOCK_WAIT):
        self.min_gap = min_gap
        self.wait = wait

    def __enter__(self):
        deadline = time.time() + self.wait
        attempt = 0
        while True:
            if store.set_nx(GAP_KEY, 1, int(self.min_gap * 1000)):
                return self
            left = max(0.0, deadline - time.time())
            if left <= 0:
                break
            attempt += 1
            # PTTL says exactly when the gate reopens, so wait that long and no
            # longer. Progressive backoff was tried here and cut Redis commands
            # ~40%, but left the gate idle - 20 turns took 81s against 29s - so
            # throughput wins. Small jitter keeps queued agents from all waking
            # on the same millisecond.
            try:
                ttl = store.pttl(GAP_KEY) / 1000.0
            except Exception:
                ttl = 0.2
            time.sleep(min(max(ttl, 0.05) + random.uniform(.02, .3), left))
        raise BusyError('এখন একসাথে অনেকজন খুঁজছেন। '
                        'কিছুক্ষণ পর আবার চেষ্টা করুন।')

    def __exit__(self, *exc):
        return False
