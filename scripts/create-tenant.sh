#!/usr/bin/env bash
# Create a lending tenant: new site + apps + setup wizard + lending-only access.
#
# Usage (on the server):
#   COMPANY_NAME="Acme Ltd" COMPANY_ABBR=AL COUNTRY=Uganda CURRENCY=UGX TIMEZONE=Africa/Kampala \
#     ./create-tenant.sh <tenant-label> <tenant-admin-email> [tenant-admin-password] [site-admin-password]
#
# Example:
#   COMPANY_NAME="Acme Ltd" COMPANY_ABBR=AL COUNTRY=Uganda CURRENCY=UGX TIMEZONE=Africa/Kampala \
#     ./create-tenant.sh acme ops@acme.com
#   -> site acme.byte10x.dev, login for ops@acme.com, workspace labeled "Acme Ltd"
#
# DOMAIN_BASE defaults to byte10x.dev (single-level subdomain) to match the
# Cloudflare Universal SSL cert, which only covers *.byte10x.dev, not a
# second-level wildcard like *.lending.byte10x.dev.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <tenant-label> <tenant-admin-email> [tenant-admin-password] [site-admin-password]" >&2
  exit 1
fi

for var in COMPANY_NAME COMPANY_ABBR COUNTRY CURRENCY TIMEZONE; do
  if [[ -z "${!var:-}" ]]; then
    echo "set $var (see usage in this script's header)" >&2
    exit 1
  fi
done

BACKEND=$(docker ps --filter "name=backend-" --format '{{.Names}}' | head -n1)
if [[ -z "$BACKEND" ]]; then
  echo "no backend container running" >&2
  exit 1
fi

docker exec -i \
  -e TENANT="$1" \
  -e TENANT_ADMIN_EMAIL="$2" \
  -e TENANT_ADMIN_PW="${3:-}" \
  -e ADMIN_PW="${4:-}" \
  -e COMPANY_NAME="$COMPANY_NAME" \
  -e COMPANY_ABBR="$COMPANY_ABBR" \
  -e COUNTRY="$COUNTRY" \
  -e CURRENCY="$CURRENCY" \
  -e TIMEZONE="$TIMEZONE" \
  -e FY_START_DATE="${FY_START_DATE:-}" \
  -e FY_END_DATE="${FY_END_DATE:-}" \
  -e DOMAIN_BASE="${LENDING_DOMAIN_BASE:-byte10x.dev}" \
  -e CONFIG_ONLY="${CONFIG_ONLY:-0}" \
  "$BACKEND" /home/frappe/frappe-bench/env/bin/python - < "$(dirname "$0")/provision_tenant.py"
