#!/usr/bin/env python3
"""Split Immich albums by display aspect ratio without downloading media."""
import argparse
import fcntl
import fnmatch
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location(
    'unclassified', Path(__file__).with_name('immich-unclassified.py'))
connection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connection)
LABELS = ('縦', '横', '正方形')


class Client(connection.Client):
    def members(self, album_id):
        items = {}
        page = 1
        while True:
            result = self.request('POST', '/search/metadata', {
                'albumIds': [album_id], 'page': page, 'size': 1000,
                'withExif': True, 'withPeople': False, 'withStacked': True,
                'withDeleted': True,
            })['assets']
            for asset in result['items']:
                exif = asset.get('exifInfo') or {}
                items[asset['id']] = {
                    'id': asset['id'], 'type': asset['type'],
                    'isTrashed': asset.get('isTrashed', False),
                    'exifInfo': {k: exif.get(k) for k in
                                 ('exifImageWidth', 'exifImageHeight', 'orientation')},
                }
            token = result['nextPage']
            if token is None:
                return items
            next_page = int(token)
            if next_page <= page:
                raise RuntimeError('検索APIのページ番号が進みません')
            page = next_page


def classify(asset):
    if asset['type'] not in {'IMAGE', 'VIDEO'} or asset.get('isTrashed'):
        return None
    exif = asset.get('exifInfo') or {}
    width, height = exif.get('exifImageWidth'), exif.get('exifImageHeight')
    if (type(width) is not int or type(height) is not int
            or width <= 0 or height <= 0):
        return None
    orientation = exif.get('orientation')
    if orientation is not None and str(orientation) not in {'1', '2', '3', '4', '5', '6', '7', '8'}:
        return None
    if str(orientation) in {'5', '6', '7', '8'}:
        width, height = height, width
    return '正方形' if width == height else ('縦' if height > width else '横')


def split(client, patterns, dry_run=False, keep_source=False):
    user_id = client.request('GET', '/users/me')['id']
    albums = client.request('GET', '/albums')
    owned_ids = {a['id'] for a in client.request('GET', '/albums?isOwned=true')}
    selected = {a['id']: a for a in albums
                if any(fnmatch.fnmatchcase(a['albumName'], p) for p in patterns)}
    if not selected:
        raise RuntimeError('指定に一致するアルバムがありません')
    # Never recursively split generated destinations matched by a broad wildcard.
    sources = [a for a in selected.values() if not a['albumName'].endswith(
        tuple('_' + label for label in LABELS))]
    if not sources:
        raise RuntimeError('分割先の接尾辞を持つアルバムのみ一致しました')
    if len({a['albumName'] for a in sources}) != len(sources):
        raise RuntimeError('同名の元アルバムが複数あります。名前を変更してください')

    plans = []
    # Read and validate every source/destination before the first write.
    for source in sources:
        detail = client.request('GET', '/albums/' + source['id'])
        members = client.members(source['id'])
        if len(members) != detail['assetCount']:
            raise RuntimeError('元アルバムの全件取得を確認できません: ' + source['albumName'])
        groups = {label: set() for label in LABELS}
        for asset_id, asset in members.items():
            label = classify(asset)
            if label:
                groups[label].add(asset_id)
        skipped = len(members) - sum(map(len, groups.values()))
        destinations = {}
        for label, ids in groups.items():
            name = source['albumName'] + '_' + label
            matches = [a for a in albums if a['albumName'] == name]
            if len(matches) > 1:
                raise RuntimeError('同名の分割先が複数あります: ' + name)
            target = matches[0] if matches else None
            if target:
                target_detail = client.request('GET', '/albums/' + target['id'])
                roles = {u['user']['id']: u['role'] for u in target_detail['albumUsers']}
                if target['id'] not in owned_ids and roles.get(user_id) != 'editor':
                    raise RuntimeError('分割先への追加権限がありません: ' + name)
            current = set(client.members(target['id'])) if target else set()
            destinations[label] = (name, target, ids - current)
            print(f'{name}: {len(ids)}件 / 追加 {len(ids - current)}件'
                  + (' (dry-run)' if dry_run else ''), flush=True)
        delete = (not keep_source and not skipped and bool(members)
                  and source['id'] in owned_ids)
        print(f'{source["albumName"]}: 未分類 {skipped}件 / 元アルバム '
              + ('削除予定' if delete else '保持'), flush=True)
        plans.append((source, detail, members, groups, destinations, delete))
    if dry_run:
        return

    for source, detail, members, groups, destinations, delete in plans:
        actual = {}
        for label, ids in groups.items():
            if not ids:
                continue
            name, target, additions = destinations[label]
            if target is None:
                # Preserve user sharing; a different original owner becomes editor.
                users = [{'userId': u['user']['id'],
                          'role': 'editor' if u['role'] == 'owner' else u['role']}
                         for u in detail['albumUsers'] if u['user']['id'] != user_id]
                target = client.request('POST', '/albums', {
                    'albumName': name, 'albumUsers': users})
            client.change('PUT', target['id'], additions)
            actual[label] = target['id']
        # Verify all memberships, even when the API reported successful additions.
        for label, target_id in actual.items():
            if not groups[label] <= set(client.members(target_id)):
                raise RuntimeError('分割先の全件確認に失敗。元アルバムは保持します')
        if delete:
            latest = client.request('GET', '/albums/' + source['id'])
            if (latest['updatedAt'] != detail['updatedAt']
                    or latest['assetCount'] != len(members)
                    or client.members(source['id']) != members):
                raise RuntimeError('処理中に元アルバムが変更されました。削除しません')
            client.request('DELETE', '/albums/' + source['id'])
            print(f'{source["albumName"]}: 削除完了', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Immich のアルバムを縦・横・正方形に分割')
    parser.add_argument('albums', nargs='+', help='アルバム名または引用符で囲んだワイルドカード')
    parser.add_argument('-n', '--dry-run', action='store_true')
    parser.add_argument('--keep-source', action='store_true', help='元アルバムを保持する')
    args = parser.parse_args()
    try:
        key = os.environ.get('IMMICH_API_KEY', '')
        if not key or key == 'your_api_key_here':
            raise RuntimeError('IMMICH_API_KEY を設定してください')
        digest = hashlib.sha256((connection.LOCAL_API_URL + '\0' + key).encode()).hexdigest()
        with open(Path('/tmp') / f'immich-album-split-{digest}.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('同じユーザーの分割が実行中です') from None
            split(Client(connection.LOCAL_API_URL, key), args.albums,
                  args.dry_run, args.keep_source)
        return 0
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as error:
        print(f'エラー: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
