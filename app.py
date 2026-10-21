"""TruckSave Cloud: ETS2 save snapshots and Google Drive upload (Windows GUI)."""
import hashlib
import json
import os
import queue
import sqlite3
import threading
import time
import uuid
import zipfile
import shutil
import tempfile
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'TruckSaveCloud'
APP.mkdir(parents=True, exist_ok=True)
BACKUPS = APP / 'backups'
BACKUPS.mkdir(exist_ok=True)
CONFIG = APP / 'settings.json'
DB = APP / 'backups.sqlite3'
SCOPES = ['https://www.googleapis.com/auth/drive.file']


def default_source():
    docs = Path.home() / 'Documents' / 'Euro Truck Simulator 2' / 'profiles'
    return str(docs)


def load_settings():
    settings = dict(source=default_source(), interval_enabled=True, interval_minutes=10,
                    changes_enabled=True, check_seconds=15, stable_seconds=12,
                    cloud_folder_id='', last_interval=0, max_local=30, max_cloud=20, retention_days=10, retention_per_day=2, start_with_windows=True, minimize_to_tray=True)
    if CONFIG.exists():
        try:
            settings.update(json.loads(CONFIG.read_text(encoding='utf8')))
        except (ValueError, OSError):
            pass
    return settings


def save_settings(settings):
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(settings, indent=2), encoding='utf8')
    temp.replace(CONFIG)


def database():
    con = sqlite3.connect(DB, timeout=20)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('''CREATE TABLE IF NOT EXISTS backups (
      id TEXT PRIMARY KEY, created TEXT NOT NULL, source TEXT NOT NULL,
      filename TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
      remote_id TEXT, error TEXT DEFAULT '')''')
    columns={row[1] for row in con.execute('PRAGMA table_info(backups)')}
    if 'source_fingerprint' not in columns:
        con.execute('ALTER TABLE backups ADD COLUMN source_fingerprint TEXT')
    con.commit()
    return con


def fingerprint(root):
    root = Path(root)
    if not root.is_dir():
        return None
    files = []
    for p in sorted(root.rglob('*')):
        if p.is_file():
            st = p.stat()
            files.append((p.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    if not files:
        return None
    return hashlib.sha256(json.dumps(files).encode()).hexdigest()


def create_snapshot(source, expected_fingerprint):
    root = Path(source).resolve()
    if fingerprint(root) != expected_fingerprint:
        raise RuntimeError('Save folder changed before snapshot; retry later')
    uid = uuid.uuid4().hex
    filename = f'ets2-{datetime.now():%Y%m%d-%H%M%S}-{uid[:8]}.zip'
    final = BACKUPS / filename
    temp = BACKUPS / (filename + '.partial')
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for p in sorted(root.rglob('*')):
                if p.is_file():
                    # Preserve source root name to make archive contents explicit.
                    archive.write(p, arcname=(Path(root.name) / p.relative_to(root)).as_posix())
        if fingerprint(root) != expected_fingerprint:
            raise RuntimeError('Save files changed while archiving; retry later')
        with zipfile.ZipFile(temp) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f'Invalid archive member: {bad}')
        digest = hashlib.sha256(temp.read_bytes()).hexdigest()
        temp.replace(final)
        with database() as con:
            con.execute('INSERT INTO backups (id,created,source,filename,digest,status) VALUES (?,?,?,?,?,?)',
                        (uid, datetime.now().isoformat(timespec='seconds'), str(root), filename, digest, 'queued'))
            con.execute('UPDATE backups SET source_fingerprint=? WHERE id=?',(expected_fingerprint,uid))
        return filename
    finally:
        temp.unlink(missing_ok=True)


def last_digest(source):
    with database() as con:
        row=con.execute('SELECT digest FROM backups WHERE source=? ORDER BY created DESC LIMIT 1',(str(Path(source).resolve()),)).fetchone()
    return row[0] if row else None


def snapshot_if_changed(source, fp):
    with database() as con:
        existing=con.execute('SELECT id FROM backups WHERE source=? AND source_fingerprint=? LIMIT 1',
                             (str(Path(source).resolve()),fp)).fetchone()
    if existing:return None
    return create_snapshot(source,fp)


def cloud_backups(settings):
    service=drive_service()
    folder=drive_folder(service,settings)
    items=[]; token=None
    while True:
        response=service.files().list(q=f"'{folder}' in parents and trashed = false",
            fields='nextPageToken,files(id,name,description,size,createdTime)',page_size=100,
            pageToken=token).execute()
        items.extend(x for x in response.get('files',[]) if x['name'].endswith('.zip'))
        token=response.get('nextPageToken')
        if not token: break
    with database() as con:
        for item in items:
            digest=(item.get('description') or '').replace('ETS2 backup SHA256: ','').strip()
            if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest): continue
            exists=con.execute('SELECT id FROM backups WHERE remote_id=?',(item['id'],)).fetchone()
            if not exists:
                con.execute('INSERT INTO backups(id,created,source,filename,digest,status,remote_id) VALUES(?,?,?,?,?,?,?)',
                            (uuid.uuid4().hex,item.get('createdTime','')[:19],settings['source'],item['name'],digest,'cloud-only',item['id']))
    return len(items)


