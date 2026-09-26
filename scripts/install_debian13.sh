#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
INSTALL_MODE="install"
if [[ "${1:-}" == "--enable-timer" ]]; then
    INSTALL_MODE="enable-timer"
elif [[ "${1:-}" == "--check" ]]; then
    INSTALL_MODE="check"
elif [[ "${1:-}" == "--set-credential" && $# -eq 2 ]]; then
    INSTALL_MODE="set-credential"
elif [[ $# -gt 0 ]]; then
    printf 'Usage: sudo bash scripts/install_debian13.sh [--check|--enable-timer|--set-credential NAME]\n' >&2
    exit 2
fi

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        printf 'Run this script with sudo/root.\n' >&2
        exit 1
    fi
}

check_debian13() {
    [[ -r /etc/os-release ]] || { printf 'Cannot identify operating system.\n' >&2; exit 1; }
    # shellcheck disable=SC1091
    . /etc/os-release
    if [[ "${ID:-}" != debian || "${VERSION_ID:-}" != 13 ]]; then
        printf 'This installer supports Debian 13 only.\n' >&2
        exit 1
    fi
}

check_installation() {
    local provider credential
    [[ -x "$PROJECT_DIR/.venv/bin/python" ]] || { printf 'Missing project virtual environment.\n' >&2; return 1; }
    [[ -r /etc/newsdaily/config ]] || { printf 'Missing /etc/newsdaily/config.\n' >&2; return 1; }
    provider="$(sed -n 's/^CREDENTIALS_MODE=//p' /etc/newsdaily/config | tail -n 1)"
    [[ "$provider" == file ]] || { printf 'CREDENTIALS_MODE must be file.\n' >&2; return 1; }
    provider="$("$PROJECT_DIR/.venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("model_provider", "deepseek"))' "$PROJECT_DIR/config/settings.json")"
    "$PROJECT_DIR/.venv/bin/python" -B -c 'import json; from app.pipeline import _model_profile; _model_profile(json.load(open("config/settings.json", encoding="utf-8")))' >/dev/null
    case "$provider" in
        deepseek) credential=deepseek_api_key ;;
        openai) credential=openai_api_key ;;
        qwen) credential=dashscope_api_key ;;
        *) printf 'Unsupported model_provider in config/settings.json.\n' >&2; return 1 ;;
    esac
    for credential in "$credential" smtp_auth_code sender_email recipient_email; do
        if [[ ! -f "/etc/newsdaily/credentials/$credential" ]]; then
            printf 'Missing required credential file: %s\n' "$credential" >&2
            return 1
        fi
        if [[ "$(stat -c '%u:%a' "/etc/newsdaily/credentials/$credential")" != '0:600' ]]; then
            printf 'Credential file must be root-owned mode 0600: %s\n' "$credential" >&2
            return 1
        fi
    done
    if [[ "$(stat -c '%u:%a' /etc/newsdaily/credentials)" != '0:700' ]]; then
        printf 'Credential directory must be root-owned mode 0700.\n' >&2
        return 1
    fi
    CREDENTIALS_MODE=file CREDENTIALS_DIRECTORY=/etc/newsdaily/credentials \
        "$PROJECT_DIR/.venv/bin/python" -B -c 'import json; from app.credentials import CredentialProvider; s=json.load(open("config/settings.json", encoding="utf-8")); n={"deepseek":"DEEPSEEK_API_KEY","openai":"OPENAI_API_KEY","qwen":"DASHSCOPE_API_KEY"}[s["model_provider"]]; c=CredentialProvider(); [c.get(k) for k in (n,"SMTP_AUTH_CODE","MAIL_FROM","MAIL_TO")]' >/dev/null
    "$PROJECT_DIR/.venv/bin/python" -m pip check >/dev/null
    runuser -u newsdaily -- "$PROJECT_DIR/.venv/bin/python" -B -m app.cli status >/dev/null
    systemctl start newsdaily-check-credentials.service
    printf 'Preflight passed (provider=%s); no credential values displayed.\n' "$provider"
}

set_credential() {
    local name="$1" value confirmation temporary
    case "$name" in
        deepseek_api_key|openai_api_key|dashscope_api_key|smtp_auth_code|sender_email|recipient_email) ;;
        *) printf 'Unsupported credential name.\n' >&2; return 2 ;;
    esac
    install -d -o root -g root -m 0700 /etc/newsdaily/credentials
    read -r -s -p "Enter $name (input hidden): " value
    printf '\n'
    read -r -s -p 'Re-enter to confirm: ' confirmation
    printf '\n'
    if [[ -z "$value" || "$value" != "$confirmation" || "$value" != "${value//$'\n'/}" || "$value" != "${value//$'\r'/}" ]]; then
        unset value confirmation
        printf 'Credential was not changed; values were empty or did not match.\n' >&2
        return 1
    fi
    if [[ "$name" == *_email ]] && [[ "$value" != *@* || "$value" == *[[:space:],\;\<\>]* ]]; then
        unset value confirmation
        printf 'Credential was not changed; enter one email address.\n' >&2
        return 1
    fi
    temporary="$(mktemp /etc/newsdaily/credentials/.credential.XXXXXX)"
    chmod 0600 "$temporary"
    printf '%s' "$value" > "$temporary"
    chown root:root "$temporary"
    mv -f -- "$temporary" "/etc/newsdaily/credentials/$name"
    unset value confirmation
    printf 'Credential installed: %s (value not displayed).\n' "$name"
}

