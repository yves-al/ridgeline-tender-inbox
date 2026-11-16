# Submission

## Run

| | |
|---|---|
| Command | |
| Setup steps | |
| Model(s) and what each one does | |
| Time for the 300 emails | |
| Model spend for the 300, and per email | |

## The 30 labeled emails

Paste the `check.py` summary. For any action that isn't right, the message id and why.

## The 300-email batch

| | |
|---|---|
| Loads booked, changed or cancelled with no person | |
| Emails correctly left alone (not a tender, or a repeat) | |
| Loads sent to review, by reason | |
| Emails that needed no person at all | of 300 |
| Second run on the same TMS: what changed | |

## Your sample

How you picked it, and how many. Then one row per email:

| Message id | What should have happened | What your pipeline did | Right? |
|---|---|---|---|

Accuracy on the sample, for what went through without a person, and what a sample this size can and can't tell you.

## Where you drew the line

When an extraction is reliable enough to act on, and when it goes to review. Compare it with one alternative, in dollars per 100 emails from the cost table, after model spend.

## What it gets wrong

The failure modes you found, with a message id for each, and what you'd do about them.

## Assumptions

Anything the brief left open that you decided.
