#!/bin/sh
set -eu

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
: "${PROCUREMENT_DATABASE_USER:?PROCUREMENT_DATABASE_USER is required}"
: "${PROCUREMENT_DATABASE_PASSWORD:?PROCUREMENT_DATABASE_PASSWORD is required}"

valid_secret() {
    value=$1
    [ "${#value}" -ge 32 ] || return 1
    case "$value" in
        *[!A-Za-z0-9._-]*) return 1 ;;
    esac
}

case "$PROCUREMENT_DATABASE_USER" in
    *[!A-Za-z0-9_-]*) echo "database usernames may contain only letters, digits, _ and -" >&2; exit 1 ;;
esac
valid_secret "$PROCUREMENT_DATABASE_PASSWORD" || { echo "PROCUREMENT_DATABASE_PASSWORD must be a 32+ character safe secret" >&2; exit 1; }

mysql --protocol=socket -uroot "-p$MYSQL_ROOT_PASSWORD" <<SQL
CREATE USER IF NOT EXISTS '$PROCUREMENT_DATABASE_USER'@'%' IDENTIFIED BY '$PROCUREMENT_DATABASE_PASSWORD';
ALTER USER '$PROCUREMENT_DATABASE_USER'@'%' IDENTIFIED BY '$PROCUREMENT_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON caijiatai_business.* TO '$PROCUREMENT_DATABASE_USER'@'%';
FLUSH PRIVILEGES;
SQL
