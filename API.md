# Ridgeline TMS API (tender inbox)

Start the server with `python3 tms/server.py`. Base URL: `http://127.0.0.1:8000`. This local training service has no authentication and binds only to localhost. Customers, facilities and contract rates come from `tms/seed.json`; loads and reviews live in memory. Restart to reset them. Your pipeline must use HTTP, not read the seed file.

Lists return `{"items":[...]}`; single resources return `{"item":{...}}`. Unknown ids return 404. An unmatched filter returns an empty list.

## Read

| GET path | Returns |
|---|---|
| `/health` | `{"status":"ok"}` |
| `/v1/customers` | All customers: `id`, `name`, `domains`, `status` (`active`, `credit_hold`, `inactive`), `contact` |
| `/v1/customers?domain={domain}` | The customer with that email domain |
| `/v1/customers/{id}` | One customer |
| `/v1/facilities?customer={id}` | A customer's facilities: `id`, `name`, `address`, `city`, `state`, `timezone`, `default_hours` (`open`, `close`, local 24-hour) |
| `/v1/facilities/{id}` | One facility |
| `/v1/rates?customer=&pickup_facility=&delivery_facility=&equipment=` | Contract lanes; any filter may be left out. Each has `rate_usd`, `valid_from` and `valid_to` (inclusive dates) |
| `/v1/loads?customer=&reference=&status=&message_id=` | Booked loads; any filter may be left out. `message_id` matches any email that created or changed the load |
| `/v1/loads/{id}` | One load, with its `history` |
| `/v1/reviews?message_id=` | The review queue |
| `/v1/export` | Every load and review: `{"loads":[...],"reviews":[...]}` |

From November 1, 2026 (after daylight saving ends), the facility time zones have these UTC offsets: `America/Denver` and `America/Phoenix` -07:00, `America/Chicago` -06:00. Every email and stop in this build falls after that date.

## Write

Send JSON with `Content-Type: application/json` and an `Idempotency-Key` header. Use a new key for each action and the same key when you retry it. A key belongs to one method and path: repeating it with the same body returns the original record with 200, and reusing it with different data returns 409. Keys reset when the server restarts.

### Book a load

`POST /v1/loads` returns 201 and the load, with `id` and `status: tendered`.

```json
{
  "message_id": "L-001",
  "customer": "C100",
  "reference": "AP-12345",
  "pickup": {"facility": "F100", "window_start": "2026-11-17T08:00:00-07:00", "window_end": "2026-11-17T09:00:00-07:00"},
  "delivery": {"facility": "F102", "window_start": "2026-11-18T08:00:00-06:00", "window_end": "2026-11-18T16:00:00-06:00"},
  "equipment": "dry_van",
  "weight_lbs": 30000,
  "rate_usd": 2240,
  "commodity": "copy paper"
}
```

Required: `message_id`, `customer`, `reference`, `pickup`, `delivery`, `equipment` (`dry_van` or `reefer`), `weight_lbs` (whole number), `rate_usd`. A reefer load also requires `temperature_f`. Optional: `commodity`, `notes` (up to 500 characters); `check.py` doesn't compare them. Times need a UTC offset; a window can't end before it starts, and delivery can't start before pickup. Both facilities must belong to the customer.

The server checks the shape of a load. It does not apply Ridgeline's booking rules.

### Change or cancel a load

`PATCH /v1/loads/{id}` returns 200 and the updated load.

```json
{"message_id": "L-014", "delivery": {"facility": "F142", "window_start": "2026-11-14T10:00:00-06:00", "window_end": "2026-11-14T11:00:00-06:00"}}
```

Send `message_id` and only the fields that change: `pickup`, `delivery`, `equipment`, `temperature_f`, `weight_lbs`, `rate_usd`, `commodity`, `notes`. To cancel, send `{"message_id": "...", "status": "cancelled"}` on its own. A cancelled load can't be changed.

Every write adds an entry to the load's `history`, with the load as it stood after that write:

```json
"history": [
  {"message_id": "L-013", "action": "created", "at": "2026-11-11T07:52:18+00:00", "load": {"id": "RF-70004", "status": "tendered", "weight_lbs": 20000, "...": "..."}},
  {"message_id": "L-022", "action": "updated", "at": "2026-11-11T07:52:19+00:00", "changes": {"pickup": {"...": "..."}}, "load": {"...": "..."}}
]
```

The load's top-level `message_id` is the email that created it; `GET /v1/loads?message_id=` also finds loads a later email changed or cancelled.

### Send to review

`POST /v1/reviews` returns 201 and the review.

```json
{"message_id": "L-007", "reason": "rate_mismatch", "summary": "Offered $1,900; contract is $2,150 for F100 to F102.", "reference": "AP-55555", "customer": "C100"}
```

Required: `message_id`, `reason`, `summary` (up to 1,000 characters). Optional: `reference`, `customer`. In an email with several loads, the `reference` says which load the review is for; a review with no reference is about the whole email, so a rep handles every load in it. Reasons: `unknown_customer`, `customer_not_active`, `new_facility`, `no_contract_lane`, `rate_mismatch`, `unsupported_equipment`, `missing_info`, `overweight`, `short_notice`, `unclear`.

## Errors

Errors return `{"error":{"code":"...","message":"..."}}`.

| Status | Meaning |
|---|---|
| 400 | Invalid JSON object or body larger than 64 KiB |
| 404 | Unknown route or id |
| 409 | Facility belongs to another customer, cancelled load, or a reused key with different data |
| 422 | Missing or invalid field, time, equipment, reason or idempotency key |
