"""Step 10: send the planned actions to the TMS.

Every record carries the email's id as message_id. Every write has an Idempotency-Key made from
the email id, the action and the reference, so a retry or a rerun can't write twice (and step 9
has already skipped anything this email wrote before).

If the TMS rejects a booking, change or cancel, the load goes to review as unclear with the TMS's
error, never a silent skip. If even that review is rejected, the run stops: a missed tender costs
more than a stopped run.
"""
import hashlib
import sys

from pipeline.plan import norm
from pipeline.tms import TMSError


def idempotency_key(message_id, kind, reference):
    return hashlib.sha256(f'{message_id}|{kind}|{norm(reference)}'.encode()).hexdigest()


def apply(action, message_id, tms):
    """Write one action. Returns what was written: {'kind', 'id', 'reason'?, 'error'?} or None."""
    body = {'message_id': message_id, **(action.body or {})}
    key = idempotency_key(message_id, action.kind, action.reference)
    try:
        if action.kind == 'create':
            return {'kind': 'create', 'id': tms.write('POST', '/v1/loads', body, key)['id']}
        if action.kind in ('update', 'cancel'):
            return {'kind': action.kind, 'id': tms.write('PATCH', f'/v1/loads/{action.load_id}', body, key)['id']}
        if action.kind == 'review':
            return {'kind': 'review', 'id': tms.write('POST', '/v1/reviews', body, key)['id'], 'reason': action.reason}
        return None  # none / done: nothing to write
    except TMSError as e:
        if action.kind == 'review':
            raise
        summary = (f'The TMS rejected the {action.kind} for {action.reference} ({e.code}: {e.message}). '
                   f'Planned because: {action.detail}')
        review = {'message_id': message_id, 'reason': 'unclear', 'summary': summary[:1000]}
        if action.reference:
            review['reference'] = action.reference
        if (action.body or {}).get('customer'):
            review['customer'] = action.body['customer']
        written = tms.write('POST', '/v1/reviews', review, idempotency_key(message_id, 'review-after-error', action.reference))
        return {'kind': 'review', 'id': written['id'], 'reason': 'unclear', 'error': str(e)}


if __name__ == '__main__':
    # Try it: python -m pipeline.write inbox/labeled.jsonl   (writes to the TMS)
    from pipeline.inbox import load_inbox
    from pipeline.plan import plan_email
    from pipeline.tms import TMS

    tms = TMS()
    for email in load_inbox(sys.argv[1]):
        for action in plan_email(email, tms)[0]:
            written = apply(action, email.id, tms)
            print(f'{email.id}  {action.kind:7} {action.reference or "":12} {written or ""}')
