"""Provision a lending tenant site.

Runs INSIDE the backend container, piped via stdin:

    docker exec -i -e TENANT=acme -e TENANT_ADMIN_EMAIL=ops@acme.com \
        backend-... python - < provision_tenant.py

Env vars:
    TENANT              required, subdomain label AND brand label -> site
                         <TENANT>.<DOMAIN_BASE>, desk label defaults to
                         TENANT.title() (e.g. "acme" -> "Acme")
    TENANT_ADMIN_EMAIL  required, first user of the tenant
    COMPANY_NAME        required, e.g. "Wewole Ltd" (the accounting Company record)
    BRAND_NAME          optional, default TENANT.title(); short label for the desk
                         icon/workspace, e.g. "Wewole" (not the full legal name)
    COMPANY_ABBR        required, e.g. "WL"
    COUNTRY             required, e.g. "Uganda"
    CURRENCY            required, e.g. "UGX"
    TIMEZONE            required, e.g. "Africa/Kampala"
    FY_START_DATE       optional, default Jan 1 of the current year
    FY_END_DATE         optional, default Dec 31 of the current year
    TENANT_ADMIN_PW     optional, random if unset
    ADMIN_PW            optional, random if unset
    DB_PASSWORD         required for site creation (the mariadb root password),
                         not needed for CONFIG_ONLY reruns
    DOMAIN_BASE         optional, default byte10x.dev (single-level subdomain,
                         matches the Cloudflare Universal SSL wildcard cert)
    CONFIG_ONLY         optional "1" to re-apply branding/lockdown to an
                         existing site without recreating it - safe to rerun

This only ever renames existing Workspace/Desktop Icon records to the brand
name; it never adds new icons, logos, or apps.
"""

import datetime
import json
import os
import re
import secrets
import subprocess
import sys

BENCH = "/home/frappe/frappe-bench"
LOG = "/tmp/provision.log"
LENDING_WORKSPACE = "Lending"
KEEP_MODULES = ["Loan Management", "Core"]
TENANT_ROLES = ["Loan Manager"]


class ProvisionError(Exception):
    pass


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd):
    with open(LOG, "ab") as log:
        proc = subprocess.run(cmd, cwd=BENCH, stdout=log, stderr=log)
    if proc.returncode != 0:
        with open(LOG, "rb") as f:
            f.seek(max(0, os.path.getsize(LOG) - 2000))
            raise ProvisionError(f"command failed: {' '.join(cmd)}\n{f.read().decode()}")


def rename_if_needed(doctype, old_name, new_name):
    import frappe

    if frappe.db.exists(doctype, new_name):
        return
    if frappe.db.exists(doctype, old_name):
        frappe.rename_doc(doctype, old_name, new_name, force=True)


