# Installing

Terminal Launcher is one Python file. Installing it only adds an entry to your app menu; you can
also run it straight from the folder. No sudo is needed for the launcher itself, and everything
it writes is per user.

## What you need
- **Python 3 with GTK 4 and libadwaita 1.4 or newer** (through PyGObject).
- **At least one terminal** for the way you want things to open (see [Open in](Open-In-Modes)):
  - `kitty` for **My kitty window**
  - `gnome-terminal` for **One new window** (one window with a tab for each)
  - `cosmic-term` for **Separate windows** (GNOME Terminal or `x-terminal-emulator` also work)

Pop!_OS 24.04 COSMIC and recent Ubuntu and Fedora GNOME desktops already have the GTK parts. If not:

| Distro | Command |
| --- | --- |
| Pop!_OS, Ubuntu, Debian | `sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1` |
| Fedora | `sudo dnf install python3-gobject python3-cairo gtk4 libadwaita` |
| Arch | `sudo pacman -S python-gobject python-cairo gtk4 libadwaita` |

The cairo part only draws the cat; without it the launcher works and the cat stays hidden.

Too old: Ubuntu 22.04 (libadwaita 1.1) and Debian 12 ship a libadwaita older than 1.4.
Ubuntu 24.04, Pop!_OS 24.04 and newer are fine.

To check what you have:
```bash
python3 -c 'import gi; gi.require_version("Adw", "1"); from gi.repository import Adw; print("libadwaita", Adw.get_major_version(), Adw.get_minor_version())'
```
It should print `libadwaita 1 4` or a higher second number. An error such as
`Namespace Adw not available` means the libadwaita package above is missing.

For kitty, the launcher prefers the official build in `~/.local/kitty.app` (from kitty's own
installer) and otherwise uses the `kitty` on your `PATH`, for example from `sudo apt install kitty`.

## Install
```bash
git clone https://github.com/Vyeche/TerminalLauncher.git
cd TerminalLauncher
bash install.sh            # adds "Terminal Launcher" to the app menu
```
Then find **Terminal Launcher** in the app menu; you can pin it to the dock.

What `install.sh` does:
- writes the menu entry `~/.local/share/applications/net.vyechi.TerminalLauncher.desktop`, which
  runs `terminal-launcher.py` from the folder you cloned into
- makes `terminal-launcher.py` executable
- removes the old **Claude Launcher** menu entry, if you had one (see
  [Files and settings](Files-and-Settings#coming-from-claude-launcher))
- if any entry is marked **Start when I log in**, rewrites the login entry so it points at this
  folder (see [Start at login](Start-At-Login))

Running it again is harmless; it just writes the same files again.

### Without installing
```bash
python3 terminal-launcher.py
```
Only one window opens at a time: starting the launcher again brings the open window to the front.

## Updating
```bash
cd TerminalLauncher
git pull
```
Close and reopen the launcher. You only need to run `install.sh` again if you moved the folder.

## Moving the folder
The menu entry and the login entry both point at the folder by its full path. After moving or
renaming the folder, run `install.sh` again from its new place:
```bash
bash install.sh
```
That fixes the menu entry and, if any entry is marked to start at login, the login entry too.

## Removing
```bash
bash install.sh remove     # removes the menu and login entries (keeps your list)
```
This removes the menu entry and `~/.config/autostart/net.vyechi.TerminalLauncher.desktop`. Your
list in `~/.config/terminal-launcher/` stays, so a later install picks up where you left off.

Two things to know:
- Your entries keep their **Start when I log in** setting. If you run the launcher again and save
  any change, the login entry comes back. To stop things opening at login for good, turn that
  switch off on each entry first (or see [Start at login](Start-At-Login#turning-it-off)).
- To delete everything, also remove the list, its settings and the log:
  ```bash
  rm -r ~/.config/terminal-launcher ~/.cache/terminal-launcher
  ```
  Then delete the cloned folder.
