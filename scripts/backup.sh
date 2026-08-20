#!/usr/bin/env bash
#
# Backup do banco de dados da sondagem.
#
# Uso:
#   ./scripts/backup.sh                 # grava em ./backups/
#   BACKUP_DIR=/mnt/backups ./scripts/backup.sh
#
# Rodar SEMPRE antes de aplicar migrations e antes de qualquer deploy —
# ver docs/PRODUCAO.md seções 4 e 5.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

# Lê só as duas variáveis necessárias, com grep — sem interpretar o .env
# como script. Com `. ./.env` sob `set -u` o .env de produção derruba o
# backup de duas formas: ADMIN_PASSWORD é um hash bcrypt e todo hash
# começa com "$2b$", que o shell expande como parâmetro posicional
# ($2 unbound); e APP_NAME tem espaços sem aspas, então a segunda palavra
# vira um comando inexistente. Descoberto em produção em 20/08/2026 — até
# aqui nenhum backup havia rodado na VPS.
if [ -f .env ] && [ -z "${POSTGRES_USER:-}" ]; then
    POSTGRES_USER=$(grep -m1 '^POSTGRES_USER=' .env | cut -d= -f2-)
fi
if [ -f .env ] && [ -z "${POSTGRES_DB:-}" ]; then
    POSTGRES_DB=$(grep -m1 '^POSTGRES_DB=' .env | cut -d= -f2-)
fi

: "${POSTGRES_USER:?defina POSTGRES_USER (no .env ou no ambiente)}"
: "${POSTGRES_DB:?defina POSTGRES_DB (no .env ou no ambiente)}"

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUTFILE="$BACKUP_DIR/backup-$TIMESTAMP.sql.gz"

echo "Gerando backup em $OUTFILE ..."
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$OUTFILE"

# pg_dump falhando no meio ainda gera um .gz pequeno e válido; conferir o
# tamanho evita guardar um "backup" que na verdade é um erro comprimido.
SIZE=$(stat -c%s "$OUTFILE" 2>/dev/null || stat -f%z "$OUTFILE")
if [ "$SIZE" -lt 1000 ]; then
    echo "ERRO: backup suspeito (${SIZE} bytes). Verifique se o container 'db' está no ar." >&2
    exit 1
fi

echo "Backup concluído: $OUTFILE ($(du -h "$OUTFILE" | cut -f1))"

echo "Removendo backups com mais de $RETENTION_DAYS dias..."
find "$BACKUP_DIR" -name "backup-*.sql.gz" -type f -mtime "+$RETENTION_DAYS" -delete

echo "Backups disponíveis:"
ls -lh "$BACKUP_DIR"/backup-*.sql.gz 2>/dev/null || echo "(nenhum)"

cat << 'EOF'

LEMBRETE: copie este backup para fora desta máquina (S3, Backblaze, etc).
Backup guardado só no mesmo servidor não protege contra perda do servidor.
E teste o restore pelo menos uma vez — ver docs/PRODUCAO.md seção 5.
EOF
