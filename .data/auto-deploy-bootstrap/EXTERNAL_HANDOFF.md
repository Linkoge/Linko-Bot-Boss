# Supported external helper v3 installation handoff

This is a proposed external operation, not an executed installation or application activation.
Run from an authorized operator terminal outside BOTH linko-bot-manager.service and
linko-deployment-helper.service cgroups. Do not use systemd-run, move the worker, clear guard
variables, modify protocol/intent files, stop active workers, or stop/restart the manager.
The v3 installer delegates to the v2 installer; that installer does not itself check cgroup.
The external context precondition below is therefore essential, not redundant.

After this worker and manager result delivery finish, inspect retained evidence and arrange
serialized operation with no pending deployment. Run the existing supported CLI from the
CLEAN retained source workspace; keep environment/guard variables intact:

```bash
cat /proc/$$/cgroup
case "$(cat /proc/$$/cgroup)" in
  *linko-bot-manager.service*|*linko-deployment-helper.service*)
    echo 'Refusing installation inside manager/helper cgroup'; exit 1 ;;
esac
cd /home/codex/.local/share/linko-bot-manager/assignment-workspaces/44097fd1a52c4f9096fcd901bd04d344
PYTHONPATH=. /home/codex/projects/Linko-Bot-Manager/.venv/bin/python scripts/install_deployment_helper_v3.py \
  auto-deploy-standing-policy-v1-20261014 \
  --data /home/codex/.local/share/linko-bot-manager
```

Immediately before invoking, rerun the read-only audit.py from this audit workspace and verify
source HEAD 0223c3493a6edf9b54b280fa5993fb6404e2d8ca with clean Git status,
registered manifest/index hashes and 82-file inventory. Inspect the existing helper unit,
v2 succeeded receipt and installed v2 bytes. Installer enforces exact release ID/helper pin,
matching data config, no gated handoff, expected v2 unit reference, helper+protocol locks,
recheck after locks, absent prior v3 receipt and no conflicting v3 destination. It changes only
helper-v3.py and the independent helper unit, runs daemon-reload, restarts ONLY the helper,
requires is-active, then writes its durable succeeded receipt. It creates no intent or deployment.

Required success evidence is DATA/deployment-helper/helper-v3-upgrade.json with phase=succeeded,
release_id=auto-deploy-standing-policy-v1-20261014,
helper_sha256=30dc382080d59f6d316e681bf9dc06fced36dc2b6226e24e0190e5c2e0e0741e,
old helper hash/unit preserved and current unit_sha256 matching the actual unit bytes.
Hash both registered helper-v3.py and installed DATA/deployment-helper/helper-v3.py; each must
match that pin. Confirm helper service active and the unit names helper-v3.py. Retain a private
copy of the succeeded receipt and identity results in the operations execution evidence.
A prepared receipt or requested restart is not success. On an exception retain any partial
receipt/unit state and inspect externally; do not delete receipts or rerun blindly.

Helper installation alone does not make application activation ready: review.json contains
provider findings and REPORT.md documents an existing live manager assignment-scope blocker.
The manager must assess these findings and resolve the supported scope-review dependency
before review_deployment_intent/deploy_release under ba8ce92a3d406a98cdfc9fa81851c251.
Do not reseal the manifest, create a replacement intent/candidate or activate via shell.
