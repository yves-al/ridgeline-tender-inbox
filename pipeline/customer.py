"""Step 3: find the customer who made the request.

The customer is found from the email domain in the TMS. When the sender is Ridgeline itself
(the voicemail line or a staff member forwarding a request), the customer is whoever made the
request: the one outside address in the email (a voicemail's "Caller:" line or a forward's
"From:" line). Ridgeline's own domain is the domain of the inbox the email was sent to.

Outcomes, with the review reason from the booking rules:
  active customer            -> found, carry on
  domain not on file         -> unknown_customer
  customer not active        -> customer_not_active (credit_hold or inactive)
  Ridgeline sender with no single outside address -> unclear
"""
import re
import sys
from dataclasses import dataclass

from pipeline.inbox import load_inbox
from pipeline.redact import redact_email
from pipeline.tms import TMS

ADDRESS = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


@dataclass(frozen=True)
class CustomerResult:
    customer: dict | None   # the TMS customer record, when one was found
    requester: str          # the address the request came from
    forwarded: bool         # True when Ridgeline passed the request along
    review_reason: str | None
    detail: str

    @property
    def ok(self):
        return self.review_reason is None


def domain_of(address):
    return address.rpartition('@')[2].lower() if '@' in address else ''


def outside_addresses(email, own_domain):
    """Distinct addresses in the email text that aren't Ridgeline's, in the order they appear."""
    texts = [email.body] + [a.text for a in email.attachments]
    found = []
    for text in texts:
        for address in ADDRESS.findall(text):
            address = address.lower().rstrip('.')
            if domain_of(address) != own_domain and address not in found:
                found.append(address)
    return found


def find_customer(tms, email):
    own_domain = domain_of(email.to)
    requester, forwarded = email.sender, False
    if email.sender_domain == own_domain:
        forwarded = True
        candidates = outside_addresses(email, own_domain)
        if len(candidates) != 1:
            return CustomerResult(None, email.sender, True, 'unclear',
                                  f'Sent by Ridgeline ({email.sender}); found {len(candidates)} outside addresses '
                                  f'({", ".join(candidates) or "none"}), so the requesting customer is not clear.')
        requester = candidates[0]

    via = f' (passed along by {email.sender})' if forwarded else ''
    customer = tms.customer_by_domain(domain_of(requester))
    if customer is None:
        return CustomerResult(None, requester, forwarded, 'unknown_customer',
                              f'{requester}{via}: domain {domain_of(requester)} is not a Ridgeline customer.')
    if customer['status'] != 'active':
        return CustomerResult(customer, requester, forwarded, 'customer_not_active',
                              f'{requester}{via}: {customer["name"]} ({customer["id"]}) is {customer["status"]}.')
    return CustomerResult(customer, requester, forwarded, None,
                          f'{requester}{via}: {customer["name"]} ({customer["id"]}), active.')


if __name__ == '__main__':
    tms = TMS()
    for e in load_inbox(sys.argv[1]):
        result = find_customer(tms, redact_email(e)[0])
        print(f'{e.id}  {result.review_reason or "ok":20} {result.detail}')
