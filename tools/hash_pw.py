#!/usr/bin/env python3
"""Generate a CX_USERS entry for the local password fallback.

    python3 tools/hash_pw.py nusrat

Google sign-in is the real login path; this exists only so the portal can run
locally without an OAuth client.
"""
import getpass
import hashlib
import json
import secrets
import sys

name = sys.argv[1] if len(sys.argv) > 1 else input('username: ').strip()
pw = getpass.getpass(f'password for {name}: ')
salt = secrets.token_hex(16)
h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000).hex()
print('\nCX_USERS=' + json.dumps({name: f'{salt}${h}'}))
