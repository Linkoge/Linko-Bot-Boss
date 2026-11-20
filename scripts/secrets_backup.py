"""Encrypted, passphrase-protected backup of the server's private credential files.

    uv run python scripts/secrets_backup.py backup TARGET [--file PATH ...] [--config FILE]
    uv run python scripts/secrets_backup.py restore BACKUP [--dry-run]

TARGET is a directory (the file is written as secrets-backup.age inside it) or a file path.
The result is one encrypted file. Nothing is written to disk unencrypted except inside a
temporary directory that is removed before the command returns.

Encryption is the system `openssl` CLI (AES-256-CBC, PBKDF2-SHA256, salted). The passphrase reaches
openssl only through an environment variable of the child process, never argv, a file or a log.
Format (binary): MAGIC | mac_salt (16) | HMAC-SHA256 tag (32) | openssl output (a tar archive).
The tag covers MAGIC, mac_salt and the openssl output; its key is pbkdf2_hmac(passphrase, mac_salt).
It is verified before openssl decrypts anything, so a wrong passphrase or a damaged file fails
cleanly with no files touched.

The passphrase comes from LINKO_SECRETS_PASSPHRASE or an interactive prompt. It is never
written to disk, logged or echoed.
"""
import argparse
import getpass
import grp
import hashlib
import hmac
import io
import json
import os
import pwd
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

MAGIC = b"LINKO-SECRETS-2\n"
MAC_SALT_BYTES = 16
TAG_BYTES = 32
PBKDF2_ITERATIONS = 600000
OPENSSL_CIPHER = ["-aes-256-cbc", "-pbkdf2", "-iter", str(PBKDF2_ITERATIONS), "-salt", "-md", "sha256"]
OPENSSL_PASS_CHILD_ENV = "LINKO_SECRETS_OPENSSL_PASS"  # set only in the openssl child's environment
DEFAULT_BACKUP_NAME = "secrets-backup.age"
PASSPHRASE_ENV = "LINKO_SECRETS_PASSPHRASE"
MIN_PASSPHRASE = 12
DEFAULT_CONFIG = Path("~/.config/linko-secrets-backup.txt")
DEFAULT_FILES = ("~/.config/linko-onboarding.env", "~/.config/linko-bot-manager.env")
# Everything else a new machine needs, backed up when present (absent ones are skipped with a note):
# file-based provider logins, the Tailscale auth key, the GitHub token and the SSH deploy key for backup pushes.
OPTIONAL_FILES = (
    "~/.claude/.credentials.json",
    "~/.codex/auth.json",
    "~/.config/linko-tailscale-authkey",
    "~/.config/linko-github-token",
    "~/.ssh/linko-deploy-key",
    "~/.ssh/linko-deploy-key.pub",
)
# Provider logins are also put into these accounts' homes on restore (when the account exists).
REPLICATE_PATHS = ("~/.claude/.credentials.json", "~/.codex/auth.json")
REPLICATE_USERS = ("linko-worker", "root")
ENV_TOKEN_FILE_KEYS = ("AI_MANAGER_GITHUB_TOKEN_FILE", "AI_MANAGER_GITHUB_COMPANY_TOKEN_FILE")
MANIFEST = "manifest.json"


class BackupError(Exception):
    """A user-facing failure. The message never contains the passphrase or file contents."""


def home():
    return Path(os.environ.get("HOME") or Path.home())


def display_path(path):
    """Store paths under $HOME as ~/... so a restore on a fresh VM lands in that VM's home."""
    absolute = Path(path).expanduser().absolute()
    try:
        return "~/" + absolute.relative_to(home()).as_posix()
    except ValueError:
        return absolute.as_posix()


def resolve_stored(stored):
    if stored.startswith("~/"):
        return home() / stored[2:]
    return Path(stored)


def open_private(path):
    """Create or truncate a file readable by its owner only, from the first byte on."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb")


def read_passphrase(confirm):
    value = os.environ.get(PASSPHRASE_ENV)
    if value is None:
        if not sys.stdin.isatty():
            raise BackupError(f"no passphrase: set {PASSPHRASE_ENV} or run interactively")
        value = getpass.getpass("Backup passphrase: ")
        if confirm and getpass.getpass("Repeat passphrase: ") != value:
            raise BackupError("the two passphrases do not match; nothing written")
    if confirm and len(value) < MIN_PASSPHRASE:
        raise BackupError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
    if not value:
        raise BackupError("passphrase is empty")
    return value


def openssl_binary():
    path = shutil.which("openssl")
    if path is None:
        raise BackupError("openssl not found: install the openssl command-line tool (e.g. `sudo apt install openssl`)")
    return path


def run_openssl(decrypt, passphrase, src, dst):
    """Run `openssl enc`. The passphrase travels only in the child's environment (-pass env:NAME)."""
    args = [openssl_binary(), "enc", *(["-d"] if decrypt else []), *OPENSSL_CIPHER,
            "-pass", f"env:{OPENSSL_PASS_CHILD_ENV}", "-in", str(src), "-out", str(dst)]
    env = {"PATH": os.environ.get("PATH", ""), OPENSSL_PASS_CHILD_ENV: passphrase}
    try:
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise BackupError("openssl timed out; nothing was written") from None
    if result.returncode != 0:
        raise BackupError("openssl could not decrypt the backup; nothing was restored" if decrypt
                          else "openssl failed to encrypt; no backup was written")


