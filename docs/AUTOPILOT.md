# Autopilot

Autopilot is an autonomous, goal-driven manager mode for a **dedicated machine** (intended: a VM you can snapshot).
You give it a goal with a "done when" checklist; it works toward it step by step, on its own, with root rights inside
that machine. The software is the same everywhere and Autopilot is **off by default**. Code: `ai_manager/autopilot.py`;
tests: `tests/test_autopilot.py`, `tests/test_autopilot_browser.py`. Everything below is also in
**Settings > Autopilot** on the machine itself, with that machine's real paths and copy buttons.

## The two locks

Both are local to the machine and outside the app's reach:

| Lock | What | Detected by | Who sets it |
| --- | --- | --- | --- |
| Config lock | `AI_MANAGER_AUTOPILOT=full` in this machine's env file (Main unit: `~/.config/linko-bot-manager.env`; another path with `AI_MANAGER_ENV_FILE`) | the process environment at start | you, by hand |
| OS lock | passwordless sudo for the service user, from `/etc/sudoers.d/linko-autopilot` | `sudo -n true` (cached 30 s) | you, by hand |

- **full** = both locks: the loop runs and workers may use sudo. **partial** = one lock: goals can be saved (with the
  config lock) but nothing runs. **off** = neither. The header badge shows `Autopilot: full / partial / off`.
- The config lock is never a database setting. No route, setting, chat action or manager action sets it; it is not part
  of the settings that Main pushes to instances (`instance_sync.py`), and deploys never edit env files. When
  `scripts/install_machine.py --secrets` restores an env file from a backup it drops the Autopilot lines
  (`AI_MANAGER_AUTOPILOT`, `AI_MANAGER_SNAPSHOT_CMD`, `AI_MANAGER_SCREENSHOT_CMD`, `AI_MANAGER_AUTOPILOT_DISPLAY`).
- The app only detects the sudoers drop-in; it never writes one.
- Restricted instances (office mode, e.g. kaxa) are always **off**: they do not even probe sudo, the Autopilot API
  answers 403 and the Settings panel is hidden there.

### Turn it on

1. Env line (Settings shows the exact file):

   ```bash
   printf '%s\n' 'AI_MANAGER_AUTOPILOT=full' >> ~/.config/linko-bot-manager.env
   ```

2. Sudoers drop-in for the service user (`USER` below is the user the service runs as; Settings shows it filled in).
   It is validated before it is installed, because a broken drop-in can break sudo:

   ```bash
   printf '%s' 'USER ALL=(ALL:ALL) NOPASSWD: ALL
   ' > /tmp/linko-autopilot
   sudo visudo -cf /tmp/linko-autopilot
   sudo install -o root -g root -m 0440 /tmp/linko-autopilot /etc/sudoers.d/linko-autopilot
   sudo visudo -cf /etc/sudoers.d/linko-autopilot
   rm /tmp/linko-autopilot
   ```

   If the separate worker user is installed (README "How work runs"), Settings adds its line too, so workers can use
   sudo as well.

3. The service unit sets `NoNewPrivileges=yes`, which blocks sudo. On this machine only, override it:

   ```bash
   mkdir -p ~/.config/systemd/user/linko-bot-manager.service.d
   printf '[Service]\nNoNewPrivileges=no\n' > ~/.config/systemd/user/linko-bot-manager.service.d/autopilot.conf
   systemctl --user daemon-reload
   ```

4. Restart: `systemctl --user restart linko-bot-manager`. The badge should read `Autopilot: full`.

`linko-doctor` reports the blanket NOPASSWD rule as this machine's OS lock (PASS) instead of a warning while the env line
is present.

### Recommended: etckeeper

```bash
sudo apt-get install -y etckeeper
sudo etckeeper commit 'baseline before autopilot' || true
```

Before and after every step Autopilot runs `sudo -n etckeeper commit ...`, so every change to `/etc` can be reviewed
and reverted. Without etckeeper it notes that in the journal and continues.

### Optional: VM snapshot hook

