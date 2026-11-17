# Submission

## Run

| | |
|---|---|
| Command | python3 -m pipeline.run inbox/batch.jsonl |
| Setup steps | In 1 terminal run python3 tms/server.py and in a second terminal run python3 -m pipeline.run inbox/batch.jsonl |
| Model(s) and what each one does | gpt-5.4-mini-2026-03-17 and it just extracts relevant info from emails to create a ticket |
| Time for the 300 emails | ~119s |
| Model spend for the 300, and per email | ~0.00221/call and ~0.66 for all calls |

## The 30 labeled emails

Paste the `check.py` summary. For any action that isn't right, the message id and why.

cmp labeled-export.json labeled-export-2.json && echo identical
identical

## The 300-email batch

| | |
|---|---|
| Loads booked, changed or cancelled with no person | 152 booked, 8 changed, 7 cancelled with no person |
| Emails correctly left alone (not a tender, or a repeat) | 42 |
| Loads sent to review, by reason | short_notice 18, rate_mismatch 16, missing_info 13, overweight 11, unclear 10, new_facility 9, customer_not_active 9, unknown_customer 9, no_contract_lane 9, unsupported_equipment 6 |
| Emails that needed no person at all | 190 of 300 |
| Second run on the same TMS: what changed | load AP-93507 updated by Run 2 |

## Your sample

How you picked it, and how many. Then one row per email:
I selected based on the cases we can encounter. So I picked emails that would go through loading a ticket perfectly fine, emails that change or cancel loads, emails that do nothing, and more importantly emails that should be sent to review

| Message id | What should have happened | What your pipeline did | Right? |
|---|---|---|---|
| B-047 | Review, unknown_customer | Sent to review: alpine-paper.example isn't a customer domain (lookalike of alpinepaper.example) | ✔ |
| B-023 | Cancel CHG-12902 | Cancelled | ✔ |
| B-021 | Book MBS-82717 at $1,400 | Booked | ✔ |
| B-010 | Review, customer_not_active | Sent to review: Sunbelt Plastics is inactive | ✔ |
| B-212 | Review, missing_info | Sent to review: no reference in the email | ✔ |
| B-018 | Book PPB-41825, card number not stored | Booked, card removed | ✔ |
| B-145 | Review, overweight | Sent to review: change to 47,000 lbs is over 44,000, load left at 41,000 | ✔ |
| B-038 | Book AP-99424 from staff forward | Sent to review: missing_info, the model dropped "Thursday" so no pickup date | ✘ |
| B-194 | Book AP-50984 (pickup 25h out) | Booked | ✔ |
| B-252 | Book CHG-92299 for Prairie Pet | Booked, separate from Cascade's CHG-92299 | ✔ |
| B-014 | Book PP-88529 at $3,150 | Booked | ✔ |
| B-037 | Book PPB-67856, MT times converted | Booked (cancelled later by B-046) | ✔ |
| B-003 | Book CHG-22521, card number not stored | Booked, card removed | ✔ |
| B-024 | Update delivery to 10 AM | Updated | ✔ |
| B-002 | Review, new_facility | Sent to review: Golden CO annex isn't on file | ✔ |
| B-200 | Review, rate_mismatch | Sent to review: $1,825 vs $1,525 contract, "VP override" text ignored | ✔ |
| B-053 | Nothing (follow-up) | Nothing | ✔ |
| B-058 | Book PPB-10039 for Cascade | Booked, separate from Pikes Peak's PPB-10039 | ✔ |
| B-267 | Review, missing_info (reference is "TBD") | Sent to review in run 1, but booked with reference "TBD" on a rerun | ✔ (✘ on rerun) |
| B-203 | Review, short_notice | Sent to review: change came 9h before pickup | ✔ |
| B-084 | Book AP-68229 at $2,240 (new rate) | Booked | ✔ |
| B-049 | Review, rate_mismatch | Sent to review: $2,825 vs $2,650 contract | ✔ |
| B-064 | Book FRF-15692 from voicemail | Booked | ✔ |
| B-179 | Nothing | Nothing | ✔ |
| B-094 | Book AP-19451 from staff forward | Booked | ✔ |
| B-022 | Nothing (resend) | Nothing | ✔ |
| B-297 | Update pickup to 10:00 | Updated in run 2, missed in run 1 | ✔ (✘ in run 1) |
| B-097 | Nothing (billing question) | Nothing | ✔ |
| B-217 | Book rows 1-2, review row 3 | Booked 1-2, row 3 sent to review: short_notice, pickup 21h out | ✔ |
| B-063 | Review, no_contract_lane | Sent to review: no rate for Denver to Salina | ✔ |
| B-113 | Update delivery to 12 PM | Updated in run 1, but nothing happened on a rerun: the model left out the new time | ✔ (✘ on rerun) |
| B-033 | Review, unsupported_equipment | Sent to review: flatbed | ✔ |
| B-012 | Review, unclear | Sent to review: body says 36,000 lbs, attachment says 42,000 | ✔ |

Accuracy on the sample, for what went through without a person, and what a sample this size can and can't tell you.
The model successfully identified most of the examples that should be booked from the 30 samples but the sample is too small. It doesn't tell us if the model can behave well enough on more cases. I would be better to use more examples.

## Where you drew the line

When an extraction is reliable enough to act on, and when it goes to review. Compare it with one alternative, in dollars per 100 emails from the cost table, after model spend.

An extraction is reliable enough if it can successfully pull all the required info that we need to log a ticket and if that info matches what we have from the real email. Anything else should go to review. It's better to have a human review than handling failures.

## What it gets wrong

The failure modes you found, with a message id for each, and what you'd do about them.

The same email can be run differently from run to run. That means if we restart the server we might get different result. So I think it would be a good thing to persist the result in some long term memory.

I think B-267 was booked with shipper reference TBD, and that should not happen. We can use reg expressions to handle data that have pattern like ids and check if the data match before logging tickets.

A rerun can change something for emails that wrote nothing on the first run. B-297 was planned again and made some change. So if an email writes nothing it should be kept in memory so nothing changes on rerun

## Assumptions

Anything the brief left open that you decided.
