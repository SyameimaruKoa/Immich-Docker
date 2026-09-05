#!/bin/bash

show_help() {
    echo "使い方: $0 [オプション] <アルバム名> <退避先ディレクトリ>"
    echo ""
    echo "Immichのデータベースから指定したアルバムの写真パスを抽出し、"
    echo "コンテナ内パスをホスト側パスに自動変換して退避先ディレクトリへファイルをまとめて複製します。"
    echo "また、退避先ディレクトリおよび複製されたファイルに対し、"
    echo "親ディレクトリの所有者、所属グループ、基本権限、ACL設定を自動的に引き継ぎます。"
    echo ""
    echo "引数:"
    echo "    <アルバム名>           退避させたいアルバム名"
    echo "    <退避先ディレクトリ>   ファイルのコピー先フォルダパス"
    echo ""
    echo "オプション:"
    echo "    -h, --help             このヘルプを表示"
}

if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ $# -lt 2 ]; then
    show_help
    exit 0
fi

ALBUM_NAME="$1"
DEST_DIR="$2"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

DB_USER="${DB_USERNAME:-postgres}"
DB_NAME="${DB_DATABASE_NAME:-immich}"

CONTAINER_ID=$(docker compose ps -q immich-server 2>/dev/null || docker compose ps -q immich_server 2>/dev/null)

if [ -n "$CONTAINER_ID" ]; then
    MOUNT_INFO=$(docker inspect "$CONTAINER_ID" --format '{{range .Mounts}}{{if or (eq .Destination "/data") (eq .Destination "/usr/src/app/upload")}}{{.Source}}:{{.Destination}}{{"\n"}}{{end}}{{end}}' | head -n 1)
fi

if [ -n "$MOUNT_INFO" ]; then
    HOST_PREFIX="${MOUNT_INFO%%:*}"
    CONTAINER_PREFIX="${MOUNT_INFO##*:}"
else
    HOST_PREFIX="${UPLOAD_LOCATION:-/mnt/NAS/Immich-library}"
    CONTAINER_PREFIX="/data"
fi

echo "実行構成の確認:"
echo "  コンテナ側基準パス : ${CONTAINER_PREFIX}"
echo "  ホスト側基準パス   : ${HOST_PREFIX}"
echo "  退避先ディレクトリ : ${DEST_DIR}"
echo ""

if [ ! -d "$DEST_DIR" ]; then
    mkdir -p "$DEST_DIR"
fi

PARENT_DIR=$(dirname "$DEST_DIR")

echo "アルバム「${ALBUM_NAME}」の写真をデータベースから照会中..."

SQL_QUERY="
SELECT a.\"originalPath\"
FROM album al
JOIN album_asset aa ON al.id = aa.\"albumId\"
JOIN asset a ON aa.\"assetId\" = a.id
WHERE al.\"albumName\" = '${ALBUM_NAME}'
  AND al.\"deletedAt\" IS NULL
  AND a.\"deletedAt\" IS NULL;
"

FILE_COUNT=0
COPY_COUNT=0

while IFS= read -r container_path; do
    if [ -n "$container_path" ]; then
        FILE_COUNT=$((FILE_COUNT + 1))
        rel_path="${container_path#${CONTAINER_PREFIX}/}"
        host_path="${HOST_PREFIX}/${rel_path}"

        if [ -f "$host_path" ]; then
            cp -p "$host_path" "$DEST_DIR/"
            COPY_COUNT=$((COPY_COUNT + 1))
            echo -ne "退避中: ${COPY_COUNT} 件完了\r"
        else
            echo ""
            echo "ファイルが見つかりません: ${host_path}"
        fi
    fi
done < <(docker compose exec -T database psql -U "${DB_USER}" -d "${DB_NAME}" -t -A -c "${SQL_QUERY}")

echo ""
echo "ファイル複製完了: 対象 ${FILE_COUNT} 件中、${COPY_COUNT} 件を退避したぞ。"

echo "親ディレクトリ（${PARENT_DIR}）の権限設定を退避先へ反映中..."

if [ -d "$PARENT_DIR" ]; then
    chown --reference="$PARENT_DIR" -R "$DEST_DIR" 2>/dev/null || echo "警告: 所有者の変更に失敗しました（管理者権限が必要な場合があります）。"
    chmod --reference="$PARENT_DIR" "$DEST_DIR" 2>/dev/null || true

    if command -v getfacl >/dev/null 2>&1 && command -v setfacl >/dev/null 2>&1; then
        getfacl -p "$PARENT_DIR" 2>/dev/null | setfacl -R --set-file=- "$DEST_DIR" 2>/dev/null || true
        getfacl -p -d "$PARENT_DIR" 2>/dev/null | setfacl -R -d -M - "$DEST_DIR" 2>/dev/null || true
    fi
    echo "権限の同期処理が完了したぞ。"
else
    echo "親ディレクトリが見つからないため、権限の同期をスキップしました。"
fi

echo "すべての処理が正常に完了したぞ。"
