---
name: gmail
description: Read, send, search, label, and manage Gmail messages via the Google API. Use when the user mentions Gmail, email, inbox, sending mail, replying, or any Gmail-specific label/search operation. Requires OAuth 2.0 credentials at ~/.Hermes/credentials/google-oauth.json; first-run prompts for browser auth.
license: MIT
source: spaice-agent-bundled
---

# Gmail

Google API integration for Gmail. Read, send, search, label, draft.

## When to use

- User asks to send an email
- User asks to read / summarise / search their inbox
- User asks to reply to or forward a specific thread
- User asks to draft a message for review before sending
- User asks to apply labels, archive, star, or mark as read

## Prerequisites

**Credentials:** Google OAuth 2.0 Desktop credentials file at `~/.Hermes/credentials/google-oauth.json`. First run of any operation opens a browser for user consent.

1. Go to https://console.cloud.google.com/apis/credentials
2. Create OAuth 2.0 Client ID (Desktop app)
3. Download JSON, save as `~/.Hermes/credentials/google-oauth.json` (chmod 600)
4. Enable Gmail API: https://console.cloud.google.com/apis/library/gmail.googleapis.com

**Libraries:**

```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

All Apache-2.0 licensed.

## Scopes

Use the minimum scope for the task:

| Scope | Purpose |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | Read only |
| `https://www.googleapis.com/auth/gmail.send` | Send new messages (no modify) |
| `https://www.googleapis.com/auth/gmail.compose` | Create drafts + send |
| `https://www.googleapis.com/auth/gmail.modify` | Read + label + archive (no delete) |
| `https://mail.google.com/` | Full access (including delete) — avoid unless needed |

## Client bootstrap

```python
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

CREDS_FILE = Path.home() / ".Hermes" / "credentials" / "google-oauth.json"
TOKEN_FILE = Path.home() / ".Hermes" / "credentials" / "google-oauth-token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]  # adjust to task

def gmail_client():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
        TOKEN_FILE.chmod(0o600)
    return build("gmail", "v1", credentials=creds)
```

## Common tasks

### Send a plain-text email

```python
import base64
from email.message import EmailMessage

svc = gmail_client()
msg = EmailMessage()
msg["To"] = "jozef@spaice.ai"
msg["From"] = "me"
msg["Subject"] = "Site visit confirmation"
msg.set_content("Hi Jozef,\n\nConfirming Thursday 10am at Hanna residence.\n\n— Jarvis")

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
print(f"Sent: id={sent['id']}")
```

### Send HTML + attachment

```python
import base64, mimetypes
from email.message import EmailMessage
from pathlib import Path

msg = EmailMessage()
msg["To"] = "client@example.com"
msg["From"] = "me"
msg["Subject"] = "Proposal attached"
msg.set_content("Hi,\n\nPlease find the proposal attached.\n\nRegards,\nJozef")
msg.add_alternative("""
<html><body>
<p>Hi,</p>
<p>Please find the proposal <b>attached</b>.</p>
<p>Regards,<br>Jozef</p>
</body></html>
""", subtype="html")

attachment = Path("/path/to/proposal.pdf")
ctype, encoding = mimetypes.guess_type(str(attachment))
maintype, subtype = ctype.split("/", 1) if ctype else ("application", "octet-stream")
msg.add_attachment(
    attachment.read_bytes(),
    maintype=maintype, subtype=subtype,
    filename=attachment.name,
)

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
svc.users().messages().send(userId="me", body={"raw": raw}).execute()
```

### Create a draft (don't send)

```python
raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
draft = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
print(f"Draft created: {draft['id']}")
```

### Search recent messages

```python
result = svc.users().messages().list(
    userId="me",
    q="from:client@example.com newer_than:30d",
    maxResults=20,
).execute()

for msg_stub in result.get("messages", []):
    msg = svc.users().messages().get(userId="me", id=msg_stub["id"], format="metadata").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    print(f"{headers.get('Date')}  {headers.get('Subject')}")
```

### Read a full message body

```python
import base64
msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()

def extract_body(payload):
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    return ""

print(extract_body(msg["payload"]))
```

### Apply / remove labels

```python
# Add label "Important" and mark as read (remove UNREAD)
svc.users().messages().modify(
    userId="me",
    id=message_id,
    body={"addLabelIds": ["IMPORTANT"], "removeLabelIds": ["UNREAD"]},
).execute()
```

### Reply to a thread

Preserving threading requires the original message's `Message-ID` and thread ID:

```python
original = svc.users().messages().get(userId="me", id=message_id, format="metadata").execute()
thread_id = original["threadId"]
msg_id_header = next(h["value"] for h in original["payload"]["headers"] if h["name"] == "Message-ID")

reply = EmailMessage()
reply["To"] = "client@example.com"
reply["From"] = "me"
reply["Subject"] = "Re: " + next(h["value"] for h in original["payload"]["headers"] if h["name"] == "Subject")
reply["In-Reply-To"] = msg_id_header
reply["References"] = msg_id_header
reply.set_content("Agreed. Locking in Thursday 10am.")

raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
svc.users().messages().send(
    userId="me",
    body={"raw": raw, "threadId": thread_id},
).execute()
```

## Gmail search operators (for `q=`)

Standard Gmail search syntax works verbatim:

| Operator | Example |
|---|---|
| `from:` | `from:jozef@spaice.ai` |
| `to:` | `to:client@example.com` |
| `subject:` | `subject:"invoice"` |
| `has:attachment` | filter attachments |
| `label:` | `label:important` |
| `newer_than:` | `newer_than:7d` |
| `older_than:` | `older_than:1y` |
| `in:` | `in:inbox`, `in:sent`, `in:spam` |
| `is:` | `is:unread`, `is:starred` |

Combine with `AND`/`OR`/`()`. Quote phrases.

## Pitfalls

- **First-run auth opens browser** — on headless servers, use `flow.run_console()` (prints URL for manual paste) instead of `run_local_server()`.
- **Token refresh** — stored refresh token lasts until revoked. If revoked, delete `google-oauth-token.json` and re-authenticate.
- **Quota** — Gmail API free tier is generous (250 units/user/sec burst; 1B units/day). Heavy polling: use watch + push notifications, not loop-polling.
- **Rate limits for send** — 500/day for consumer Gmail, 2000/day for Workspace. Bulk sending: use a proper ESP (Resend, SendGrid, etc.) not Gmail.
- **Raw message encoding** — MUST be URL-safe base64 (`base64.urlsafe_b64encode`). Standard base64 breaks Gmail.
- **Threading** — replies miss the original thread unless `threadId` is set AND `In-Reply-To` / `References` headers are present.
- **HTML + plain-text** — always provide both via `set_content()` + `add_alternative()`. Pure HTML triggers spam filters.

## Alternatives

- **Workspace accounts** — prefer `google-workspace` skill (if available) which uses a service account + domain-wide delegation, skips OAuth browser flow.
- **Pure SMTP** — `smtplib` with an app password works for sending-only scenarios without the OAuth complexity. No inbox access.

## Related

- `google-workspace` — broader Google API coverage (Calendar, Drive, Sheets, Docs)
- `email` — IMAP/SMTP alternative for non-Gmail providers (see `himalaya` skill)
- `pdf` / `docx` / `xlsx` — generate the attachment before sending