def main():
    global LOG

    tenant = os.environ.get("TENANT", "").strip().lower()
    admin_email = os.environ.get("TENANT_ADMIN_EMAIL", "").strip().lower()
    if not tenant or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", admin_email):
        die("set TENANT and a valid TENANT_ADMIN_EMAIL")
    if not tenant.replace("-", "").isalnum():
        die("TENANT must be alphanumeric/hyphen only")
    LOG = f"/tmp/provision-{tenant}.log"

    # short label for the desk icon/workspace/sidebar - "Acme", not the full
    # legal "Acme Ltd" (that stays as the accounting Company record's name)
    brand_name = os.environ.get("BRAND_NAME", "").strip() or tenant.title()
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

    db_root_pw = os.environ.get("DB_PASSWORD", "").strip()
    if not config_only and not db_root_pw:
        die("set DB_PASSWORD (the site's mariadb root password)")
    admin_pw = os.environ.get("ADMIN_PW") or secrets.token_urlsafe(12)
    tenant_pw = os.environ.get("TENANT_ADMIN_PW") or secrets.token_urlsafe(12)

    if not config_only:
        if os.path.exists(LOG):
            os.remove(LOG)

        try:
            print(f"creating site {site} ...", flush=True)
            run(["bench", "new-site", site, "--db-root-password", db_root_pw, "--admin-password", admin_pw])

            for app in ("erpnext", "lending"):
                print(f"installing {app} (this takes several minutes) ...", flush=True)
                run(["bench", "--site", site, "install-app", app])

            run(["bench", "--site", site, "enable-scheduler"])
        except ProvisionError as e:
            print(f"ERROR: {e}\ncleaning up partial site {site} ...", file=sys.stderr)
            subprocess.run(
                ["bench", "drop-site", site, "--db-root-password", db_root_pw, "--no-backup", "--force"],
                cwd=BENCH,
            )
            sys.exit(1)
    else:
        import frappe

        frappe.init(site=site, sites_path=os.path.join(BENCH, "sites"))
        admin_pw = "(unchanged)"

    print("configuring lending-only access ...", flush=True)
    import frappe
    from frappe.permissions import add_permission

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
        # must run after it, not before. Keep condition checks both the
        # pre-rename ("Lending") and post-rename (brand_name) name so this is
        # safe to rerun with CONFIG_ONLY=1.
        for name in frappe.get_all("Workspace", filters={"public": 1}, pluck="name"):
            if name not in (LENDING_WORKSPACE, brand_name):
                frappe.db.set_value("Workspace", name, "is_hidden", 1)
        # Workspace.autoname is "field:label", i.e. "label" IS the docname
        # AND the desk sidebar/breadcrumb text - a plain set_value doesn't
        # take effect, this needs a real rename. The Desktop Icon below has
        # a hardcoded link like "/app/lending" pointing at this Workspace by
        # (lowercased) name, so its link must be updated to match in the
        # same step or it 404s.
        rename_if_needed("Workspace", LENDING_WORKSPACE, brand_name)
        frappe.db.set_value("Workspace", brand_name, "title", brand_name)

        # The /desk home screen is driven by a SEPARATE "Desktop Icon"
        # doctype, unrelated to Workspace.is_hidden - most default visible,
        # so these need to be hidden independently.
        for name in frappe.get_all("Desktop Icon", pluck="name"):
            if name not in (LENDING_WORKSPACE, brand_name):
                frappe.db.set_value("Desktop Icon", name, "hidden", 1)
        rename_if_needed("Desktop Icon", LENDING_WORKSPACE, brand_name)
        frappe.db.set_value("Desktop Icon", brand_name, "link", f"/app/{brand_name.lower()}")

        # A THIRD, separate doctype - "Workspace Sidebar" - drives the sidebar
        # nav tree/header on every non-home desk route (reports, dashboards,
        # doctype lists under the module). Renaming Workspace/Desktop Icon
        # alone leaves this one still labeled "Lending" everywhere except the
        # workspace's own home page.
        rename_if_needed("Workspace Sidebar", LENDING_WORKSPACE, brand_name)

        created_user = not frappe.db.exists("User", admin_email)
        if created_user:
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
        else:
            user = frappe.get_doc("User", admin_email)

        existing_roles = {r.role for r in user.get("roles")}
        for role in TENANT_ROLES:
            if role not in existing_roles:
                user.add_roles(role)
            # the "Page" doctype (module-home routes like /desk/lending) only
            # grants read to Administrator/System Manager by default - without
            # this, the tenant role can't open any Page-type route at all.
            # Note: this grants the role read on Page documents site-wide,
            # not just the kept workspace's page - acceptable since actual
            # module/doctype access is still blocked below.
            add_permission("Page", role, 0)

        already_blocked = {d.module for d in user.get("block_modules")}
        for module in frappe.get_all("Module Def", pluck="name"):
            if module not in KEEP_MODULES and module not in already_blocked:
                user.append("block_modules", {"module": module})
        user.save(ignore_permissions=True)

        frappe.db.commit()
        frappe.clear_cache()
        # desktop icon list AND full bootinfo are each cached per-user in
        # their own redis hash, independent of the generic site cache clear
        # above - without this, a user who loaded /desk before this rename
        # can keep seeing the stale label/icon indefinitely
        frappe.cache.delete_key("desktop_icons")
        frappe.cache.delete_key("bootinfo")
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
                "tenant_admin_password": tenant_pw if created_user else "(unchanged)",
            },
            indent=2,
        )
    )


main()
