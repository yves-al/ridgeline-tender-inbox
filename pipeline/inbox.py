"""Step 1: retrieve the emails from an inbox file.

Each line of the file is one email: id, received_at, from, to, subject, body and attachments
(each with a filename and its extracted text). Emails come back in received order, so a change
is always handled after the booking it changes.
"""
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path


@dataclass(frozen=True)
class Attachment:
    filename: str
    text: str


@dataclass(frozen=True)
class Email:
    id: str
    received_at: datetime
    sender: str          # bare address, lowercased: dana@alpinepaper.example
    sender_domain: str   # alpinepaper.example
    to: str
    subject: str
    body: str
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)


def parse_received_at(value, where):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f'{where}: received_at is not an ISO 8601 timestamp: {value!r}')
    if moment.utcoffset() is None:
        raise ValueError(f'{where}: received_at has no UTC offset: {value!r}')
    return moment


def parse_email(raw, where):
    if not isinstance(raw, dict):
        raise ValueError(f'{where}: expected a JSON object')
    if not raw.get('id'):
        raise ValueError(f'{where}: missing id')
    sender = parseaddr(raw.get('from') or '')[1].strip().lower()
    return Email(
        id=str(raw['id']),
        received_at=parse_received_at(raw.get('received_at'), f'{where} ({raw["id"]})'),
        sender=sender,
        sender_domain=sender.rpartition('@')[2] if '@' in sender else '',
        to=raw.get('to') or '',
        subject=raw.get('subject') or '',
        body=raw.get('body') or '',
        attachments=tuple(Attachment(a.get('filename') or '', a.get('text') or '')
                          for a in raw.get('attachments') or []),
    )


def load_inbox(path):
    """Read every email in a .jsonl inbox, sorted by received_at (file order breaks ties).

    A line that can't be read stops the run with its line number: skipping it would leave a
    tender with no load and no review.
    """
    emails, seen = [], set()
    with Path(path).open(encoding='utf-8') as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            where = f'{path}:{number}'
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f'{where}: invalid JSON ({e.msg})')
            email = parse_email(raw, where)
            if email.id in seen:
                raise ValueError(f'{where}: duplicate email id {email.id}')
            seen.add(email.id)
            emails.append(email)
    return sorted(emails, key=lambda e: e.received_at)  # sorted() is stable


if __name__ == '__main__':
    for e in load_inbox(sys.argv[1]):
        print(f'{e.id}  {e.received_at.isoformat()}  {e.sender_domain:28} {len(e.attachments)} att  {e.subject}')
