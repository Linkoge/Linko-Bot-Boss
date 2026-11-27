# Autopilot

Autopilot is an autonomous, goal-driven manager mode for a **dedicated machine** (intended: a VM you can snapshot).
You give it a goal with a "done when" checklist; it works toward it step by step, on its own, with root rights inside
that machine. The software is the same everywhere and Autopilot is **off by default**. Code: `ai_manager/autopilot.py`,
`scripts/autopilot_setup.sh`; tests: `tests/test_autopilot.py`, `tests/test_autopilot_setup.py`,
`tests/test_autopilot_browser.py`. The UI is deliberately small: an **Autopilot** switch in the harness menu (☼, a green
dot on that button while it is on) and **Settings > Autopilot** (status, the setup command, goals). This page has the
details; the panel's **Details** link opens it.

## Set up (once per machine)

Settings > Autopilot shows one command with this machine's real paths; paste it in a terminal on that machine:

```bash
sudo bash /path/to/checkout/scripts/autopilot_setup.sh --user SERVICE_USER
```

It lists what it will do and asks you to type `yes` (`--yes` skips that; `--dry-run` only prints the plan). It is
idempotent; running it again changes nothing. In order:

1. Writes `/etc/sudoers.d/linko-autopilot` (`SERVICE_USER ALL=(ALL:ALL) NOPASSWD: ALL`, plus the separate worker
   user's line when that is installed, README "How work runs"), checked with `visudo -cf` **before** it is installed
   (root:root 0440) and again after; a rejected file is never left in place, because a broken drop-in can break sudo.
2. Adds `AI_MANAGER_AUTOPILOT=full` to this machine's env file (default `~/.config/linko-bot-manager.env`;
   `--env-file` for another one; the command shows it when this machine uses another path).
3. Writes the systemd user-unit override `~/.config/systemd/user/linko-bot-manager.service.d/autopilot.conf` with
   `NoNewPrivileges=no`: the unit sets `NoNewPrivileges=yes`, which blocks sudo (`--unit` for another unit name).
4. Installs etckeeper when `apt-get` is available (skipped otherwise) and commits a baseline of `/etc`.
5. Reloads the user's systemd and restarts **only** this machine's service, then prints `Autopilot ready`.

It refuses to run on a restricted instance (`AI_MANAGER_RESTRICTED=1`) and on the owner's Main: put
`AI_MANAGER_MACHINE_ROLE=main` in Main's env file (the script reads it there; sudo strips the environment). On Main
Settings then shows no setup command at all.

**Remove**: `sudo bash .../scripts/autopilot_setup.sh --remove` deletes the env line, the drop-in and the override,
reloads systemd and restarts the service. etckeeper stays installed: it only records `/etc` history.

In chat, "set up autopilot" gets you the same command: the manager cannot use sudo before setup, so on a machine that is
not set up it receives the command in its context (`autopilot.setup`, only when your message mentions Autopilot) and
replies with it. The manager tool is unchanged.

## The two locks and the switch

Both locks are local to the machine and outside the app's reach:

| Lock | What | Detected by |
| --- | --- | --- |
| Config lock | `AI_MANAGER_AUTOPILOT=full` in this machine's env file | the process environment at start |
| OS lock | passwordless sudo for the service user, from `/etc/sudoers.d/linko-autopilot` | `sudo -n true` (cached 30 s) |

- **full** = both locks ("set up"): the loop runs and workers may use sudo. **partial** = one lock: goals can be saved
  (with the config lock) but nothing runs; Settings says "Set up: partly", run the script again. **off** = neither.
- **The switch** (harness menu): on a machine that is set up it turns Autopilot on or off at once; off stops every goal
  (a running step finishes; nothing new starts). It is stored in this machine's local database (`autopilot_paused`),
  is never synced from Main (`instance_sync.py` pushes only its own key list) and can only stop work: it never creates
  a lock. Until setup it is disabled with "Not set up · Set up" (the link opens Settings > Autopilot).
- The config lock is never a database setting. No route, setting, chat action or manager action sets it; deploys never
  edit env files. When `scripts/install_machine.py --secrets` restores an env file from a backup it drops the
  Autopilot lines (`AI_MANAGER_AUTOPILOT`, `AI_MANAGER_SNAPSHOT_CMD`, `AI_MANAGER_SCREENSHOT_CMD`,
  `AI_MANAGER_AUTOPILOT_DISPLAY`).
- The app itself never writes the drop-in; only the script you run with sudo does.
- Restricted instances (office mode, e.g. kaxa) are always **off**: they do not even probe sudo, the Autopilot API
  answers 403, and the switch and the Settings panel are hidden there.

`linko-doctor` reports the blanket NOPASSWD rule as this machine's OS lock (PASS) instead of a warning while the env line
is present.

### etckeeper

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

- **Switch** in the harness menu: off at once (see above).
- For good: `sudo bash .../scripts/autopilot_setup.sh --remove`.

## Goals

Create one in a chat on that machine ("Autopilot goal: ..."; with the config lock set the
manager gets the `autopilot_goal` action and an `autopilot` context entry; elsewhere its tool and context are unchanged).
A goal has a title, description, reference images (PNG/JPEG/WebP, at most 6; chat attachments become reference images),
a "done when" checklist (1-30 items), an optional daily token budget and an optional deadline, and belongs to a project
whose queue workers carry out the steps. Stored in `autopilot_goals` and `autopilot_journal`. Settings > Autopilot lists each
goal with status, score and Pause / Resume / Stop (`/api/autopilot/goals` also accepts goals with reference images).

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