require_root
check_debian13
cd "$PROJECT_DIR"

if [[ "$INSTALL_MODE" == check ]]; then
    check_installation
    exit
fi

if [[ "$INSTALL_MODE" == set-credential ]]; then
    set_credential "$2"
    exit
fi

if [[ "$INSTALL_MODE" == enable-timer ]]; then
    check_installation
    if [[ "$(systemctl is-enabled newsdaily-send.timer 2>/dev/null || true)" == enabled ]]; then
        if systemctl is-enabled --quiet newsdaily-preview.timer; then
            systemctl disable --now newsdaily-preview.timer
        fi
        printf 'Formal-send timer is already enabled.\n'
        exit 0
    fi
    printf 'This enables real daily email delivery at 08:00 Asia/Shanghai.\n'
    read -r -p 'After reviewing a successful preflight and test email, type ENABLE to proceed: ' confirmation
    [[ "$confirmation" == ENABLE ]] || { printf 'Timer remains disabled.\n'; exit 1; }
    if systemctl is-enabled --quiet newsdaily-preview.timer; then
        systemctl disable --now newsdaily-preview.timer
    fi
    systemctl enable --now newsdaily-send.timer
    printf 'Formal-send timer enabled.\n'
    exit
fi

command -v apt-get >/dev/null || { printf 'apt-get is required.\n' >&2; exit 1; }
if [[ "$PROJECT_DIR" != /opt/newsdaily ]]; then
    printf 'Upload the package to /opt/newsdaily first (detected %s).\n' "$PROJECT_DIR" >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates

if ! getent passwd newsdaily >/dev/null; then
    useradd --system --user-group --home-dir "$PROJECT_DIR" --no-create-home --shell /usr/sbin/nologin newsdaily
fi
install -d -o root -g root -m 0755 /opt/newsdaily
install -d -o newsdaily -g newsdaily -m 0700 "$PROJECT_DIR/data" "$PROJECT_DIR/output"
chown newsdaily:newsdaily "$PROJECT_DIR/data" "$PROJECT_DIR/output"
find "$PROJECT_DIR/data" "$PROJECT_DIR/output" -maxdepth 1 -type f -exec chown newsdaily:newsdaily -- {} +

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$PROJECT_DIR/.venv"
fi
"$PROJECT_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_DIR/requirements.lock"
"$PROJECT_DIR/.venv/bin/python" -m pip check
chown -R root:root "$PROJECT_DIR/.venv"
chmod -R go-w "$PROJECT_DIR/.venv"
chmod -R go+rX "$PROJECT_DIR/.venv"

install -d -o root -g root -m 0755 /etc/newsdaily
install -d -o root -g root -m 0700 /etc/newsdaily/credentials
if [[ ! -e /etc/newsdaily/config ]]; then
    install -o root -g root -m 0644 "$PROJECT_DIR/.env.example" /etc/newsdaily/config
else
    printf 'Preserving existing /etc/newsdaily/config.\n'
fi

for unit in "$PROJECT_DIR"/systemd/*.service "$PROJECT_DIR"/systemd/*.timer; do
    target="/etc/systemd/system/$(basename "$unit")"
    if [[ -e "$target" ]] && ! cmp -s "$unit" "$target"; then
        backup="${target}.newsdaily-backup-$(date -u +%Y%m%dT%H%M%SZ)"
        cp -a -- "$target" "$backup"
        printf 'Backed up previous systemd unit: %s\n' "$backup"
    fi
    install -o root -g root -m 0644 "$unit" "$target"
done
systemctl daemon-reload

if [[ -e "$PROJECT_DIR/data/news.sqlite3" ]]; then
    printf 'Preserving existing database; init-db only adds missing sources.\n'
else
    printf 'Initializing the new database.\n'
fi
runuser -u newsdaily -- "$PROJECT_DIR/.venv/bin/python" -B -m app.cli init-db
runuser -u newsdaily -- "$PROJECT_DIR/.venv/bin/python" -B -m app.cli status

printf '\nInstallation complete. No timer was enabled or started.\n'
printf 'Enter root-only credentials, then run: sudo bash %s --check\n' "$PROJECT_DIR/scripts/install_debian13.sh"
