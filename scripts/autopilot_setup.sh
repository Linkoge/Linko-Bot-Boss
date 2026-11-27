#!/usr/bin/env bash
# Autopilot setup for ONE dedicated machine (docs/AUTOPILOT.md). The owner runs it once, by hand, with sudo:
#   sudo bash scripts/autopilot_setup.sh [--user USER] [--env-file PATH] [--unit NAME] [--worker USER] [--yes]
#   sudo bash scripts/autopilot_setup.sh --remove     undoes everything below (etckeeper stays installed)
# It sets both local locks and nothing else: the env line AI_MANAGER_AUTOPILOT=full, the sudoers drop-in for the
# service user (checked with visudo -cf before it is installed), the systemd user-unit override NoNewPrivileges=no,
# etckeeper when apt is there; then it reloads systemd and restarts only this machine's service. Idempotent.
# It refuses on a restricted instance and on the owner's Main (AI_MANAGER_MACHINE_ROLE=main in the env or env file).
# Tests: --root DIR prefixes every system path and allows running without root; --dry-run only prints the plan;
# VISUDO, SYSTEMCTL, APT_GET, ETCKEEPER and RUNUSER replace the tools.
set -euo pipefail

ENV_LINE="AI_MANAGER_AUTOPILOT=full"
SUDOERS_NAME="linko-autopilot"
USER_NAME="${SUDO_USER:-}" ENV_FILE="" UNIT="linko-bot-manager" WORKER="" YES=0 REMOVE=0 DRY=0
ROOT="${AUTOPILOT_SETUP_ROOT:-}"
VISUDO="${VISUDO:-visudo}" SYSTEMCTL="${SYSTEMCTL:-systemctl}" APT_GET="${APT_GET:-apt-get}"
ETCKEEPER="${ETCKEEPER:-etckeeper}" RUNUSER="${RUNUSER:-runuser}"

die() { echo "autopilot_setup: $*" >&2; exit 1; }
say() { echo "$*"; }
usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --user) USER_NAME="${2:?--user needs a name}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file needs a path}"; shift 2 ;;
    --unit) UNIT="${2:?--unit needs a name}"; shift 2 ;;
    --worker) WORKER="${2:?--worker needs a name}"; shift 2 ;;
    --root) ROOT="${2:?--root needs a directory}"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --remove) REMOVE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 2 ;;
  esac
done

valid_name() { [[ "$1" =~ ^[a-z_][a-z0-9_.-]*$ ]]; }
[ -n "$USER_NAME" ] || die "no service user: run it with sudo from the service user's account, or pass --user USER"
valid_name "$USER_NAME" || die "invalid user name: $USER_NAME"
[ "$USER_NAME" != root ] || die "the service user must not be root (pass --user USER)"
[ -z "$WORKER" ] || valid_name "$WORKER" || die "invalid worker user name: $WORKER"
[[ "$UNIT" =~ ^[A-Za-z0-9@_.:-]+$ ]] || die "invalid unit name: $UNIT"
if [ -z "$ROOT" ] && [ "$(id -u)" != 0 ]; then
  die "needs root: sudo bash $0"
fi
PASSWD="$(getent passwd "$USER_NAME")" || die "no such user: $USER_NAME"
USER_UID="$(cut -d: -f3 <<<"$PASSWD")"
HOME_DIR="$ROOT$(cut -d: -f6 <<<"$PASSWD")"
[ -n "$ENV_FILE" ] || ENV_FILE="$HOME_DIR/.config/linko-bot-manager.env"
SUDOERS="$ROOT/etc/sudoers.d/$SUDOERS_NAME"
OVERRIDE_DIR="$HOME_DIR/.config/systemd/user/${UNIT}.service.d"
OVERRIDE="$OVERRIDE_DIR/autopilot.conf"
OVERRIDE_TEXT=$'[Service]\nNoNewPrivileges=no'

env_value() {  # the last NAME=value in the env file (the service reads the file the same way)
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" | tail -n 1 | tr -d "\"' \r"
}

# Refusals: never on a restricted instance, never on Main. Setup only; --remove is always allowed.
if [ "$REMOVE" = 0 ]; then
  restricted="${AI_MANAGER_RESTRICTED:-}"; [ -n "$restricted" ] || restricted="$(env_value AI_MANAGER_RESTRICTED)"
  [ "$restricted" != 1 ] || die "refused: this is a restricted instance (AI_MANAGER_RESTRICTED=1); Autopilot never runs there"
  role="${AI_MANAGER_MACHINE_ROLE:-}"; [ -n "$role" ] || role="$(env_value AI_MANAGER_MACHINE_ROLE)"
  [ "${role,,}" != main ] || die "refused: this is the owner's Main (AI_MANAGER_MACHINE_ROLE=main); use a dedicated machine"
fi

act() {  # act DESCRIPTION COMMAND...: run it, or only print it with --dry-run
  local what="$1"; shift
  if [ "$DRY" = 1 ]; then say "would: $what"; else "$@"; fi
}
as_user() {  # run as the service user with its systemd user manager
  if [ "$(id -u)" = "$USER_UID" ]; then "$@"
  else "$RUNUSER" -u "$USER_NAME" -- env XDG_RUNTIME_DIR="/run/user/$USER_UID" \
         DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus" "$@"
  fi
}
owned() { [ -n "$ROOT" ] && [ "$(id -u)" != 0 ] && return 0; chown "$USER_NAME": "$@"; }  # test roots: already ours

