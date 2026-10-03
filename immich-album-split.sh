#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for arg in "$@"; do
    if [[ "$arg" == -h || "$arg" == --help ]]; then
        cat <<'EOF'
使用方法: ./immich-album-split.sh [--dry-run] [--keep-source] <アルバム名・パターン>...
画像・動画を「元名_縦」「元名_横」「元名_正方形」へ分割します。
全件分類・追加確認後、所有する元アルバムを既定で削除します。
-n, --dry-run  変更せず分割先名・件数・元アルバム削除予定を表示
--keep-source  元アルバムを保持
例: ./immich-album-split.sh --dry-run '旅行*' '家族'
EOF
        exit 0
    fi
done
if [[ $# -eq 0 ]]; then
    echo 'エラー: アルバム名またはパターンを指定してください。' >&2
    exit 1
fi
if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    echo 'エラー: スクリプトと同じ場所の .env に IMMICH_API_KEY を設定してください。' >&2
    exit 1
fi
cd "$SCRIPT_DIR"
exec docker compose -f "${SCRIPT_DIR}/docker-compose.album-split.yml" run --rm -T immich-album-split "$@"
