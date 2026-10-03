import copy
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'split', Path(__file__).resolve().parents[1] / 'immich-album-split.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def asset(i, w=100, h=200, kind='IMAGE', orientation=None):
    return {'id': i, 'type': kind, 'isTrashed': False, 'exifInfo': {
        'exifImageWidth': w, 'exifImageHeight': h, 'orientation': orientation}}


class Fake(m.Client):
    def __init__(self):
        self.albums = [{'id': 'source', 'albumName': '旅行', 'assetCount': 3,
                        'updatedAt': 'initial', 'albumUsers': [
                            {'user': {'id': 'me'}, 'role': 'owner'},
                            {'user': {'id': 'friend'}, 'role': 'viewer'}]}]
        self.owned = {'source'}
        self.assets = {'source': {'p': asset('p'),
                                 'v': asset('v', 200, 100, 'VIDEO'),
                                 's': asset('s', 100, 100)}}
        self.writes = []
        self.fail = False
        self.mutate = False
        self.lie = False

    def members(self, i):
        if self.mutate and self.writes and i == 'source':
            return {**self.assets[i], 'new': asset('new')}
        return copy.deepcopy(self.assets[i])

    def request(self, method, path, body=None):
        if method == 'GET':
            if path == '/users/me':
                return {'id': 'me'}
            if path == '/albums':
                return copy.deepcopy(self.albums)
            if path == '/albums?isOwned=true':
                return [a for a in self.albums if a['id'] in self.owned]
            return copy.deepcopy(next(a for a in self.albums if a['id'] == path.split('/')[2]))
        self.writes.append((method, path, body))
        if method == 'POST':
            new = {**body, 'id': 'dest' + str(len(self.albums)), 'assetCount': 0,
                   'updatedAt': 'initial', 'albumUsers': [{'user': {'id': 'me'}, 'role': 'owner'}]}
            self.albums.append(new)
            self.assets[new['id']] = {}
            self.owned.add(new['id'])
            return new
        if method == 'PUT':
            if self.fail:
                return [{'id': i, 'success': False} for i in body['ids']]
            if not self.lie:
                for i in body['ids']:
                    self.assets[path.split('/')[2]][i] = self.assets['source'][i]
            return [{'id': i, 'success': True} for i in body['ids']]
        if method == 'DELETE':
            return None


