"""HTTP client for the Ridgeline TMS (see API.md)."""
import os
import time

import requests

DEFAULT_URL = 'http://127.0.0.1:8000'


class TMSError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f'TMS {status} {code}: {message}')
        self.status, self.code, self.message = status, code, message


class TMS:
    def __init__(self, base_url=None, timeout=10):
        self.base_url = (base_url or os.environ.get('TMS_URL') or DEFAULT_URL).rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()

    def get(self, path, **params):
        """GET a path; filters left as None are left out. Returns the decoded JSON."""
        return self._send('GET', path, params=params)

    def write(self, method, path, body, key):
        """POST or PATCH with an Idempotency-Key. Retrying with the same key is safe: the TMS returns
        the original record, so network failures and server errors are retried."""
        for attempt in range(3):
            try:
                return self._send(method, path, json=body, headers={'Idempotency-Key': key})['item']
            except TMSError as e:
                if e.status not in (0, 500, 502, 503) or attempt == 2:
                    raise
                time.sleep(1 + attempt)

    def _send(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base_url + path, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise TMSError(0, 'unreachable', f'{self.base_url}: {e}')
        if not response.ok:
            try:
                error = response.json().get('error', {})
            except ValueError:
                error = {}
            raise TMSError(response.status_code, error.get('code', 'http_error'), error.get('message', response.reason))
        return response.json()

    def customer_by_domain(self, domain):
        """The customer with that email domain, or None."""
        items = self.get('/v1/customers', domain=domain.lower())['items']
        return items[0] if items else None

    def facilities(self, customer_id):
        """The customer's facilities on file."""
        return self.get('/v1/facilities', customer=customer_id)['items']

    def loads(self, **filters):
        """Booked loads; filters: customer, reference, status, message_id (any email that created or changed it)."""
        return self.get('/v1/loads', **filters)['items']

    def reviews(self, **filters):
        """The review queue; filter: message_id."""
        return self.get('/v1/reviews', **filters)['items']

    def rates(self, customer_id, pickup_facility, delivery_facility, equipment):
        """Contract lanes for that customer, lane and trailer (any validity dates)."""
        return self.get('/v1/rates', customer=customer_id, pickup_facility=pickup_facility,
                        delivery_facility=delivery_facility, equipment=equipment)['items']
