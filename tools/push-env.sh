#!/usr/bin/env bash
# Push every environment variable the portal needs from .env into Vercel.
#
# Run this instead of hunting for the Environment Variables screen - the Vercel
# dashboard moves things around, the CLI does not.
#
#   npx vercel login     # once, in your terminal - opens a browser
#   npx vercel link      # once, connects this folder to the Vercel project
#   bash tools/push-env.sh
#
# Values are read from .env, which is gitignored and never leaves your machine
# except to Vercel.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

VARS=(ADMIN_EMAIL ADMIN_PASSWORD PORTAL_SECRET ALLOWED_DOMAIN
      GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
      UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN)
TARGETS=(production preview development)

[ -f .env ] || { echo "no .env here - nothing to push"; exit 1; }
[ -d .vercel ] || { echo "run 'npx vercel link' first (this folder is not linked to a project)"; exit 1; }

echo "Pushing ${#VARS[@]} variables to ${#TARGETS[@]} environments."
echo

for v in "${VARS[@]}"; do
  # cut -d= -f2- keeps '=' characters inside the value itself
  val=$(grep -m1 "^${v}=" .env | cut -d= -f2-)
  if [ -z "$val" ]; then
    echo "  SKIP  $v  (not set in .env)"
    continue
  fi
  for t in "${TARGETS[@]}"; do
    # Remove any existing value first; 'env add' refuses to overwrite.
    npx --yes vercel env rm "$v" "$t" --yes >/dev/null 2>&1
    if printf '%s' "$val" | npx --yes vercel env add "$v" "$t" >/dev/null 2>&1; then
      printf '  ok    %-28s %s\n' "$v" "$t"
    else
      printf '  FAIL  %-28s %s\n' "$v" "$t"
    fi
  done
done

echo
echo "Done. Now redeploy so the new values are picked up:"
echo "    npx vercel --prod"
echo
echo "Then check https://<your-domain>/health - you want"
echo '    "shared_state": true, "google_login": true'
