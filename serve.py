#!/usr/bin/env python3
"""Run the portal locally - the same handler Vercel serves.

    python3 serve.py            # http://localhost:8443

Reads the same environment variables as the deployment. For a quick local run
without Google sign-in, put them in a .env file next to this script (it is
gitignored) and they will be loaded automatically:

    ADMIN_EMAIL=...
    ADMIN_PASSWORD=...
    PORTAL_SECRET=...
    CX_USERS={"nusrat": "<salt>$<hash>"}        # from tools/hash_pw.py
"""
import os
import sys
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

env_file = os.path.join(HERE, '.env')
if os.path.exists(env_file):
    for line in open(env_file):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.join(HERE, 'api'))
import auth              # noqa: E402
from index import handler  # noqa: E402
from store import DEGRADED  # noqa: E402

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8443))
    print(f'portal        http://localhost:{port}')
    print(f'google login  {"on - @" + auth.ALLOWED_DOMAIN if auth.OAUTH_ON else "off (password fallback)"}')
    print(f'shared state  {"per-instance memory - NOT safe for many users" if DEGRADED else "Upstash Redis"}')
    try:
        ThreadingHTTPServer(('0.0.0.0', port), handler).serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')