def mac_key(passphrase, mac_salt):
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), mac_salt, PBKDF2_ITERATIONS, 32)


def compute_tag(passphrase, header, ciphertext):
    return hmac.new(mac_key(passphrase, header[len(MAGIC):]), header + ciphertext, hashlib.sha256).digest()


def seal(plain_path, passphrase, scratch):
    """Encrypt plain_path and return the complete backup blob."""
    cipher_path = Path(scratch) / "cipher.bin"
    run_openssl(False, passphrase, plain_path, cipher_path)
    ciphertext = cipher_path.read_bytes()
    header = MAGIC + secrets.token_bytes(MAC_SALT_BYTES)
    return header + compute_tag(passphrase, header, ciphertext) + ciphertext


def unseal(blob, passphrase, scratch):
    """Verify the tag, then decrypt into scratch and return the plaintext archive path."""
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + MAC_SALT_BYTES + TAG_BYTES:
        raise BackupError("not a Linko secrets backup (unknown header)")
    header_end = len(MAGIC) + MAC_SALT_BYTES
    header, tag, ciphertext = blob[:header_end], blob[header_end:header_end + TAG_BYTES], blob[header_end + TAG_BYTES:]
    if not hmac.compare_digest(tag, compute_tag(passphrase, header, ciphertext)):
        raise BackupError("wrong passphrase, or the backup file is damaged; nothing was restored")
    cipher_path = Path(scratch) / "cipher.bin"
    with open_private(cipher_path) as stream:
        stream.write(ciphertext)
    plain_path = Path(scratch) / "plain.tar"
    run_openssl(True, passphrase, cipher_path, plain_path)
    return plain_path


def read_list_file(path):
    """One path per line; blank lines and # comments are ignored."""
    entries = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def env_token_files():
    """GitHub token files named by the manager env file (AI_MANAGER_GITHUB_*TOKEN_FILE=path)."""
    env_file = home() / ".config/linko-bot-manager.env"
    found = []
    try:
        lines = env_file.read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        key, _, value = line.partition("=")
        value = value.strip().strip("\"'")
        if key.strip() in ENV_TOKEN_FILE_KEYS and value:
            found.append(Path(value).expanduser())
    return found


def collect_sources(extra, config):
    """Return [(path, required)]: the default files plus anything from the config file or --file.
    Defaults that do not exist are skipped with a warning; requested paths must exist."""
    sources = [(Path(p).expanduser(), False) for p in DEFAULT_FILES + OPTIONAL_FILES]
    sources += [(p, False) for p in env_token_files()]
    if config is not None:
        if not Path(config).expanduser().is_file():
            raise BackupError(f"config file not found: {config}")
        sources += [(Path(p).expanduser(), True) for p in read_list_file(Path(config).expanduser())]
    sources += [(Path(p).expanduser(), True) for p in (extra or [])]
    return sources


def owner_names(stat_result):
    try:
        user = pwd.getpwuid(stat_result.st_uid).pw_name
    except KeyError:
        user = str(stat_result.st_uid)
    try:
        group = grp.getgrgid(stat_result.st_gid).gr_name
    except KeyError:
        group = str(stat_result.st_gid)
    return user, group


def build_archive(sources, out):
    """Write the tar (manifest + file bodies) to `out`, a binary file object. Returns entry count."""
    entries = []
    archived = []
    seen = set()
    for path, required in sources:
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            if required:
                raise BackupError(f"secret file not found: {path}")
            print(f"skipped (not present): {path}", file=sys.stderr)
            continue
        info = path.stat()
        user, group = owner_names(info)
        name = f"data/{len(entries)}"
        entries.append({
            "path": display_path(path),
            "member": name,
            "mode": stat.S_IMODE(info.st_mode) & 0o777,
            "owner": user,
            "group": group,
        })
        if any(path == Path(p).expanduser() for p in REPLICATE_PATHS):
            entries[-1]["replicate"] = list(REPLICATE_USERS)
        archived.append((path, name))
    if not entries:
        raise BackupError("no secret files found to back up")
    manifest = json.dumps({"version": 1, "files": entries}, indent=2).encode()
    with tarfile.open(fileobj=out, mode="w") as archive:
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(manifest)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(manifest))
        for path, name in archived:
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
    return len(entries)


