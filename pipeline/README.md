# Tender inbox pipeline

This program reads the emails that shippers send to Ridgeline's tender inbox and handles them in the TMS. For each load in an email it does one of these:

- **books** a new load,
- **changes** or **cancels** a load that's already booked,
- **sends it to the review queue** with a reason and a short summary for a rep, or
- **does nothing**, for a repeat, a follow-up, or an email that isn't a tender.

It writes one line per email to `decisions.jsonl` saying what it did and why. Running it twice on the same inbox doesn't book anything twice.

The one rule behind the design: **the AI model only reads emails.** It pulls out what each email says (reference, stops, dates, weight and so on). Plain code then checks what the model copied, works out the dates and times, applies Ridgeline's booking rules and does every write. If anything doesn't check out, the load goes to review instead of being booked.

![How the pipeline works](../docs/system-diagram.png)

## Setup

You need Python 3.10 or later (we used 3.12) and an OpenAI API key.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "OPENAI_API_KEY=sk-..." > .env      # stays local, .env is in .gitignore
```

Start the TMS in its own terminal. It keeps everything in memory, so restarting it gives you a fresh TMS.

```bash
python3 tms/server.py
```

## Running it

```bash
.venv/bin/python -m pipeline.run inbox/batch.jsonl
```

Options:

| Option | Default | What it does |
|---|---|---|
| `--out` | `decisions.jsonl` | where the decision log goes |
| `--workers` | `8` | how many emails the model reads at once |

The TMS address comes from `TMS_URL` (default `http://127.0.0.1:8000`). When the run ends it prints the number of each action, the reviews by reason, and how many emails needed no person at all.

To save what ended up in the TMS and score it against the answer key for the labeled emails:

```bash
curl -s http://127.0.0.1:8000/v1/export > labeled-export.json
python3 check.py --export labeled-export.json --expected inbox/labeled-expected.jsonl --details
```

Score a fresh TMS: restart the server before a run you want to score.

## How it works

The run has two phases.

1. **Read ahead** (`run.read_ahead`): the model reads all the emails, 8 at a time, and its answers are saved in `.cache/extract`. This is just for speed.
2. **Handle** (`run.handle`): the emails are handled one at a time in the order they arrived, so a change always comes after the booking it changes. For each email, `plan.plan_email()` decides what to do, `write.apply()` writes it, and the decision is logged.

Each email goes through these steps. The numbers match DESIGN.md.

| Step | File, function | What it does |
|---|---|---|
| 1 | `inbox.py` `load_inbox()` | Reads the `.jsonl` inbox into `Email` objects and sorts them by received time. Stops with the line number if a line is broken. |
| 2 | `redact.py` `redact_email()` | Removes card numbers from the body and attachments before anything else, so the model never sees them and nothing stored has them. |
| 3 | `customer.py` `find_customer()` | Finds the customer from the sender's domain. For Ridgeline's voicemail and staff forwards, the customer is the outside address in the "Caller:" or "From:" line. Unknown or inactive senders go to review. |
| 4 | `extract.py` `extract()` | The model (`gpt-5.4-mini`) reads the email plus the customer's facility list. It fills in a fixed form for each load: new / change / cancel / follow-up, reference, stops, the date and time words exactly as written, equipment, weight, temperature, rate. It has no tools and can't write anything. |
| 5 | `verify.py` `verify()` | Checks the model's answer against the email: the reference, weight, temperature and rate must really be in the text, the place, date and time words must be copied from it, the trailer must be named, and the body and attachment must agree. Anything off goes to review as `unclear`. |
| 6 | `facility.py` `confirm_facility()` | Confirms each stop is one of the customer's facilities, by name, city or street address. A stop that isn't on file is `new_facility`. |
| 7 | `times.py` `load_windows()` | Turns the date and time words into exact windows with the facility's UTC offset. Handles weekdays ("Thursday"), "tomorrow", one time (a one-hour appointment), ranges, FCFS (the facility's default hours) and stated zones like MT. Anything it doesn't understand is `unclear`. |
| 8 | `rules.py` `decide()` | Applies the booking rules in a fixed order; the first one that fails gives the review reason: customer, checks, reference, facilities, equipment, times, weight and temperature, over 44,000 lbs, under 24 hours' notice, rate. Books at the contract rate for the lane on the pickup date. |
| 9 | `plan.py` `plan_load()` | Looks up the reference in the TMS and decides what the email means for it: a new booking, a repeat (nothing), a change (update only what changed, if it still passes the rules), a cancel, or a change or cancel for a load that was never booked (review). |
| 10 | `write.py` `apply()` | Books, updates, cancels or creates the review in the TMS. Every write has an idempotency key made from the email id, the action and the reference. If the TMS rejects a write, the load goes to review instead. |
| 11 | `run.py` `handle()` | Writes the email's line to `decisions.jsonl`. |

`tms.py` is the small HTTP client every step uses to talk to the TMS. It retries temporary errors, and the run stops if the TMS can't be reached at all, rather than skip an email.

## What a decision line looks like

```json
{"message_id": "B-003", "received_at": "2026-11-09T08:31:00-07:00", "from": "priya@cascadehome.example",
 "subject": "Tender: CHG-22521", "customer": "C102", "requester": "priya@cascadehome.example",
 "customer_check": "...", "card_numbers_removed": 1, "instructions_to_automation": [],
 "loads": [{"reference": "CHG-22521", "action": "create", "reason": null, "why": "passes every rule", "record": "RF-70002"}]}
```

`action` is one of `create`, `update`, `cancel`, `review`, `none` (nothing needed) or `done` (an earlier run already handled it). For a review, `reason` is the review reason and `why` is the sentence the rep sees.

## Running it again

A rerun on the same TMS doesn't write anything twice:

- Before doing anything, each email checks whether it already wrote a record. If it did, that load is `done`.
- Every write carries an idempotency key, so even a retried request can't book twice.
- The model's answers are cached by email, so a rerun reads each email the same way. Clear `.cache/extract` if you want the model to read everything again.

## Trying one step on its own

Most files can be run alone on an inbox, which is handy for seeing what one step does:

```bash
.venv/bin/python -m pipeline.extract inbox/labeled.jsonl    # what the model reads from each email
.venv/bin/python -m pipeline.verify inbox/labeled.jsonl     # what the checks flag
.venv/bin/python -m pipeline.facility inbox/labeled.jsonl   # facility matches
.venv/bin/python -m pipeline.plan inbox/labeled.jsonl       # what it would do, without writing
```

`python -m pipeline.write` does write to the TMS, so only use it on a TMS you don't mind changing.

## Known weak spots

- The model can leave something out, for example a new time on a change. The checks catch words the model makes up, not words it misses, so a change can be skipped silently (B-297, B-113).
- A placeholder reference like "TBD" is accepted as a reference (B-267).
- A fresh run can read an email differently from the last one, because the model isn't fully deterministic.

SUBMISSION.md has the details and the fixes we'd make.
