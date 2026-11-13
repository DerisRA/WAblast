"""Exercise the batch worker without opening Tk, WhatsApp, or user data."""

import queue
import re
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.callbacks = []

    def later(self, delay, callback):
        self.callbacks.append((self.now + delay, callback))

    def advance(self, seconds):
        end = self.now + seconds
        while self.callbacks and min(item[0] for item in self.callbacks) <= end:
            due = min(self.callbacks, key=lambda item: item[0])
            self.callbacks.remove(due)
            self.now = due[0]
            due[1]()
        self.now = end


class FakeEvent:
    def __init__(self, clock, initial=False):
        self.clock = clock
        self.value = initial

    def is_set(self):
        return self.value

    def set(self):
        self.value = True

    def clear(self):
        self.value = False

    def wait(self, timeout=None):
        if not self.value:
            self.clock.advance(timeout or 0)
        return self.value


class LocalClient:
    pass


class FakeClient:
    def __init__(self, clock):
        self.clock = clock
        self.sends = []
        self.checks = []
        self.session = lambda: 'WORKING'
        self.exists = lambda phone: {'numberExists': True, 'chatId': f'{phone}@c.us'}
        self.send_error = None

    def get_session(self):
        return {'status': self.session()}

    def check_exists(self, phone):
        self.checks.append((phone, self.clock.now))
        return self.exists(phone)

    def send(self, target, text, image, chat_id=None, image_mime=None, check_first=False):
        self.sends.append((target.phone, text, self.clock.now, chat_id))
        if self.send_error:
            raise self.send_error


