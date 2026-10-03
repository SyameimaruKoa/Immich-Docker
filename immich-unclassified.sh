#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.unclassified.yml"

show_help() {
    cat <<'EOF'
使用方法:
    ./immich-unclassified.sh [オプション]

説明:
    このホストの Immich の未分類画像・動画を専用アルバムに同期します。
    Docker Compose と、このスクリプトと同じ場所の .env を使用します。

オプション:
    -A, --album-name <名前>  専用アルバム名 (既定: 未分類)
    --include-archived       アーカイブも対象に含める
    -n, --dry-run            変更せず追加・除外予定件数だけを表示
    --interval <秒>         指定間隔で繰り返す (既定: 0 = 1回だけ)
    -h, --help              このヘルプを表示

実行例:
    ./immich-unclassified.sh --dry-run
    ./immich-unclassified.sh
    ./immich-unclassified.sh --include-archived
    ./immich-unclassified.sh --interval 86400
EOF
}

ARGS=(--interval 0)
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) show_help; exit 0 ;;
        -n|--dry-run) ARGS+=(--dry-run); shift ;;
        --include-archived) ARGS+=(--include-archived); shift ;;
        -A|--album-name|--interval)
            if [ "$#" -lt 2 ] || [ -z "$2" ] || [[ "$2" == -* ]]; then
                echo "エラー: $1 の後に値を指定してください。" >&2
                exit 1
            fi
            if [ "$1" = --interval ] && [[ ! "$2" =~ ^[0-9]+$ ]]; then
                echo "エラー: 間隔は0以上の整数を指定してください。" >&2
                exit 1
            fi
            if [ "$1" = -A ]; then
                ARGS+=(--album-name "$2")
            else
                ARGS+=("$1" "$2")
            fi
            shift 2
            ;;
        *)
            echo "エラー: 不明なオプションです: $1" >&2
            exit 1
            ;;
    esac
done

if [ ! -f "${SCRIPT_DIR}/.env" ]; then
    echo "エラー: ${SCRIPT_DIR}/.env に IMMICH_API_KEY を設定してください。" >&2
    exit 1
fi

cd "$SCRIPT_DIR"
# run の引数で定期サービスの command を上書きし、通常は1回だけ実行する。
exec docker compose -f "$COMPOSE_FILE" run --rm -T immich-unclassified "${ARGS[@]}"
