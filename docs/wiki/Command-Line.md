# Command line

```bash
python3 terminal-launcher.py                # the window
python3 terminal-launcher.py --all          # every saved entry as tabs in one GNOME Terminal window
python3 terminal-launcher.py --all-windows  # every saved entry in its own window
python3 terminal-launcher.py --autostart    # the entries marked "Start when I log in"
```
Add `--dry-run` to any of the last three to print what would open instead of opening it.

The three switches open terminals and exit without showing the window.

| Switch | Opens | Follows **Open in**? |
| --- | --- | --- |
| *(none)* | the launcher's window (or brings an open one to the front) | |
| `--all` | every entry, as tabs in one new GNOME Terminal window (separate windows without GNOME Terminal) | no |
| `--all-windows` | every entry, each in its own window: COSMIC Terminal, else GNOME Terminal, else `x-terminal-emulator` | no |
| `--autostart` | only the entries marked **Start when I log in**, after a 5 second wait, and logs the result | yes |
| `--dry-run` | nothing: prints a `would start: …` line for each terminal instead | |
| `--sounds` | nothing: plays each of the launcher's little sounds once (with `--dry-run`, only makes the files) | |

`--all` and `--all-windows` never use kitty, whatever **Open in** says. `--autostart` does: in kitty
mode it adds tabs to the launcher's kitty window. If you give more than one, `--autostart` wins over
`--all-windows`, which wins over `--all`.

## What you'll see
- A line on stderr for each problem, such as `Folder not found: ~/code/old-project`. An entry whose
  folder is missing is skipped and the others still open; a kitty problem (*kitty isn't installed
  yet*, *kitty started but isn't answering*) stops the kitty tabs from opening.
- Exit code **0** when everything opened, **1** when anything went wrong (even if the rest opened).
- Only `--autostart` writes to the log, `~/.cache/terminal-launcher/launcher.log`, and not with
  `--dry-run`. See [Start at login](Start-At-Login#checking-it).

## Examples
See what opens at login, without opening anything:
```bash
python3 terminal-launcher.py --autostart --dry-run
```
```
would start: cosmic-term -w ~/code/web -- bash -lc 'npm run dev; echo; echo '"'"'[Command finished. This is a normal shell now.]'"'"'; exec bash'   (in ~/code/web)
```
(Real output shows full paths.) In kitty mode a dry run still asks the launcher's kitty window
whether it is open (read-only, `kitty @ ls`), so it can tell you whether it would start one or add
tabs.

A keyboard shortcut for "open everything": in your desktop's keyboard settings, add a custom
shortcut that runs
```bash
python3 <your clone>/terminal-launcher.py --all
```

## Trying it on a test list
The launcher follows `XDG_CONFIG_HOME` and `XDG_CACHE_HOME`, so you can try it on a made-up list
without touching your own:
```bash
T=$(mktemp -d)
export XDG_CONFIG_HOME=$T/cfg XDG_CACHE_HOME=$T/cache
mkdir -p $T/cfg/terminal-launcher $T/proj
echo '[{"name":"demo","folder":"'$T/proj'","kind":"omp","autostart":true}]' > $T/cfg/terminal-launcher/sessions.json
python3 terminal-launcher.py --autostart --dry-run    # prints what would open
python3 terminal-launcher.py                          # the window, showing the test list
```
Close any launcher window you already have open first: a second start only brings that one to the
front, with your real list.
