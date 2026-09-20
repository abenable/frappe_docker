"""Provision a lending tenant site.

Runs INSIDE the backend container, piped via stdin:

    docker exec -i -e TENANT=acme -e TENANT_ADMIN_EMAIL=ops@acme.com \
        backend-... python - < provision_tenant.py

Env vars:
    TENANT              required, subdomain label -> site <TENANT>.<DOMAIN_BASE>
    TENANT_ADMIN_EMAIL  required, first user of the tenant
    COMPANY_NAME        required, e.g. "Wewole Ltd" (also becomes the workspace label)
    COMPANY_ABBR        required, e.g. "WL"
    COUNTRY             required, e.g. "Uganda"
    CURRENCY            required, e.g. "UGX"
    TIMEZONE            required, e.g. "Africa/Kampala"
    FY_START_DATE       optional, default Jan 1 of the current year
    FY_END_DATE         optional, default Dec 31 of the current year
    TENANT_ADMIN_PW     optional, random if unset
    ADMIN_PW            optional, random if unset
    DOMAIN_BASE         optional, default byte10x.dev (single-level subdomain,
                         matches the Cloudflare Universal SSL wildcard cert)
"""

import datetime
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

    company_name = os.environ.get("COMPANY_NAME", "").strip()
    company_abbr = os.environ.get("COMPANY_ABBR", "").strip()
    country = os.environ.get("COUNTRY", "").strip()
    currency = os.environ.get("CURRENCY", "").strip()
    timezone = os.environ.get("TIMEZONE", "").strip()
    if not all([company_name, company_abbr, country, currency, timezone]):
        die("set COMPANY_NAME, COMPANY_ABBR, COUNTRY, CURRENCY, TIMEZONE")
    this_year = datetime.date.today().year
    fy_start_date = os.environ.get("FY_START_DATE") or f"{this_year}-01-01"
    fy_end_date = os.environ.get("FY_END_DATE") or f"{this_year}-12-31"

    domain_base = os.environ.get("DOMAIN_BASE", "byte10x.dev")
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
        frappe.set_user("Administrator")
        if not frappe.is_setup_complete():
            print("running setup wizard ...", flush=True)
            from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

            setup_complete(
                {
                    # no "email"/"full_name": passing them makes setup_wizard
                    # create a NEW User with a System Manager role, which
                    # would defeat the lending-only lockdown below
                    "language": "English",
                    "country": country,
                    "timezone": timezone,
                    "currency": currency,
                    "company_name": company_name,
                    "company_abbr": company_abbr,
                    "fy_start_date": fy_start_date,
                    "fy_end_date": fy_end_date,
                    "chart_of_accounts": "Standard",
                    "bank_account": "Bank Account",
                }
            )
            frappe.db.commit()

        # db-level update: saving the Single revalidates language/time_zone,
        # which are empty in a raw (non-request) frappe context
        frappe.db.set_value("System Settings", "System Settings", "default_app", "lending")

        # setup wizard re-creates/unhides several standard workspaces, so this
        # must run after it, not before
        for name in frappe.get_all("Workspace", filters={"public": 1}, pluck="name"):
            if name not in KEEP_WORKSPACES:
                frappe.db.set_value("Workspace", name, "is_hidden", 1)
        for name in KEEP_WORKSPACES:
            frappe.db.set_value("Workspace", name, {"title": company_name, "label": company_name})

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
