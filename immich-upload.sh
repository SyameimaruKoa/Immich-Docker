#!/usr/bin/env bash
set -e

# ==============================================================================
# Immich 一括アップロード支援スクリプト
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.cli.yml"
ENV_FILE="${SCRIPT_DIR}/.env"

# ------------------------------------------------------------------------------
# ヘルプ表示関数
# ------------------------------------------------------------------------------
show_help() {
    cat << 'EOF'
使用方法:
    ./immich-upload.sh [オプション] <アップロード対象ディレクトリパス>

説明:
    指定したホスト上のディレクトリ内の画像・動画を、Docker Compose を介して
    Immich サーバーに一括アップロードします。

引数:
    <アップロード対象ディレクトリパス>
        アップロードしたい画像・動画が含まれるホスト上のディレクトリ（絶対パスまたは相対パス）。

オプション:
    -a, --album             フォルダ名に基づいて自動的にアルバムを作成（デフォルトで有効）
    --no-album              アルバム自動作成を無効化（タイムラインにのみ追加）
    -A, --album-name <名前> 指定したアルバム名にすべてのメディアを追加
    -r, --recursive         サブディレクトリ内も再帰的に探索してアップロード（デフォルトで有効）
    --no-recursive          直下のファイルのみアップロード
    -n, --dry-run           実際にはアップロードせず、対象ファイルの確認のみ実行（テスト用）
    -d, --delete            アップロード成功後にホスト側の元ファイルを削除
    --delete-duplicates     既にサーバー上に存在する重複ファイルをホスト側から削除
    -k, --key <API_KEY>     Immich API キーを明示的に指定（デフォルトは .env 内の値）
    -u, --url <URL>         Immich API の URL を指定（デフォルト: http://localhost:2283/api）
    -h, --help              このヘルプメッセージを表示して終了

実行例:
    # 1. 基本的なアップロード（フォルダごとにアルバム自動作成）
    ./immich-upload.sh /mnt/NAS/Documents/Pictures/壁紙

    # 2. 特定のアルバム名を指定してアップロード
    ./immich-upload.sh -A "2026年壁紙コレクション" /mnt/NAS/Documents/Pictures/壁紙4K

    # 3. アップロード対象の確認（ドライラン）
    ./immich-upload.sh --dry-run /mnt/NAS/Documents/Pictures/壁紙

    # 4. アルバムを作らずタイムラインにのみ追加
    ./immich-upload.sh --no-album /mnt/NAS/Documents/Pictures/日常写真
EOF
}

# ------------------------------------------------------------------------------
# 引数チェック
# ------------------------------------------------------------------------------
if [ "$#" -eq 0 ]; then
    show_help
    exit 1
fi

# デフォルト設定
AUTO_ALBUM=true
CUSTOM_ALBUM=""
RECURSIVE=true
DRY_RUN=false
DELETE_ASSETS=false
DELETE_DUPLICATES=false
API_KEY=""
INSTANCE_URL=""
TARGET_DIR=""

# 引数解析
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -a|--album)
            AUTO_ALBUM=true
            shift
            ;;
        --no-album)
            AUTO_ALBUM=false
            shift
            ;;
        -A|--album-name)
            if [ -n "$2" ] && [[ "$2" != -* ]]; then
                CUSTOM_ALBUM="$2"
                shift 2
            else
                echo "エラー: --album-name の後にアルバム名を指定してください。" >&2
                exit 1
            fi
            ;;
        -r|--recursive)
            RECURSIVE=true
            shift
            ;;
        --no-recursive)
            RECURSIVE=false
            shift
            ;;
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -d|--delete)
            DELETE_ASSETS=true
            shift
            ;;
        --delete-duplicates)
            DELETE_DUPLICATES=true
            shift
            ;;
        -k|--key)
            if [ -n "$2" ] && [[ "$2" != -* ]]; then
                API_KEY="$2"
                shift 2
            else
                echo "エラー: --key の後に API キーを指定してください。" >&2
                exit 1
            fi
            ;;
        -u|--url)
            if [ -n "$2" ] && [[ "$2" != -* ]]; then
                INSTANCE_URL="$2"
                shift 2
            else
                echo "エラー: --url の後に URL を指定してください。" >&2
                exit 1
            fi
            ;;
        -*)
            echo "エラー: 不明なオプションです: $1" >&2
            echo "ヘルプを表示するには $0 --help を実行してください。" >&2
            exit 1
            ;;
        *)
            if [ -z "$TARGET_DIR" ]; then
                TARGET_DIR="$1"
                shift
            else
                echo "エラー: アップロード対象ディレクトリは1つだけ指定してください: $1" >&2
                exit 1
            fi
            ;;
    esac
