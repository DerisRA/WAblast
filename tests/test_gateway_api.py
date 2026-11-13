import base64
import copy
import io
import json
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from gateway_api import (CUSTOM_PROFILE_EXAMPLE, GatewayError, GatewayWhatsApp,
                         _NoRedirect, validate_base_url, validate_profile)


def response(body):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(body).encode('utf-8')
    return result


class GatewayTests(unittest.TestCase):
    def client(self, custom=False):
        client = GatewayWhatsApp(queue.Queue(), 'https://gateway.example', 'private-key',
                                 profile=CUSTOM_PROFILE_EXAMPLE if custom else None)
        client.opener = Mock()
        return client

    def test_only_loopback_may_use_http(self):
        for url in ['http://localhost:3000', 'http://127.0.0.1:3000', 'http://[::1]:3000', 'https://gateway.example/api']:
            self.assertEqual(validate_base_url(url), url)
        for url in ['http://192.168.1.2', 'http://gateway.example', 'https://key:secret@example.com',
                    'https://example.com?key=secret', 'file:///tmp/test', 'https://example.com\\@evil.test']:
            with self.subTest(url=url), self.assertRaises(GatewayError):
                validate_base_url(url)

    def test_redirect_is_never_followed(self):
        with self.assertRaises(GatewayError):
            _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test')

    def test_custom_profile_rejects_embedded_credentials(self):
        for mutation in [
            lambda p: p.update(api_key='secret'),
            lambda p: p.update(headers={'X-Api-Key': 'secret'}),
            lambda p: p['auth'].update(prefix='Bearer secret'),
            lambda p: p['endpoints']['send_text']['body'].update(token='secret'),
            lambda p: p['endpoints']['status'].update(path='https://other.test/status'),
            lambda p: p['endpoints']['status'].update(path='/status?token=secret'),
        ]:
            profile = copy.deepcopy(CUSTOM_PROFILE_EXAMPLE)
            mutation(profile)
            with self.assertRaises(GatewayError):
                validate_profile(profile)

    def test_malformed_profile_is_rejected(self):
        for header in [5, None, 'Bad\r\nHeader']:
            profile = copy.deepcopy(CUSTOM_PROFILE_EXAMPLE)
            profile['auth']['header'] = header
            with self.assertRaises(GatewayError):
                validate_profile(profile)

    def test_number_check_capability_required_before_run(self):
        profile = copy.deepcopy(CUSTOM_PROFILE_EXAMPLE)
        del profile['endpoints']['check_exists']
        client = GatewayWhatsApp(None, 'https://gateway.example', profile=profile)
        with self.assertRaises(GatewayError):
            client.validate_run()

    def test_media_missing_fails_before_network(self):
        client = self.client(custom=True)
        with self.assertRaises(GatewayError):
            client.validate_run(has_images=True)
        client.opener.open.assert_not_called()

    def test_custom_status_maps_provider_state(self):
        client = self.client(custom=True)
        client.opener.open.return_value = response({'status': 'connected', 'account': {'phone': '123'}})
        self.assertEqual(client.get_session(), {'status': 'WORKING', 'account': {'phone': '123'}})

    def test_waha_session_exists_is_not_created_or_restarted(self):
        client = self.client()
        client.opener.open.side_effect = [response({'status': 'WORKING', 'me': None}),
                                          response({'status': 'WORKING', 'me': None})]
        self.assertEqual(client.connect()['status'], 'WORKING')
        self.assertTrue(all(call.args[0].method == 'GET' for call in client.opener.open.call_args_list))

    def test_waha_missing_session_created_once(self):
        client = self.client()
        client.opener.open.side_effect = [HTTPError('url', 404, '', {}, None), response({}),
            response({'status': 'STARTING', 'me': None}), response({'status': 'STARTING', 'me': None})]
        self.assertEqual(client.connect()['status'], 'STARTING')
        create = client.opener.open.call_args_list[1].args[0]
        self.assertEqual(create.method, 'POST')
        self.assertEqual(json.loads(create.data), {'name': 'default', 'start': True})

    def test_number_check_requires_actual_boolean(self):
        client = self.client()
        client.opener.open.return_value = response({'numberExists': 'false', 'chatId': '123@c.us'})
        with self.assertRaises(GatewayError):
            client.check_exists('123')

    def test_qr_decodes_json_base64(self):
        client = self.client()
        client.opener.open.return_value = response({'data': base64.b64encode(b'png fixture').decode()})
        self.assertEqual(client.qr(), b'png fixture')

    def test_custom_text_is_json_escaped_and_key_stays_in_header(self):
        client = self.client(custom=True)
        client.opener.open.return_value = response({'success': True})
        text = '"hello"\n{session} café'
        client.send(SimpleNamespace(phone='123'), text, '')
        req = client.opener.open.call_args.args[0]
        self.assertEqual(json.loads(req.data), {'phone': '123', 'text': text})
        self.assertEqual(req.get_header('Authorization'), 'Bearer private-key')
        self.assertNotIn('private-key', req.full_url)
        self.assertNotIn(b'private-key', req.data)

    def test_send_failure_and_uncertain_result_never_retry(self):
        for result in [response({'success': False}), URLError('key=private-key'),
                       HTTPError('url?private-key', 401, 'private-key', {}, io.BytesIO(b'private-key'))]:
            client = self.client(custom=True)
            client.opener.open.side_effect = result if isinstance(result, Exception) else None
            client.opener.open.return_value = result
            with self.assertRaises(GatewayError) as raised:
                client.send(SimpleNamespace(phone='123'), 'Hello', '')
            self.assertNotIn('private-key', str(raised.exception))
            self.assertEqual(client.opener.open.call_count, 1)

    def test_custom_disconnect_clears_key_without_revocation(self):
        client = self.client(custom=True)
        self.assertFalse(client.logout()['remoteLogout'])
        self.assertEqual(client.key, '')
        client.opener.open.assert_not_called()

    def test_waha_logout_targets_only_selected_session(self):
        client = self.client()
        client.session = 'my session'
        client.opener.open.return_value = response({})
        self.assertTrue(client.logout()['remoteLogout'])
        self.assertTrue(client.opener.open.call_args.args[0].full_url.endswith('/api/sessions/my%20session/logout'))
        self.assertEqual(client.key, '')


if __name__ == '__main__':
    unittest.main()
