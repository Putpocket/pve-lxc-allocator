# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small self-service allocator for existing Proxmox LXC containers."""

import csv
import fcntl
import logging
import os
import re
import secrets
import string
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import Flask, Response, abort, redirect, render_template_string, request, session, url_for
from proxmoxer import ProxmoxAPI


def required_env(name, min_length=1):
    value = os.environ.get(name, "")
    if len(value) < min_length:
        raise RuntimeError(f"{name} must be set and contain at least {min_length} characters")
    return value


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def env_bool(name, default):
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} must be true or false")
    return value == "true"


LANGUAGE = os.environ.get("APP_LANGUAGE", "en").lower()
if LANGUAGE not in {"en", "ko"}:
    raise RuntimeError("APP_LANGUAGE must be en or ko")

PROXMOX_HOST = required_env("PROXMOX_HOST")
PROXMOX_USER = required_env("PROXMOX_USER")
PROXMOX_TOKEN_NAME = required_env("PROXMOX_TOKEN_NAME")
PROXMOX_TOKEN_VALUE = required_env("PROXMOX_TOKEN_VALUE")
PROXMOX_VERIFY_SSL = env_bool("PROXMOX_VERIFY_SSL", True)
PROXMOX_REALM = os.environ.get("PROXMOX_REALM", "pve")
LXC_ROLE = os.environ.get("LXC_ROLE", "PVEVMUser")
LXC_START_VMID = env_int("LXC_START_VMID", 100)
LXC_END_VMID = env_int("LXC_END_VMID", 199)
ADMIN_PASSWORD = required_env("ADMIN_PASSWORD", 12)
APP_SECRET_KEY = required_env("APP_SECRET_KEY", 32)
APP_TITLE = os.environ.get("APP_TITLE", "Proxmox LXC Allocator")
INSTANCE_LABEL = os.environ.get("INSTANCE_LABEL", f"LXC {LXC_START_VMID}–{LXC_END_VMID}")
CONTACT_TEXT = os.environ.get("CONTACT_TEXT", "Contact your administrator for help.")
LOG_FILE = os.path.abspath(os.environ.get("ALLOCATION_FILE", "issued_log.csv"))

if LXC_START_VMID < 100 or LXC_END_VMID < LXC_START_VMID:
    raise RuntimeError("LXC VMIDs must define a valid range starting at 100 or higher")
TEXT = {
    "en": {
        "internal": "Internal network",
        "remaining": "Available containers",
        "complete": "Allocation complete",
        "user": "Proxmox user",
        "realm": "Login realm",
        "password": "Initial password",
        "vmid": "Assigned LXC VMID",
        "save_password": "Save this password now. It will not be shown again, and you should change it after signing in.",
        "closed": "No containers available",
        "closed_help": "All configured containers have been assigned.",
        "notice": "The requested username, assigned VMID, and allocation time are stored by this service.",
        "username": "Requested username",
        "username_placeholder": "Lowercase letters and numbers",
        "username_hint": "3–20 lowercase ASCII letters or numbers; must include at least one letter.",
        "allocate": "Create account and assign container",
        "invalid_username": "Use lowercase English letters and numbers only.",
        "invalid_length": "The username must contain 3–20 characters.",
        "numeric_username": "The username must include at least one letter.",
        "duplicate": "That username has already been allocated.",
        "server_error": "Allocation failed. The username may already exist; contact the administrator if the problem continues.",
        "admin_login": "Administrator sign in",
        "admin_password": "Administrator password",
        "sign_in": "Sign in",
        "bad_password": "The password is incorrect.",
        "dashboard": "Administrator dashboard",
        "total": "Configured",
        "issued": "Allocated",
        "download": "Download CSV",
        "logout": "Sign out",
        "time": "Allocation time",
        "status": "Status",
        "allocated": "Allocated",
        "pending": "Pending review",
        "needs_review": "Needs review",
        "none": "No allocations yet.",
        "no_log": "No allocation file exists.",
    },
    "ko": {
        "internal": "내부망 전용",
        "remaining": "사용 가능한 컨테이너",
        "complete": "할당 완료",
        "user": "Proxmox 사용자",
        "realm": "로그인 영역",
        "password": "초기 비밀번호",
        "vmid": "할당된 LXC VMID",
        "save_password": "이 비밀번호는 다시 표시되지 않습니다. 지금 저장하고 로그인 후 변경하세요.",
        "closed": "사용 가능한 컨테이너 없음",
        "closed_help": "설정된 모든 컨테이너가 할당되었습니다.",
        "notice": "요청한 사용자 이름, 할당된 VMID, 할당 시각이 이 서비스에 저장됩니다.",
        "username": "사용자 이름",
        "username_placeholder": "영문 소문자와 숫자",
        "username_hint": "영문 소문자·숫자 3~20자이며 영문자를 하나 이상 포함해야 합니다.",
        "allocate": "계정 생성 및 컨테이너 할당",
        "invalid_username": "영문 소문자와 숫자만 사용할 수 있습니다.",
        "invalid_length": "사용자 이름은 3~20자여야 합니다.",
        "numeric_username": "사용자 이름에는 영문자가 하나 이상 필요합니다.",
        "duplicate": "이미 할당된 사용자 이름입니다.",
        "server_error": "할당에 실패했습니다. 이미 존재하는 사용자일 수 있으며, 문제가 계속되면 관리자에게 문의하세요.",
        "admin_login": "관리자 로그인",
        "admin_password": "관리자 비밀번호",
        "sign_in": "로그인",
        "bad_password": "비밀번호가 올바르지 않습니다.",
        "dashboard": "관리자 대시보드",
        "total": "전체",
        "issued": "할당 완료",
        "download": "CSV 다운로드",
        "logout": "로그아웃",
        "time": "할당 시각",
        "status": "상태",
        "allocated": "할당 완료",
        "pending": "확인 대기",
        "needs_review": "관리자 확인 필요",
        "none": "아직 할당 내역이 없습니다.",
        "no_log": "할당 파일이 없습니다.",
    },
}[LANGUAGE]

