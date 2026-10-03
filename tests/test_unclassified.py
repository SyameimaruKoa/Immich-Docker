import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import io
import json
import os
import subprocess
import tempfile
import shutil

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
                patch.object(module, 'sync', side_effect=[RuntimeError('offline'), None]) as sync, \
                patch.object(module.time, 'sleep', side_effect=[None, KeyboardInterrupt]) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                module.main()
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(sleep.call_args_list[0].args, (86400,))

    def test_remote_environment_cannot_change_destination(self):
        with patch.object(module.sys, 'argv', ['worker', '--dry-run']), \
                patch.dict(module.os.environ, {'IMMICH_API_KEY': 'test',
                           'IMMICH_INSTANCE_URL': 'https://remote.invalid/api',
                           'IMMICH_UNCLASSIFIED_INCLUDE_ARCHIVED': 'false'}), \
                patch.object(module, 'Client') as client, \
                patch.object(module, 'sync'):
            self.assertEqual(module.main(), 0)
            client.assert_called_once_with('http://127.0.0.1:2283/api', 'test')


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='immich shell ')
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.script = self.root / 'immich-unclassified.sh'
        shutil.copyfile(Path(__file__).resolve().parents[1] / self.script.name, self.script)
        self.script.chmod(0o755)
        (self.root / '.env').write_text('IMMICH_API_KEY=test\n')
        self.log = self.root / 'docker-args.json'
        docker = self.root / 'docker'
        docker.write_text('#!/usr/bin/env python3\nimport os, sys, json\n'
                          'with open(os.environ["TEST_DOCKER_LOG"], "w") as f:\n'
                          ' json.dump({"cwd": os.getcwd(), "args": sys.argv[1:]}, f)\n'
                          'sys.exit(int(os.environ.get("TEST_DOCKER_EXIT", "0")))\n')
        docker.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
                    'TEST_DOCKER_LOG': str(self.log)}

    def run_script(self, *args):
        return subprocess.run([str(self.script), *args], cwd='/tmp', env=self.env,
                              capture_output=True, text=True)

    def test_local_compose_and_quoted_arguments_from_another_directory(self):
        result = self.run_script('-n', '-A', '未分類 写真', '--include-archived')
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads(self.log.read_text())
        self.assertEqual(call['cwd'], str(self.root))
        self.assertEqual(call['args'], ['compose', '-f',
            str(self.root / 'docker-compose.unclassified.yml'), 'run', '--rm', '-T',
            'immich-unclassified', '--interval', '0', '--dry-run', '--album-name', '未分類 写真',
            '--include-archived'])

    def test_default_runs_once_without_service_interval(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(json.loads(self.log.read_text())['args'][-3:],
                         ['immich-unclassified', '--interval', '0'])

    def test_remote_and_invalid_options_do_not_start_docker(self):
        for args in [('--url', 'https://remote.invalid/api'), ('--interval', '-1'),
                     ('--interval', 'abc'), ('--album-name',)]:
            self.assertNotEqual(self.run_script(*args).returncode, 0)
        self.assertFalse(self.log.exists())

    def test_help_does_not_start_docker(self):
        self.assertEqual(self.run_script('--help').returncode, 0)
        self.assertFalse(self.log.exists())

    def test_docker_exit_status_is_preserved(self):
        self.env['TEST_DOCKER_EXIT'] = '7'
        self.assertEqual(self.run_script().returncode, 7)

    def test_missing_env_does_not_start_docker(self):
        (self.root / '.env').unlink()
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
