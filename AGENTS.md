# AGENTS.md: Terminal Launcher

Guidance for coding agents (Claude Code, omp, opencode, …) working in this repo.

## What this is
A small GTK 4 / libadwaita app (one Python file) that keeps a list of terminals and coding-agent
sessions and opens them in kitty tabs, one GNOME Terminal window, or separate COSMIC Terminal
windows. Entries can be marked to start at login. Made for Pop!_OS COSMIC (Wayland); should run on
any Linux desktop with GTK 4 and libadwaita 1.4+. See `README.md` for the user-facing description.

## Layout
| Path | What |
| --- | --- |
| `terminal-launcher.py` | the whole app: storage, launching, start at login, UI, command line |
| `install.sh` | per-user app-menu entry (`remove` to undo); replaces the old Claude Launcher entry |
| `scripts/check.sh` | every check (`--quick` skips the window test); the Gauntlet runs it |
| `scripts/sync-public.sh` | copies the committed files to the public GitHub clone (see Git) |
| `scripts/publish-wiki.sh` | pushes `docs/wiki/` to the GitHub wiki |
| `assets/terminal-launcher.svg` | icon |
| `README.md` | user docs; keep it in step with any change users can see |
| `docs/wiki/` | the GitHub wiki's pages (a line in `Whats-New.md` for user-visible changes) |
| `docs/images/` | README screenshots and GIFs, made from a made-up list (`tools/`) |
| `.github/` | the Gauntlet (CI), Dependabot, issue forms, PR template, funding, code owners |
| `.coderabbit.yaml` | CodeRabbit's review settings for pull requests on GitHub |
| `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `LICENSE` (MIT) | community files |

User data (never in the repo): `~/.config/terminal-launcher/` (`sessions.json`, `settings.json`),
the login entry `~/.config/autostart/net.vyechi.TerminalLauncher.desktop`, and the log
`~/.cache/terminal-launcher/launcher.log`.

## Rules
- **This repo is public.** Never commit personal data: home-folder paths, session IDs, IP addresses,
  e-mail addresses, hostnames of private machines, tokens, or screenshots that show someone's
  session list. `DEFAULTS` stays empty. Before committing, grep the tree for these.
- **Per user only.** No sudo, no system files. Everything lives under `~/.config`, `~/.cache` and
  `~/.local/share/applications`.
- **Don't open terminals against the real list while testing.** `--all`, `--all-windows` and
  `--autostart` resume real agent sessions, which may already be open, as duplicate tabs. Test with
  a throwaway config instead (see Testing).
- **Keep the kitty socket name** `unix:@claude-launcher-kitty`. It's from before the rename; open
  kitty windows and other scripts (e.g. a kitty relaunch script) find the window by it.
- **Keep the migration** from `~/.config/claude-launcher/` and the removal of the old
  `net.vyechi.ClaudeLauncher.desktop` until nobody can still have the old version.
- Start at login stays **off** for every entry unless the user turns it on. It runs at login (XDG
  autostart), not at power-on; say so wherever it's described.
- Saving the list must keep the autostart file in step (`save_sessions` → `sync_autostart`): present
  while some entry is marked, removed when none is.
- New agent types go in `KINDS` and `AGENTS` (label, new-session command, resume command). The
  "recent sessions" picker reads only Claude Code's history; don't guess at other agents' stores.

## Style
- Python 3, standard library plus PyGObject (Gtk 4, Adw 1). No other dependencies.
- UI text is plain, friendly and short; buttons say what happens ("Add 3 to kitty"). Every control
  has a tooltip. Follow the libadwaita patterns already used (PreferencesGroup, ActionRow, toasts).
- Scripts start with a short comment saying what they do and how to run them.

## Testing
`bash scripts/check.sh` runs everything the Gauntlet runs (throwaway HOME, stub terminals, the window
on a hidden Xvfb display); add a check there with every fix. For trying things by hand, use a
throwaway config folder so nothing touches the real list or opens real sessions:
```bash
T=$(mktemp -d)   # or a scratch folder of your session
export XDG_CONFIG_HOME=$T/cfg XDG_CACHE_HOME=$T/cache
mkdir -p $T/cfg/terminal-launcher $T/proj
echo '[{"name":"demo","folder":"'$T/proj'","kind":"omp","autostart":true}]' > $T/cfg/terminal-launcher/sessions.json
python3 terminal-launcher.py --autostart --dry-run          # prints what would open
desktop-file-validate $T/cfg/autostart/net.vyechi.TerminalLauncher.desktop   # after a save
```
- Check each **Open in** mode (`settings.json`: `{"open_mode": "kitty" | "tabs" | "windows"}`) with
  `--dry-run`. In kitty mode the dry-run still asks the live kitty window (`kitty @ ls`, read-only)
  whether it exists.
- UI changes: build `Window` and `EditDialog` in a script without calling `present()`, or run the
  app with the throwaway `XDG_CONFIG_HOME`. Don't take the mouse while the user is at the PC.
- `python3 -m py_compile terminal-launcher.py` and `bash -n install.sh` before committing.

## Git
- Default branch `main`. This repo is the **workshop** (the owner's own Git server, `origin`); all
  work happens here. The public copy is https://github.com/Vyeche/TerminalLauncher, with its own
  history. It only receives synced results and never gets this repo's history: don't add a GitHub
  remote here, and don't use a push mirror.
- To publish: commit here, then `bash scripts/sync-public.sh <public clone> -m "…"`, review, and push
  the public clone. The script copies `HEAD` and stops if a pattern from `.private-words` (one
  `grep -E` pattern per line, git-ignored so the words stay private) shows up in the files or the message.
- GitHub side: the owner's account creates and owns the repo; the public clone commits and pushes as
  the second account (a write collaborator) with its GitHub no-reply address. Public commit
  messages keep `Co-Authored-By:` but not the `Claude-Session:` link; PRs and releases there get no
  "Generated with Claude Code" footer.
- Commit or push only when the user asks; describe user-visible changes in `README.md` in the same
  commit.