done

# 対象ディレクトリのバリデーション
if [ -z "$TARGET_DIR" ]; then
    echo "エラー: アップロード対象ディレクトリが指定されていません。" >&2
    echo "" >&2
    show_help
    exit 1
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "エラー: 指定されたディレクトリが存在しません: $TARGET_DIR" >&2
    exit 1
fi

ABS_TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

# .env ファイルの存在確認
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# APIキーの優先順位（引数 > .env）
if [ -n "$API_KEY" ]; then
    export IMMICH_API_KEY="$API_KEY"
fi

if [ -z "$IMMICH_API_KEY" ]; then
    echo "エラー: IMMICH_API_KEY が設定されていません。" >&2
    echo ".env ファイルに IMMICH_API_KEY を設定するか、-k オプションで指定してください。" >&2
    exit 1
fi

# インスタンスURLの設定（引数 > .env > デフォルト）
if [ -n "$INSTANCE_URL" ]; then
    export IMMICH_INSTANCE_URL="$INSTANCE_URL"
elif [ -z "$IMMICH_INSTANCE_URL" ]; then
    export IMMICH_INSTANCE_URL="http://localhost:2283/api"
fi

# CLI コマンド引数の組み立て
CLI_ARGS=("upload")

if [ "$RECURSIVE" = true ]; then
    CLI_ARGS+=("--recursive")
fi

if [ -n "$CUSTOM_ALBUM" ]; then
    CLI_ARGS+=("--album-name" "$CUSTOM_ALBUM")
elif [ "$AUTO_ALBUM" = true ]; then
    CLI_ARGS+=("--album")
fi

if [ "$DRY_RUN" = true ]; then
    CLI_ARGS+=("--dry-run")
fi

if [ "$DELETE_ASSETS" = true ]; then
    CLI_ARGS+=("--delete")
fi

if [ "$DELETE_DUPLICATES" = true ]; then
    CLI_ARGS+=("--delete-duplicates")
fi

CLI_ARGS+=("/import")

# 実行情報の表示
echo "========================================================"
echo "Immich CLI アップロード開始"
echo "========================================================"
echo "対象ディレクトリ: $ABS_TARGET_DIR"
echo "Immich URL     : $IMMICH_INSTANCE_URL"
echo "アルバム設定   : $(if [ -n "$CUSTOM_ALBUM" ]; then echo "固定アルバム: $CUSTOM_ALBUM"; elif [ "$AUTO_ALBUM" = true ]; then echo "フォルダ名で自動作成"; else echo "なし (タイムラインのみ)"; fi)"
echo "再帰探索       : $(if [ "$RECURSIVE" = true ]; then echo "有効"; else echo "無効"; fi)"
echo "ドライラン     : $(if [ "$DRY_RUN" = true ]; then echo "有効 (テスト実行)"; else echo "無効 (実際にアップロード)"; fi)"
echo "========================================================"

# Docker Compose の実行
export UPLOAD_DIR="$ABS_TARGET_DIR"
cd "$SCRIPT_DIR"
docker compose -f "$COMPOSE_FILE" run --rm immich-cli "${CLI_ARGS[@]}"
