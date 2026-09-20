#!/usr/bin/env bash
# Create a lending tenant: new site + apps + lending-only access.
#
# Usage (on the server):
#   ./create-tenant.sh <tenant-label> <tenant-admin-email> [tenant-admin-password] [site-admin-password]
#
# Example:
#   ./create-tenant.sh acme ops@acme.com
#   -> site acme.byte10x.dev, login for ops@acme.com
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <tenant-label> <tenant-admin-email> [tenant-admin-password] [site-admin-password]" >&2
  exit 1
fi

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
  -e DOMAIN_BASE="${LENDING_DOMAIN_BASE:-lending.byte10x.dev}" \
  -e CONFIG_ONLY="${CONFIG_ONLY:-0}" \
  "$BACKEND" /home/frappe/frappe-bench/env/bin/python - < "$(dirname "$0")/provision_tenant.py"
