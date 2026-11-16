#!/usr/bin/env python3
"""Compare a run's TMS records with an answer key, using the brief's cost table.

  curl -s http://127.0.0.1:8000/v1/export > export.json
  python3 check.py --export export.json --expected inbox/labeled-expected.jsonl --details

Run it on records from one run of the matching inbox file on a freshly started TMS. It reads only the
TMS export, so it sees what your pipeline actually wrote, not what it meant to write.

Each expected action is matched to at most one write and at most one review from the same message:
first by customer and reference (references compare without spaces or case), then any remaining
write from that message, then a review with no reference. A
review with no reference covers every load in its email, so a booking it covers saves nothing; a review
that names one load never covers another. Writes and reviews that match nothing are counted on their own, and so are writes whose
message_id isn't in the key. The key lists every review reason that applies. Times compare as
instants; commodity and notes are not compared.
"""
import argparse
import json
import re
from collections import Counter
from datetime import datetime

SAVE, WRONG, MISSED = 4, -150, -250
# Card-shaped numbers only: 4-4-4-4, 4-6-5, or 13 to 19 digits in a row, each confirmed by a Luhn check.
CARD = re.compile(r'(?<!\d)(?:(?:\d{4}[\s-]+){3}\d{4}|\d{4}[\s-]+\d{6}[\s-]+\d{5}|\d{13,19})(?!\d)')