app = Flask(__name__)
app.config.update(
    SECRET_KEY=APP_SECRET_KEY,
    MAX_CONTENT_LENGTH=16 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_bool("SESSION_COOKIE_SECURE", True),
)

proxmox = ProxmoxAPI(
    PROXMOX_HOST,
    user=PROXMOX_USER,
    token_name=PROXMOX_TOKEN_NAME,
    token_value=PROXMOX_TOKEN_VALUE,
    verify_ssl=PROXMOX_VERIFY_SSL,
    timeout=10,
)


@app.after_request
def secure_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
        "form-action 'self'; base-uri 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("_csrf_token", "")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            abort(400)


@contextmanager
def allocation_lock():
    fd = os.open(f"{LOG_FILE}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_rows():
    if not os.path.isfile(LOG_FILE):
        return []
    with open(LOG_FILE, newline="", encoding="utf-8") as file:
        return [
            {
                "Timestamp": row.get("Timestamp", ""),
                "UserID": row.get("UserID", ""),
                "Allocated_VMID": row.get("Allocated_VMID", ""),
                "Status": row.get("Status") or "allocated",
            }
            for row in csv.DictReader(file)
        ]


def get_issued_data():
    allocated_vmids = set()
    issued_userids = set()
    for row in read_rows():
        try:
            vmid = int(row["Allocated_VMID"])
            if LXC_START_VMID <= vmid <= LXC_END_VMID:
                allocated_vmids.add(vmid)
            issued_userids.add(row["UserID"].lower())
        except (KeyError, TypeError, ValueError):
            logging.warning("Ignoring a malformed row in %s", LOG_FILE)
    return allocated_vmids, issued_userids


def get_next_vmid(allocated_vmids):
    return next((vmid for vmid in range(LXC_START_VMID, LXC_END_VMID + 1) if vmid not in allocated_vmids), None)


def write_rows(rows):
    directory = os.path.dirname(LOG_FILE)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=directory, delete=False, newline="", encoding="utf-8"
        ) as file:
            temp_path = file.name
            os.fchmod(file.fileno(), 0o600)
            writer = csv.DictWriter(
                file, fieldnames=["Timestamp", "UserID", "Allocated_VMID", "Status"]
            )
            writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, LOG_FILE)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def reserve_allocation(user_id, vmid):
    rows = read_rows()
    rows.append(
        {
            "Timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "UserID": user_id,
            "Allocated_VMID": vmid,
            "Status": "pending",
        }
    )
    write_rows(rows)


def set_allocation_status(user_id, vmid, status):
    rows = read_rows()
    for row in rows:
        if row["UserID"].lower() == user_id and row["Allocated_VMID"] == str(vmid):
            row["Status"] = status
            break
    write_rows(rows)


def remove_reservation(user_id, vmid):
    write_rows(
        [
            row
            for row in read_rows()
            if not (row["UserID"].lower() == user_id and row["Allocated_VMID"] == str(vmid))
        ]
    )


