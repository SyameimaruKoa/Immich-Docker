# 未分類アルバムの定期同期

Immich v3 の API で、APIキー所有ユーザーの画像・動画のうち、他のアルバムに所属していないものを専用アルバム「未分類」にまとめます。別のアルバムに分類したものは次回同期で「未分類」から外します。元のファイルや他のアルバムは削除・変更しません。

鍵付きフォルダ (`locked`)、非表示 (`hidden`)、ゴミ箱、他ユーザーのファイルは対象外です。Immich は鍵付きフォルダへの移動時にすべてのアルバムから所属を外します。アーカイブは既定で対象外です。設定を切り替えると次回同期で専用アルバムへの所属も反映されます。

画像の取得・表示・解析、タグの取得・閲覧は行いません。メタデータ検索はタグを返さない Immich v3 の検索APIを使い、EXIF・人物情報も無効にしています。処理に保持するのはID・所有者ID・公開範囲・画像/動画種別・ゴミ箱状態だけで、ログは件数のみです。

## 設定

`.env` の `IMMICH_API_KEY` に対象ユーザーの APIキーを設定します。必要な権限は `user.read`, `asset.read`, `album.read`, `album.create`, `albumAsset.create`, `albumAsset.delete` です。APIキーや `.env` をコミットしないでください。

```dotenv
IMMICH_UNCLASSIFIED_INCLUDE_ARCHIVED=false
```

`true` にするとアーカイブを含めます。接続先はこのホストの `http://127.0.0.1:2283/api` に固定しています。`IMMICH_INSTANCE_URL` やプロキシの設定は使いません。専用アルバムは自動作成されます。同名の所有アルバムが既にあれば再利用します。同名の所有アルバムが複数ある場合、または専用アルバムが共有されている場合はエラーで停止します。

## 手動実行・事前確認

既存の `immich-upload.sh` と同様に、シェルスクリプトから Docker Compose 経由で実行します。ホストへの Python のインストールは不要です。初回は Python コンテナを取得します。どのディレクトリから呼び出しても、スクリプトと同じ場所の `.env` と Compose ファイルを使います。

```bash
./immich-unclassified.sh --dry-run
./immich-unclassified.sh
./immich-unclassified.sh --include-archived
./immich-unclassified.sh --album-name 未整理
```

通常の実行は1回の同期後に終了します。`--dry-run` は読み取りのみで、追加・除外予定件数を表示します。対象アルバム名は一度決めたら固定してください。変更すると以前の専用アルバムも通常の分類済みアルバムとして扱われます。

## 毎日自動実行

```bash
docker compose -f docker-compose.unclassified.yml up -d
docker compose -f docker-compose.unclassified.yml logs -f
```

Python コンテナを取得し、起動時に1回、その後86400秒間隔で同期します。既存の Immich サービスを再起動する必要はありません。定刻の実行ではなく、各同期の完了後から24時間の間隔です。通信・権限エラーはログに記録し、次回の周期で再試行します。設定を変更したら上記の `up -d` を再実行してください。

停止するには以下を実行します。

```bash
docker compose -f docker-compose.unclassified.yml down
```

`./immich-unclassified.sh --interval 86400` でもフォアグラウンドで定期実行できます。同じコンテナ内では同じAPIキーでの並行実行を防止します。手動実行コンテナと定期サービスはロックを共有しないため、重複起動しないでください。

すべての検索ページを読み終えてから、専用アルバムの差分だけを500件単位で更新します。読み取りに失敗した場合は変更しません。更新途中の失敗は次回同期で再計算します。実行中のユーザー操作とはトランザクションを共有できないため、実行中に分類・アーカイブなどを変更したものは次回同期で整合します。

## 検証

```bash
python3 -m unittest discover -s tests -v
```

API仕様: [searchAssets](https://api.immich.app/endpoints/search/searchAssets)、[getAllAlbums](https://api.immich.app/endpoints/albums/getAllAlbums)。Immich v3 の `albumIds` / `page` / `visibility` 形式を使用します (v3.2 では非推奨ですが引き続き利用可能)。