def delete_backup(uid, remove_cloud=False):
    with database() as con:
        row=con.execute('SELECT filename,remote_id FROM backups WHERE id=?',(uid,)).fetchone()
    if not row: raise RuntimeError('Backup no longer exists')
    filename,remote_id=row
    if remove_cloud and remote_id:
        drive_service().files().delete(fileId=remote_id).execute()
    elif remote_id:
        with database() as con:
            con.execute("UPDATE backups SET status='cloud-only' WHERE id=?",(uid,))
        (BACKUPS/filename).unlink(missing_ok=True)
        return
    (BACKUPS/filename).unlink(missing_ok=True)
    with database() as con: con.execute('DELETE FROM backups WHERE id=?',(uid,))


def prune_local(max_count):
    # Never remove local copies of pending uploads.
    with database() as con:
        rows=con.execute("SELECT id,filename FROM backups WHERE status='uploaded' ORDER BY created DESC").fetchall()
    for uid,filename in rows[max_count:]:
        (BACKUPS/filename).unlink(missing_ok=True)
        with database() as con:
            con.execute("UPDATE backups SET status='cloud-only' WHERE id=?",(uid,))


def retention_candidates(rows, days=10, per_day=2, now=None):
    """Return IDs eligible for pruning, newest two per local calendar day.

    rows: (id, created ISO string, status, remote_id). Pinned/unsynced rows
    are never pruned. Retention counts distinct backup days, including today.
    """
    now = now or datetime.now()
    start = now.date() - timedelta(days=days - 1)
    counts = {}
    candidates = []
    for uid, created, status, remote_id in sorted(rows, key=lambda r: r[1], reverse=True):
        if status not in ('uploaded', 'cloud-only') or not remote_id:
            continue
        try:
            day = datetime.fromisoformat(created.replace('Z', '+00:00')).date()
        except ValueError:
            continue  # Never delete backups with unparseable dates
        if day > now.date():
            continue  # Clock skew: keep future-dated backups
        if day < start:
            candidates.append(uid)
        else:
            count = counts.get(day, 0)
            if count >= per_day:
                candidates.append(uid)
            counts[day] = count + 1
    return candidates


def apply_retention(settings, emit, cloud=True):
    """Prune safely: only uploaded backups with a verified remote ID.

    When cloud=False, no pruning is done: local copies remain as an offline
    safety net until the provider can confirm deletion of remote versions.
    """
    if not cloud:
        return
    days = int(settings.get('retention_days', 10))
    per_day = int(settings.get('retention_per_day', 2))
    if not (1 <= days <= 365 and 1 <= per_day <= 20):
        raise ValueError('Invalid retention settings')
    with database() as con:
        rows = con.execute('SELECT id,created,status,remote_id FROM backups').fetchall()
    victims = retention_candidates(rows, days, per_day)
    if not victims:
        return
    service = drive_service()
    for uid in victims:
        with database() as con:
            row = con.execute('SELECT filename,remote_id,status FROM backups WHERE id=?', (uid,)).fetchone()
        if not row or row[2] not in ('uploaded', 'cloud-only') or not row[1]:
            continue
        filename, remote_id, _ = row
        # Only delete locally AFTER Drive confirms the remote deletion.
        try:
            service.files().delete(fileId=remote_id).execute()
        except Exception as exc:
            emit('Retention deferred for ' + filename + ': ' + str(exc)[:120])
            continue
        (BACKUPS / filename).unlink(missing_ok=True)
        with database() as con:
            con.execute('DELETE FROM backups WHERE id=?', (uid,))
        emit('Retention removed ' + filename)