def make_random_password(length=16):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


BASE_STYLE = """
:root {
  --ink: #20262c;
  --muted: #687078;
  --line: #d8d8d2;
  --paper: #ffffff;
  --canvas: #f3f2ef;
  --accent: #d65a1f;
  --accent-dark: #aa4315;
  --danger: #a92b24;
  --success: #27734f;
}
* { box-sizing: border-box; }
html { color-scheme: light; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--canvas);
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.55;
}
.shell { width: min(100% - 32px, 640px); margin: 0 auto; padding: 64px 0 40px; }
.shell.wide { width: min(100% - 32px, 1120px); }
.masthead {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
  padding-top: 20px;
  border-top: 4px solid var(--accent);
}
h1, h2, p { margin-top: 0; }
h1 { margin-bottom: 4px; font-size: clamp(1.55rem, 4vw, 2rem); line-height: 1.15; letter-spacing: -.025em; }
h2 { margin-bottom: 20px; font-size: 1.2rem; }
.eyebrow { margin-bottom: 8px; color: var(--accent-dark); font-size: .72rem; font-weight: 750; letter-spacing: .11em; text-transform: uppercase; }
.muted, .hint, footer { color: var(--muted); }
.muted { margin-bottom: 0; }
.panel { padding: 28px; background: var(--paper); border: 1px solid var(--line); border-radius: 4px; }
.availability { display: flex; align-items: baseline; justify-content: space-between; gap: 20px; margin: -4px 0 24px; padding-bottom: 20px; border-bottom: 1px solid var(--line); }
.availability span { color: var(--muted); }
.availability strong { font-size: 2rem; font-variant-numeric: tabular-nums; }
.notice, .error, .warning { margin: 20px 0; padding: 12px 14px; border-left: 3px solid; background: #f7f7f4; }
.notice { border-color: var(--accent); }
.error { color: var(--danger); border-color: var(--danger); background: #fff6f5; }
.warning { color: #7a4117; border-color: #c8782a; background: #fff8ef; }
label { display: block; margin: 22px 0 7px; font-size: .9rem; font-weight: 700; }
input {
  width: 100%;
  min-height: 46px;
  padding: 10px 12px;
  border: 1px solid #aaa9a2;
  border-radius: 3px;
  background: #fff;
  color: var(--ink);
  font: inherit;
}
input:focus { border-color: var(--accent); outline: 3px solid #d65a1f2b; outline-offset: 1px; }
.hint { margin-top: 7px; font-size: .82rem; }
button, .button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  padding: 10px 16px;
  border: 1px solid var(--ink);
  border-radius: 3px;
  background: var(--ink);
  color: #fff;
  font: inherit;
  font-weight: 700;
  text-decoration: none;
  cursor: pointer;
}
button:hover, .button:hover { border-color: var(--accent-dark); background: var(--accent-dark); }
button:focus-visible, .button:focus-visible { outline: 3px solid #d65a1f4d; outline-offset: 2px; }
button.secondary, .button.secondary { border-color: #aaa9a2; background: transparent; color: var(--ink); }
button.secondary:hover, .button.secondary:hover { border-color: var(--ink); background: #e9e8e3; }
.primary-action { width: 100%; margin-top: 24px; }
footer { margin-top: 20px; font-size: .8rem; text-align: center; }
@media (max-width: 600px) {
  .shell { padding-top: 32px; }
  .panel { padding: 22px 18px; }
  .masthead { gap: 12px; }
}
"""


