#!/usr/bin/env bash
# 启动 mihomo；目标站点筛选和切换由 utils/proxy.py 在签到时完成。
set -euo pipefail

if [[ -z "${PROXY_SUBSCRIPTION_URL:-}" ]]; then
	echo "[INFO] PROXY_SUBSCRIPTION_URL not set, skip proxy setup"
	exit 0
fi

umask 077
PROXY_DIR="${RUNNER_TEMP:-/tmp}/checkin-proxy"
PROXY_PORT="${PROXY_PORT:-7890}"
PROXY_CONTROLLER_PORT="${PROXY_CONTROLLER_PORT:-9090}"
MIHOMO_VERSION="${MIHOMO_VERSION:-v1.19.27}"
CONTROLLER_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export PROXY_PORT PROXY_CONTROLLER_PORT CONTROLLER_SECRET

if [[ -n "${GITHUB_ENV:-}" ]]; then
	echo "CHECKIN_PROXY_CONFIGURED=true" >> "${GITHUB_ENV}"
	echo "::add-mask::${CONTROLLER_SECRET}"
fi

mkdir -p "${PROXY_DIR}"
cd "${PROXY_DIR}"

failed() {
	echo "[FAILED] Proxy setup failed; accounts requiring the subscription will not fall back to direct access"
	if [[ -f mihomo.pid ]]; then
		kill "$(cat mihomo.pid)" 2>/dev/null || true
	fi
	if [[ "${PROXY_REQUIRED:-false}" == "true" ]]; then exit 1; fi
	exit 0
}
trap failed ERR

echo "[INFO] Downloading mihomo ${MIHOMO_VERSION}..."
ARCHIVE="mihomo-linux-amd64-${MIHOMO_VERSION}.gz"
curl --retry 3 --retry-delay 5 --retry-all-errors --connect-timeout 15 --max-time 120 -fsSL \
	-o "${ARCHIVE}" "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${ARCHIVE}" || failed
gunzip -f "${ARCHIVE}"
chmod +x "mihomo-linux-amd64-${MIHOMO_VERSION}"

# JSON 是合法 YAML，订阅 URL 由序列化器转义，避免插值破坏配置。
python3 - <<'PY'
import json, os
config = {
    'mixed-port': int(os.environ['PROXY_PORT']),
    'external-controller': '127.0.0.1:' + os.environ['PROXY_CONTROLLER_PORT'],
    'secret': os.environ['CONTROLLER_SECRET'],
    'allow-lan': False, 'ipv6': False, 'mode': 'rule', 'log-level': 'warning',
    'proxy-providers': {'subscription': {
        'type': 'http', 'url': os.environ['PROXY_SUBSCRIPTION_URL'],
        'interval': 3600, 'path': './subscription.yaml',
    }},
    'proxy-groups': [{'name': 'CHECKIN', 'type': 'select', 'use': ['subscription']}],
    'rules': ['MATCH,CHECKIN'],
}
with open('config.yaml', 'w') as stream:
    json.dump(config, stream)
PY

echo "[INFO] Starting mihomo on 127.0.0.1:${PROXY_PORT}..."
nohup "${PROXY_DIR}/mihomo-linux-amd64-${MIHOMO_VERSION}" -d "${PROXY_DIR}" -f config.yaml > mihomo.log 2>&1 &
echo $! > mihomo.pid

CONTROLLER_URL="http://127.0.0.1:${PROXY_CONTROLLER_PORT}"
READY=false
for attempt in $(seq 1 45); do
	if curl -fsS --max-time 3 -H "Authorization: Bearer ${CONTROLLER_SECRET}" \
		"${CONTROLLER_URL}/proxies/CHECKIN" 2>/dev/null | \
		python3 -c 'import json,sys; sys.exit(not any(n not in {"DIRECT", "REJECT", "REJECT-DROP", "PASS"} for n in json.load(sys.stdin).get("all", [])))' 2>/dev/null; then
		READY=true
		break
	fi
	sleep 2
done
[[ "${READY}" == "true" ]] || failed

echo "[SUCCESS] Subscription loaded; target checks will run before login"
if [[ -n "${GITHUB_ENV:-}" ]]; then
	echo "CHECKIN_PROXY_URL=http://127.0.0.1:${PROXY_PORT}" >> "${GITHUB_ENV}"
	echo "CHECKIN_PROXY_CONTROLLER=${CONTROLLER_URL}" >> "${GITHUB_ENV}"
	echo "CHECKIN_PROXY_SECRET=${CONTROLLER_SECRET}" >> "${GITHUB_ENV}"
fi
