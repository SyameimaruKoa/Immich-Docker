#!/usr/bin/env python3
"""Synchronize a private Immich album containing otherwise unclassified assets."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API key to a redirect destination.


class Client:
    def __init__(self, url, key):
        self.url = url.rstrip('/')
        self.key = key
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.url + path, data=data, method=method,
                          headers={'x-api-key': self.key, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f'{method} {path}: HTTP {error.code}') from None
        except (URLError, TimeoutError, ValueError) as error:
            raise RuntimeError(f'{method} {path}: API通信またはJSON応答のエラー') from error

    def search(self, **filters):
        result = {}
        page = 1
        while True:
            response = self.request('POST', '/search/metadata', {
                'size': 1000, 'page': page, 'withStacked': True,
                'withExif': False, 'withPeople': False, **filters,
            })['assets']
            for asset in response['items']:
                # Keep only classification fields; never inspect image previews,
                # filenames, tags, EXIF, or people data.
                result[asset['id']] = {field: asset[field] for field in
                                       ('id', 'ownerId', 'visibility', 'type', 'isTrashed')}
            token = response['nextPage']
            if token is None:
                return result
            next_page = int(token)
            if next_page <= page:
                raise RuntimeError('検索APIのページ番号が進みません')
            page = next_page

    def change(self, method, album_id, ids):
        ids = sorted(ids)
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            response = self.request(method, f'/albums/{album_id}/assets', {'ids': batch})
            results = {item['id']: item for item in response}
            failed = [asset_id for asset_id in batch
                      if asset_id not in results or not results[asset_id]['success']]
            if failed:
                raise RuntimeError(f'{method}: アルバム更新に失敗 ({len(failed)}件)')


def sync(client, album_name, include_archived=False, dry_run=False):
    owner_id = client.request('GET', '/users/me')['id']
    albums = client.request('GET', '/albums')
    owned = client.request('GET', '/albums?isOwned=true')
    matches = [album for album in owned if album['albumName'] == album_name]
    if len(matches) > 1:
        raise RuntimeError('同名の専用アルバムが複数あります。名前を変更してください')
    target = matches[0] if matches else None
    if target and (target['shared'] or target.get('hasSharedLink')):
        raise RuntimeError('専用アルバムが共有されています。共有を解除してください')

    allowed = {'timeline', 'archive'} if include_archived else {'timeline'}
    eligible = {}
    for visibility in sorted(allowed):
        for asset_id, asset in client.search(visibility=visibility).items():
            # Independently enforce ownership and visibility on API responses.
            if (asset['ownerId'] == owner_id and asset['visibility'] in allowed
                    and asset['type'] in {'IMAGE', 'VIDEO'} and not asset['isTrashed']):
                eligible[asset_id] = asset

    classified = set()
    current = set()
    # Fetch every page before mutating: writes must not shift pagination.
    for album in albums:
        members = client.search(albumIds=[album['id']], withDeleted=True)
        if target and album['id'] == target['id']:
            current.update(members)
        else:
            classified.update(members)
    desired = set(eligible) - classified
    additions = desired - current
    removals = current - desired
    print(f'{album_name}: 追加 {len(additions)}件 / 除外 {len(removals)}件'
          + (' (dry-run)' if dry_run else ''), flush=True)
    if dry_run:
        return
    if target is None:
        target = client.request('POST', '/albums', {'albumName': album_name})
    client.change('DELETE', target['id'], removals)
    client.change('PUT', target['id'], additions)


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        name, separator, raw = line.partition('=')
        if separator:
            values = shlex.split(raw, comments=True)
            os.environ.setdefault(name.strip(), ' '.join(values))


def main():
    parser = argparse.ArgumentParser(description='未分類の画像・動画を専用アルバムに同期 (Immich v3)')
    parser.add_argument('--env-file', type=Path, default=Path(__file__).with_name('.env'))
    parser.add_argument('--url', help='Immich API URL (末尾 /api)')
    parser.add_argument('--album-name', default='未分類')
    parser.add_argument('--include-archived', action='store_true', help='アーカイブも含める')
    parser.add_argument('--dry-run', action='store_true', help='変更せず件数を表示')
    parser.add_argument('--interval', type=int, default=0, help='定期実行間隔 (秒)。0 は1回だけ')
    args = parser.parse_args()
    try:
        load_env(args.env_file)
        url = args.url or os.environ.get('IMMICH_INSTANCE_URL', 'http://localhost:2283/api')
        key = os.environ.get('IMMICH_API_KEY', '')
        if not key or key == 'your_api_key_here':
            raise RuntimeError('IMMICH_API_KEY を設定してください')
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip('/') != '/api'):
            raise RuntimeError('URL は http(s)://ホスト[:ポート]/api を指定してください')
        if args.interval < 0 or not args.album_name.strip():
            raise RuntimeError('間隔は0以上、アルバム名は空白以外を指定してください')
        archived_setting = os.environ.get('IMMICH_UNCLASSIFIED_INCLUDE_ARCHIVED', 'false').lower()
        if archived_setting not in {'true', 'false'}:
            raise RuntimeError('IMMICH_UNCLASSIFIED_INCLUDE_ARCHIVED は true / false を指定してください')
        include_archived = args.include_archived or archived_setting == 'true'
        # Separate keys for different users; do not expose the key in the filename.
        digest = hashlib.sha256((url + '\0' + key).encode()).hexdigest()
        with open(Path('/tmp') / f'immich-unclassified-{digest}.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('同じユーザーの同期が実行中です') from None
            client = Client(url, key)
            while True:
                try:
                    sync(client, args.album_name, include_archived, args.dry_run)
                except (RuntimeError, KeyError, TypeError, ValueError) as error:
                    if not args.interval:
                        raise
                    print(f'同期失敗: {error}', file=sys.stderr, flush=True)
                if not args.interval:
                    return 0
                time.sleep(args.interval)
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as error:
        print(f'エラー: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
