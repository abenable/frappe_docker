"""Provision a lending tenant site.

Runs INSIDE the backend container, piped via stdin:

    docker exec -i -e TENANT=acme -e TENANT_ADMIN_EMAIL=ops@acme.com \
        backend-... python - < provision_tenant.py

Env vars:
    TENANT              required, subdomain label -> site <TENANT>.<DOMAIN_BASE>
    TENANT_ADMIN_EMAIL  required, first user of the tenant
    TENANT_ADMIN_PW     optional, random if unset
    ADMIN_PW            optional, random if unset
    DOMAIN_BASE         optional, default lending.byte10x.dev
"""

import json
import os
import secrets
import subprocess
import sys

BENCH = "/home/frappe/frappe-bench"
LOG = "/tmp/provision.log"
KEEP_WORKSPACES = ["Lending"]
KEEP_MODULES = ["Loan Management", "Core"]
TENANT_ROLES = ["Loan Manager"]


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd):
    with open(LOG, "ab") as log:
        proc = subprocess.run(cmd, cwd=BENCH, stdout=log, stderr=log)
    if proc.returncode != 0:
        with open(LOG, "rb") as f:
            f.seek(max(0, os.path.getsize(LOG) - 2000))
            print(f"ERROR: command failed: {' '.join(cmd)}\n{f.read().decode()}", file=sys.stderr)
        sys.exit(1)


def main():
    tenant = os.environ.get("TENANT", "").strip().lower()
    admin_email = os.environ.get("TENANT_ADMIN_EMAIL", "").strip().lower()
    if not tenant or not admin_email.replace(".", "").replace("@", "").isalnum():
        die("set TENANT and TENANT_ADMIN_EMAIL")
    if not tenant.replace("-", "").isalnum():
        die("TENANT must be alphanumeric/hyphen only")

    domain_base = os.environ.get("DOMAIN_BASE", "lending.byte10x.dev")
    site = f"{tenant}.{domain_base}"
    site_dir = os.path.join(BENCH, "sites", site)
    config_only = os.environ.get("CONFIG_ONLY") == "1"
    if not config_only and os.path.exists(site_dir):
        die(f"site {site} already exists")

    db_root_pw = os.environ.get("DB_PASSWORD") or "123"
    admin_pw = os.environ.get("ADMIN_PW") or secrets.token_urlsafe(12)
    tenant_pw = os.environ.get("TENANT_ADMIN_PW") or secrets.token_urlsafe(12)

    if not config_only:
        if os.path.exists(LOG):
            os.remove(LOG)

        print(f"creating site {site} ...", flush=True)
        run(["bench", "new-site", site, "--db-root-password", db_root_pw, "--admin-password", admin_pw])

        for app in ("erpnext", "lending"):
            print(f"installing {app} (this takes several minutes) ...", flush=True)
            run(["bench", "--site", site, "install-app", app])

        run(["bench", "--site", site, "enable-scheduler"])
    else:
        import frappe

        frappe.init(site=site, sites_path=os.path.join(BENCH, "sites"))
        admin_pw = frappe.get_site_config().get("admin_password") or "(unchanged)"

    print("configuring lending-only access ...", flush=True)
    import frappe

    sites_dir = os.path.join(BENCH, "sites")
    os.makedirs(os.path.join(sites_dir, site, "logs"), exist_ok=True)
    # frappe's logger builds relative paths, so the CWD must be the sites dir
    os.chdir(sites_dir)
    frappe.init(site=site, sites_path=sites_dir)
    frappe.connect()
    try:
        # db-level update: saving the Single revalidates language/time_zone,
        # which are empty in a raw (non-request) frappe context
        frappe.db.set_value("System Settings", "System Settings", "default_app", "lending")

        for name in frappe.get_all("Workspace", filters={"public": 1}, pluck="name"):
            if name not in KEEP_WORKSPACES:
                frappe.db.set_value("Workspace", name, "is_hidden", 1)

        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": admin_email,
                "first_name": admin_email.split("@")[0].title(),
                "enabled": 1,
                "user_type": "System User",
                "send_welcome_email": 0,
                "new_password": tenant_pw,
            }
        )
        user.flags.ignore_permissions = True
        user.insert()
        for role in TENANT_ROLES:
            user.add_roles(role)
        for module in frappe.get_all("Module Def", pluck="name"):
            if module not in KEEP_MODULES:
                user.append("block_modules", {"module": module})
        user.save(ignore_permissions=True)

        frappe.db.commit()
        frappe.clear_cache()
    finally:
        frappe.destroy()

    print(
        json.dumps(
            {
                "site": site,
                "url": f"https://{site}",
                "site_administrator": "Administrator",
                "site_admin_password": admin_pw,
                "tenant_admin": admin_email,
                "tenant_admin_password": tenant_pw,
            },
            indent=2,
        )
    )


main()
