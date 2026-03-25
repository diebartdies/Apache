#!/bin/sh
set -eu

start_easydns_ddns() {
	# Optional EasyDNS Dynamic DNS updater.
	# Enabled when EASYDNS_HOSTNAME, EASYDNS_USERNAME, and EASYDNS_PASSWORD are set.
	if [ -z "${EASYDNS_HOSTNAME:-}" ] || [ -z "${EASYDNS_USERNAME:-}" ] || [ -z "${EASYDNS_PASSWORD:-}" ]; then
		echo "EasyDNS DDNS: disabled (set EASYDNS_HOSTNAME/EASYDNS_USERNAME/EASYDNS_PASSWORD to enable)"
		return 0
	fi

	if ! command -v ddclient >/dev/null 2>&1; then
		echo "EasyDNS DDNS: ddclient not installed"
		return 1
	fi

	conf="/etc/ddclient.conf"
	umask 077

	server="${EASYDNS_SERVER:-api.cp.easydns.com}"
	interval="${EASYDNS_INTERVAL_SECONDS:-300}"

	cat >"$conf" <<EOF
daemon=$interval
pid=/var/run/ddclient.pid
ssl=yes

use=web, web=icanhazip.com/, web-skip=''

protocol=dyndns2
server=$server
login=$EASYDNS_USERNAME
password=$EASYDNS_PASSWORD

$EASYDNS_HOSTNAME
EOF

	chmod 600 "$conf" || true

	echo "EasyDNS DDNS: enabled (hostname=$EASYDNS_HOSTNAME, server=$server, interval=${interval}s)"
	ddclient -foreground -file "$conf" &
}

start_easydns_ddns

if [ "${SYNC_DISCS_ON_STARTUP:-1}" = "1" ]; then
	echo "PostgreSQL sync: attempting startup sync"
	if ! /opt/venv/bin/python /opt/webapp/disc_sync.py; then
		echo "PostgreSQL sync: startup sync failed (continuing)"
	fi
else
	echo "PostgreSQL sync: disabled (set SYNC_DISCS_ON_STARTUP=1 to enable)"
fi

# Start the Python web.py app inside the same container.
/opt/venv/bin/python /opt/webapp/app.py &

# Run Apache in foreground.
exec httpd-foreground
