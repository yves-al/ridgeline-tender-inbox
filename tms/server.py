#!/usr/bin/env python3
"""Local Ridgeline TMS for the tender inbox build. State resets on restart; no dependencies."""
import argparse
import copy
import json
import math
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

EQUIPMENT = ('dry_van', 'reefer')
REASONS = ('unknown_customer', 'customer_not_active', 'new_facility', 'no_contract_lane', 'rate_mismatch',
           'unsupported_equipment', 'missing_info', 'overweight', 'short_notice', 'unclear')
LOAD_FIELDS = ('pickup', 'delivery', 'equipment', 'temperature_f', 'weight_lbs', 'rate_usd', 'commodity', 'notes')


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def text(body, field, required=True, limit=1000):
    value = body.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise APIError(422, 'invalid_field', f'{field} must be a nonempty string up to {limit} characters')
    return value.strip()


def number(body, field, integer=False, required=True):
    value = body.get(field)
    if value is None and not required:
        return None
    kinds = (int,) if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds) or not math.isfinite(value) or (value <= 0 and field != 'temperature_f'):
        raise APIError(422, 'invalid_field', f'{field} must be a positive whole number' if integer else f'{field} must be a number')
    return value


def moment(value, field):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise APIError(422, 'invalid_time', f'{field} must be an ISO 8601 timestamp with a UTC offset')


def snapshot(load):
    return {k: copy.deepcopy(v) for k, v in load.items() if k not in ('history', 'message_id')}