class Tests(unittest.TestCase):
    def run_split(self, c, patterns=None, **kwargs):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            m.split(c, patterns or ['旅行*'], **kwargs)
        return output.getvalue()

    def test_image_video_square_rotation_and_invalid(self):
        self.assertEqual(m.classify(asset('a')), '縦')
        self.assertEqual(m.classify(asset('a', 200, 100, 'VIDEO', '6')), '縦')
        self.assertEqual(m.classify(asset('a', 100, 200, orientation='8')), '横')
        self.assertEqual(m.classify(asset('a', 100, 100)), '正方形')
        for a in [asset('a', None), asset('a', 0), asset('a', orientation='bad'),
                  asset('a', kind='OTHER'), {**asset('a'), 'isTrashed': True}]:
            self.assertIsNone(m.classify(a))

    def test_split_shared_and_delete_only_after_verified_additions(self):
        c = Fake()
        self.run_split(c)
        self.assertEqual(c.writes[-1], ('DELETE', '/albums/source', None))
        created = [body for method, path, body in c.writes if method == 'POST']
        self.assertEqual({a['albumName'] for a in created}, {'旅行_縦', '旅行_横', '旅行_正方形'})
        self.assertEqual(created[0]['albumUsers'], [{'userId': 'friend', 'role': 'viewer'}])

    def test_dry_run_reports_without_writes(self):
        c = Fake()
        out = self.run_split(c, dry_run=True)
        self.assertIn('旅行_正方形: 1件', out)
        self.assertIn('削除予定', out)
        self.assertEqual(c.writes, [])

    def test_unknown_keep_source_and_non_owned_shared_prevent_deletion(self):
        for mode in ['unknown', 'keep', 'shared', 'empty']:
            c = Fake()
            if mode == 'unknown':
                c.assets['source']['p'] = asset('p', None)
            if mode == 'shared':
                c.owned.clear()
            if mode == 'empty':
                c.assets['source'].clear()
                c.albums[0]['assetCount'] = 0
            self.run_split(c, keep_source=mode == 'keep')
            self.assertFalse(any(w[0] == 'DELETE' for w in c.writes))

    def test_addition_failure_false_success_and_source_change_prevent_delete(self):
        for mode in ['fail', 'lie', 'mutate']:
            c = Fake()
            setattr(c, mode, True)
            with self.assertRaises(RuntimeError):
                self.run_split(c)
            self.assertFalse(any(w[0] == 'DELETE' for w in c.writes))

    def test_existing_destination_and_repeat_do_not_add_duplicates(self):
        c = Fake()
        self.run_split(c, keep_source=True)
        c.writes.clear()
        self.run_split(c, keep_source=True)
        self.assertEqual(c.writes, [])

    def test_ambiguous_destination_and_incomplete_reads_prevent_all_writes(self):
        for mode in ['duplicates', 'incomplete', 'no-match', 'read-only']:
            c = Fake()
            if mode in {'duplicates', 'read-only'}:
                target = {**c.albums[0], 'id': 'target', 'albumName': '旅行_縦',
                          'albumUsers': [{'user': {'id': 'me'}, 'role': 'viewer'}]}
                c.albums.append(target)
                c.assets['target'] = {}
                if mode == 'duplicates':
                    c.albums.append({**target, 'id': 'duplicate'})
            if mode == 'incomplete':
                c.albums[0]['assetCount'] = 4
            with self.assertRaises(RuntimeError):
                self.run_split(c, ['missing'] if mode == 'no-match' else None)
            self.assertEqual(c.writes, [])

    def test_multiple_patterns_are_individual_and_deduplicated(self):
        c = Fake()
        other = {**c.albums[0], 'id': 'second', 'albumName': '家族'}
        c.albums.append(other)
        c.assets['second'] = copy.deepcopy(c.assets['source'])
        out = self.run_split(c, ['旅行*', '*', '家族'], dry_run=True)
        self.assertEqual(out.count('旅行_縦:'), 1)
        self.assertIn('家族_縦:', out)

    def test_members_pagination_retains_only_classification_metadata(self):
        c = m.Client('http://localhost/api', 'test')
        calls = []
        def request(method, path, body):
            calls.append(body)
            return {'assets': {'items': [{**asset(str(body['page'])), 'tags': ['unused']}],
                               'nextPage': '2' if body['page'] == 1 else None}}
        c.request = request
        result = c.members('source')
        self.assertEqual(set(result), {'1', '2'})
        self.assertNotIn('tags', result['1'])
        self.assertTrue(calls[0]['withExif'])
        self.assertTrue(calls[0]['withStacked'])
        c.request = lambda *args: {'assets': {'items': [], 'nextPage': '1'}}
        with self.assertRaises(RuntimeError):
            c.members('source')

    def test_transport_accepts_no_content_delete(self):
        class Response(io.BytesIO):
            status = 204
        c = m.Client('http://localhost/api', 'test')
        c.opener.open = lambda *args, **kwargs: Response(b'')
        self.assertIsNone(c.request('DELETE', '/albums/source'))


class ShellTests(unittest.TestCase):
    def test_compose_arguments_from_other_directory(self):
        import json
        import os
        import shutil
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory(prefix='album split ') as directory:
            root = Path(directory)
            script = root / 'immich-album-split.sh'
            shutil.copyfile(Path(__file__).resolve().parents[1] / script.name, script)
            script.chmod(0o755)
            (root / '.env').write_text('IMMICH_API_KEY=test\n')
            docker = root / 'docker'
            docker.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                              'print(json.dumps([os.getcwd(), sys.argv[1:]]))\n')
            docker.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH']}
            result = subprocess.run([str(script), '--dry-run', '--keep-source', '旅行 *', '家族'],
                                    cwd='/tmp', env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [str(root), ['compose', '-f',
                str(root / 'docker-compose.album-split.yml'), 'run', '--rm', '-T',
                'immich-album-split', '--dry-run', '--keep-source', '旅行 *', '家族']])
            (root / '.env').unlink()
            result = subprocess.run([str(script), '旅行'], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            result = subprocess.run([str(script), '--help'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
