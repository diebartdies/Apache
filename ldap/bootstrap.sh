#!/bin/sh
set -eu

LDAP_ORGANISATION="${LDAP_ORGANISATION:-drsrv}"
LDAP_DOMAIN="${LDAP_DOMAIN:-drsrv.net.ar}"
LDAP_BASE_DN="${LDAP_BASE_DN:-}"
LDAP_ADMIN_PASSWORD="${LDAP_ADMIN_PASSWORD:-change-me-now}"
LDAP_TLS_CN="${LDAP_TLS_CN:-localhost}"
LDAP_DEFAULT_USER="${LDAP_DEFAULT_USER:-}"
LDAP_DEFAULT_PASSWORD="${LDAP_DEFAULT_PASSWORD:-}"

if [ -z "$LDAP_BASE_DN" ]; then
    OLD_IFS="$IFS"
    IFS='.'
    set -- $LDAP_DOMAIN
    IFS="$OLD_IFS"
    LDAP_BASE_DN=""
    for part in "$@"; do
        if [ -n "$LDAP_BASE_DN" ]; then
            LDAP_BASE_DN="$LDAP_BASE_DN,"
        fi
        LDAP_BASE_DN="${LDAP_BASE_DN}DC=${part}"
    done
fi

DB_DIR="/var/lib/openldap/openldap-data"
CERT_DIR="/etc/openldap/certs"
RUN_DIR="/run/openldap"
CONF_FILE="/etc/openldap/slapd.conf"
STAMP_FILE="$DB_DIR/.bootstrapped"

mkdir -p "$DB_DIR" "$CERT_DIR" "$RUN_DIR"
chown -R ldap:ldap "$DB_DIR" "$CERT_DIR" "$RUN_DIR"

CERT_FILE="$CERT_DIR/server.crt"
KEY_FILE="$CERT_DIR/server.key"
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    openssl req -x509 -nodes -newkey rsa:2048 \
        -days 3650 \
        -subj "/CN=${LDAP_TLS_CN}" \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE"
    chown ldap:ldap "$CERT_FILE" "$KEY_FILE"
    chmod 600 "$KEY_FILE"
fi

ROOTPW_HASH="$(slappasswd -s "$LDAP_ADMIN_PASSWORD")"

cat > "$CONF_FILE" <<EOF
include         /etc/openldap/schema/core.schema
include         /etc/openldap/schema/cosine.schema
include         /etc/openldap/schema/inetorgperson.schema
include         /etc/openldap/schema/nis.schema

pidfile         $RUN_DIR/slapd.pid
argsfile        $RUN_DIR/slapd.args

modulepath      /usr/lib/openldap
moduleload      back_mdb

TLSCertificateFile $CERT_FILE
TLSCertificateKeyFile $KEY_FILE
TLSCACertificateFile $CERT_FILE

loglevel stats
sizelimit 500

database mdb
maxsize 1073741824
suffix "$LDAP_BASE_DN"
rootdn "cn=admin,$LDAP_BASE_DN"
rootpw $ROOTPW_HASH
directory $DB_DIR
index objectClass eq
index uid,cn,sn,mail eq,pres,sub

access to attrs=userPassword
    by self write
    by anonymous auth
    by * none

access to *
    by self read
    by users read
    by anonymous auth
EOF

if [ ! -f "$STAMP_FILE" ]; then
    cat > /tmp/bootstrap.ldif <<EOF

dn: $LDAP_BASE_DN
objectClass: top
objectClass: dcObject
objectClass: organization
o: $LDAP_ORGANISATION
dc: $(printf '%s' "$LDAP_DOMAIN" | cut -d. -f1)

dn: ou=users,$LDAP_BASE_DN
objectClass: organizationalUnit
ou: users

dn: ou=groups,$LDAP_BASE_DN
objectClass: organizationalUnit
ou: groups
EOF

    if [ -n "$LDAP_DEFAULT_USER" ] && [ -n "$LDAP_DEFAULT_PASSWORD" ]; then
        USER_HASH="$(slappasswd -s "$LDAP_DEFAULT_PASSWORD")"
        cat >> /tmp/bootstrap.ldif <<EOF

dn: uid=$LDAP_DEFAULT_USER,ou=users,$LDAP_BASE_DN
objectClass: inetOrgPerson
objectClass: posixAccount
objectClass: shadowAccount
cn: $LDAP_DEFAULT_USER
sn: $LDAP_DEFAULT_USER
uid: $LDAP_DEFAULT_USER
uidNumber: 10000
gidNumber: 10000
homeDirectory: /home/$LDAP_DEFAULT_USER
loginShell: /bin/sh
mail: $LDAP_DEFAULT_USER@$LDAP_DOMAIN
userPassword: $USER_HASH
EOF
    fi

    slapadd -f "$CONF_FILE" -l /tmp/bootstrap.ldif
    chown -R ldap:ldap "$DB_DIR"
    touch "$STAMP_FILE"
fi

exec /usr/sbin/slapd -h "ldap:/// ldaps:///" -f "$CONF_FILE" -u ldap -g ldap -d 0
