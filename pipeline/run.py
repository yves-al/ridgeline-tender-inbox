"""Run the whole pipeline on an inbox file, and log one decision line per email (step 11).

  .venv/bin/python -m pipeline.run inbox/batch.jsonl [--out decisions.jsonl]

Two phases:
  1. The model reads every email, several at a time. Each read depends only on the email and its
     customer's facilities, and the answers are saved, so phase 2 reuses them.
  2. The emails are handled one at a time, in received order (a change needs the booking before it):
     plan (steps 2-9), write (step 10), log (step 11).

The run stops if the TMS can't be reached. Any other error on an email sends that email to review
as unclear, so it is never skipped.
"""
import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from pipeline import extract as model
from pipeline.customer import find_customer
from pipeline.inbox import load_inbox
from pipeline.plan import Action, plan_email
from pipeline.redact import redact_email
from pipeline.tms import TMS, TMSError
from pipeline.write import apply


def read_ahead(email):
    """Phase 1 for one email: ask the model now so phase 2 finds the answer saved."""
    tms = TMS()  # one client per thread
    clean, _ = redact_email(email)
    customer = find_customer(tms, clean).customer
    try:
        model.extract(clean, customer, tms.facilities(customer['id']) if customer else [])
    except Exception:
        pass  # phase 2 asks again, and sends the email to review if it fails again


def handle(email, tms):
    """Phase 2 for one email: plan, write, and return its decision line."""
    try:
        actions, facts = plan_email(email, tms)
    except TMSError:
        raise
    except Exception as e:
        summary = f'The pipeline failed on this email ({type(e).__name__}: {e}). A person needs to read it.'
        actions, facts = [Action('review', None, summary, reason='unclear',
                                 body={'reason': 'unclear', 'summary': summary[:1000]})], {}
    loads = []
    for action in actions:
        written = apply(action, email.id, tms)
        entry = {'reference': action.reference, 'action': written['kind'] if written else action.kind,
                 'reason': written.get('reason') if written else action.reason, 'why': action.detail}
        if written and written.get('error'):
            entry['why'] = f'{action.kind} rejected by the TMS ({written["error"]}); sent to review. Planned because: {action.detail}'
        if written:
            entry['record'] = written['id']
        elif action.load_id:
            entry['record'] = action.load_id
        loads.append(entry)
    return {'message_id': email.id, 'received_at': email.received_at.isoformat(), 'from': email.sender,
            'subject': email.subject, **facts, 'loads': loads}


def main():
    parser = argparse.ArgumentParser(description='Run the tender inbox pipeline on an inbox file.')
    parser.add_argument('inbox', help='a .jsonl inbox file, e.g. inbox/batch.jsonl')
    parser.add_argument('--out', default='decisions.jsonl', help='where to write the decision log')
    parser.add_argument('--workers', type=int, default=8, help='model calls at a time')
    args = parser.parse_args()

    tms = TMS()
    tms.get('/health')  # stop now if the TMS isn't running
    emails = load_inbox(args.inbox)

    print(f'Reading {len(emails)} emails with {model.MODEL}...', file=sys.stderr)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(read_ahead, emails))

    actions, reasons, no_person, already = Counter(), Counter(), 0, 0
    with open(args.out, 'w') as out:
        for email in emails:
            line = handle(email, tms)
            out.write(json.dumps(line) + '\n')
            out.flush()
            kinds = [entry['action'] for entry in line['loads']]
            actions.update(kinds)
            reasons.update(entry['reason'] for entry in line['loads'] if entry['action'] == 'review')
            if all(kind == 'done' for kind in kinds):
                already += 1  # handled by an earlier run
            elif 'review' not in kinds:
                no_person += 1

    print(f'\n{len(emails)} emails -> {args.out}', file=sys.stderr)
    print(f'Actions: {dict(actions)}', file=sys.stderr)
    print(f'Reviews by reason: {dict(reasons)}', file=sys.stderr)
    print(f'Emails needing no person: {no_person} of {len(emails)}'
          + (f' ({already} already handled by an earlier run)' if already else ''), file=sys.stderr)


if __name__ == '__main__':
    main()
