"""Step 8: apply Ridgeline's booking rules to one load.

decide() returns the load exactly as it should be in the TMS, or the review reason and a sentence
for the rep. The rules run in a fixed order and the first one that fails decides the reason
(rule 12: when several apply, any of them is fine).

For a change, current is the booked load: the change is merged onto it, and the result must pass
every rule as of when the change arrived, or the load is left alone.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from pipeline.facility import confirm_facility
from pipeline.times import TimeProblem, load_windows

MAX_WEIGHT_LBS = 44000
MIN_LEAD_TIME = timedelta(hours=24)
RATE_TOLERANCE_USD = 25


@dataclass
class Decision:
    load: dict | None = None    # what to book (or what a changed load should become)
    reason: str | None = None   # review reason; None means book it
    detail: str = ''            # why, in a sentence a rep can act on
    notes: list = field(default_factory=list)  # things worth logging even when it's booked

    @property
    def ok(self):
        return self.reason is None


def review(reason, detail):
    return Decision(reason=reason, detail=detail)


def money(value):
    return f'${value:,.0f}'


def decide(load, email, customer, facilities, problems, tms, current=None, lead_time=True):
    """customer: step 3 result. facilities: the customer's. problems: step 5's for this load.
    lead_time=False skips the 24-hour rule, to compare a resend with a load already booked."""
    # 1. Customer
    if not customer.ok:
        return review(customer.review_reason, customer.detail)
    # The model's answer must have passed every check (step 5)
    if problems:
        return review('unclear', 'The extraction did not check out: ' + '; '.join(problems))
    if not current and not load.reference:
        return review('missing_info', "No shipper's reference.")

    # 2. Facilities (a change that doesn't mention a stop keeps the booked one)
    stops = {}
    for kind in ('pickup', 'delivery'):
        result = confirm_facility(getattr(load, kind), facilities)
        if result.review_reason:
            return review(result.review_reason, f'{kind.capitalize()}: {result.detail}')
        if result.facility:
            stops[kind] = result.facility
        elif current:
            stops[kind] = next(f for f in facilities if f['id'] == current[kind]['facility'])
        else:
            return review('missing_info', f'No {kind} location.')

    # 3. Equipment (no trailer stated means a dry van)
    changes = set(load.changed_fields) if current else None
    def value(name, extracted):
        """The extracted value for a new load or a changed field, else the booked one."""
        return extracted if current is None or name in changes else current.get(name)
    equipment = value('equipment', load.equipment)
    if equipment is None:
        equipment = 'dry_van' if current is None else None
    if equipment == 'other':
        return review('unsupported_equipment', 'The email asks for a trailer Ridgeline does not run (dry van and reefer only).')
    if equipment not in ('dry_van', 'reefer'):
        return review('unclear', 'The change does not say which trailer.')

    # 4. Times
    try:
        windows = load_windows(load, stops['pickup'], stops['delivery'], email.received_at, current)
    except TimeProblem as problem:
        return review(problem.reason, problem.detail)

    # 5. Required details
    weight = value('weight_lbs', load.weight_lbs)
    temperature = value('temperature_f', load.temperature_f)
    if weight is None:
        return review('missing_info', 'No weight.')
    if equipment == 'reefer' and temperature is None:
        return review('missing_info', 'Reefer load with no temperature.')
    if equipment == 'dry_van' and temperature is not None and (current is None or 'temperature_f' in changes):
        return review('unclear', f'Dry van load with a temperature ({temperature:g}F): does it need a reefer?')

    # 6. Weight
    if weight > MAX_WEIGHT_LBS:
        return review('overweight', f'{weight:,} lbs is over the {MAX_WEIGHT_LBS:,} lb limit.')

    # 7. Lead time
    pickup_start = windows['pickup'][0]
    lead = pickup_start - email.received_at
    if lead_time and lead < MIN_LEAD_TIME:
        hours = lead.total_seconds() / 3600
        return review('short_notice', f'Pickup {pickup_start:%a %m/%d %H:%M} is {hours:.0f} hours after the email arrived '
                                      f'({email.received_at:%a %m/%d %H:%M}); the minimum is 24.')

    # 8. Rate: the contract rate for the lane on the pickup date
    pickup_date = pickup_start.date()
    lane = f'{stops["pickup"]["id"]} to {stops["delivery"]["id"]} ({equipment.replace("_", " ")})'
    rates = [r for r in tms.rates(customer.customer['id'], stops['pickup']['id'], stops['delivery']['id'], equipment)
             if r['valid_from'] <= pickup_date.isoformat() <= r['valid_to']]
    if not rates:
        return review('no_contract_lane', f'No contract rate for {lane} on {pickup_date:%m/%d}.')
    if len(rates) > 1:
        return review('unclear', f'More than one contract rate for {lane} on {pickup_date:%m/%d}.')
    contract = rates[0]['rate_usd']
    stated = load.rate_usd if current is None or 'rate_usd' in changes else None
    if stated is not None and abs(stated - contract) > RATE_TOLERANCE_USD:
        return review('rate_mismatch', f'Offered {money(stated)}; contract is {money(contract)} for {lane} on {pickup_date:%m/%d}.')

    booked = {
        'customer': customer.customer['id'],
        'reference': current['reference'] if current else load.reference,
        'pickup': {'facility': stops['pickup']['id'], 'window_start': windows['pickup'][0].isoformat(),
                   'window_end': windows['pickup'][1].isoformat()},
        'delivery': {'facility': stops['delivery']['id'], 'window_start': windows['delivery'][0].isoformat(),
                     'window_end': windows['delivery'][1].isoformat()},
        'equipment': equipment,
        'weight_lbs': weight,
        'rate_usd': contract,
    }
    if equipment == 'reefer':
        booked['temperature_f'] = temperature
    commodity = current.get('commodity') if current else load.commodity
    if commodity:
        booked['commodity'] = commodity[:500]
    notes = [f'stated rate {money(stated)} is within $25 of contract {money(contract)}'] if stated is not None and stated != contract else []
    return Decision(load=booked, notes=notes)