def revert_backup(filename, source):
    try:
        import psutil
        if any('eurotrucks2' in (p.info.get('name') or '').lower() for p in psutil.process_iter(['name'])):
            raise RuntimeError('Close Euro Truck Simulator 2 before reverting.')
    except ImportError:
        raise RuntimeError('Install psutil to verify ETS2 is closed before reverting.')
    source=Path(source).resolve()
    if not source.is_dir(): raise RuntimeError('The selected save folder does not exist')
    parent=source.parent
    with tempfile.TemporaryDirectory(prefix='trucksave-restore-',dir=parent) as staging:
        staging=Path(staging)
        restore_backup(filename, staging/'extracted')
        restored=staging/'extracted'/source.name
        if not restored.is_dir(): raise RuntimeError('Archive does not match the configured save folder')
        safety=parent/(source.name+'.trucksave-before-revert-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        if safety.exists(): raise RuntimeError('Safety backup destination already exists')
        source.rename(safety)
        try:
            shutil.copytree(restored,source)
        except Exception:
            if source.exists(): shutil.rmtree(source)
            safety.rename(source)
            raise
    return str(safety)


def get_credentials():
    import keyring
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    raw = keyring.get_password('TruckSaveCloud', 'google-oauth')
    if not raw:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        keyring.set_password('TruckSaveCloud', 'google-oauth', creds.to_json())
    return creds if creds.valid else None


def connect_google():
    import keyring
    from google_auth_oauthlib.flow import InstalledAppFlow
    client = (Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent) / 'credentials.json'
    if not client.exists():
        raise RuntimeError('Missing credentials.json; see README.md for Google Cloud setup')
    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    keyring.set_password('TruckSaveCloud', 'google-oauth', creds.to_json())
    return creds


def drive_service():
    from googleapiclient.discovery import build
    creds = get_credentials()
    if not creds:
        raise RuntimeError('Connect Google Drive first')
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def drive_folder(service, settings):
    folder_id = settings.get('cloud_folder_id')
    if folder_id:
        try:
            obj = service.files().get(fileId=folder_id, fields='id,trashed').execute()
            if not obj.get('trashed'):
                return folder_id
        except Exception:
            pass
    # drive.file permits searching files/folders this app created.
    result = service.files().list(q="name = 'TruckSave Cloud' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                                  fields='files(id,name)', page_size=100).execute()
    if result.get('files'):
        folder_id = result['files'][0]['id']
    else:
        folder_id = service.files().create(body={'name':'TruckSave Cloud',
                       'mimeType':'application/vnd.google-apps.folder'}, fields='id').execute()['id']
    settings['cloud_folder_id'] = folder_id
    save_settings(settings)
    return folder_id


def upload_pending(settings, emit):
    from googleapiclient.http import MediaFileUpload
    service = drive_service()
    folder = drive_folder(service, settings)
    with database() as con:
        rows = con.execute("SELECT id,filename,digest FROM backups WHERE status IN ('queued','failed') ORDER BY created").fetchall()
    for uid, filename, digest in rows:
        path = BACKUPS / filename
        if not path.exists():
            with database() as con:
                con.execute('UPDATE backups SET status=?,error=? WHERE id=?', ('missing','Local archive missing',uid))
            continue
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Archive checksum mismatch')
            media = MediaFileUpload(str(path), mimetype='application/zip', resumable=True)
            remote = service.files().create(body={'name':filename,'parents':[folder],
                      'description':f'ETS2 backup SHA256: {digest}'},media_body=media,fields='id').execute()
            with database() as con:
                con.execute('UPDATE backups SET status=?,remote_id=?,error=? WHERE id=?', ('uploaded',remote['id'],'',uid))
            emit('Uploaded ' + filename)
            prune_local(int(settings.get('max_local',30)))
            apply_retention(settings, emit)
        except Exception as e:
            with database() as con:
                con.execute('UPDATE backups SET status=?,error=? WHERE id=?', ('failed',str(e)[:500],uid))
            raise


def restore_backup(filename, target):
    archive = BACKUPS / filename
    if not archive.is_file():
        raise RuntimeError('Archive is not available locally. Download it first.')
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise RuntimeError('Restore target must be an empty folder (or a new folder).')
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError('Archive integrity check failed')
        for member in z.infolist():
            name = member.filename
            parts = Path(name).parts
            if not parts or any(part in ('..', '') for part in parts) or Path(name).is_absolute() or ':' in name:
                raise RuntimeError('Unsafe archive path')
            # Reject symlinks encoded in ZIP external attributes.
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise RuntimeError('Archive contains a symlink')
        target.mkdir(parents=True, exist_ok=True)
        z.extractall(target)
    return target



def startup_command():
    """Return a Windows command line without a visible console for source runs."""
    if getattr(sys, 'frozen', False):
        return f'"{Path(sys.executable).resolve()}" --background'
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == 'python.exe':
        candidate = executable.with_name('pythonw.exe')
        if candidate.exists():
            executable = candidate
    return f'"{executable}" "{Path(__file__).resolve()}" --background'


def configure_windows_startup(enabled):
    if sys.platform != 'win32':
        return False
    import winreg
    key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, 'TruckSaveCloud', 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, 'TruckSaveCloud')
            except FileNotFoundError:
                pass
    return True


def make_tray_icon():
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (64, 64), '#172334')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 18, 56, 46), radius=9, fill='#73b5f5')
    draw.rectangle((16, 25, 48, 31), fill='#172334')
    draw.ellipse((16, 40, 27, 51), fill='#f4f7fa')
    draw.ellipse((38, 40, 49, 51), fill='#f4f7fa')
    return image


