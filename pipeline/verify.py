"""Step 5: check the model's answer against the email before anything is written.

Every value the model gave must really be in the email. A value it left out (None) is a missing
detail, which the booking rules handle later. Any problem sends the load to review as unclear.
"""
import re
import sys

TRAILER_WORDS = {'reefer': r'reefer|refrigerat', 'dry_van': r'\bvans?\b'}
OTHER_TRAILERS = r'reefer|refrigerat|flatbed|flat bed|step ?deck|power only'


def squash(text):
    """Lowercase, with runs of spaces collapsed."""
    return re.sub(r'\s+', ' ', text).strip().lower()


def numbers_in(text):
    """Every number in the text, reading '21.4k' as 21400, '39,800' as 39800 and '-10F' as -10."""
    return {float(n.replace(',', '')) * (1000 if k else 1)
            for n, k in re.findall(r'(?<![\w-])(-?\d[\d,]*(?:\.\d+)?)\s*(k\b)?', text, re.IGNORECASE)}


def has_reference(text, reference):
    """The reference appears on its own, allowing spaces or dashes between characters ("F R F - 2 6 7 1 4")."""
    pattern = r'[\s-]*'.join(re.escape(c) for c in reference if c.isalnum())
    return bool(pattern) and re.search(rf'(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])', text, re.IGNORECASE) is not None


def check_load(load, email, single_load):
    sources = [email.subject, email.body] + [a.text for a in email.attachments]
    text = '\n'.join(sources)
    problems = list(load.conflicts)  # contradictions the model noticed

    if load.reference and not has_reference(text, load.reference):
        problems.append(f'reference {load.reference} is not in the email')

    for name in ('weight_lbs', 'temperature_f', 'rate_usd'):
        value = getattr(load, name)
        if value is not None and value not in numbers_in(text):
            problems.append(f'{name} {value} is not in the email')

    stops = [('pickup', load.pickup), ('delivery', load.delivery)]
    for stop_name, stop in stops:
        for field in ('place', 'date', 'time'):
            value = stop and getattr(stop, field)
            if value and squash(value) not in squash(text):
                problems.append(f'{stop_name} {field} "{value}" is not in the email')

    # Both stops with the same date or time: it must be written twice, or the model copied one onto the other.
    for field in ('date', 'time'):
        value = load.pickup and load.delivery and getattr(load.pickup, field)
        if value and squash(value) == squash(getattr(load.delivery, field) or ''):
            if max(squash(s).count(squash(value)) for s in sources) < 2:
                problems.append(f'pickup and delivery both have {field} "{value}", but the email writes it once')

    if load.equipment in TRAILER_WORDS and not re.search(TRAILER_WORDS[load.equipment], text, re.IGNORECASE):
        problems.append(f'equipment {load.equipment}, but the email never names that trailer')
    if load.equipment is None and re.search(OTHER_TRAILERS, text, re.IGNORECASE):
        problems.append('no equipment extracted, but the email names a trailer')

    # With one load in the email, every attachment must carry the same weight as the body.
    if single_load and load.weight_lbs is not None:
        for a in email.attachments:
            if load.weight_lbs not in numbers_in(a.text):
                problems.append(f'{a.filename} does not have weight {load.weight_lbs}')
    return problems


def verify(email, extraction):
    """One list of problems per load, plus problems with the email as a whole."""
    email_problems = []
    if extraction.about_a_load != bool(extraction.loads):
        email_problems.append('the model contradicts itself about whether the email is about a load')
    single = len(extraction.loads) == 1
    return email_problems, [check_load(load, email, single) for load in extraction.loads]


if __name__ == '__main__':
    # Try it: python -m pipeline.verify inbox/labeled.jsonl
    from pipeline.customer import find_customer
    from pipeline.extract import extract
    from pipeline.inbox import load_inbox
    from pipeline.redact import redact_email
    from pipeline.tms import TMS

    tms = TMS()
    for email in load_inbox(sys.argv[1]):
        email, _ = redact_email(email)
        customer = find_customer(tms, email).customer
        extraction = extract(email, customer, tms.facilities(customer['id']) if customer else [])
        email_problems, load_problems = verify(email, extraction)
        print(email.id, email_problems + [p for ps in load_problems for p in ps] or 'ok')