write_env_line() {
  mkdir -p "$(dirname "$ENV_FILE")"
  if [ ! -f "$ENV_FILE" ]; then (umask 077; : >"$ENV_FILE"); owned "$ENV_FILE"; fi
  if grep -qx "$ENV_LINE" "$ENV_FILE"; then say "env line: already in $ENV_FILE"; return; fi
  [ ! -s "$ENV_FILE" ] || [ -z "$(tail -c 1 "$ENV_FILE")" ] || echo >>"$ENV_FILE"  # end the last line first
  if grep -q '^AI_MANAGER_AUTOPILOT=' "$ENV_FILE"; then sed -i "s/^AI_MANAGER_AUTOPILOT=.*/$ENV_LINE/" "$ENV_FILE"
  else echo "$ENV_LINE" >>"$ENV_FILE"; fi
  say "env line: $ENV_LINE added to $ENV_FILE"
}

sudoers_text() {
  echo "# Autopilot OS lock (scripts/autopilot_setup.sh); remove with: sudo bash scripts/autopilot_setup.sh --remove"
  echo "$USER_NAME ALL=(ALL:ALL) NOPASSWD: ALL"
  [ -z "$WORKER" ] || echo "$WORKER ALL=(ALL:ALL) NOPASSWD: ALL"
}
install_sudoers() {
  local staged
  if [ -f "$SUDOERS" ] && [ "$(cat "$SUDOERS")" = "$(sudoers_text)" ]; then say "sudoers: $SUDOERS already installed"; return; fi
  staged="$(mktemp)"
  sudoers_text >"$staged"
  if ! "$VISUDO" -cf "$staged" >/dev/null; then rm -f "$staged"; die "visudo rejected the drop-in; nothing installed"; fi
  mkdir -p "$(dirname "$SUDOERS")"
  if [ -n "$ROOT" ] && [ "$(id -u)" != 0 ]; then install -m 0440 "$staged" "$SUDOERS"
  else install -o root -g root -m 0440 "$staged" "$SUDOERS"; fi
  rm -f "$staged"
  "$VISUDO" -cf "$SUDOERS" >/dev/null || { rm -f "$SUDOERS"; die "visudo rejected $SUDOERS after install; removed it"; }
  say "sudoers: $SUDOERS installed and validated"
}

install_override() {
  if [ -f "$OVERRIDE" ] && [ "$(cat "$OVERRIDE")" = "$OVERRIDE_TEXT" ]; then say "systemd override: already in $OVERRIDE"; return; fi
  mkdir -p "$OVERRIDE_DIR"
  printf '%s\n' "$OVERRIDE_TEXT" >"$OVERRIDE"
  owned "$HOME_DIR/.config/systemd" "$HOME_DIR/.config/systemd/user" "$OVERRIDE_DIR" "$OVERRIDE"
  say "systemd override: $OVERRIDE (NoNewPrivileges=no for $UNIT only)"
}

install_etckeeper() {
  if command -v "$ETCKEEPER" >/dev/null; then say "etckeeper: already installed"
  elif command -v "$APT_GET" >/dev/null; then
    DEBIAN_FRONTEND=noninteractive "$APT_GET" install -y etckeeper >/dev/null || { say "etckeeper: install failed; skipped"; return; }
    say "etckeeper: installed"
  else say "etckeeper: apt-get not found; skipped (recommended, see docs/AUTOPILOT.md)"; return
  fi
  "$ETCKEEPER" commit "baseline before autopilot" >/dev/null 2>&1 || true
}

restart_service() {
  as_user "$SYSTEMCTL" --user daemon-reload || say "systemd: daemon-reload failed for $USER_NAME; restart the service yourself"
  as_user "$SYSTEMCTL" --user restart "$UNIT" || die "restart of $UNIT failed; check: systemctl --user status $UNIT"
  say "service: $UNIT restarted"
}

remove_all() {
  if [ -f "$ENV_FILE" ] && grep -q '^AI_MANAGER_AUTOPILOT=' "$ENV_FILE"; then
    act "remove the AI_MANAGER_AUTOPILOT line from $ENV_FILE" sed -i '/^AI_MANAGER_AUTOPILOT=/d' "$ENV_FILE"
    [ "$DRY" = 1 ] || say "env line: removed"
  else say "env line: not present"; fi
  if [ -e "$SUDOERS" ]; then act "delete $SUDOERS" rm -f "$SUDOERS"; [ "$DRY" = 1 ] || say "sudoers: removed"; else say "sudoers: not present"; fi
  if [ -e "$OVERRIDE" ]; then
    act "delete $OVERRIDE" rm -f "$OVERRIDE"
    [ "$DRY" = 1 ] || rmdir "$OVERRIDE_DIR" 2>/dev/null || true
    [ "$DRY" = 1 ] || say "systemd override: removed"
  else say "systemd override: not present"; fi
  act "reload systemd and restart $UNIT for $USER_NAME" restart_service
  say "etckeeper stays installed (it only records /etc history)."
  if [ "$DRY" = 1 ]; then say "dry run: nothing changed"; else say "Autopilot removed"; fi
}

if [ "$REMOVE" = 1 ]; then remove_all; exit 0; fi

say "Autopilot setup on $(hostname) for service user $USER_NAME (unit $UNIT):"
say "  - $ENV_LINE in $ENV_FILE"
say "  - $SUDOERS: passwordless sudo for $USER_NAME${WORKER:+ and $WORKER} (validated with visudo -cf)"
say "  - $OVERRIDE: NoNewPrivileges=no"
say "  - etckeeper (if apt is available), then a restart of $UNIT"
if [ "$DRY" = 1 ]; then say "dry run: nothing changed"; exit 0; fi
if [ "$YES" != 1 ]; then
  read -r -p 'This gives the manager root on this machine. Type "yes" to continue: ' answer || answer=""
  [ "$answer" = yes ] || die "not confirmed; nothing changed"
fi
install_sudoers
write_env_line
install_override
install_etckeeper
restart_service
say "Autopilot ready"