def backup(target, extra=None, config=None):
    sources = collect_sources(extra, config)
    passphrase = read_passphrase(confirm=True)
    target = Path(target).expanduser()
    if target.is_dir() or (not target.exists() and target.suffix != ".age"):
        target.mkdir(parents=True, exist_ok=True)
        target = target / DEFAULT_BACKUP_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linko-secrets-") as scratch:
        plain = Path(scratch) / "plain.tar"
        with open_private(plain) as stream:
            count = build_archive(sources, stream)
        blob = seal(plain, passphrase, scratch)
    partial = target.with_name(target.name + ".partial")
    with open_private(partial) as stream:
        stream.write(blob)
    os.replace(partial, target)
    return target, count


def load_manifest(archive):
    try:
        data = archive.extractfile(MANIFEST)
    except KeyError:
        raise BackupError("backup has no manifest") from None
    return json.loads(data.read())


def restore(source, dry_run=False):
    source = Path(source).expanduser()
    if not source.is_file():
        raise BackupError(f"backup file not found: {source}")
    passphrase = read_passphrase(confirm=False)
    blob = source.read_bytes()
    with tempfile.TemporaryDirectory(prefix="linko-secrets-") as scratch:
        plain = unseal(blob, passphrase, scratch)
        with tarfile.open(plain, mode="r") as archive:
            manifest = load_manifest(archive)
            restored = []
            for entry in manifest["files"]:
                target = resolve_stored(entry["path"])
                body = archive.extractfile(entry["member"]).read()
                restored.append((target, entry, body))
        return apply_restore(restored, dry_run)


def unused_backup_path(target):
    candidate = target.with_name(target.name + ".bak")
    number = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = target.with_name(f"{target.name}.bak.{number}")
        number += 1
    return candidate


def apply_restore(restored, dry_run):
    """Write every file back. Existing files are first copied to a .bak (never overwritten silently)."""
    actions = []
    for target, entry, body in restored:
        existing = target.exists() or target.is_symlink()
        backup_path = unused_backup_path(target) if existing else None
        actions.append((target, entry, body, backup_path))
    if dry_run:
        return [(str(t), str(b) if b else None) for t, _, _, b in actions]
    done = []
    for target, entry, body, backup_path in actions:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.parent.name == ".ssh":
            os.chmod(target.parent, 0o700)
        if backup_path is not None:
            shutil.copy2(target, backup_path)
            os.chmod(backup_path, 0o600)
        temporary = target.with_name(target.name + ".restore-tmp")
        with open_private(temporary) as stream:
            stream.write(body)
        chown_if_possible(temporary, entry)
        os.replace(temporary, target)
        done.append((str(target), str(backup_path) if backup_path else None))
        for user in entry.get("replicate", []):
            replicate_for(user, entry, body)
    return done


def replicate_for(user, entry, body):
    """Put a provider login into another account's home (linko-worker, root) when it exists and has no login yet.
    The body goes to the child's stdin, never argv. Without passwordless sudo the account is skipped with a note."""
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        return
    stored = entry["path"]
    if not stored.startswith("~/"):
        return
    target = Path(account.pw_dir) / stored[2:]
    prefix = [] if account.pw_uid == os.geteuid() else ["sudo", "-n", "-u", user]
    script = ('umask 077; [ -e "$1" ] && exit 3; mkdir -p "$(dirname "$1")" && cat > "$1.tmp" '
              '&& chmod 600 "$1.tmp" && mv "$1.tmp" "$1"')
    try:
        result = subprocess.run(prefix + ["sh", "-c", script, "sh", str(target)], input=body,
                                capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode not in (0, 3):
        print(f"not placed for {user}: {target} (needs passwordless sudo; log in there by hand)", file=sys.stderr)
    elif result.returncode == 3:
        print(f"kept existing login for {user}: {target}", file=sys.stderr)


def chown_if_possible(path, entry):
    """Root restores to the recorded owner when that account exists; anyone else owns what they write."""
    if os.geteuid() != 0:
        return
    try:
        uid = pwd.getpwnam(entry["owner"]).pw_uid
        gid = grp.getgrnam(entry["group"]).gr_gid
    except KeyError:
        return
    os.chown(path, uid, gid)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("backup", help="encrypt the secret files into one file")
    save.add_argument("target", help="directory (writes secrets-backup.age) or file path")
    save.add_argument("--file", action="append", default=[], help="extra secret file (repeatable)")
    save.add_argument("--config", help=f"list of paths, one per line (default {DEFAULT_CONFIG} if it exists)")
    load = commands.add_parser("restore", help="decrypt and put files back at their original paths")
    load.add_argument("backup", help="the encrypted backup file")
    load.add_argument("--dry-run", action="store_true", help="show what would be written; change nothing")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            config = args.config
            if config is None and DEFAULT_CONFIG.expanduser().is_file():
                config = str(DEFAULT_CONFIG.expanduser())
            target, count = backup(args.target, args.file, config)
            print(f"wrote {target} ({count} files, encrypted)")
        else:
            done = restore(args.backup, dry_run=args.dry_run)
            verb = "would write" if args.dry_run else "restored"
            for target, bak in done:
                suffix = f" (previous copy saved to {bak})" if bak else ""
                print(f"{verb} {target}{suffix}")
    except BackupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
