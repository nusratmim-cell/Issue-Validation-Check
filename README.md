# GPA-5 Issue Validation Check

A read-only window onto the gpa5reception admin panel for the CX team.

An agent types a roll / registration / mobile / name — or pastes a whole column
from Excel — and sees whether that student's profile actually exists, with the
**exact board, roll and registration the admin panel holds**, not what was typed
into a support sheet.

> Why it matters: of 163 students who reported "OTP পাচ্ছি না", 151 already had
> an account and 149 already had a password set. They weren't failing to
> register — they were failing to log in, and CX had no way to tell the
> difference. This tool tells them in one search.

## Running locally

Needs `python3` and `requests`. Nothing else.

```bash
python3 tools/hash_pw.py nusrat     # prints a CX_USERS line
cp .env.example .env                # then fill it in
python3 serve.py                    # http://localhost:8443
```

`.env` is gitignored. Google sign-in is skipped locally, so the password
fallback in `CX_USERS` is used instead.

## Deploying to Vercel

Import the repo, then set these under **Settings → Environment Variables**:

| variable | what it is |
|---|---|
| `ADMIN_EMAIL` | admin panel login |
| `ADMIN_PASSWORD` | admin panel password — never reaches a browser |
| `PORTAL_SECRET` | any long random string; signs session cookies |
| `GOOGLE_CLIENT_ID` | OAuth client (below) |
| `GOOGLE_CLIENT_SECRET` | " |
| `ALLOWED_DOMAIN` | `shikho.com` |
| `UPSTASH_REDIS_REST_URL` | shared state — see the warning below |
| `UPSTASH_REDIS_REST_TOKEN` | " |

**Google sign-in.** In Google Cloud Console → APIs & Services → Credentials,
create an *OAuth client ID* of type *Web application*, and add
`https://<your-vercel-domain>/auth/callback` as an authorised redirect URI.
Anyone with a `@shikho.com` Google account can then sign in — no passwords to
issue, no accounts to remove when someone leaves.

**Upstash Redis is not optional in production.** Add the integration from the
Vercel marketplace (free tier is enough). Without it the portal still runs, but
prints a warning banner — see below.

## The pacing rule, and why Redis matters

The admin panel returns 504 under burst. It went down twice during the 2 Sep
data pull. So **every** upstream request passes through one global lock that
holds a minimum **1.5s gap** between requests:

```python
with Pacer():
    r = self.s.get(BASE + path, timeout=TIMEOUT)
```

On Vercel each request may land in a different instance, so that lock has to
live outside the process — that is what Upstash is for. Without it, ten agents
searching at once become ten independent pacers firing together, which is
exactly the burst that took the site down.

Ten agents share roughly **40 lookups per minute**. A single search feels
instant; a 50-student bulk run takes about 90 seconds. That is the trade, and
it is the right way round — the alternative is the site going down for the
students CX is trying to help.

Bulk checking is driven from the browser, one student per request, because no
serverless function can stay alive for 90 seconds.

## Read-only by construction

`api/upstream.py` whitelists exactly two paths:

```
/students?...            (search)
/student-data/<digits>   (detail)
```

Anything else — `/add-student`, `/log-out`, `/dashboard`, a traversal attempt —
raises before a request is made. No code path POSTs to the admin panel except
the login itself. CX cannot change student data through this tool even by
editing the URL.

## Matching is shown, not assumed

The admin panel holds junk identifiers — three separate students share the
registration `0000000000`. So a result never just says "found": it says **which
field matched**, and warns when one value maps to several students. CX confirms
the person rather than trusting the first row.

## Audit

Every login, failed login, search and error is recorded with the agent's real
Google email, what they searched, and the client IP — to stdout (Vercel logs)
and to Redis. Because agents sign in as themselves, the trail names a person
rather than a shared account.

## Files

| file | purpose |
|---|---|
| `api/index.py` | routes, UI, bulk check, CSV export |
| `api/auth.py` | Google sign-in restricted to the company domain |
| `api/upstream.py` | admin client — pacing, read-only whitelist, detail parser |
| `api/store.py` | Redis-backed pacer, session, cache, audit |
| `serve.py` | run the same handler locally |
| `public/logo.svg` | brand mark |