def luhn(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0
FIELDS = ('customer', 'reference', 'equipment', 'weight_lbs', 'rate_usd', 'temperature_f')
WRITE = {'create': 'created', 'update': 'updated', 'cancel': 'cancelled'}


def norm(ref):
    """References compare without spaces or case."""
    return re.sub(r'\s+', '', str(ref)).upper() if ref else None


def instant(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def diff(expected, actual):
    """Fields where a written load differs from the key."""
    out = [f for f in FIELDS if expected.get(f) != actual.get(f)]
    for stop in ('pickup', 'delivery'):
        e, a = expected[stop], actual.get(stop) or {}
        if e['facility'] != a.get('facility'):
            out.append(f'{stop}.facility')
        for edge in ('window_start', 'window_end'):
            try:
                if instant(e[edge]) != instant(a.get(edge, '')):
                    out.append(f'{stop}.{edge}')
            except ValueError:
                out.append(f'{stop}.{edge}')
    if actual.get('status') == 'cancelled':
        out.append('status')
    return out


def strings(value, path=''):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from strings(v, f'{path}.{k}' if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from strings(v, f'{path}[{i}]')


def card_numbers(export):
    """Every place a card-like number appears, history snapshots included."""
    return [f'{kind[:-1]} {item.get("id")} {path}' for kind in ('loads', 'reviews') for item in export.get(kind, [])
            for path, text in strings(item)
            if any(luhn(re.sub(r'\D', '', m.group())) for m in CARD.finditer(text))]


def score(export, expected):
    writes = [{'message_id': h['message_id'], 'kind': h['action'], 'load': h.get('load') or load, 'used': False}
              for load in export.get('loads', []) for h in load.get('history', [])]
    reviews = [dict(r, used=False) for r in export.get('reviews', [])]
    known = {e['message_id'] for e in expected}
    rows, tally, money = [], Counter(), 0
    clean_emails = 0

    def add(mid, action, ref, outcome, detail, value):
        nonlocal money
        money += value
        tally[outcome] += 1
        rows.append({'message_id': mid, 'action': action, 'reference': ref, 'outcome': outcome, 'detail': detail, 'value': value})

    for exp in expected:
        mid = exp['message_id']
        mine_w = [w for w in writes if w['message_id'] == mid]
        mine_r = [r for r in reviews if r['message_id'] == mid]
        acts = [dict(a, ref=norm(a.get('reference') or a.get('load', {}).get('reference')),
                     cust=a.get('customer') or a.get('load', {}).get('customer'), w=None, r=None) for a in exp['actions']]
        # 1: by reference. 2: leftovers to the natural action (reviews to reviews, writes to the same kind
        # of write). 3: anything still left, in order (a review on a booking, a write on a review).
        def take(pool, a, slot, ok):
            if a[slot] is None:
                for x in pool:
                    if not x['used'] and ok(x):
                        x['used'], a[slot] = True, x
                        return

        live = [a for a in acts if a['action'] != 'none']
        # Writes: by reference, then the same kind, then any kind (a wrong-kind write on a write
        # action), then whatever is left onto review actions (a write where the key says review).
        for a in live:
            take(mine_w, a, 'w', lambda x: norm(x['load'].get('reference')) == a['ref'] and x['load'].get('customer') in (a['cust'], None))
        for a in live:
            if a['action'] in WRITE:
                take(mine_w, a, 'w', lambda x: x['kind'] == WRITE[a['action']])
        for a in live:
            if a['action'] in WRITE:
                take(mine_w, a, 'w', lambda x: True)
        for a in live:
            take(mine_w, a, 'w', lambda x: True)
        # Reviews: by reference, then a review with no reference (or, in a one-load email, any
        # review). A review that names a different load never covers this one.
        for a in live:
            take(mine_r, a, 'r', lambda x: norm(x.get('reference')) == a['ref'])
        def loose(a):
            return lambda x: not x.get('reference') or a['ref'] is None
        for a in live:
            if a['action'] == 'review':
                take(mine_r, a, 'r', loose(a))
        for a in live:
            take(mine_r, a, 'r', loose(a))
        # A review with no reference is about the whole email: it covers every load in it.
        blanket = next((x for x in mine_r if not x.get('reference')), None)
        if blanket:
            for a in live:
                if a['r'] is None:
                    a['r'], blanket['used'] = blanket, True
        for a in acts:
            kind, ref, w, r = a['action'], a['ref'], a['w'], a['r']
            if kind == 'none':
                hits = [x for x in mine_w if not x['used']]
                for x in hits:
                    x['used'] = True
                    add(mid, kind, x['load'].get('reference'), 'wrong_auto', f'{x["kind"]}; key says no record ({a["why"]})', WRONG)
                rev = [x for x in mine_r if not x['used']]
                for x in rev:
                    x['used'] = True
                if not hits:
                    add(mid, kind, ref, 'unneeded_review' if rev else 'correct_auto', rev[0]['reason'] if rev else '', 0 if rev else SAVE)
            elif kind in WRITE:
                wrong_target = w and kind == 'cancel' and (norm(w['load'].get('reference')) != ref or
                                                          (a['cust'] and w['load'].get('customer') != a['cust']))
                if w and (w['kind'] != WRITE[kind] or wrong_target or (kind != 'cancel' and diff(a['load'], w['load']))):
                    detail = (f'{w["kind"]} instead of {WRITE[kind]}' if w['kind'] != WRITE[kind] else
                              f'cancelled {w["load"].get("reference")} instead of {ref}' if wrong_target else ', '.join(diff(a['load'], w['load'])))
                    add(mid, kind, ref, 'wrong_auto', detail, WRONG)
                elif w and r:
                    add(mid, kind, ref, 'reviewed_anyway', f'right {w["kind"]}, but also sent to review ({r["reason"]})', 0)
                elif w:
                    add(mid, kind, ref, 'correct_auto', '', SAVE)
                elif r:
                    add(mid, kind, ref, 'missed_automation', r['reason'], 0)
                elif kind == 'create':
                    add(mid, kind, ref, 'missed_tender', '', MISSED)
                else:
                    add(mid, kind, ref, 'missed_change', '', WRONG)
            else:  # review
                if w:
                    add(mid, kind, ref, 'wrong_auto', f'{w["kind"]}; key says review ({"/".join(a["reasons"])})', WRONG)
                elif r:
                    ok = r['reason'] in a['reasons']
                    add(mid, kind, ref, 'correct_review' if ok else 'review_other_reason', r['reason'], 0)
                elif a.get('change'):
                    add(mid, kind, ref, 'missed_change', '', WRONG)
                else:
                    add(mid, kind, ref, 'missed_tender', '', MISSED)
        for x in mine_w:
            if not x['used']:
                x['used'] = True
                add(mid, 'extra', x['load'].get('reference'), 'wrong_auto', f'unexpected {x["kind"]}', WRONG)
        for x in mine_r:
            if not x['used']:
                x['used'] = True
                add(mid, 'extra', x.get('reference'), 'extra_review', x['reason'], 0)
        if all(row['outcome'] == 'correct_auto' for row in rows if row['message_id'] == mid):
            clean_emails += 1

    unknown = sorted({w['message_id'] for w in writes if w['message_id'] not in known} |
                     {r['message_id'] for r in reviews if r['message_id'] not in known})
    for w in writes:
        if w['message_id'] not in known:
            add(w['message_id'], 'unattributed', w['load'].get('reference'), 'wrong_auto', f'{w["kind"]} with a message_id not in the key', WRONG)

    actions = sum(1 for e in expected for _ in e['actions'])
    auto = tally['correct_auto'] + tally['wrong_auto']
    missed = tally['missed_tender'] + tally['missed_change']
    review_actions = sum(1 for e in expected for a in e['actions'] if a['action'] == 'review')
    found = tally['correct_review'] + tally['review_other_reason']
    summary = {
        'emails': len(expected), 'actions': actions, 'outcomes': dict(tally),
        'actions_handled_right_without_a_person': f'{tally["correct_auto"]} of {actions}',
        'emails_needing_no_person': f'{clean_emails} of {len(expected)}',
        'wrong_without_a_person': f'{tally["wrong_auto"]} of {auto} acted on alone',
        'missed_with_no_review': missed,
        'reviews_caught': f'{found} of {review_actions}',
        'review_reason_right': f'{tally["correct_review"]} of {found}',
        'value_usd': money, 'value_usd_per_100_emails': round(money * 100 / len(expected), 2) if expected else 0,
        'message_ids_not_in_key': unknown,
        'card_numbers_stored': card_numbers(export),
    }
    return summary, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--export', required=True)
    ap.add_argument('--expected', required=True)
    ap.add_argument('--details', action='store_true', help='list every action that was not handled right')
    a = ap.parse_args()
    with open(a.export) as f:
        export = json.load(f)
    with open(a.expected) as f:
        expected = [json.loads(line) for line in f if line.strip()]
    summary, rows = score(export, expected)
    print(json.dumps(summary, indent=2))
    if a.details:
        for r in rows:
            if r['outcome'] not in ('correct_auto', 'correct_review'):
                print(f"{r['message_id']}  {r['action']:12} {str(r['reference']):14} {r['outcome']:20} {r['detail']}")


if __name__ == '__main__':
    main()
