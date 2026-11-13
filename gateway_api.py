"""Declarative JSON gateway adapter; credentials are kept in memory only."""

import base64
import copy
import ipaddress
import json
import re
from pathlib import Path
from urllib import error, parse, request


class GatewayError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GatewayError('Gateway redirect refused. Use the final server address.', code)


WAHA_PROFILE = {
    'name': 'WAHA',
    'auth': {'header': 'X-Api-Key', 'prefix': ''},
    'endpoints': {
        'status': {'method': 'GET', 'path': '/api/sessions/{session}',
                   'response': {'status': 'status', 'account': 'me'}},
        'connect': {'method': 'POST', 'path': '/api/sessions/{session}/start'},
        'create': {'method': 'POST', 'path': '/api/sessions',
                   'body': {'name': '{session}', 'start': True}},
        'qr': {'method': 'GET', 'path': '/api/{session}/auth/qr',
               'response': {'data': 'data'}},
        'check_exists': {'method': 'GET', 'path': '/api/contacts/check-exists',
                         'query': {'session': '{session}', 'phone': '{phone}'},
                         'response': {'exists': 'numberExists', 'chat_id': 'chatId'}},
        'send_text': {'method': 'POST', 'path': '/api/sendText',
                      'body': {'session': '{session}', 'chatId': '{chat_id}', 'text': '{text}'},
                      'response': {'message_id': 'id'}},
        'send_image': {'method': 'POST', 'path': '/api/sendImage',
                       'body': {'session': '{session}', 'chatId': '{chat_id}',
                                'caption': '{text}', 'file': {'data': '{image_base64}',
                                'mimetype': '{image_mime}', 'filename': '{image_name}'}},
                       'response': {'message_id': 'id'}},
        'logout': {'method': 'POST', 'path': '/api/sessions/{session}/logout'},
    },
    'status_map': {'WORKING': 'WORKING', 'STARTING': 'STARTING',
                   'SCAN_QR_CODE': 'SCAN_QR_CODE', 'STOPPED': 'STOPPED', 'FAILED': 'FAILED'},
}

CUSTOM_PROFILE_EXAMPLE = {
    'name': 'Custom gateway',
    'auth': {'header': 'Authorization', 'prefix': 'Bearer '},
    'endpoints': {
        'status': {'method': 'GET', 'path': '/status',
                   'response': {'status': 'status', 'account': 'account'}},
        'check_exists': {'method': 'GET', 'path': '/contacts/check',
                         'query': {'phone': '{phone}'},
                         'response': {'exists': 'exists', 'chat_id': 'chatId'}},
        'send_text': {'method': 'POST', 'path': '/messages',
                      'body': {'phone': '{phone}', 'text': '{text}'},
                      'response': {'success': 'success'}},
    },
    'status_map': {'connected': 'WORKING', 'disconnected': 'STOPPED'},
}

_VARIABLES = {'session', 'phone', 'chat_id', 'text', 'image_base64', 'image_mime', 'image_name'}
_TOKEN = re.compile(r'\{([A-Za-z_][A-Za-z_0-9]*)\}')
_HEADER = re.compile(r'^[A-Za-z0-9!#$%&\x27*+.^_`|~-]+$')
_STATES = {'WORKING', 'STARTING', 'SCAN_QR', 'SCAN_QR_CODE', 'STOPPED', 'FAILED'}


