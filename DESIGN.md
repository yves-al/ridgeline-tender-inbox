## System's parts and roles

Retrieve email and get all info related to that email, the sender, the email id, the domain, etc.
Next we need to find the customer from our customer base: code
Pass all the email info to the LLM and get the LLM to only extract the info from the content passed (load, is it tender, change, cancel, etc., reference, stops, facilities, dates, weight, rate, etc.): LLM
Once we have all that info we need to check if we have all the data, if anything is missing then we throw that email in the review queue to be reviewed by a human, or if the model returns ambiguous info: code
Before submitting a ticket, we check if it's not duplicate using the idempotency key. if it's duplicate then we don't submit a ticket for that. If there's change we go ahead and make the change in the ticket: code
Once we verify that everything is OK and we have all the info we need for the ticket, we can go ahead and create the ticket with the info received from the LLM: code
Review queue is handled by a human to make decision regarding ambiguous cases

## Where we draw the line

We act only when every check and every rule passes. Anything we're not sure about goes to review.