def make_server(port=8000):
    seed = json.loads(Path(__file__).with_name('seed.json').read_text())
    state = {'loads': [], 'reviews': []}
    saved = {}

    def find(collection, ident):
        items = seed[collection] if collection in seed else state[collection]
        for item in items:
            if item['id'] == ident:
                return item
        raise APIError(404, 'not_found', f'{collection}: {ident} not found')

    def stop(body, name, customer):
        value = body.get(name)
        if not isinstance(value, dict):
            raise APIError(422, 'invalid_field', f'{name} must be an object with facility, window_start and window_end')
        facility = find('facilities', text(value, 'facility'))
        if facility['customer'] != customer:
            raise APIError(409, 'facility_mismatch', f'{facility["id"]} does not belong to customer {customer}')
        start, end = moment(value.get('window_start'), f'{name}.window_start'), moment(value.get('window_end'), f'{name}.window_end')
        if end < start:
            raise APIError(422, 'invalid_time', f'{name}.window_end is before window_start')
        return {'facility': facility['id'], 'window_start': value['window_start'], 'window_end': value['window_end']}, start

    def load_fields(body, customer, current=None):
        """Validate the load fields present in body; with current, only the supplied fields (an update)."""
        out = {}
        starts = {}
        for name in ('pickup', 'delivery'):
            if current is None or name in body:
                out[name], starts[name] = stop(body, name, customer)
            else:
                starts[name] = moment(current[name]['window_start'], name)
        if starts['delivery'] < starts['pickup']:
            raise APIError(422, 'invalid_time', 'delivery starts before pickup')
        if current is None or 'equipment' in body:
            if body.get('equipment') not in EQUIPMENT:
                raise APIError(422, 'invalid_equipment', 'equipment must be dry_van or reefer')
            out['equipment'] = body['equipment']
        equipment = out.get('equipment', current and current['equipment'])
        if current is None or 'weight_lbs' in body:
            out['weight_lbs'] = number(body, 'weight_lbs', integer=True)
        if current is None or 'rate_usd' in body:
            out['rate_usd'] = number(body, 'rate_usd')
        if 'temperature_f' in body:
            out['temperature_f'] = number(body, 'temperature_f', required=equipment == 'reefer')
        if equipment == 'reefer' and out.get('temperature_f', current and current.get('temperature_f')) is None:
            raise APIError(422, 'invalid_field', 'temperature_f is required for reefer loads')
        for field in ('commodity', 'notes'):
            if field in body:
                out[field] = text(body, field, required=False, limit=500)
        return out

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, payload):
            raw = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        @staticmethod
        def reject(constant):
            raise ValueError(f'{constant} is not allowed')

        def body(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError()
                body = json.loads(self.rfile.read(length), parse_constant=self.reject)
                if not isinstance(body, dict):
                    raise ValueError()
                return body
            except (ValueError, UnicodeDecodeError):
                raise APIError(400, 'invalid_json', 'Send a JSON object up to 64 KiB')

        def do_GET(self):
            try:
                url = urlsplit(self.path)
                parts = url.path.strip('/').split('/')
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == '/health':
                    return self.reply(200, {'status': 'ok'})
                if parts == ['v1', 'export']:
                    return self.reply(200, {'loads': state['loads'], 'reviews': state['reviews']})
                if parts == ['v1', 'rates']:
                    items = seed['rates']
                    for field in ('customer', 'pickup_facility', 'delivery_facility', 'equipment'):
                        if field in query:
                            items = [r for r in items if r[field] == query[field]]
                    return self.reply(200, {'items': items})
                collections = {'customers': 'customers', 'facilities': 'facilities', 'loads': 'loads', 'reviews': 'reviews'}
                if len(parts) in (2, 3) and parts[0] == 'v1' and parts[1] in collections:
                    collection = collections[parts[1]]
                    if len(parts) == 3:
                        return self.reply(200, {'item': find(collection, parts[2])})
                    items = seed[collection] if collection in seed else state[collection]
                    if 'domain' in query:
                        items = [c for c in items if query['domain'].lower() in c.get('domains', [])]
                    for field in ('customer', 'reference', 'status'):
                        if field in query:
                            items = [i for i in items if i.get(field) == query[field]]
                    if 'message_id' in query:  # a load matches if any message created or changed it
                        items = [i for i in items if i.get('message_id') == query['message_id']
                                 or any(h['message_id'] == query['message_id'] for h in i.get('history', []))]
                    return self.reply(200, {'items': items})
                raise APIError(404, 'not_found', 'Unknown route')
            except APIError as e:
                self.reply(e.status, {'error': {'code': e.code, 'message': e.message}})

        def write(self, method):
            path = urlsplit(self.path).path
            parts = path.strip('/').split('/')
            body = self.body()
            key = self.headers.get('Idempotency-Key', '').strip()
            if not key:
                raise APIError(422, 'missing_key', 'Idempotency-Key is required')
            cache_key = (method, path, key)
            if cache_key in saved:
                old_body, status, item = saved[cache_key]
                if old_body != body:
                    raise APIError(409, 'key_reused', 'Key already used with different data')
                return 200, copy.deepcopy(item)
            now = datetime.now(timezone.utc).isoformat()
            message_id = text(body, 'message_id', limit=100)
            if method == 'POST' and parts == ['v1', 'loads']:
                customer = find('customers', text(body, 'customer'))['id']
                item = {'id': f'RF-{70001 + len(state["loads"])}', 'customer': customer, 'reference': text(body, 'reference', limit=100),
                        **load_fields(body, customer), 'status': 'tendered', 'message_id': message_id,
                        'history': []}
                item['history'].append({'message_id': message_id, 'action': 'created', 'at': now, 'load': snapshot(item)})
                state['loads'].append(item)
                status = 201
            elif method == 'PATCH' and len(parts) == 3 and parts[:2] == ['v1', 'loads']:
                load = find('loads', parts[2])
                if load['status'] == 'cancelled':
                    raise APIError(409, 'load_cancelled', 'Cannot change a cancelled load')
                unknown = set(body) - set(LOAD_FIELDS) - {'message_id', 'status'}
                if unknown:
                    raise APIError(422, 'invalid_field', f'Cannot change: {", ".join(sorted(unknown))}')
                if 'status' in body:
                    if body['status'] != 'cancelled' or len(body) != 2:
                        raise APIError(422, 'invalid_field', 'status can only be set to cancelled, on its own')
                    load['status'] = 'cancelled'
                    load['history'].append({'message_id': message_id, 'action': 'cancelled', 'at': now, 'load': snapshot(load)})
                else:
                    changes = load_fields(body, load['customer'], current=load)
                    if not changes:
                        raise APIError(422, 'invalid_field', 'Nothing to change')
                    load.update(changes)
                    load['history'].append({'message_id': message_id, 'action': 'updated', 'at': now, 'changes': changes, 'load': snapshot(load)})
                item, status = load, 200
            elif method == 'POST' and parts == ['v1', 'reviews']:
                if body.get('reason') not in REASONS:
                    raise APIError(422, 'invalid_reason', 'reason must be one of: ' + ', '.join(REASONS))
                item = {'id': f'REV-{len(state["reviews"]) + 1:04d}', 'message_id': message_id, 'reason': body['reason'],
                        'summary': text(body, 'summary'), 'reference': text(body, 'reference', required=False, limit=100),
                        'customer': text(body, 'customer', required=False, limit=20), 'status': 'open', 'created_at': now}
                state['reviews'].append(item)
                status = 201
            else:
                raise APIError(404, 'not_found', 'Unknown route')
            saved[cache_key] = (body, status, copy.deepcopy(item))
            return status, item

        def handle_write(self, method):
            try:
                status, item = self.write(method)
                self.reply(status, {'item': item})
            except APIError as e:
                self.reply(e.status, {'error': {'code': e.code, 'message': e.message}})

        def do_POST(self):
            self.handle_write('POST')

        def do_PATCH(self):
            self.handle_write('PATCH')

    return HTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f'Ridgeline TMS: http://127.0.0.1:{server.server_port} (restart resets state)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