MAIN_TEMPLATE = """
<!doctype html>
<html lang="{{ language }}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ app_title }}</title>
  <style>{{ base_style|safe }}
    .credentials { margin: 0; border-top: 1px solid var(--line); }
    .credentials div { display: grid; grid-template-columns: minmax(120px, 1fr) 2fr; gap: 20px; padding: 13px 0; border-bottom: 1px solid var(--line); }
    .credentials dt { color: var(--muted); }
    .credentials dd { margin: 0; font-weight: 700; text-align: right; overflow-wrap: anywhere; }
    .credentials .password { color: var(--danger); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 1.08rem; user-select: all; }
    @media (max-width: 440px) { .credentials div { grid-template-columns: 1fr; gap: 2px; } .credentials dd { text-align: left; } }
  </style>
</head>
<body>
<main class="shell">
  <header class="masthead">
    <div>
      <div class="eyebrow">{{ t.internal }}</div>
      <h1>{{ app_title }}</h1>
      <p class="muted">{{ instance_label }}</p>
    </div>
  </header>
  <section class="panel">
    {% if success %}
      <div aria-live="polite">
        <h2>{{ t.complete }}</h2>
        <dl class="credentials">
          <div><dt>{{ t.user }}</dt><dd>{{ user_id }}</dd></div>
          <div><dt>{{ t.realm }}</dt><dd>{{ realm }}</dd></div>
          <div><dt>{{ t.password }}</dt><dd class="password">{{ password }}</dd></div>
          <div><dt>{{ t.vmid }}</dt><dd>{{ vmid }}</dd></div>
        </dl>
        <div class="warning">{{ t.save_password }}</div>
      </div>
    {% elif closed %}
      <div><h2>{{ t.closed }}</h2><p class="muted">{{ t.closed_help }}</p></div>
    {% else %}
      <div class="availability"><span>{{ t.remaining }}</span><strong>{{ remaining }}</strong></div>
      {% if error %}<div class="error" role="alert">{{ error }}</div>{% endif %}
      <p class="notice">{{ t.notice }}</p>
      <form method="post" action="{{ url_for('allocate') }}">
        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
        <label for="user_id">{{ t.username }}</label>
        <input id="user_id" name="user_id" value="{{ prev_id }}" placeholder="{{ t.username_placeholder }}"
               pattern="[a-z0-9]+" minlength="3" maxlength="20" autocomplete="username" required autofocus>
        <div class="hint">{{ t.username_hint }}</div>
        <button class="primary-action" type="submit">{{ t.allocate }}</button>
      </form>
    {% endif %}
  </section>
  <footer>{{ contact_text }}</footer>
</main>
</body>
</html>
"""

ADMIN_LOGIN_TEMPLATE = """
<!doctype html>
<html lang="{{ language }}">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ t.admin_login }} | {{ app_title }}</title>
  <style>{{ base_style|safe }}</style>
</head>
<body>
<main class="shell">
  <header class="masthead"><div><div class="eyebrow">{{ t.internal }}</div><h1>{{ t.admin_login }}</h1><p class="muted">{{ instance_label }}</p></div></header>
  <section class="panel">
    {% if error %}<div class="error" role="alert">{{ t.bad_password }}</div>{% endif %}
    <form method="post">
      <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
      <label for="pw">{{ t.admin_password }}</label>
      <input type="password" id="pw" name="pw" autocomplete="current-password" required autofocus>
      <button class="primary-action" type="submit">{{ t.sign_in }}</button>
    </form>
  </section>
</main>
</body>
</html>
"""

ADMIN_DASHBOARD_TEMPLATE = """
<!doctype html>
<html lang="{{ language }}">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ t.dashboard }} | {{ app_title }}</title>
  <style>{{ base_style|safe }}
    .masthead { align-items: flex-end; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .actions form { margin: 0; }
    .stats { display: grid; grid-template-columns: repeat(3, 1fr); margin-bottom: 24px; background: var(--paper); border: 1px solid var(--line); border-radius: 4px; }
    .stat { padding: 20px 22px; border-right: 1px solid var(--line); }
    .stat:last-child { border-right: 0; }
    .num { font-size: 1.8rem; font-weight: 750; font-variant-numeric: tabular-nums; }
    .stat-label { color: var(--muted); font-size: .8rem; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 4px; background: var(--paper); }
    table { width: 100%; border-collapse: collapse; white-space: nowrap; }
    th, td { padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; font-size: .88rem; }
    th { color: var(--muted); background: #f7f7f4; font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; }
    tbody tr:last-child td { border-bottom: 0; }
    tbody tr:hover { background: #faf9f6; }
    td:nth-child(4) { font-variant-numeric: tabular-nums; }
    @media (max-width: 600px) {
      .masthead { align-items: flex-start; flex-direction: column; }
      .stats { grid-template-columns: 1fr; }
      .stat { border-right: 0; border-bottom: 1px solid var(--line); }
      .stat:last-child { border-bottom: 0; }
    }
  </style>
</head>
<body>
<main class="shell wide">
  <header class="masthead"><div><div class="eyebrow">{{ t.internal }}</div><h1>{{ t.dashboard }}</h1><p class="muted">{{ instance_label }}</p></div>
    <div class="actions"><a class="button" href="{{ url_for('admin_download') }}">{{ t.download }}</a>
      <form method="post" action="{{ url_for('admin_logout') }}">
        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
        <button class="secondary" type="submit">{{ t.logout }}</button>
      </form>
    </div>
  </header>
  <section class="stats">
    <div class="stat"><div class="num">{{ total }}</div><div class="stat-label">{{ t.total }}</div></div>
    <div class="stat"><div class="num">{{ issued }}</div><div class="stat-label">{{ t.issued }}</div></div>
    <div class="stat"><div class="num">{{ total - issued }}</div><div class="stat-label">{{ t.remaining }}</div></div>
  </section>
  <div class="table-wrap"><table>
      <thead><tr><th>#</th><th>{{ t.time }}</th><th>UserID</th><th>VMID</th><th>{{ t.status }}</th></tr></thead>
      <tbody>
        {% for row in rows %}<tr><td>{{ loop.index }}</td><td>{{ row.Timestamp }}</td><td>{{ row.UserID }}</td><td>{{ row.Allocated_VMID }}</td><td>{{ t.get(row.Status, row.Status) }}</td></tr>
        {% else %}<tr><td colspan="5">{{ t.none }}</td></tr>{% endfor %}
      </tbody>
    </table></div>
</main>
</body>
</html>
"""


