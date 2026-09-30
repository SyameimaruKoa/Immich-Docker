# Immich CLI 一括アップロード ガイド

ホストマシン上の写真・動画ディレクトリを Immich に一括アップロードするためのツールです。

---

## 特徴
1. **追加イメージ不要**: ローカルの `ghcr.io/immich-app/immich-server:v3` に内蔵されている公式 CLI を使用します。
2. **Docker Compose 準拠**: `docker run` を使わず、すべて `docker compose` 経由で実行されます。
3. **安全・簡単**: ディレクトリパスを渡すだけで、アルバム作成・再帰探索を自動で行います。

---

## 使い方

### 1. スクリプトで簡単にアップロード（推奨）

```bash
cd /opt/Docker_Container/Immich-Docker

# 基本実行（各ファイルの格納フォルダ名でアルバム作成）
./immich-upload.sh /mnt/NAS/Documents/Pictures/壁紙

# 親フォルダ名も含めてアルバム作成
./immich-upload.sh --album-with-parent /mnt/NAS/Documents/Pictures/壁紙

# アップロード対象の事前確認（ドライラン）
./immich-upload.sh --dry-run /mnt/NAS/Documents/Pictures/壁紙

# 指定したアルバム名にまとめて追加
./immich-upload.sh -A "2026年お気に入り壁紙" /mnt/NAS/Documents/Pictures/壁紙

# アルバムを作成せずタイムラインにのみ追加
./immich-upload.sh --no-album /mnt/NAS/Documents/Pictures/写真
```

### アルバム名の決まり方

対象が `/Pictures/壁紙` の場合、`壁紙/a.jpg` と `壁紙/旅行/b.jpg` は次のアルバムに入ります。

| モード | `a.jpg` | `b.jpg` |
| :--- | :--- | :--- |
| 既定 / `--album` | `壁紙` | `旅行` |
| `--album-with-parent` | `Pictures_壁紙` | `壁紙_旅行` |
| `--album-name "写真"` | `写真` | `写真` |
| `--no-album` | 追加しない | 追加しない |

`--album-with-parent` は各フォルダの**直上の親**を使います。`--no-recursive` を付けると対象フォルダ直下のファイルだけを処理します。アルバムモードは同時に指定できません。アップロード対象の事前確認には `--dry-run` を使ってください。

### 2. オプション一覧

| オプション | 説明 | デフォルト |
| :--- | :--- | :--- |
| `-a`, `--album` | 各ファイルの格納フォルダ名でアルバム作成 | **有効** |
| `--album-with-parent` | `親フォルダ名_格納フォルダ名` でアルバム作成 | 無効 |
| `--no-album` | アルバム作成を無効化（タイムラインのみ） | 無効 |
| `-A`, `--album-name <名前>` | 指定したアルバム名にすべてのメディアを追加 | 未設定 |
| `-r`, `--recursive` | サブディレクトリ内も再帰的に探索 | **有効** |
| `--no-recursive` | 直下のファイルのみ探索 | 無効 |
| `-n`, `--dry-run` | 実際にはアップロードせずテスト実行 | 無効 |
| `-d`, `--delete` | アップロード完了後にホスト側の元ファイルを削除 | 無効 |
| `--delete-duplicates` | サーバー上に既存の重複ファイルをホスト側から削除 | 無効 |
| `-k`, `--key <API_KEY>` | APIキーを一時的に指定 | `.env` の値 |
| `-u`, `--url <URL>` | Immich API URL を指定 | `http://localhost:2283/api` |
| `-h`, `--help` | ヘルプメッセージを表示 | - |

---

### 3. Docker Compose で直接実行する場合

スクリプトを使わず直接 `docker compose` コマンドで実行することも可能です。

```bash
cd /opt/Docker_Container/Immich-Docker

# UPLOAD_DIR 環境変数で対象ディレクトリを指定して実行
UPLOAD_DIR="/mnt/NAS/Documents/Pictures/壁紙" docker compose -f docker-compose.cli.yml run --rm immich-cli
```
