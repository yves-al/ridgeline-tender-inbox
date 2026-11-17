"""Step 9: decide what to do with each load, given what is already in the TMS.

plan_email() runs steps 2 to 9 for one email and returns one Action per load (or one for the whole
email). Nothing is written here; step 10 sends the actions to the TMS.

  this email already wrote a record for the load  -> done (a rerun changes nothing)
  follow-up, or not about a load                   -> nothing
  new, nothing booked                              -> the booking rules decide: create or review
  new, booked with the same details                -> nothing (a repeat)
  new, booked with other details, or cancelled     -> review unclear
  new, already in review from another email       -> review unclear
  change to a booked load                          -> update only what changes, if the result passes
                                                      the rules; otherwise review, load left alone
  change or cancel, never booked                   -> review unclear
  cancel a booked load                             -> cancel (already cancelled: nothing)
"""
import re
import sys
from dataclasses import dataclass
from datetime import datetime

from pipeline.customer import find_customer
from pipeline.extract import extract
from pipeline.redact import redact_email
from pipeline.rules import decide
from pipeline.verify import verify


@dataclass
class Action:
    kind: str                 # create, update, cancel, review, none, done
    reference: str | None
    detail: str               # why, for the decision log and the review summary
    reason: str | None = None # review reason
    body: dict | None = None  # what step 10 sends (without message_id)
    load_id: str | None = None


def norm(reference):
    return re.sub(r'\s+', '', reference or '').upper()


def same_stop(a, b):
    return a['facility'] == b['facility'] and all(
        datetime.fromisoformat(a[k]) == datetime.fromisoformat(b[k]) for k in ('window_start', 'window_end'))


def differences(new, booked):
    """The fields of new that differ from the booked load, ready for a PATCH."""
    out = {k: new[k] for k in ('pickup', 'delivery') if not same_stop(new[k], booked[k])}
    out.update({k: new[k] for k in ('equipment', 'weight_lbs', 'rate_usd', 'temperature_f')
                if new.get(k) is not None and new.get(k) != booked.get(k)})
    return out


def describe(load):
    """A short description of what the email asked for, for a rep."""
    if load is None:
        return ''
    parts = []
    for kind in ('pickup', 'delivery'):
        stop = getattr(load, kind)
        if stop and (stop.place or stop.date or stop.time):
            parts.append(f'{kind.capitalize()}: {" ".join(w for w in (stop.place, stop.date, stop.time) if w)}.')
    facts = [load.equipment and load.equipment.replace('_', ' '),
             load.weight_lbs and f'{load.weight_lbs:,} lbs',
             load.temperature_f is not None and f'{load.temperature_f:g}F',
             load.rate_usd and f'offered ${load.rate_usd:,.0f}']
    if any(facts):
        parts.append(', '.join(f for f in facts if f) + '.')
    return ' '.join(parts)


def review(reason, detail, email, customer, load=None, reference=None, notes=()):
    reference = reference or (load and load.reference)
    who = customer.requester + (f' ({customer.customer["name"]}, {customer.customer["id"]})' if customer.customer else '')
    asked = {'new': 'New tender', 'change': 'Change', 'cancel': 'Cancellation'}.get(load and load.action, 'Email')
    summary = ' '.join(s for s in (f'{asked} {reference or ""} from {who}.'.replace('  ', ' '),
                                   describe(load), f'Review: {detail}', *notes) if s)
    body = {'reason': reason, 'summary': summary[:1000]}
    if reference:
        body['reference'] = reference
    if customer.customer:
        body['customer'] = customer.customer['id']
    return Action('review', reference, detail, reason=reason, body=body)


def plan_email(email, tms):
    """Steps 2-9 for one email. Returns (actions, facts for the decision log)."""
    email, cards = redact_email(email)
    customer = find_customer(tms, email)
    facilities = tms.facilities(customer.customer['id']) if customer.customer else []
    facts = {'customer': customer.customer and customer.customer['id'], 'requester': customer.requester,
             'customer_check': customer.detail, 'card_numbers_removed': cards}

    mine_loads = tms.loads(message_id=email.id)
    mine_reviews = tms.reviews(message_id=email.id)
    whole_email_review = next((r for r in mine_reviews if not r.get('reference')), None)
    if whole_email_review:
        return [Action('done', None, f'already sent to review as {whole_email_review["id"]}')], facts

    try:
        extraction = extract(email, customer.customer, facilities)
    except Exception as e:  # the model failed: a person reads it
        return [review('unclear', f'The model could not read this email ({e}).', email, customer)], facts
    facts['instructions_to_automation'] = extraction.instructions_to_automation
    notes = ['The email contains text aimed at automated systems; it was ignored.'] if extraction.instructions_to_automation else []

    email_problems, load_problems = verify(email, extraction)
    if email_problems:
        return [review('unclear', '; '.join(email_problems), email, customer, notes=notes)], facts
    if not extraction.about_a_load:
        return [Action('none', None, 'not about a load')], facts
    refs = [norm(load.reference) for load in extraction.loads if load.reference]
    if len(refs) != len(set(refs)):
        return [review('unclear', 'The email lists the same reference more than once.', email, customer, notes=notes)], facts

    all_reviews = tms.reviews()
    actions = []
    for load, problems in zip(extraction.loads, load_problems):
        actions.append(plan_load(load, problems, email, customer, facilities, tms,
                                 mine_loads, mine_reviews, all_reviews, notes))
    return actions, facts


