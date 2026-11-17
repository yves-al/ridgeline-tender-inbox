"""Step 6: confirm the facility the model picked for each stop.

The model picks a facility id from the customer's list (step 4). Code confirms it:
  - no id picked                         -> new_facility (the stop isn't on file)
  - id not one of this customer's         -> unclear (invented, or another customer's)
  - the stop's words are exactly the facility's name, and no other facility of theirs has it -> ok
  - the stop's words don't name the facility's city or street address -> unclear
  - they give a different street number   -> new_facility (another building in that city)
  - no place words (a change that keeps the stop) -> nothing to confirm
"""
import re
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class FacilityResult:
    facility: dict | None
    review_reason: str | None
    detail: str


def confirm_facility(stop, facilities):
    place = (stop.place or '').strip() if stop else ''
    if not place:
        return FacilityResult(None, None, 'no place given')
    if stop.facility_id is None:
        return FacilityResult(None, 'new_facility', f'"{place}" is not one of the customer\'s facilities')

    facility = next((f for f in facilities if f['id'] == stop.facility_id), None)
    if facility is None:
        return FacilityResult(None, 'unclear', f'{stop.facility_id} is not one of the customer\'s facilities')

    on_file = f'{facility["id"]} ({facility["address"]}, {facility["city"]})'
    words = place.lower()
    same_name = [f for f in facilities if f['name'].lower() == facility['name'].lower()]
    if words == facility['name'].lower() and len(same_name) == 1:
        return FacilityResult(facility, None, f'"{place}" is the name of {facility["id"]}, {facility["city"]}')
    if facility['city'].lower() not in words and facility['address'].lower() not in words:
        return FacilityResult(None, 'unclear', f'"{place}" does not name the city or address of {on_file}')
    street_numbers = re.findall(r'\b\d{2,6}(?= [A-Za-z])', place)
    if street_numbers and facility['address'].split()[0] not in street_numbers:
        return FacilityResult(None, 'new_facility', f'"{place}" is a different address from {on_file}')
    return FacilityResult(facility, None, f'"{place}" is {facility["id"]} {facility["name"]}, {facility["city"]}')


if __name__ == '__main__':
    # Try it: python -m pipeline.facility inbox/labeled.jsonl
    from pipeline.customer import find_customer
    from pipeline.extract import extract
    from pipeline.inbox import load_inbox
    from pipeline.redact import redact_email
    from pipeline.tms import TMS

    tms = TMS()
    for email in load_inbox(sys.argv[1]):
        email, _ = redact_email(email)
        customer = find_customer(tms, email).customer
        facilities = tms.facilities(customer['id']) if customer else []
        for load in extract(email, customer, facilities).loads:
            for name in ('pickup', 'delivery'):
                stop = getattr(load, name)
                if stop:
                    r = confirm_facility(stop, facilities)
                    print(f'{email.id} {load.reference} {name:8} {r.review_reason or "ok":12} {r.detail}')