class FakeLocalClient(FakeClient, LocalClient):
    pass


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.preparation_delay = 0
        self.choice = Mock(side_effect=lambda items: items[0])
        self.randint = Mock(return_value=0)
        clock = self.clock
        suite = self

        class FakeThread:
            def __init__(self, target, daemon=False):
                self.target = target

            def start(self):
                if suite.preparation_delay:
                    clock.later(suite.preparation_delay, self.target)
                else:
                    self.target()

            def join(self, timeout=None):
                clock.advance(timeout or 0)

        namespace = {
            'threading': types.SimpleNamespace(Thread=FakeThread, Event=threading.Event),
            'time': types.SimpleNamespace(monotonic=lambda: clock.now),
            'random': types.SimpleNamespace(choice=self.choice, randint=self.randint),
            're': re,
            'validate_image': lambda path: 'image/png',
            'DirectWhatsApp': LocalClient,
            'GatewayWhatsApp': FakeClient,
            'BATCH_REST_INTERVAL': 10,
            'BATCH_REST_DELAY': 300,
            'DELAY': 60,
            'DELAY_JITTER_MIN': 0,
            'DELAY_JITTER_MAX': 120,
        }
        source = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
        method = '    def send_batch(' + source.split('    def send_batch(', 1)[1].split('\n    def write_history(', 1)[0]
        exec(compile('class Worker:\n' + method, 'app.py:send_batch', 'exec'), namespace)
        self.worker = namespace['Worker']()
        self.worker.cancel = FakeEvent(clock)
        self.worker.resume = FakeEvent(clock, True)
        self.worker.events = queue.Queue()
        self.worker.run_delay = 10
        self.worker.render_text = lambda text, target: text.replace('{{nomor}}', target.phone)
        self.worker.write_history = Mock()
        self.client = FakeLocalClient(clock)
        self.templates = [{'name': 'A', 'text': 'A {{nomor}}', 'image': ''}]

    def run_batch(self, count=2, random_mode=False):
        targets = [types.SimpleNamespace(phone=f'62{i}', name='') for i in range(count)]
        self.worker.send_batch(self.client, targets, self.templates, random_mode)
        return self.worker.write_history.call_args.args[2]

    def test_single_send_and_number_check_per_recipient_with_manual_delay(self):
        outcomes = self.run_batch(3)
        self.assertEqual(outcomes, ['Dikirim'] * 3)
        self.assertEqual([send[0] for send in self.client.sends], ['620', '621', '622'])
        self.assertEqual([round(send[2], 4) for send in self.client.sends], [0, 10, 20])
        self.assertEqual(len(self.client.checks), 3)
        self.assertEqual(self.client.checks[1][1], 0)
        self.randint.assert_not_called()

    def test_random_template_is_chosen_once_and_matches_history(self):
        self.templates.append({'name': 'B', 'text': 'B {{nomor}}', 'image': ''})
        self.choice.side_effect = [self.templates[0], self.templates[1]]
        self.run_batch(random_mode=True)
        self.assertEqual(self.choice.call_count, 2)
        self.assertEqual([send[1] for send in self.client.sends], ['A 620', 'B 621'])
        self.assertEqual(self.worker.write_history.call_args.args[3], ['A', 'B'])

    def test_default_random_delay_preserves_both_endpoints(self):
        self.worker.run_delay = None
        self.randint.side_effect = [0, 120]
        self.run_batch(3)
        self.assertEqual([round(send[2], 4) for send in self.client.sends], [0, 60, 240])
        self.assertEqual(self.randint.call_count, 2)

    def test_batch_rest_uses_larger_delay_and_has_no_trailing_rest(self):
        for delay, final_time in [(None, 840), (10, 390), (600, 6000)]:
            with self.subTest(delay=delay):
                self.setUp()
                self.worker.run_delay = delay
                self.run_batch(11)
                self.assertAlmostEqual(self.clock.now, final_time, places=4)
                self.assertEqual(len(self.client.sends), 11)

    def test_pause_suspends_countdown_and_stop_cancels_remaining(self):
        self.clock.later(2, self.worker.resume.clear)
        self.clock.later(7, self.worker.resume.set)
        self.run_batch()
        self.assertGreaterEqual(self.client.sends[1][2], 15 - 0.00001)
        self.assertLess(self.client.sends[1][2], 15.25)
        self.setUp()
        self.clock.later(2, self.worker.cancel.set)
        self.assertEqual(self.run_batch(3), ['Dikirim', 'Dibatalkan', 'Dibatalkan'])
        self.assertEqual(len(self.client.sends), 1)
        self.assertLess(self.clock.now, 2.2)

    def test_cancellation_while_waiting_for_preparation_returns_promptly(self):
        self.preparation_delay = 20
        self.clock.later(1, self.worker.cancel.set)
        self.assertEqual(self.run_batch(), ['Dibatalkan', 'Dibatalkan'])
        self.assertFalse(self.client.sends)
        self.assertLess(self.clock.now, 1.2)
        self.clock.advance(30)
        self.assertFalse(self.client.sends)

    def test_stop_works_while_paused_and_while_session_is_starting(self):
        self.clock.later(0.5, self.worker.resume.clear)
        self.clock.later(1, self.worker.cancel.set)
        self.assertEqual(self.run_batch(), ['Dikirim', 'Dibatalkan'])
        self.assertLess(self.clock.now, 1.2)
        self.setUp()
        self.client.session = lambda: 'STARTING'
        self.clock.later(0.5, self.worker.cancel.set)
        self.assertEqual(self.run_batch(), ['Dibatalkan', 'Dibatalkan'])
        self.assertFalse(self.client.sends)
        self.assertLess(self.clock.now, 1.2)

    def test_pause_prevents_send_even_after_preparation_completes(self):
        self.preparation_delay = 2
        self.clock.later(1, self.worker.resume.clear)
        self.clock.later(5, self.worker.resume.set)
        self.run_batch(1)
        self.assertGreaterEqual(self.client.sends[0][2], 5)

    def test_starting_session_waits_until_ready_and_times_out_at_five_minutes(self):
        self.client.session = lambda: 'STARTING' if self.clock.now < 2 else 'WORKING'
        self.run_batch(1)
        self.assertGreaterEqual(self.client.sends[0][2], 2)
        self.setUp()
        self.client.session = lambda: 'STARTING'
        self.assertEqual(self.run_batch(), ['Dibatalkan', 'Dibatalkan'])
        self.assertFalse(self.client.sends)
        self.assertAlmostEqual(self.clock.now, 300, places=4)

    def test_disconnect_prevents_later_sends(self):
        self.client.session = lambda: 'STOPPED' if self.client.sends else 'WORKING'
        self.assertEqual(self.run_batch(3), ['Dikirim', 'Dibatalkan', 'Dibatalkan'])
        self.assertEqual(len(self.client.sends), 1)

    def test_gateway_number_check_failure_is_not_a_send_fallback(self):
        self.client = FakeClient(self.clock)
        self.client.exists = Mock(side_effect=RuntimeError('Number check failed'))
        self.assertEqual(self.run_batch(1), ['Gagal'])
        self.assertFalse(self.client.sends)

    def test_direct_client_retains_existing_number_check_fallback(self):
        self.client.exists = Mock(side_effect=RuntimeError('Local lookup timed out'))
        self.assertEqual(self.run_batch(1), ['Dikirim'])
        self.assertEqual(self.client.sends[0][3], '620@c.us')

    def test_unregistered_number_is_skipped_and_ambiguous_send_is_not_retried(self):
        self.client.exists = lambda phone: {'numberExists': False}
        self.assertEqual(self.run_batch(1), ['Dilewati'])
        self.assertFalse(self.client.sends)
        self.setUp()
        self.client.send_error = RuntimeError('Connection timed out')
        self.assertEqual(self.run_batch(1), ['Gagal'])
        self.assertEqual(len(self.client.sends), 1)


if __name__ == '__main__':
    unittest.main()