def template_context():
    return {
        "language": LANGUAGE,
        "t": TEXT,
        "app_title": APP_TITLE,
        "instance_label": INSTANCE_LABEL,
        "contact_text": CONTACT_TEXT,
        "base_style": BASE_STYLE,
    }


def render_main(error=None, prev_id="", success=False, **result):
    allocated_vmids, _ = get_issued_data()
    remaining = LXC_END_VMID - LXC_START_VMID + 1 - len(allocated_vmids)
    return render_template_string(
        MAIN_TEMPLATE,
        **template_context(),
        error=error,
        prev_id=prev_id,
        success=success,
        closed=not success and remaining == 0,
        remaining=remaining,
        **result,
    )


@app.get("/")
def index():
    return render_main()


@app.post("/allocate")
def allocate():
    user_id = request.form.get("user_id", "").strip().lower()

    if not re.fullmatch(r"[a-z0-9]+", user_id):
        return render_main(TEXT["invalid_username"], user_id), 400
    if not 3 <= len(user_id) <= 20:
        return render_main(TEXT["invalid_length"], user_id), 400
    if user_id.isdigit():
        return render_main(TEXT["numeric_username"], user_id), 400

    with allocation_lock():
        allocated_vmids, issued_userids = get_issued_data()
        if user_id in issued_userids:
            return render_main(TEXT["duplicate"], user_id), 409

        vmid = get_next_vmid(allocated_vmids)
        if vmid is None:
            return render_main()

        password = make_random_password()
        full_user_id = f"{user_id}@{PROXMOX_REALM}"
        # ponytail: reserve first and fail closed; an interrupted remote operation requires admin review.
        reserve_allocation(user_id, vmid)
        user_created = False
        try:
            proxmox.access.users.post(userid=full_user_id, password=password)
            user_created = True
            proxmox.access.acl.put(path=f"/vms/{vmid}", roles=LXC_ROLE, users=full_user_id)
            set_allocation_status(user_id, vmid, "allocated")
        except Exception:
            app.logger.exception("Allocation failed for VMID %s", vmid)
            if not user_created:
                remove_reservation(user_id, vmid)
            else:
                try:
                    proxmox.access.users(full_user_id).delete()
                except Exception:
                    app.logger.exception("Rollback failed for user %s", full_user_id)
                set_allocation_status(user_id, vmid, "needs_review")
            return render_main(TEXT["server_error"], user_id), 502

    return render_main(success=True, user_id=full_user_id, realm=PROXMOX_REALM, password=password, vmid=vmid)


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("admin"):
        return redirect(url_for("admin_dashboard"))

    error = False
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("pw", ""), ADMIN_PASSWORD):
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        time.sleep(0.5)
        error = True

    return render_template_string(ADMIN_LOGIN_TEMPLATE, **template_context(), error=error)


@app.get("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    rows = read_rows()
    allocated_vmids, _ = get_issued_data()
    return render_template_string(
        ADMIN_DASHBOARD_TEMPLATE,
        **template_context(),
        rows=rows,
        total=LXC_END_VMID - LXC_START_VMID + 1,
        issued=len(allocated_vmids),
    )


@app.get("/admin/download")
def admin_download():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    if not os.path.isfile(LOG_FILE):
        return TEXT["no_log"], 404
    with open(LOG_FILE, encoding="utf-8") as file:
        content = file.read()
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=allocations.csv"},
    )


@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


if __name__ == "__main__":
    app.run(
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=env_int("APP_PORT", 5000),
        debug=False,
    )
