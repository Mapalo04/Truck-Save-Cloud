# TruckSave Cloud

Python desktop backup manager for **Euro Truck Simulator 2 (ETS2)**. Watches the selected game-save folder, creates compressed ZIP snapshots, queues them offline, and can upload them to a user's **own Google Drive**. Includes backup listing, download, restore, revert, delete, and configurable retention.

> **Prototype warning:** Not yet end-to-end tested on a Windows ETS2 installation or against a live Google Drive account. Keep independent copies of important saves. The application backs up **existing** ETS2 saves; it does not force the game to save while driving.

## Features

| Feature | Status |
|---|---|
| Tkinter Windows desktop interface | Implemented (prototype) |
| Manual snapshot | Implemented |
| Interval-based checks | Implemented |
| Change detection / periodic polling | Implemented; not OS event-based monitoring |
| Offline upload queue and retry | Implemented (prototype) |
| Google Drive OAuth and upload | Implemented; live testing required |
| List and download cloud backups | Implemented; live testing required |
| Restore to folder / revert with safety copy | Implemented; test before use |
| Manual deletion and rolling cloud retention | Implemented; deletion requires caution |
| Automatic Windows startup / system tray | **Not implemented yet** |
| Per-profile retention / pinned backups | **Not implemented yet** |

## Requirements
- Windows 10 or 11; Python 3.11+ with Tkinter.
- ETS2 installed, with a known save-profile folder.
- Internet and a Google account for optional cloud backup.

## Install and run

Open PowerShell in this directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Choose your actual ETS2 profiles folder. Typical **non-Steam-Cloud** location:

```text
%USERPROFILE%\Documents\Euro Truck Simulator 2\profiles
```

Steam Cloud profile paths may be under:

```text
<Steam install>\userdata\<Steam account ID>\227300\remote\profiles
```

Paths depend on Steam installation and profile settings. Confirm which folder ETS2 is using before restoring.

## Connect your own Google Drive

1. Create a project at https://console.cloud.google.com/ and enable the **Google Drive API**.
2. Configure the Google Auth Platform consent screen; for testing, add the Google accounts that will use the app as test users.
3. Create an OAuth client of type **Desktop app**. Download the JSON and save it as `credentials.json` beside `app.py` **on your own computer**.
4. Run the app and choose **Connect Google Drive**. Approve access in the browser.
5. The app uses the `https://www.googleapis.com/auth/drive.file` scope, creates an app-managed Drive folder, and stores the user OAuth token in the operating-system keyring.

For distribution to other people, configure Google's publishing/verification requirements as applicable. Desktop OAuth client settings are not a confidential server secret, but **user access tokens are sensitive**. Never commit or share `credentials.json` or tokens in this repository.

## Automatic backup modes

- **Interval checks:** periodically check for new or changed game saves (e.g., every 15 minutes).
- **Change detection:** periodically inspect the save tree and schedule a backup when its contents appear changed. This prototype uses polling, not a native filesystem event listener.
- Both may be enabled together. Unchanged snapshots are skipped where possible.
- Backups are written locally first; cloud uploads are retried when connectivity is available.
- This is a **completed-save backup tool**, not an ETS2 in-game quick-save trigger.

## Retention policy

Default: **10 calendar days**, **2 latest successful cloud backups per day** (at most **20 normal cloud snapshots** for the configured source folder).

- Settings allow changing days and backups/day.
- Successful uploads can trigger cleanup; a manual cleanup control is also available.
- Pending or failed uploads are excluded from automatic cloud cleanup.
- Local uploaded-backup retention is controlled separately and may keep fewer local copies.
- Applies to the selected source folder as a whole, **not separately per ETS2 profile**.
- Pinned/protected backups are not yet supported; export important milestones before cleanup.
- On a second computer, sync the cloud list before running cleanup. Deletion is permanent from the app's perspective.

## Backup format and example

Each snapshot is a ZIP of the selected folder with a date/time-based name. Example (illustrative):

```text
TruckSave Cloud local data/
  backups/
    ets2-20261021-181500-a1b2c3d4.zip
    ets2-20261021-193000-e5f6a7b8.zip
  backup-index.sqlite3  # illustrative metadata database name

Google Drive/
  TruckSave Cloud/
    ets2-20261021-181500-a1b2c3d4.zip
    ets2-20261021-193000-e5f6a7b8.zip
```

