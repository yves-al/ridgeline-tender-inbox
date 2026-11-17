"""Step 2: remove card numbers from an email before anything else sees it.

Rule 11: never store a payment card number. Removing them here, right after reading, means
the model never sees them and no load, review, note or log line can contain one.

Card-shaped numbers are 4-4-4-4 groups, 4-6-5 groups (Amex), or 13 to 19 digits in a row,
the same shapes check.py looks for. Unlike check.py, we remove them even when the Luhn check
fails: a mistyped card number is still a card number, and none of the details a booking needs
(references, dates, weights, rates) look like one.
"""
import re
import sys
from dataclasses import replace

from pipeline.inbox import load_inbox

CARD = re.compile(r'(?<!\d)(?:(?:\d{4}[\s-]+){3}\d{4}|\d{4}[\s-]+\d{6}[\s-]+\d{5}|\d{13,19})(?!\d)')
MASK = '[card number removed]'


def redact_text(text):
    """Return the text with every card-shaped number replaced, and how many were replaced."""
    return CARD.subn(MASK, text)


def redact_email(email):
    """Return a copy of the email with card numbers removed from every text field, and the count."""
    subject, n_subject = redact_text(email.subject)
    body, n_body = redact_text(email.body)
    attachments, n_attachments = [], 0
    for a in email.attachments:
        filename, n_name = redact_text(a.filename)
        text, n_text = redact_text(a.text)
        attachments.append(replace(a, filename=filename, text=text))
        n_attachments += n_name + n_text
    clean = replace(email, subject=subject, body=body, attachments=tuple(attachments))
    return clean, n_subject + n_body + n_attachments


if __name__ == '__main__':
    for e in load_inbox(sys.argv[1]):
        clean, count = redact_email(e)
        if count:
            print(f'{e.id}  removed {count}')