class App:
    def __init__(self, window):
        self.window = window
        self.window.title('TruckSave Cloud — ETS2')
        self.window.geometry('1050x760')
        self.settings = load_settings()
        self.messages = queue.Queue()
        self.stop = threading.Event()
        self.busy = threading.Lock()
        self.tray = None
        self.tray_thread = None
        self.quitting = False
        self.start_with_windows = tk.BooleanVar(value=self.settings.get('start_with_windows', True))
        self.minimize_to_tray = tk.BooleanVar(value=self.settings.get('minimize_to_tray', True))
        self.source = tk.StringVar(value=self.settings['source'])
        self.interval_enabled = tk.BooleanVar(value=self.settings['interval_enabled'])
        self.changes_enabled = tk.BooleanVar(value=self.settings['changes_enabled'])
        self.interval = tk.StringVar(value=str(self.settings['interval_minutes']))
        self.status = tk.StringVar(value='Ready — connect Google Drive to enable uploads')
        self.max_local=tk.StringVar(value=str(self.settings.get('max_local',30)))
        self.retention_days=tk.StringVar(value=str(self.settings.get('retention_days',10)))
        self.retention_per_day=tk.StringVar(value=str(self.settings.get('retention_per_day',2)))
        self.build()
        self.window.after(250, self.poll)
        threading.Thread(target=self.monitor, daemon=True).start()
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        try:
            configure_windows_startup(self.start_with_windows.get())
        except Exception as exc:
            self.emit('Windows startup could not be configured: ' + str(exc))
        try:
            self.start_tray()
        except Exception as exc:
            self.emit('System tray unavailable; closing the window will exit: ' + str(exc))
        if '--background' in sys.argv and self.tray is not None:
            self.window.withdraw()

    def emit(self, msg):
        self.messages.put(msg)

    def build(self):
        root = ttk.Frame(self.window, padding=18)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='TruckSave Cloud', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(root, text='Local snapshots • Offline queue • Your own Google Drive', foreground='#536579').pack(anchor='w', pady=(0,15))
        frame = ttk.LabelFrame(root, text='ETS2 save folder', padding=10)
        frame.pack(fill='x', pady=5)
        line = ttk.Frame(frame); line.pack(fill='x')
        ttk.Entry(line, textvariable=self.source).pack(side='left', fill='x', expand=True)
        ttk.Button(line, text='Browse', command=self.browse).pack(side='left', padx=6)
        ttk.Button(line, text='Apply', command=self.apply).pack(side='left')
        ttk.Label(frame, text='Select the profiles folder containing the save files (Steam Cloud paths also supported).').pack(anchor='w', pady=(5,0))
        startup = ttk.LabelFrame(root, text='Background operation', padding=10)
        startup.pack(fill='x', pady=5)
        ttk.Checkbutton(startup, text='Start automatically when I sign in to Windows',
                        variable=self.start_with_windows, command=self.apply).pack(anchor='w')
        ttk.Checkbutton(startup, text='Close window to system tray (keep backups running)',
                        variable=self.minimize_to_tray, command=self.apply).pack(anchor='w')
        ttk.Label(startup, text='Use the tray icon to open TruckSave Cloud or exit completely.').pack(anchor='w')
        frame = ttk.LabelFrame(root, text='Automatic backup triggers', padding=10)
        frame.pack(fill='x', pady=5)
        ttk.Checkbutton(frame, text='Scheduled interval checks', variable=self.interval_enabled, command=self.apply).grid(row=0,column=0,sticky='w')
        ttk.Label(frame, text='Minutes:').grid(row=0,column=1,padx=(25,5))
        ttk.Combobox(frame, textvariable=self.interval, values=['5','10','15','30','60'], width=6, state='readonly').grid(row=0,column=2)
        ttk.Button(frame,text='Apply interval',command=self.apply).grid(row=0,column=3,padx=10)
        ttk.Checkbutton(frame, text='Automatic change detection (checks every 15 seconds)', variable=self.changes_enabled,command=self.apply).grid(row=1,column=0,columnspan=4,sticky='w',pady=(10,0))
        ttk.Label(frame, text='A save must remain unchanged for at least 12 seconds before snapshotting.').grid(row=2,column=0,columnspan=4,sticky='w',pady=(8,0))
        retention=ttk.Frame(root);retention.pack(fill='x',pady=5)
        ttk.Label(retention,text='Keep latest local uploaded backups:').pack(side='left')
        ttk.Combobox(retention,textvariable=self.max_local,values=['5','10','20','30','50','100'],width=6,state='readonly').pack(side='left',padx=8)
        ttk.Button(retention,text='Apply local limit',command=self.apply).pack(side='left')
        policy=ttk.Frame(root);policy.pack(fill='x',pady=5)
        ttk.Label(policy,text='Cloud history: keep days').pack(side='left')
        ttk.Spinbox(policy,from_=1,to=365,textvariable=self.retention_days,width=5).pack(side='left',padx=6)
        ttk.Label(policy,text='latest backups per day').pack(side='left')
        ttk.Spinbox(policy,from_=1,to=20,textvariable=self.retention_per_day,width=5).pack(side='left',padx=6)
        ttk.Button(policy,text='Save policy',command=self.apply).pack(side='left',padx=8)
        ttk.Button(policy,text='Run cleanup now',command=self.cleanup_now).pack(side='left')
        actions = ttk.Frame(root); actions.pack(fill='x', pady=12)
        for name, fn in [('Connect Google Drive', self.connect),('Backup now',self.backup_now),('Upload queue',self.upload_now),('Sync cloud list',self.sync_cloud),('Download selected',self.download_selected),('Restore to folder',self.restore),('Revert selected',self.revert),('Delete selected',self.delete_selected),('Refresh list',self.refresh)]:
            ttk.Button(actions,text=name,command=fn).pack(side='left',padx=(0,6))
        ttk.Label(root,textvariable=self.status,wraplength=780).pack(anchor='w',pady=(0,8))
        cols=('created','filename','status','size')
        self.tree=ttk.Treeview(root,columns=cols,show='headings',height=12)
        for c,title,width in [('created','Created',160),('filename','Archive',520),('status','Status',120),('size','Local MB',90)]:
            self.tree.heading(c,text=title); self.tree.column(c,width=width)
        self.tree.pack(fill='both',expand=True)
        ttk.Label(root,text='Restore extracts to a separate folder only. Close ETS2 before copying restored files into your game profile.',foreground='#6b7280',wraplength=760).pack(anchor='w',pady=(8,0))
        self.refresh()

    def browse(self):
        folder=filedialog.askdirectory(title='Select ETS2 profiles folder')
        if folder: self.source.set(folder); self.apply()

    def apply(self):
        try:
            minutes=int(self.interval.get())
            if minutes not in (5,10,15,30,60): raise ValueError()
        except ValueError:
            messagebox.showerror('Interval','Choose a valid interval'); return
        try:
            days=int(self.retention_days.get()); per_day=int(self.retention_per_day.get())
            if not (1 <= days <= 365 and 1 <= per_day <= 20): raise ValueError()
        except ValueError:
            messagebox.showerror('Retention','Days must be 1–365 and backups per day 1–20'); return
        self.settings.update(source=self.source.get().strip(), interval_enabled=self.interval_enabled.get(),
                             changes_enabled=self.changes_enabled.get(),interval_minutes=minutes,max_local=int(self.max_local.get()),
                             retention_days=days,retention_per_day=per_day,
                             start_with_windows=self.start_with_windows.get(), minimize_to_tray=self.minimize_to_tray.get())
        try:
            configure_windows_startup(self.start_with_windows.get())
        except Exception as exc:
            self.emit('Could not update Windows startup: ' + str(exc))
        save_settings(self.settings)
        self.emit('Settings saved')

    def task(self, action):
        def work():
            if not self.busy.acquire(blocking=False):
                self.emit('Another operation is already running'); return
            try: action()
            except Exception as e: self.emit('Error: ' + str(e))
            finally: self.busy.release(); self.messages.put('__refresh__')
        threading.Thread(target=work,daemon=True).start()

    def connect(self):
        def work():
            connect_google(); self.emit('Google Drive connected successfully')
            upload_pending(self.settings,self.emit)
        self.task(work)

    def cleanup_now(self):
        self.apply()
        self.task(lambda: (apply_retention(self.settings,self.emit), self.emit("Retention cleanup finished")))

    def backup_now(self):
        def work():
            source=self.settings['source']
            fp=fingerprint(source)
            if fp is None: raise RuntimeError('No save files found at selected path')
            # Explicit user action: verify stable contents across 12 seconds.
            time.sleep(12)
            if fingerprint(source)!=fp: raise RuntimeError('Files still changing; try again')
            name=snapshot_if_changed(source,fp)
            self.emit('Created '+name if name else 'No new backup: unchanged contents')
            try: upload_pending(self.settings,self.emit)
            except Exception: self.emit('Cloud unavailable; backup queued locally')
        self.task(work)

    def upload_now(self): self.task(lambda: upload_pending(self.settings,self.emit))

    def download_selected(self):
        selected=self.tree.selection()
        if not selected:
            messagebox.showinfo('Download','Select a backup first'); return
        filename=self.tree.item(selected[0])['values'][1]
        def work():
            from googleapiclient.http import MediaIoBaseDownload
            with database() as con:
                row=con.execute('SELECT remote_id,digest FROM backups WHERE filename=?',(filename,)).fetchone()
            if not row or not row[0]:raise RuntimeError('This backup has not been uploaded')
            remote_id,digest=row
            service=drive_service()
            temp=BACKUPS/(filename+'.download')
            try:
                with temp.open('wb') as out:
                    downloader=MediaIoBaseDownload(out,service.files().get_media(fileId=remote_id))
                    done=False
                    while not done: _,done=downloader.next_chunk()
                if hashlib.sha256(temp.read_bytes()).hexdigest()!=digest:
                    raise RuntimeError('Downloaded backup checksum mismatch')
                with zipfile.ZipFile(temp) as z:
                    if z.testzip():raise RuntimeError('Downloaded archive is damaged')
                temp.replace(BACKUPS/filename)
                with database() as con: con.execute("UPDATE backups SET status='uploaded' WHERE filename=?",(filename,))
                self.emit('Downloaded and verified '+filename)
            finally:temp.unlink(missing_ok=True)
        self.task(work)

    def restore(self):
        selected=self.tree.selection()
        if not selected:
            messagebox.showinfo('Restore','Select a backup first'); return
        filename=self.tree.item(selected[0])['values'][1]
        target=filedialog.askdirectory(title='Select EMPTY restore destination')
        if not target:return
        if not messagebox.askyesno('Restore','ETS2 must be closed. Extract backup into the selected empty folder?'):return
        self.task(lambda:self.emit('Restored to ' + str(restore_backup(filename,target))))

    def selected_backup(self):
        selection=self.tree.selection()
        if not selection: raise RuntimeError('Select a backup first')
        return selection[0]

    def sync_cloud(self):
        self.task(lambda:self.emit(f'Cloud inventory refreshed: {cloud_backups(self.settings)} files found'))

    def revert(self):
        try: uid=self.selected_backup()
        except RuntimeError as e: messagebox.showinfo('Revert',str(e));return
        with database() as con:
            row=con.execute('SELECT filename FROM backups WHERE id=?',(uid,)).fetchone()
        if not row:return
        if not (BACKUPS/row[0]).exists():
            messagebox.showinfo('Download required','Download this backup before reverting.');return
        if not messagebox.askyesno('Revert ETS2 progress',
                'ETS2 must be CLOSED. This replaces the configured save folder with the selected backup. '
                'The current folder will be preserved under a timestamped safety name. Continue?'):return
        self.task(lambda:self.emit('Reverted successfully. Previous files preserved at: '+revert_backup(row[0],self.settings['source'])))

    def delete_selected(self):
        try: uid=self.selected_backup()
        except RuntimeError as e: messagebox.showinfo('Delete',str(e));return
        with database() as con:
            row=con.execute('SELECT filename,remote_id FROM backups WHERE id=?',(uid,)).fetchone()
        if not row:return
        remote=bool(row[1])
        remove_cloud=remote and messagebox.askyesno('Delete from Google Drive?',
            'Delete this backup from your Google Drive as well? Yes = delete everywhere; No = delete only local copy.')
        if not messagebox.askyesno('Confirm deletion',
            'Delete selected backup'+(' from Drive and this computer' if remove_cloud else ' from this computer')+'?'):return
        self.task(lambda:(delete_backup(uid,remove_cloud),self.emit('Backup deleted')))

    def refresh(self):
        for item in self.tree.get_children():self.tree.delete(item)
        with database() as con:
            for uid,created,filename,status in con.execute('SELECT id,created,filename,status FROM backups ORDER BY created DESC LIMIT 500'):
                path=BACKUPS/filename
                size=f'{path.stat().st_size/1048576:.2f}' if path.is_file() else '—'
                self.tree.insert('', 'end',iid=uid,values=(created,filename,status,size))

    def poll(self):
        try:
            while True:
                msg=self.messages.get_nowait()
                if msg=='__refresh__':self.refresh()
                else:self.status.set(msg)
        except queue.Empty:pass
        if not self.stop.is_set():self.window.after(250,self.poll)

    def monitor(self):
        last_fp=None; changed_at=time.monotonic(); last_snapshot_fp=None
        next_retry=0
        while not self.stop.wait(3):
            if self.busy.locked():continue
            source=self.settings['source']
            try: fp=fingerprint(source)
            except OSError: fp=None
            now=time.monotonic()
            if fp!=last_fp:
                last_fp=fp;changed_at=now
            scheduled=self.settings['interval_enabled'] and now-float(self.settings.get('last_interval',0))>=int(self.settings['interval_minutes'])*60
            changed=self.settings['changes_enabled'] and fp!=last_snapshot_fp
            if fp and (scheduled or changed) and now-changed_at>=12:
                if self.busy.acquire(blocking=False):
                    try:
                        # Always check current fingerprint; interval mode may create a repeated snapshot intentionally.
                        name=snapshot_if_changed(source,fp)
                        last_snapshot_fp=fp
                        self.settings['last_interval']=now
                        if name: self.emit('Created ' + name)
                    except Exception as e:self.emit('Backup retry later: '+str(e))
                    finally:self.busy.release()
            if now>=next_retry and self.busy.acquire(blocking=False):
                try:
                    with database() as con:
                        count=con.execute("SELECT COUNT(*) FROM backups WHERE status IN ('queued','failed')").fetchone()[0]
                    if count:
                        try: upload_pending(self.settings,self.emit)
                        except Exception:pass
                finally:self.busy.release()
                next_retry=now+60
                self.messages.put('__refresh__')

    def start_tray(self):
        import pystray
        self.tray = pystray.Icon(
            'TruckSaveCloud', make_tray_icon(), 'TruckSave Cloud — monitoring',
            menu=pystray.Menu(
                pystray.MenuItem('Open TruckSave Cloud', lambda icon, item: self.window.after(0, self.show_window), default=True),
                pystray.MenuItem('Backup now', lambda icon, item: self.window.after(0, self.backup_now)),
                pystray.MenuItem('Exit completely', lambda icon, item: self.window.after(0, self.exit_app)),
            ),
        )
        self.tray_thread = threading.Thread(target=self.tray.run, daemon=True)
        self.tray_thread.start()

    def show_window(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        if self.minimize_to_tray.get() and self.tray is not None and not self.quitting:
            self.window.withdraw()
            return
        self.exit_app()

    def exit_app(self):
        if self.quitting:
            return
        self.quitting = True
        self.stop.set()
        self.apply()
        if self.tray is not None:
            self.tray.stop()
        self.window.destroy()

if __name__=='__main__':
    database().close()
    # Avoid launching a second monitor (and simultaneous cloud uploads) on Windows.
    mutex = None
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, 'Local\\TruckSaveCloud_SingleInstance')
        if kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(None, 'TruckSave Cloud is already running. Open it from the system tray.', 'TruckSave Cloud', 0x40)
            sys.exit(0)
    window=tk.Tk()
    App(window)
    window.mainloop()
