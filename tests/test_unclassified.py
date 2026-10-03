import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import io
import json

spec = importlib.util.spec_from_file_location(
    'unclassified', Path(__file__).resolve().parents[1] / 'immich-unclassified.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def asset(asset_id, visibility='timeline', owner='me', kind='IMAGE', trashed=False):
    return dict(id=asset_id, visibility=visibility, ownerId=owner,
                type=kind, isTrashed=trashed)


class FakeClient(module.Client):
    def __init__(self, target=True):
        self.assets = [asset('orphan'), asset('video', kind='VIDEO'),
                       asset('classified'), asset('already'), asset('archived', 'archive'),
                       asset('locked', 'locked'), asset('hidden', 'hidden'),
                       asset('partner', owner='partner'), asset('trash', trashed=True)]
        self.albums = [dict(id='other', albumName='旅行', shared=False)]
        if target:
            self.albums.append(dict(id='target', albumName='未分類', shared=False))
        self.members = {'target': {'already', 'classified', 'archived', 'hidden'},
                        'other': {'classified'}}
        self.writes = []
        self.fail_album = False

    def request(self, method, path, body=None):
        if path == '/users/me':
            return {'id': 'me'}
        if method == 'GET' and path.startswith('/albums'):
            return self.albums
        if path == '/search/metadata':
            if 'albumIds' in body:
                if self.fail_album:
                    raise RuntimeError('検索失敗')
                ids = self.members[body['albumIds'][0]]
                items = [a for a in self.assets if a['id'] in ids]
            else:
                # Include incorrect visibility/ownership in server results to verify
                # the client independently excludes locked and partner assets.
                items = self.assets
            page = body['page']
            start = (page - 1) * 2
            return {'assets': {'items': items[start:start + 2],
                               'nextPage': str(page + 1) if start + 2 < len(items) else None}}
        self.writes.append((method, path, body))
        if method == 'POST':
            self.albums.append(dict(id='target', albumName=body['albumName'], shared=False))
            self.members['target'] = set()
            return self.albums[-1]
        if method == 'PUT':
            self.members['target'].update(body['ids'])
        else:
            self.members['target'].difference_update(body['ids'])
        return [{'id': i, 'success': True} for i in body['ids']]


class SyncTests(unittest.TestCase):
    def test_sync_pagination_privacy_and_removal(self):
        client = FakeClient()
        module.sync(client, '未分類')
        self.assertEqual(client.members['target'], {'orphan', 'video', 'already'})
        self.assertEqual(client.members['other'], {'classified'})

    def test_archived_opt_in(self):
        client = FakeClient()
        module.sync(client, '未分類', include_archived=True)
        self.assertEqual(client.members['target'], {'orphan', 'video', 'already', 'archived'})

    def test_idempotence(self):
        client = FakeClient()
        module.sync(client, '未分類')
        client.writes.clear()
        module.sync(client, '未分類')
        self.assertEqual(client.writes, [])

    def test_create_missing_album(self):
        client = FakeClient(target=False)
        module.sync(client, '未分類')
        self.assertEqual(client.members['target'], {'orphan', 'video', 'already'})
        self.assertEqual(client.writes[0][0], 'POST')

    def test_dry_run_never_creates_or_updates(self):
        for target in [False, True]:
            client = FakeClient(target)
            module.sync(client, '未分類', dry_run=True)
            self.assertEqual(client.writes, [])

    def test_read_failure_prevents_writes(self):
        client = FakeClient()
        client.fail_album = True
        with self.assertRaises(RuntimeError):
            module.sync(client, '未分類')
        self.assertEqual(client.writes, [])

    def test_ambiguous_album_prevents_writes(self):
        client = FakeClient()
        client.albums.append(dict(id='duplicate', albumName='未分類', shared=False))
        with self.assertRaises(RuntimeError):
            module.sync(client, '未分類')
        self.assertEqual(client.writes, [])

    def test_shared_album_prevents_writes(self):
        for field in ['shared', 'hasSharedLink']:
            client = FakeClient()
            client.albums[-1][field] = True
            with self.assertRaises(RuntimeError):
                module.sync(client, '未分類')
            self.assertEqual(client.writes, [])

    def test_batching(self):
        client = FakeClient()
        ids = {str(i) for i in range(1001)}
        client.change('PUT', 'target', ids)
        self.assertEqual([len(w[2]['ids']) for w in client.writes], [500, 500, 1])
        self.assertTrue(ids <= client.members['target'])

    def test_partial_failure_is_reported(self):
        client = module.Client('http://localhost/api', 'test')
        client.request = lambda *args: [{'id': 'x', 'success': False}]
        with self.assertRaises(RuntimeError):
            client.change('PUT', 'target', {'x'})

    def test_non_advancing_page_is_rejected(self):
        client = module.Client('http://localhost/api', 'test')
        client.request = lambda *args: {'assets': {'items': [], 'nextPage': '1'}}
        with self.assertRaises(RuntimeError):
            client.search()

    def test_http_transport_and_minimal_metadata(self):
        client = module.Client('http://localhost:2283/api', 'secret')
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return io.BytesIO(json.dumps({'assets': {
                    'items': [dict(asset('x'), originalFileName='unused',
                                   thumbhash='unused')], 'nextPage': None}}).encode())
        client.opener = Opener()
        self.assertEqual(client.search(visibility='timeline'), {'x': asset('x')})
        request = client.opener.request
        self.assertEqual(request.full_url, 'http://localhost:2283/api/search/metadata')
        self.assertEqual(request.get_header('X-api-key'), 'secret')
        payload = json.loads(request.data)
        self.assertFalse(payload['withExif'])
        self.assertFalse(payload['withPeople'])
        self.assertTrue(payload['withStacked'])

    def test_daily_worker_retries_after_failure(self):
        with patch.object(module.sys, 'argv', ['worker', '--interval', '86400']), \
                patch.dict(module.os.environ, {'IMMICH_API_KEY': 'test',
                           'IMMICH_INSTANCE_URL': 'http://localhost:2283/api',
                           'IMMICH_UNCLASSIFIED_INCLUDE_ARCHIVED': 'false'}), \
                patch.object(module, 'load_env'), \
                patch.object(module, 'sync', side_effect=[RuntimeError('offline'), None]) as sync, \
                patch.object(module.time, 'sleep', side_effect=[None, KeyboardInterrupt]) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                module.main()
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(sleep.call_args_list[0].args, (86400,))


if __name__ == '__main__':
    unittest.main()