def plan_load(load, problems, email, customer, facilities, tms, mine_loads, mine_reviews, all_reviews, notes):
    ref = load.reference

    # Already handled by this email (a rerun)?
    for booked in mine_loads:
        if norm(booked['reference']) == norm(ref):
            return Action('done', ref, f'{booked["id"]} already written by this email', load_id=booked['id'])
    for r in mine_reviews:
        if norm(r.get('reference')) == norm(ref):
            return Action('done', ref, f'already sent to review as {r["id"]}')

    if load.action == 'follow_up':
        return Action('none', ref, 'a follow-up about a load; nothing to do')
    if not customer.ok:
        return review(customer.review_reason, customer.detail, email, customer, load, notes=notes)
    if problems and load.action != 'new':  # a new load's problems are handled by the booking rules
        return review('unclear', 'The extraction did not check out: ' + '; '.join(problems), email, customer, load, notes=notes)
    if not ref:
        if load.action == 'new':
            return review('missing_info', "No shipper's reference.", email, customer, load, notes=notes)
        return review('unclear', f'A {load.action} with no reference.', email, customer, load, notes=notes)

    cid = customer.customer['id']
    booked = [b for b in tms.loads(customer=cid) if norm(b['reference']) == norm(ref)]
    in_review = [r for r in all_reviews if r.get('customer') == cid and norm(r.get('reference')) == norm(ref)]
    if len(booked) > 1:
        return review('unclear', f'{ref} matches {len(booked)} loads in the TMS.', email, customer, load, notes=notes)
    current = booked[0] if booked else None
    review_note = f' It is already in the review queue as {in_review[0]["id"]}.' if in_review else ''

    if load.action == 'new':
        if current and current['status'] == 'cancelled':
            return review('unclear', f'{ref} was booked as {current["id"]} and cancelled; this email tenders it again.',
                          email, customer, load, notes=notes)
        if current:
            d = decide(load, email, customer, facilities, problems, tms, lead_time=False)
            if d.ok and not differences(d.load, current):
                return Action('none', ref, f'a repeat of {current["id"]}, already booked with the same details',
                              load_id=current['id'])
            why = f'differs in {", ".join(differences(d.load, current))}' if d.ok else f'does not pass the rules ({d.detail})'
            return review('unclear', f'{ref} is already booked as {current["id"]}; this tender {why}.',
                          email, customer, load, notes=notes)
        if in_review:
            return review('unclear', f'{ref} was tendered before and is in the review queue as {in_review[0]["id"]}.',
                          email, customer, load, notes=notes)
        d = decide(load, email, customer, facilities, problems, tms)
        if not d.ok:
            return review(d.reason, d.detail, email, customer, load, notes=notes)
        return Action('create', ref, 'passes every rule' + (f' ({"; ".join(d.notes)})' if d.notes else ''), body=d.load)

    if current is None:
        return review('unclear', f'A {load.action} for {ref}, which was never booked.{review_note}',
                      email, customer, load, notes=notes)
    if load.action == 'cancel':
        if current['status'] == 'cancelled':
            return Action('none', ref, f'{current["id"]} is already cancelled', load_id=current['id'])
        return Action('cancel', ref, f'cancels {current["id"]}', body={'status': 'cancelled'}, load_id=current['id'])

    # A change
    if current['status'] == 'cancelled':
        return review('unclear', f'A change for {ref}, but {current["id"]} is cancelled.', email, customer, load, notes=notes)
    d = decide(load, email, customer, facilities, problems, tms, current=current)
    if not d.ok:
        return review(d.reason, f'Change not applied to {current["id"]}: {d.detail}', email, customer, load,
                      reference=current['reference'], notes=notes)
    changes = differences(d.load, current)
    if not changes:
        return Action('none', ref, f'{current["id"]} already matches this change', load_id=current['id'])
    return Action('update', ref, f'changes {", ".join(changes)} on {current["id"]}', body=changes, load_id=current['id'])


if __name__ == '__main__':
    # Try it (plans only, writes nothing): python -m pipeline.plan inbox/labeled.jsonl
    from pipeline.inbox import load_inbox
    from pipeline.tms import TMS

    tms = TMS()
    for email in load_inbox(sys.argv[1]):
        for a in plan_email(email, tms)[0]:
            print(f'{email.id}  {a.kind:7} {a.reason or "":17} {a.reference or "":12} {a.detail}')
