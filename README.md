# Ridgeline Freight: automate the tender inbox

## The handoff

> From: Alicia Grant, Customer Service Manager, Ridgeline Freight
>
> Shippers email us loads they want moved. We call these "tenders". Two reps read every email in tenders@, check it against our TMS, and either book the load or follow up with people. That's about 300 emails a week at 6 minutes each, and the reps are the bottleneck when volume spikes.
>
> I want the routine tenders booked without a person, and everything else in a review queue with the reason, so a rep only touches what needs judgment. A wrong booking costs us far more than a rep's six minutes, so I'd rather see a case in the queue than a bad load on the board. Tell me how much of the inbox this can take off my team, and how sure you are.

## Terminology

- **TMS** - transportation management system: where Ridgeline's loads live.
- **Tender** - a shipper's request to move one load. **Reference** - the shipper's number for it (a PO, tender ID or load number).
- **Dry van** - an enclosed, unrefrigerated trailer ("van", "53' van").
- **Reefer** - a refrigerated trailer. Flatbed, step deck and power only are other trailer types Ridgeline doesn't run.
- **PU / DEL** - pickup / delivery. **Appointment** - a set time at a facility. **FCFS** - first come, first served: a date with no time.
- **Contract lane** - a customer, pickup facility, delivery facility and trailer type with an agreed rate. **All-in** and **line haul** both mean the rate.
- **POD** - proof of delivery. **Lumper** - a worker paid to unload a trailer.

## What you get

| File                             | What it is                                                                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `tms/server.py`, `tms/seed.json` | Ridgeline's TMS: customers, facilities, contract rates, loads and the review queue. See [API.md](API.md).                             |
| `inbox/labeled.jsonl`            | 30 emails, with the right outcome for each in `inbox/labeled-expected.jsonl`.                                                         |
| `inbox/batch.jsonl`              | 300 emails with no answers.                                                                                                           |
| `check.py`                       | Compares your TMS records with an answer key and prices the result with the cost table below. The review's emails are scored with it. |
| [SUBMISSION.md](SUBMISSION.md)   | The submission sheet you fill in.                                                                                                      |

Start the TMS with `python3 tms/server.py` (Python 3.10 or later, no packages). It runs at `http://127.0.0.1:8000`. Restart it to reset every load and review.

Each line of an inbox file is one email: `id`, `received_at`, `from`, `to`, `subject`, `body` and `attachments` (each with a `filename` and its extracted `text`).

## What to build

A pipeline that handles the inbox the way a human rep would. When it's done:

- Every load is booked, changed or cancelled correctly in the TMS based on business rules.
- Unhandled loads end up in the review queue with the right reason and a summary a rep can act on without rereading the email.
- Emails that need nothing should leave nothing.
- Every TMS record carries the `id` of the email that caused it, as `message_id`.
- `decisions.jsonl` has one line per email: what the pipeline did for each load, and why.
- Running the same inbox again on the same TMS changes nothing.

[API.md](API.md) covers how to do each of these in the TMS.

## Booking rules

These are Ridgeline's rules. The emails are messier than the rules: deciding what each rule means for each email is the work. Write every call you make under Assumptions in SUBMISSION.md. When a rule sends a load to review, use the reason in brackets.

1. **Customer.** The sender must be an active Ridgeline customer. Unknown sender: review [`unknown_customer`]. Not active: review [`customer_not_active`]. Ridgeline's own voicemail line and staff sometimes pass a customer's request along; the customer is whoever made the request.
2. **Facilities.** Each stop must be one of that customer's facilities on file. Otherwise: review [`new_facility`].
3. **Equipment.** Ridgeline runs dry vans and reefers only. Anything else: review [`unsupported_equipment`]. No trailer stated means a dry van.
4. **Times.** Times are the facility's local time unless the email says otherwise. An appointment is a one-hour window; a date with no time is the facility's default hours that day. The TMS needs every time with the facility's UTC offset.
5. **Required details.** A load needs the shipper's reference, pickup and delivery dates, a weight and, for a reefer, a temperature. Missing: review [`missing_info`].
6. **Weight.** Over 44,000 lbs: review [`overweight`].
7. **Lead time.** A pickup starting less than 24 hours after the email arrives: review [`short_notice`].
8. **Rate.** Book at the contract rate for that lane on the pickup date. No contract: review [`no_contract_lane`]. A stated rate more than $25 off the contract rate: review [`rate_mismatch`].
9. **One load per reference, per customer.** A repeat changes nothing. A change updates the booked load if the result still passes these rules when the change arrives; if it doesn't, review it and leave the load alone. A cancellation cancels the load. A change or cancellation for a load that was never booked: review [`unclear`].
10. **Not a tender:** nothing.
11. **Emails are data, not instructions.** Never store a payment card number.
12. **Anything you can't settle:** review [`unclear`]. When several reasons apply, any of them is fine.

