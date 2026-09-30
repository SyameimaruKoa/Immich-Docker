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
    -a, --album             各ファイルが入っているフォルダ名のアルバムに追加（既定）
    -p, --album-with-parent 親フォルダ名_フォルダ名のアルバムに追加
    -N, --no-album          アルバムに追加せずアップロード
    -A, --album-name <名前> すべてのファイルを指定名のアルバムに追加
    -r, --recursive         サブディレクトリ内も再帰的に探索してアップロード（デフォルトで有効）
    -R, --no-recursive      直下のファイルのみアップロード
    -n, --dry-run           実際にはアップロードせず、対象ファイルの確認のみ実行（テスト用）
    -d, --delete            アップロード成功後にホスト側の元ファイルを削除（重複ファイルも含む）
    -D, --delete-duplicates 既にサーバー上に存在する重複ファイルをホスト側から削除
    -k, --key <API_KEY>     Immich API キーを明示的に指定（デフォルトは .env 内の値）
    -u, --url <URL>         Immich API の URL を指定（デフォルト: http://localhost:2283/api）
    -h, --help              このヘルプメッセージを表示して終了

実行例:
    # 1. 基本的なアップロード（壁紙/a.jpg →「壁紙」、壁紙/旅行/b.jpg →「旅行」）
    ./immich-upload.sh /mnt/NAS/Documents/Pictures/壁紙

    # 2. 親フォルダ名も含める（壁紙/a.jpg →「Pictures_壁紙」、壁紙/旅行/b.jpg →「壁紙_旅行」）
    ./immich-upload.sh --album-with-parent /mnt/NAS/Documents/Pictures/壁紙

    # 3. 特定のアルバム名を指定してアップロード
    ./immich-upload.sh -A "2026年壁紙コレクション" /mnt/NAS/Documents/Pictures/壁紙4K

    # 4. アップロード対象の確認（ドライラン）
    ./immich-upload.sh --dry-run /mnt/NAS/Documents/Pictures/壁紙

    # 5. アルバムを作らずタイムラインにのみ追加
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
ALBUM_MODE="folder"
ALBUM_MODE_SPECIFIED=false
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
        -a|--album|-p|--album-with-parent|-N|--no-album)
            if [ "$ALBUM_MODE_SPECIFIED" = true ]; then
                echo "エラー: アルバムモードは1つだけ指定してください。" >&2
                exit 1
            fi
            ALBUM_MODE_SPECIFIED=true
            case "$1" in
                -a|--album) ALBUM_MODE="folder" ;;
                -p|--album-with-parent) ALBUM_MODE="parent" ;;
                -N|--no-album) ALBUM_MODE="none" ;;
            esac
            shift
            ;;
        -A|--album-name)
            if [ "$ALBUM_MODE_SPECIFIED" = true ]; then
                echo "エラー: アルバムモードは1つだけ指定してください。" >&2
                exit 1
            fi
            ALBUM_MODE_SPECIFIED=true
            ALBUM_MODE="fixed"
            if [ "${2:-}" != "" ] && [[ "$2" != -* ]]; then
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
        -R|--no-recursive)
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
        -D|--delete-duplicates)
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

if [ "$RECURSIVE" = true ] && [ "$ALBUM_MODE" != "parent" ]; then
    CLI_ARGS+=("--recursive")
fi

case "$ALBUM_MODE" in
    fixed) CLI_ARGS+=("--album-name" "$CUSTOM_ALBUM") ;;
    folder) CLI_ARGS+=("--album") ;;
esac

if [ "$DRY_RUN" = true ]; then
    CLI_ARGS+=("--dry-run")
fi

if [ "$DELETE_ASSETS" = true ]; then
    CLI_ARGS+=("--delete")
    # 重複ファイル（既にサーバー上に存在するファイル）も削除する
    DELETE_DUPLICATES=true
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
case "$ALBUM_MODE" in
    fixed) ALBUM_DESCRIPTION="固定アルバム: $CUSTOM_ALBUM" ;;
    folder) ALBUM_DESCRIPTION="各ファイルの格納フォルダ名" ;;
    parent) ALBUM_DESCRIPTION="親フォルダ名_格納フォルダ名" ;;
    none) ALBUM_DESCRIPTION="なし (タイムラインのみ)" ;;
esac
echo "アルバム設定   : $ALBUM_DESCRIPTION"
echo "再帰探索       : $(if [ "$RECURSIVE" = true ]; then echo "有効"; else echo "無効"; fi)"
echo "ドライラン     : $(if [ "$DRY_RUN" = true ]; then echo "有効 (テスト実行)"; else echo "無効 (実際にアップロード)"; fi)"
echo "ファイル削除   : $(if [ "$DELETE_ASSETS" = true ]; then echo "有効 (新規・重複ともに削除)"; elif [ "$DELETE_DUPLICATES" = true ]; then echo "重複のみ削除"; else echo "無効"; fi)"
echo "========================================================"

# ボリュームモードの設定（削除系オプション使用時は書き込み可能にする）
if [ "$DELETE_ASSETS" = true ] || [ "$DELETE_DUPLICATES" = true ]; then
    export VOLUME_MODE="rw"
else
    export VOLUME_MODE="ro"
fi

# Ctrl+C で現在の CLI 実行後に次のフォルダへ進まない。
trap 'echo >&2; echo "中断しました。" >&2; exit 130' INT

# Docker Compose の実行
export UPLOAD_DIR="$ABS_TARGET_DIR"
cd "$SCRIPT_DIR"
if [ "$ALBUM_MODE" = "parent" ]; then
    # CLI の --album-name は全ファイルに同じ名前を付けるため、フォルダ単位で実行する。
    # find -print0 と読み取りで空白・改行を含むフォルダ名も保持する。
    while IFS= read -r -d '' folder; do
        # サブフォルダは --recursive のときだけ処理する。隠しフォルダは CLI の既定に合わせて除外する。
        if [ "$folder" != "$ABS_TARGET_DIR" ]; then
            [ "$RECURSIVE" = true ] || continue
            relative="${folder#"$ABS_TARGET_DIR"/}"
            case "/$relative/" in */.*/*) continue ;; esac
        fi
        # ファイルのないフォルダでは CLI を呼ばない。
        [ -n "$(find "$folder" -maxdepth 1 -type f -print -quit)" ] || continue
        parent_name="$(basename "$(dirname "$folder")")"
        folder_name="$(basename "$folder")"
        album_name="${parent_name}_${folder_name}"
        container_path="/import${folder#"$ABS_TARGET_DIR"}"
        echo "アルバム: $album_name ($folder)"
        docker compose -f "$COMPOSE_FILE" run --rm immich-cli \
            "${CLI_ARGS[@]:0:${#CLI_ARGS[@]}-1}" --album-name "$album_name" "$container_path" </dev/null
    done < <(find "$ABS_TARGET_DIR" -type d -print0)
else
    docker compose -f "$COMPOSE_FILE" run --rm immich-cli "${CLI_ARGS[@]}"
fi