`AI_MANAGER_SNAPSHOT_CMD` in the env file, for example
`AI_MANAGER_SNAPSHOT_CMD=ssh owner@hypervisor /usr/local/bin/snapshot-vm botos`. It runs without a shell; Autopilot
appends two arguments, the goal id and a label (`goal-start` or `risky-step`). It runs at goal start and before risky
steps: the planner marks a step risky, and a keyword backstop catches desktop/display manager changes, boot
configuration and package removal. With no hook, the journal says so and the step goes ahead.

### Desktop screenshots

Visual goals (goals with reference images) are verified against a screenshot of this machine's real desktop, taken with
ImageMagick `import -window root`, `scrot`, `grim` or `gnome-screenshot` on `DISPLAY` (default `:0`;
`AI_MANAGER_AUTOPILOT_DISPLAY=:1` for a VNC display). `AI_MANAGER_SCREENSHOT_CMD` replaces the tool; the output path is
appended as its last argument.

### Turn it off

- **Pause** (Settings > Autopilot): stops every goal at once. A running step finishes; nothing new starts.
- For good: remove the env line, the drop-in and the override, then restart:

  ```bash
  sed -i '/^AI_MANAGER_AUTOPILOT=/d' ~/.config/linko-bot-manager.env
  sudo rm -f /etc/sudoers.d/linko-autopilot
  rm -f ~/.config/systemd/user/linko-bot-manager.service.d/autopilot.conf && systemctl --user daemon-reload
  systemctl --user restart linko-bot-manager
  ```

## Goals

Create one in Settings > Autopilot or in a chat on that machine ("Autopilot goal: ..."; with the config lock set the
manager gets the `autopilot_goal` action and an `autopilot` context entry; elsewhere its tool and context are unchanged).
A goal has a title, description, reference images (PNG/JPEG/WebP, at most 6; chat attachments become reference images),
a "done when" checklist (1-30 items), an optional daily token budget and an optional deadline, and belongs to a project
whose queue workers carry out the steps. Stored in `autopilot_goals` and `autopilot_journal`. The panel lists each goal
with status, score, rounds, approach, the last journal entry and Pause / Resume / Stop.

## The loop (full only)

1. **Wake**: read the goal, its journal and the last result.
2. **Plan ONE step** (Opus, low effort; medium for the first plan and after 2 consecutive failed steps). If the step
   needs the owner, the goal waits (status `waiting`) and the chat gets one line.
3. **Delegate** it as a normal queue task (request key `autopilot:...`; checks, repair rounds, merge and deploy as usual).
   Its result goes to the loop, not as a chat notice. etckeeper commits before and after.
4. **Verify**: score each checklist item (Opus low, vision: reference images plus a desktop screenshot when the goal has
   images, otherwise the worker report).
5. **Journal**: what was done, result, per-item scores, score, next idea, model and effort.
6. **Next**: plan again at once, or finish.

- **Done** when at least 90% of the checklist passes **and** a final check on Opus medium confirms; wake-ups are then off.
- **Stuck**: no score improvement in 4 rounds -> a different approach (logged); a second stall pauses the goal and asks
  the owner in chat. No round cap while the score improves.
- **Budget / deadline**: reached -> progress is saved and the goal pauses. Usage is estimated per day from the manager's
  prompts and replies plus the workers' reported token usage.
- **Checkpoints**: when the score improves, a non-forcing GitHub backup push of the project is requested
  (`github_backup.py`).

## Wake-ups

- Events wake at once: a finished or failed step wakes its goal on the next scheduler tick (deploys behave as before).
- While a step runs, every 20 minutes a cheap non-AI probe checks the task: queued in line, new events, output growth,
  worker process alive. Progressing -> sleep (zero tokens). Unclear (one quiet interval) -> Haiku decides. Stuck (held,
  process gone, or two quiet intervals) -> the Opus manager decides to wait or cancel the step and plan another.

## Still needs the owner, even on full

Spending money or paid services; publishing or anything outward-facing; touching Main or other machines; the owner's
real accounts or secrets; changing Autopilot's own rules or limits (env lines, the drop-in, budgets). Workers are told
to stop with `NEEDS OWNER APPROVAL: ...` instead; the goal then waits for you. Everything inside the VM is Autopilot's
own call.