def validate_base_url(value):
    """Require TLS except for explicit localhost/literal loopback addresses."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise GatewayError('Enter a valid gateway base URL.')
    try:
        url = parse.urlsplit(value)
        url.port
        host = url.hostname or ''
        loopback = host.lower() == 'localhost'
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if (url.scheme not in {'http', 'https'} or not host or url.username is not None
                or url.password is not None or url.query or url.fragment
                or '\\' in value or (url.scheme == 'http' and not loopback)):
            raise ValueError
    except ValueError:
        raise GatewayError('Use HTTPS for remote gateways; HTTP is allowed only on loopback. No credentials or query in the base URL.') from None
    return value.rstrip('/')


def _lookup(data, path):
    if path == '':
        return data
    try:
        for part in path.split('.'):
            data = data[int(part)] if isinstance(data, list) else data[part]
        return data
    except (KeyError, IndexError, TypeError, ValueError):
        raise GatewayError('Gateway response does not match the configured field mapping.') from None


def _validate_template(value, depth=0):
    if depth > 12:
        raise GatewayError('Gateway template nesting is too deep.')
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise GatewayError('Gateway template keys must be text.')
            if re.search(r'token|password|secret|api.?key|authorization', key, re.I):
                raise GatewayError('Keep credentials in the separate API key field, not the profile.')
            _validate_template(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_template(item, depth + 1)
    elif isinstance(value, str):
        if set(_TOKEN.findall(value)) - _VARIABLES:
            raise GatewayError('Unknown gateway template variable.')
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise GatewayError('Gateway configuration must contain JSON values only.')


def validate_profile(profile):
    """Validate and copy a credential-free profile before persisting or using it."""
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except (ValueError, TypeError):
            raise GatewayError('Gateway profile must be valid JSON.') from None
    if not isinstance(profile, dict) or set(profile) - {'name', 'auth', 'headers', 'endpoints', 'status_map'}:
        raise GatewayError('Invalid gateway profile fields.')
    profile = copy.deepcopy(profile)
    if not isinstance(profile.get('name'), str) or not profile['name'].strip():
        raise GatewayError('Gateway profile needs a name.')
    auth = profile.get('auth', {})
    if (not isinstance(auth, dict) or set(auth) - {'header', 'prefix'}
            or not isinstance(auth.get('header', 'Authorization'), str)
            or not _HEADER.fullmatch(auth.get('header', 'Authorization'))
            or not isinstance(auth.get('prefix', ''), str)
            or auth.get('prefix', '') not in {'', 'Bearer ', 'Token ', 'Basic '}):
        raise GatewayError('Invalid gateway authentication header.')
    headers = profile.get('headers', {})
    if not isinstance(headers, dict):
        raise GatewayError('Gateway headers must be an object.')
    for key, value in headers.items():
        if (not isinstance(key, str) or not _HEADER.fullmatch(key) or not isinstance(value, str)
                or any(c in value for c in '\r\n')
                or key.lower() in {'host', 'cookie', 'content-length', 'authorization', 'x-api-key'}
                or re.search(r'token|secret|password|api.?key', key, re.I)
                or key.lower() == auth.get('header', 'Authorization').lower()):
            raise GatewayError('Invalid or sensitive gateway header; use the separate API key field.')
    endpoints = profile.get('endpoints')
    if not isinstance(endpoints, dict) or not {'status', 'send_text'} <= set(endpoints):
        raise GatewayError('Gateway profile requires status and send_text endpoints.')
    if set(endpoints) - {'status', 'send_text', 'send_image', 'check_exists', 'connect', 'qr', 'logout', 'create'}:
        raise GatewayError('Unknown gateway endpoint operation.')
    for operation, endpoint in endpoints.items():
        if (not isinstance(endpoint, dict) or set(endpoint) - {'method', 'path', 'query', 'body', 'response'}
                or endpoint.get('method') not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}):
            raise GatewayError('Invalid gateway endpoint configuration.')
        path = endpoint.get('path', '')
        if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
                or any(c in path for c in ('?', '#', '\\', '\r', '\n'))
                or set(_TOKEN.findall(path)) - {'session', 'phone', 'chat_id'}):
            raise GatewayError('Endpoint path must be relative to the gateway server.')
        _validate_template(endpoint.get('body'))
        query = endpoint.get('query', {})
        if not isinstance(query, dict):
            raise GatewayError('Gateway query must be an object.')
        _validate_template(query)
        mapping = endpoint.get('response', {})
        if not isinstance(mapping, dict) or any(not isinstance(p, str) for p in mapping.values()):
            raise GatewayError('Gateway response paths must be text.')
        if operation in {'send_text', 'send_image'} and not ({'success', 'message_id'} & set(mapping)):
            raise GatewayError('Send endpoints require a success or message_id response mapping.')
        if operation == 'check_exists' and 'exists' not in mapping:
            raise GatewayError('Number-check endpoint requires an exists response mapping.')
    if 'status' not in endpoints['status'].get('response', {}):
        raise GatewayError('Status endpoint requires a status response mapping.')
    states = profile.get('status_map', {})
    if not isinstance(states, dict) or any(not isinstance(v, str) or v not in _STATES for v in states.values()):
        raise GatewayError('Invalid status mapping; use WORKING, STARTING, SCAN_QR_CODE, STOPPED, or FAILED.')
    return profile


def profile_public_config(profile):
    return validate_profile(profile)


class GatewayWhatsApp:
    def __init__(self, events, base_url, key='', session='default', profile=None):
        self.events = events
        self.base_url = validate_base_url(base_url)
        if not isinstance(key, str) or any(c in key for c in '\r\n'):
            raise GatewayError('Invalid API key.')
        if not isinstance(session, str) or not session.strip():
            raise GatewayError('Enter a gateway session name.')
        self.key = key
        self.session = session.strip()
        self.is_waha = profile is None
        self.profile = validate_profile(WAHA_PROFILE if profile is None else profile)
        self.opener = request.build_opener(_NoRedirect())
        self.closed = False

    def preflight(self, images=False):
        endpoints = self.profile['endpoints']
        if 'check_exists' not in endpoints:
            raise GatewayError('This gateway has no number-check endpoint. Configure it before sending.')
        if images and 'send_image' not in endpoints:
            raise GatewayError('This gateway profile does not support images. Configure send_image before sending.')
        return True

    def validate_run(self, has_images=False):
        return self.preflight(has_images)

    def _values(self, **extra):
        return {'session': self.session, 'phone': '', 'chat_id': '', 'text': '',
                'image_base64': '', 'image_mime': '', 'image_name': '', **extra}

    def _render(self, value, values, path=False):
        if isinstance(value, dict):
            return {k: self._render(v, values) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render(v, values) for v in value]
        if isinstance(value, str):
            return _TOKEN.sub(lambda m: parse.quote(str(values[m[1]]), safe='') if path else str(values[m[1]]), value)
        return value

    def _call(self, operation, values=None, timeout=20):
        if self.closed:
            raise GatewayError('Gateway connection is closed. Connect again.')
        endpoint = self.profile['endpoints'].get(operation)
        if endpoint is None:
            raise GatewayError('This gateway operation is not configured.')
        values = values or self._values()
        path = self._render(endpoint['path'], values, path=True)
        query = self._render(endpoint.get('query', {}), values)
        url = self.base_url + path
        if query:
            url += '?' + parse.urlencode(query)
        headers = {'Accept': 'application/json', **self.profile.get('headers', {})}
        if self.key:
            auth = self.profile.get('auth', {})
            headers[auth.get('header', 'Authorization')] = auth.get('prefix', '') + self.key
        body = self._render(endpoint.get('body'), values)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = request.Request(url, data=data, headers=headers, method=endpoint['method'])
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read(24 * 1024 * 1024 + 1)
                if len(raw) > 24 * 1024 * 1024:
                    raise GatewayError('Gateway response is too large.')
                if not raw:
                    result = {}
                else:
                    result = json.loads(raw.decode('utf-8'))
        except error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {401, 403}:
                raise GatewayError('Gateway authentication failed. Check your API key and permissions.', code) from None
            raise GatewayError(f'Gateway request failed (HTTP {code}). Check the server and endpoint configuration.', code) from None
        except (error.URLError, TimeoutError, OSError):
            raise GatewayError('Gateway is unreachable or the request timed out. Delivery may be uncertain; check the provider before retrying.') from None
        except (UnicodeError, ValueError):
            raise GatewayError('Gateway returned invalid JSON. Check the endpoint configuration.') from None
        mapping = endpoint.get('response', {})
        return {key: _lookup(result, field) for key, field in mapping.items()} if mapping else result

    def connect(self):
        if self.is_waha:
            try:
                info = self.get_session()
            except GatewayError as exc:
                if exc.status != 404:
                    raise
                self._call('create')
                info = self.get_session()
            if info['status'] == 'STOPPED':
                self._call('connect')
        elif 'connect' in self.profile['endpoints']:
            self._call('connect')
        info = self.get_session()
        if self.events is not None:
            self.events.put(('status', (info['status'], None, info.get('account'))))
        return info

    def get_session(self):
        result = self._call('status')
        state = str(result.get('status', 'UNKNOWN'))
        state = self.profile.get('status_map', {}).get(state, state)
        if state not in _STATES:
            state = 'FAILED'
        account = result.get('account')
        if not isinstance(account, dict):
            account = None
        return {'status': state, 'account': account}

    def qr(self):
        result = self._call('qr')
        try:
            data = result['data']
            if data.startswith('data:'):
                data = data.split(',', 1)[1]
            return base64.b64decode(data, validate=True)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise GatewayError('Gateway returned invalid QR data.') from None

    def check_exists(self, phone):
        result = self._call('check_exists', self._values(phone=phone, chat_id=phone + '@c.us'))
        if not isinstance(result.get('exists'), bool):
            raise GatewayError('Number-check response must contain a boolean exists value.')
        return {'numberExists': result['exists'], 'chatId': result.get('chat_id') or phone + '@c.us'}

    def send(self, target, text, image, chat_id=None, image_mime=None, check_first=False):
        self.preflight(bool(image))
        phone = target.phone
        if check_first:
            result = self.check_exists(phone)
            if not result['numberExists']:
                raise GatewayError('Number is not registered on WhatsApp.')
            chat_id = result['chatId']
        values = self._values(phone=phone, text=text, chat_id=chat_id or phone + '@c.us')
        operation = 'send_text'
        if image:
            path = Path(image)
            try:
                with path.open('rb') as source:
                    raw = source.read(16 * 1024 * 1024 + 1)
            except OSError:
                raise GatewayError('Cannot read the selected image.') from None
            if len(raw) > 16 * 1024 * 1024:
                raise GatewayError('Image must be at most 16 MB.')
            if image_mime not in {'image/jpeg', 'image/png', 'image/webp'}:
                raise GatewayError('Unsupported image format.')
            values.update(image_base64=base64.b64encode(raw).decode('ascii'),
                          image_mime=image_mime, image_name=path.name)
            operation = 'send_image'
        result = self._call(operation, values, timeout=45)
        if ('success' in result and result['success'] is not True
                or 'message_id' in result and not result['message_id']):
            raise GatewayError('Gateway did not confirm message acceptance. Check delivery before retrying.')
        return result

    def logout(self):
        remote = False
        try:
            if self.is_waha:
                self._call('logout')
                remote = True
            # Custom API keys are disconnected locally, never revoked implicitly.
            return {'status': 'STOPPED', 'wiped': True, 'remoteLogout': remote,
                    'gateway': True}
        finally:
            self.close()

    def wipe_session(self):
        self.close()
        return {'status': 'STOPPED', 'wiped': True, 'remoteLogout': False, 'gateway': True}

    def close(self):
        self.key = ''
        self.closed = True
