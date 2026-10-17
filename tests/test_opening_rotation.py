"""Shuffle cycles and persistence use only generated, disposable libraries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from opening_rotation import choose
from server import AlbumStore


class OpeningRotationTests(unittest.TestCase):
    lines = [{'id': f'custom_{i}', 'text': f'句子 {i}', 'origin': 'custom'} for i in range(12)]

    def test_random_complete_cycles_no_repeat_same_day_and_round_boundary(self):
        rng, state, draws = random.Random(16), None, []
        for number in range(60):
            day = (date(2026, 1, 1) + timedelta(days=number)).isoformat()
            selection, state = choose(self.lines, state, day, rng)
            repeated, unchanged = choose(self.lines, state, day, rng)
            self.assertEqual(repeated, selection)
            self.assertEqual(unchanged, state)
            draws.append(selection['id'])
        expected = {line['id'] for line in self.lines}
        rounds = [draws[start:start + 12] for start in range(0, 60, 12)]
        self.assertTrue(all(set(items) == expected for items in rounds))
        self.assertGreater(len({tuple(items) for items in rounds}), 1)
        self.assertTrue(all(draws[i - 1] != draws[i] for i in range(1, 60)))
        self.assertNotEqual(rounds[0], [line['id'] for line in self.lines])

    def test_add_delete_keeps_today_and_consumed_progress(self):
        rng = random.Random(3)
        today, state = choose(self.lines, None, '2026-10-17', rng)
        addition = {'id': 'custom_new', 'text': '新的句子'}
        expanded = self.lines + [addition]
        same, state = choose(expanded, state, '2026-10-17', rng)
        self.assertEqual(same, today)
        self.assertIn('custom_new', state['remaining'])
        removed = state['remaining'][0]
        reduced = [line for line in expanded if line['id'] != removed]
        same, state = choose(reduced, state, '2026-10-17', rng)
        self.assertEqual(same, today)
        self.assertNotIn(removed, state['remaining'])
        seen = {today['id']}
        for index in range(len(reduced) - 1):
            selection, state = choose(reduced, state, (date(2026, 10, 18) + timedelta(days=index)).isoformat(), rng)
            self.assertNotIn(selection['id'], seen)
            seen.add(selection['id'])
        self.assertEqual(seen, {line['id'] for line in reduced})
        current = state['current']
        reduced = [line for line in reduced if line['id'] != current]
        replacement, state = choose(reduced, state, state['day'], rng)
        self.assertNotEqual(replacement['id'], current)

    def test_empty_single_and_closed_days_do_not_consume_hidden_draws(self):
        selection, state = choose([], None, '2026-01-01')
        self.assertEqual(selection['text'], '')
        first, state = choose(self.lines[:1], state, '2026-01-01')
        second, state = choose(self.lines[:1], state, '2027-12-31')
        self.assertEqual(first['text'], second['text'])
        self.assertEqual(len(state['seen']), 1)
        first, state = choose(self.lines, None, '2026-01-01', random.Random(2))
        second, state = choose(self.lines, state, '2026-12-31')
        self.assertEqual(len(state['seen']), 2)
        self.assertNotEqual(first['id'], second['id'])

    def test_database_reopen_concurrent_reads_export_and_failed_commit(self):
        with tempfile.TemporaryDirectory(prefix='shiguang-opening-only-') as temporary:
            root = Path(temporary)
            store = AlbumStore(root)
            with store.connect() as db:
                store.save_opening_lines(db, self.lines)
            with ThreadPoolExecutor(max_workers=8) as pool:
                selections = list(pool.map(lambda _: store.daily_opening(), range(24)))
            self.assertTrue(all(item == selections[0] for item in selections))
            self.assertEqual(len(store.opening_rotation()['seen']), 1)
            self.assertEqual(AlbumStore(root).daily_opening(), selections[0])
            before = store.opening_rotation()
            real_connect = store.connect

            @contextmanager
            def failed_commit():
                with real_connect() as db:
                    yield db
                    raise sqlite3.OperationalError('injected write failure')

            clock = unittest.mock.Mock(wraps=datetime)
            clock.now.return_value = datetime.now() + timedelta(days=1)
            with patch('server.datetime', clock), patch.object(store, 'connect', failed_commit):
                with self.assertRaises(sqlite3.OperationalError): store.daily_opening()
            self.assertEqual(store.opening_rotation(), before)
            self.assertEqual(store.daily_opening(), selections[0])
            with store.connect() as db:
                db.execute("UPDATE app_settings SET value_json=? WHERE key='opening-rotation'", (json.dumps({'broken': True}),))
            with self.assertRaisesRegex(Exception, '抽取记录'): store.daily_opening()