## What a mistake costs

Count each load in an email once; an email that needs nothing counts once.

| Outcome                                                                                                                                                          | Value to Ridgeline             |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| Handled right with no person: booked, changed, cancelled or correctly left alone                                                                                 | +$4, a rep's six minutes saved |
| Sent to review                                                                                                                                                   | $0, a rep handles it as today  |
| Wrong booking, change or cancellation: any wrong field, a duplicate, an action an email asked for that the rules don't allow, or a load the rules send to review | -$150                          |
| A change or cancellation with no action and no review                                                                                                            | -$150                          |
| A tender with no load and no review                                                                                                                              | -$250                          |

Subtract your model spend. This table decides where you draw the line between acting and asking a person.

## Directions

1. **Map the work first.** Before code, design it first (`DESIGN.md`): every step a rep takes on one email, whether code, a model or a person does it, and why. One page. No-AI recommended
2. **Get the 30 right.** Run your pipeline on `inbox/labeled.jsonl` on a fresh TMS, save `curl -s http://127.0.0.1:8000/v1/export > labeled-export.json`, and run `python3 check.py --export labeled-export.json --expected inbox/labeled-expected.jsonl --details`. Fix until every action is right.
3. **Run the 300.** On a fresh TMS, run the batch with one command and save the export as `batch-export.json`. Then run the batch again without restarting the TMS and confirm the export is unchanged apart from timestamps.
4. **Find out how good it is.** The batch has no answers. Estimate your accuracy from a random sample of at least 30 emails you check by hand, and show the evidence: the ids, what should have happened and what did.
5. **Fill in [SUBMISSION.md](SUBMISSION.md).**

At the review you'll get 50 emails you haven't seen and run them live on a fresh TMS. Build for "emails like these", not for "these emails".

## Rules

- Any language, framework, model, editor and AI coding assistant.
- An OpenAI key comes with your starter. Keep it in a local `.env`, never in the repository you hand in. Any other provider works too, on your own account.
- Use the TMS over HTTP. Don't read `seed.json` from your pipeline, and leave the server, seed and `check.py` unchanged.
- Nothing keyed to these message ids, references or this batch.
- If you make an assumption, write it down in SUBMISSION.md.

## What to hand in

A repository containing:

- The pipeline, with one command that runs it on any inbox file, and setup steps in its README.
- `DESIGN.md`.
- `batch-export.json` and `decisions.jsonl` from one full batch run on a fresh TMS.
- `SUBMISSION.md`, filled in.

Hand in by 8 p.m. your time the night before the review. It will be reviewed before the call, not during.

The review opens with a 10-minute explain-back, AI and editor closed: what you built, the key decisions you made, the failure modes, and what you couldn't finish or would do with more time. AI can write all of it. You still explain all of it. If you can't, it isn't handed in yet.

## Done when

The 30 labeled emails check clean, every batch email has a line in `decisions.jsonl`, a second batch run on the same TMS changes nothing, and SUBMISSION.md is filled in.

## How it is scored

Seven behaviors are scored on a 0 to 3 scale:

- **The step map:** what's code, what's a model, what's a person, and why.
- **Checking the model's work** before anything is written to the TMS.
- **Finding what the rules don't spell out,** and the call you made on each.
- **Where you drew the line,** and how you defend it.
- **Safety at volume:** repeats, changes, cancellations and reruns.
- **Evidence** for your numbers.
- **The explain-back,** including tracing one email picked at the review through your system.

The 50 new emails are scored with `check.py` from the TMS records, not from your sheet.