The ZIP contains the backed-up source folder hierarchy, such as profile configuration and `save/` directories. It is a full snapshot, not an incremental diff. Exact local database and backup paths are defined in `app.py` and may differ from this illustration.

For storage efficiency, use compressed snapshots, skip unchanged files, and enforce retention. Future per-profile and incremental backups could reduce usage further but add complexity.

## Retrieve, restore, revert, and delete

- **Sync Cloud List:** refresh cloud backup inventory.
- **Download Selected:** download a remote archive to local storage.
- **Restore to Folder:** extract to a separate directory for inspection.
- **Revert Selected:** replace the selected source folder with the chosen archive after making a safety copy. **Close ETS2 and pause Steam Cloud syncing first.**
- **Delete Selected:** remove a chosen local backup and optionally its cloud copy.

**Safety:** Test on a spare profile. ZIP files are not encrypted. Verify backup integrity, and never delete the safety copy until a restored profile loads successfully.

## Security and repository hygiene

Read [SECURITY.md](SECURITY.md). `.gitignore` excludes local credentials, databases, logs, backup ZIPs, and virtual environments. Do not commit real player saves or OAuth tokens. The MIT license is a copyright/use license, **not** a security control.

## License

MIT — see [LICENSE](LICENSE). The copyright line uses a neutral placeholder (`TruckSave Cloud contributors`); the project owner should replace it with the desired legal name before publishing.

## Roadmap

1. Test Google OAuth, interrupted uploads, delete behavior, and restore on Windows with disposable saves.
2. Add true Windows startup and system-tray support (currently **not implemented**).
3. Add per-profile backups and protected/pinned restore points.
4. Add cloud storage usage indicators and secure account disconnect.
5. Add integration tests, signed Windows installer, and release documentation.

## Windows startup and system tray (v4)

Install dependencies with `py -m pip install -r requirements.txt` and start with `py app.py`.
On the first launch, **Start automatically when I sign in to Windows** is enabled by default. The application creates a **current-user** Windows Run entry; no administrator privileges or Windows service are required. It starts minimized to the system tray on subsequent Windows sign-ins. This starts when you **sign in**, not before login.

Closing the window hides it to the system tray by default and **continues monitoring**. Right-click the tray icon to **Open TruckSave Cloud**, **Backup now**, or **Exit completely**. Disable either behavior using the Background operation checkboxes. Exiting completely stops monitoring until the app is started again; it does not disable startup at next sign-in. Disabling the Windows startup checkbox removes the Run entry.

To run the source application without a console, Windows startup prefers `pythonw.exe` where available. If you move or rename the project folder or Python interpreter, launch the app again so the startup entry can be updated. A packaged executable uses its own executable path.

**Testing:** Python syntax compilation has been performed, but actual Windows sign-in, tray behavior, Google OAuth, cloud deletion and restore have not been tested in this environment. Test with a spare ETS2 profile first. The app runs only when the user is logged in and their session is active; Windows sleep prevents monitoring until wake. A single-instance Windows mutex prevents two copies from launching in the same session.

**Privacy:** The program stores backup metadata and ZIP snapshots under `%LOCALAPPDATA%\\TruckSaveCloud`; Google tokens use the OS credential manager through `keyring`. Never commit `credentials.json` or local backups. Google Drive API authorization requires your own OAuth client configuration.

## v5 PySide6 interface and Windows installer

This release adds `desktop.py`, a modern four-tab PySide6 UI (Dashboard, Backup History,
Cloud Accounts, Settings). The original `app.py` remains the backup engine. Launch with
`py desktop.py` after `py -m pip install -r requirements.txt`.

On **Windows**, run `build_windows.bat` to produce `dist/TruckSaveCloud/TruckSaveCloud.exe`.
Install Inno Setup 6, open `installer/TruckSaveCloud.iss`, and compile to produce a setup EXE.
The setup EXE must be built on Windows; this repository contains build scripts, not a prebuilt binary.

Google Drive requires a Desktop OAuth `credentials.json` next to `app.py`/`desktop.py` for
source runs. For a packaged build, place `credentials.json` beside the installed EXE (the engine looks there for packaged builds). OAuth desktop
client IDs are not secrets, but user tokens must remain in Windows Credential Manager.

**Important:** Startup registration from the legacy backup engine uses `app.py` for
source installs. v5 overrides the startup command in `desktop.py`; verify startup after installation. Backup restore, Google
Drive operations, and Windows tray behavior need real Windows/ETS2 integration tests.
