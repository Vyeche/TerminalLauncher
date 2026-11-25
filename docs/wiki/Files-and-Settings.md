# Files and settings

Everything Terminal Launcher keeps is in your own config and cache folders; your list is never
stored in the cloned folder.

| Path | What |
| --- | --- |
| `~/.config/terminal-launcher/sessions.json` | your list |
| `~/.config/terminal-launcher/settings.json` | the **Open in** choice |
| `~/.config/autostart/net.vyechi.TerminalLauncher.desktop` | the login entry, only while some entry is marked ([Start at login](Start-At-Login)) |
| `~/.local/share/applications/net.vyechi.TerminalLauncher.desktop` | the app-menu entry, written by `install.sh` |
| `~/.cache/terminal-launcher/launcher.log` | one line per login start |

If you set `XDG_CONFIG_HOME` or `XDG_CACHE_HOME`, those folders are used instead of `~/.config` and
`~/.cache`. The menu entry is always in `~/.local/share/applications`.

The launcher only reads Claude Code's history in `~/.claude/projects` (for
[the recent sessions list](Entry-Types#start-from-a-recent-claude-session)); it never writes there.

## sessions.json
A list of entries, in the order the window shows them. A made-up example:
```json
[
  {
    "name": "api",
    "folder": "~/code/api",
    "kind": "claude",
    "session": "1a2b3c4d-0000-4000-8000-000000000000"
  },
  {
    "name": "docs",
    "folder": "~/code/docs",
    "kind": "omp",
    "session": ""
  },
  {
    "name": "dev server",
    "folder": "~/code/web",
    "kind": "command",
    "command": "npm run dev",
    "autostart": true
  },
  {
    "name": "home",
    "folder": "~",
    "kind": "terminal"
  }
]
```

| Field | Meaning |
| --- | --- |
| `name` | shown in the list (the dialog fills in the folder's name if you leave it empty) |
| `folder` | where the terminal starts; `~` works. **Required**: an entry without it is ignored |
| `kind` | `claude`, `omp`, `opencode`, `terminal` or `command`. Missing means `claude` |
| `session` | agents only: the session ID to resume; empty starts a new session |
| `command` | `command` only: what to run |
| `autostart` | `true` to open at login. Only written when on; missing means off |

### Editing it by hand
It's plain JSON, so you can, with care:
- **Close the launcher first.** The window keeps the list in memory and writes all of it back the
  next time you add, edit, move or remove something, which would undo your edit.
- **Keep a copy.** If the file isn't valid JSON, the launcher shows an empty list (and prints
  `can't read …` when started from a terminal). Don't add anything then: the next save would replace
  the broken file. Fix the JSON, then reopen the launcher.
- Entries without a `folder` are dropped from the list, and gone from the file at the next save.
- After changing `autostart` by hand, open the launcher and save any change (or run
  `bash install.sh`) so the login entry is added or removed to match.

## settings.json
```json
{"open_mode": "tabs"}
```
| `open_mode` | **Open in** |
| --- | --- |
| `kitty` | My kitty window |
| `tabs` | One new window (also used when the file is missing or unreadable) |
| `windows` | Separate windows |

It's written whenever you change **Open in**.

## Coming from Claude Launcher
Terminal Launcher is the renamed **Claude Launcher**. Moving over is automatic:
- The first time Terminal Launcher reads its list and finds no `~/.config/terminal-launcher/sessions.json`
  but an old `~/.config/claude-launcher/sessions.json`, it copies `sessions.json` and
  `settings.json` from the old folder. That happens on whatever runs first: the window,
  `--autostart`, `--all` or `install.sh`. The old folder is left as it was; delete it when you're
  happy.
- `install.sh` removes the old **Claude Launcher** menu entry
  (`~/.local/share/applications/net.vyechi.ClaudeLauncher.desktop`).
- The kitty socket keeps its old name, `unix:@claude-launcher-kitty`, so a kitty window the old
  version opened keeps getting the new tabs.
