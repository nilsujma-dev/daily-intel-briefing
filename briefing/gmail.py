"""Gmail API sending over OAuth 2.0 - standard library only.

Implements the installed-application flow with PKCE against a loopback redirect,
stores a refresh token locally, and posts a MIME message to users.messages.send.
Scope is gmail.send only: this app can send mail and can do nothing else.
"""
from __future__ import annotations
import base64, hashlib, json, os, secrets, ssl, threading, urllib.error, urllib.parse, urllib.request, webbrowser
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SEND_URI = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
PROFILE_URI = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


class GmailError(RuntimeError):
    pass


# --------------------------------------------------------------------- helpers
def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=45, context=ssl.create_default_context()) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise GmailError("token endpoint %s: %s" % (exc.code, exc.read().decode("utf-8", "replace")))


def _post_json(url, payload, token):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise GmailError("Gmail API %s: %s" % (exc.code, exc.read().decode("utf-8", "replace")))


def _get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
        return json.loads(resp.read().decode())


def _load_client(path):
    if not os.path.exists(path):
        raise GmailError(
            "Missing OAuth client file: %s\n"
            "Create an OAuth client (Desktop app) in Google Cloud Console, download the\n"
            "JSON, and save it to that path. See README section 'One-time Google setup'." % path)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    node = data.get("installed") or data.get("web")
    if not node:
        raise GmailError("Unrecognised client secret file - expected an 'installed' (Desktop app) client.")
    return node["client_id"], node.get("client_secret", "")


# ----------------------------------------------------------------- oauth flow
class _CallbackHandler(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        _CallbackHandler.result = {k: v[0] for k, v in params.items()}
        ok = "code" in _CallbackHandler.result
        body = ("<html><body style=\"font-family:-apple-system,sans-serif;padding:48px;text-align:center\">"
                "<h2 style=\"color:%s\">%s</h2><p>You can close this tab and return to the terminal.</p>"
                "</body></html>") % (("#1a7f37", "Authorisation complete") if ok
                                     else ("#b3261e", "Authorisation failed"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_args):
        return


def authorize(cfg, log=print):
    client_secret_path = str(cfg.secret_path("client_secret_file"))
    token_path = str(cfg.secret_path("token_file"))
    scope = cfg.path("oauth.scope")
    client_id, client_secret = _load_client(client_secret_path)

    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)

    # Desktop OAuth clients use a dynamic loopback port. Google accepts both
    # 127.0.0.1 and localhost for this client type regardless of the port shown
    # in the console; 127.0.0.1 avoids the IPv6-first resolution that can make
    # "localhost" hit ::1 while the server listens on IPv4 only.
    host = cfg.path("oauth.redirect_host", "127.0.0.1")
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = "http://%s:%d/" % (host, port)

    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": scope, "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    }
    url = AUTH_URI + "?" + urllib.parse.urlencode(params)

    log("\nOpening your browser to authorise Gmail sending…")
    log("If it does not open, paste this URL manually:\n\n%s\n" % url)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    thread.join(timeout=300)
    server.server_close()

    result = _CallbackHandler.result
    if not result.get("code"):
        err = result.get("error", "timed out")
        hint = ""
        if "redirect_uri_mismatch" in str(err):
            hint = ("\nSet oauth.redirect_host in config/settings.json to 'localhost' "
                    "and run --authorize again.")
        raise GmailError("No authorisation code received (%s).%s" % (err, hint))
    if result.get("state") != state:
        raise GmailError("State mismatch - aborting for safety.")

    fields = {"code": result["code"], "client_id": client_id, "redirect_uri": redirect_uri,
              "grant_type": "authorization_code", "code_verifier": verifier}
    if client_secret:
        fields["client_secret"] = client_secret
    tok = _post_form(TOKEN_URI, fields)
    if "refresh_token" not in tok:
        raise GmailError("Google did not return a refresh token. Revoke prior access at "
                         "https://myaccount.google.com/permissions and retry.")

    _save_token(token_path, {
        "refresh_token": tok["refresh_token"],
        "access_token": tok.get("access_token"),
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(seconds=int(tok.get("expires_in", 3600)) - 60)).isoformat(),
        "client_id": client_id, "client_secret": client_secret, "scope": scope,
    })
    who = _get_json(PROFILE_URI, tok["access_token"]).get("emailAddress", "unknown")
    log("Authorised as %s. Token saved to %s" % (who, token_path))
    return who


def _save_token(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def access_token(cfg):
    token_path = str(cfg.secret_path("token_file"))
    if not os.path.exists(token_path):
        raise GmailError("Not authorised yet. Run:  ./run.sh --authorize")
    with open(token_path, "r", encoding="utf-8") as fh:
        tok = json.load(fh)

    expires_at = tok.get("expires_at")
    if tok.get("access_token") and expires_at:
        try:
            if datetime.fromisoformat(expires_at) > datetime.now(timezone.utc):
                return tok["access_token"]
        except ValueError:
            pass

    fields = {"client_id": tok["client_id"], "refresh_token": tok["refresh_token"],
              "grant_type": "refresh_token"}
    if tok.get("client_secret"):
        fields["client_secret"] = tok["client_secret"]
    fresh = _post_form(TOKEN_URI, fields)
    tok["access_token"] = fresh["access_token"]
    tok["expires_at"] = (datetime.now(timezone.utc)
                         + timedelta(seconds=int(fresh.get("expires_in", 3600)) - 60)).isoformat()
    _save_token(token_path, tok)
    return tok["access_token"]


def build_message(to_addr, subject, html_body, text_body, sender=None):
    msg = EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = subject
    if sender and sender != "me":
        msg["From"] = sender
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def send(cfg, to_addr, subject, html_body, text_body):
    token = access_token(cfg)
    msg = build_message(to_addr, subject, html_body, text_body, cfg.path("sender"))
    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    resp = _post_json(SEND_URI, payload, token)
    return resp.get("id", "")


def send_test(cfg, log=print):
    """Prove the whole delivery path works without touching the news pipeline."""
    from datetime import datetime
    now = datetime.now().astimezone()
    to = cfg.require_recipient()
    html = (
        "<div style=\"font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:560px;"
        "padding:22px;border:1px solid #e2e6ea;border-radius:10px;\">"
        "<div style=\"font-size:18px;font-weight:700;color:#12283f;\">Briefing test message</div>"
        "<p style=\"font-size:14px;color:#4a5560;line-height:1.6;\">Gmail sending is working. "
        "Your daily AI and Cyber Security briefing will arrive at 08:00 once the schedule is "
        "installed.</p>"
        "<p style=\"font-size:12px;color:#8894a0;\">Sent %s</p></div>" % now.strftime("%d %b %Y, %H:%M %Z"))
    text = "Briefing test message\n\nGmail sending is working. Sent %s\n" % now.strftime("%d %b %Y, %H:%M %Z")
    msg_id = send(cfg, to, "Briefing test - delivery confirmed", html, text)
    log("Test email sent to %s (message %s)" % (to, msg_id))
    return msg_id
