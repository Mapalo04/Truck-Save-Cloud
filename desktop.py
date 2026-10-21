"""PySide6 interface for TruckSave Cloud; existing app.py is the backup engine."""
import hashlib
import io
import os
import sys
import threading
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QSpinBox, QLineEdit, QTabWidget, QTableWidget,
    QTableWidgetItem, QFileDialog, QMessageBox, QSystemTrayIcon, QMenu, QHeaderView,
    QGroupBox, QFormLayout, QProgressBar)
import app as engine

# Windows startup must launch the PySide6 frontend, not the legacy Tkinter UI.
def frontend_startup_command():
    if getattr(sys, 'frozen', False):
        return f'"{Path(sys.executable).resolve()}" --background'
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == 'python.exe' and executable.with_name('pythonw.exe').exists():
        executable = executable.with_name('pythonw.exe')
    return f'"{executable}" "{Path(__file__).resolve()}" --background'
engine.startup_command = frontend_startup_command

class Signals(QObject):
    message = Signal(str)
    refresh = Signal()

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = engine.load_settings()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.signals = Signals()
        self.signals.message.connect(self.statusBar().showMessage)
        self.signals.refresh.connect(self.refresh)
        self.setWindowTitle('TruckSave Cloud')
        self.resize(980, 650)
        self.build()
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_DriveNetIcon))
        menu = QMenu()
        show = QAction('Open TruckSave Cloud', self); show.triggered.connect(self.showNormal); menu.addAction(show)
        backup = QAction('Backup now', self); backup.triggered.connect(self.backup_now); menu.addAction(backup)
        quit_action = QAction('Exit completely', self); quit_action.triggered.connect(self.quit_app); menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        self.quitting = False
        self.refresh()
        threading.Thread(target=self.monitor, daemon=True).start()
        if '--background' in sys.argv: self.hide()

    def button(self, text, fn, layout):
        b=QPushButton(text); b.clicked.connect(fn); layout.addWidget(b); return b

    def build(self):
        self.setStyleSheet('''QMainWindow,QWidget{background:#f4f6f9;color:#263244;font-size:13px}
        QGroupBox{border:1px solid #d9e0e9;border-radius:10px;margin-top:12px;padding:12px;background:white;font-weight:600}
        QPushButton{background:#344e70;color:white;padding:9px 14px;border-radius:7px}
        QPushButton:hover{background:#496a94} QLineEdit,QSpinBox{background:white;border:1px solid #cbd4df;padding:7px;border-radius:6px}
        QTableWidget{background:white;alternate-background-color:#f0f3f7;gridline-color:#e1e6ec}
        QTabWidget::pane{border:0}''')
        tabs=QTabWidget(); self.setCentralWidget(tabs)
        dash=QWidget(); dl=QVBoxLayout(dash)
        dl.addWidget(QLabel('TruckSave Cloud  •  ETS2 backup manager'))
        self.summary=QLabel(); dl.addWidget(self.summary)
        row=QHBoxLayout(); self.button('Backup now',self.backup_now,row); self.button('Upload pending',lambda:self.task(lambda:engine.upload_pending(self.settings,self.log)),row); self.button('Refresh',self.refresh,row); dl.addLayout(row)
        self.usage=QProgressBar(); self.usage.setRange(0,100); dl.addWidget(self.usage)
        self.usage_label=QLabel(); dl.addWidget(self.usage_label); dl.addStretch()
        tabs.addTab(dash,'Dashboard')
        history=QWidget(); hl=QVBoxLayout(history)
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(['Date','Archive','Status','Local size','Cloud'])
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        hl.addWidget(self.table)
        actions=QHBoxLayout()
        for title,fn in [('Download',self.download),('Restore to folder',self.restore),('Revert',self.revert),('Delete',self.delete),('Refresh',self.refresh)]:self.button(title,fn,actions)
        hl.addLayout(actions); tabs.addTab(history,'Backup History')
        cloud=QWidget(); cl=QVBoxLayout(cloud)
        cl.addWidget(QLabel('Connect your own Google Drive account using the official browser sign-in.'))
        cloudrow=QHBoxLayout(); self.button('Connect Google Drive',lambda:self.task(lambda:(engine.connect_google(),self.log('Google Drive connected'))),cloudrow)
        self.button('Sync cloud inventory',lambda:self.task(lambda:self.log(f'Found {engine.cloud_backups(self.settings)} cloud backups')),cloudrow)
        self.button('Upload pending',lambda:self.task(lambda:engine.upload_pending(self.settings,self.log)),cloudrow)
        cl.addLayout(cloudrow); cl.addStretch(); tabs.addTab(cloud,'Cloud Accounts')
        settings=QWidget(); sl=QVBoxLayout(settings)
        folder=QGroupBox('ETS2 source folder'); fl=QHBoxLayout(folder)
        self.source=QLineEdit(self.settings['source']); fl.addWidget(self.source)
        self.button('Browse',self.browse,fl); sl.addWidget(folder)
        scheduling=QGroupBox('Automatic backup'); form=QFormLayout(scheduling)
        self.interval=QCheckBox('Enable scheduled checks'); self.interval.setChecked(self.settings['interval_enabled']); form.addRow(self.interval)
        self.minutes=QSpinBox(); self.minutes.setRange(1,1440); self.minutes.setValue(int(self.settings['interval_minutes'])); form.addRow('Interval (minutes)',self.minutes)
        self.changes=QCheckBox('Detect changes automatically'); self.changes.setChecked(self.settings['changes_enabled']); form.addRow(self.changes)
        sl.addWidget(scheduling)
        retention=QGroupBox('Retention (per configured source folder)'); rf=QFormLayout(retention)
        self.days=QSpinBox();self.days.setRange(1,365);self.days.setValue(int(self.settings.get('retention_days',10)));rf.addRow('Calendar days',self.days)
        self.perday=QSpinBox();self.perday.setRange(1,20);self.perday.setValue(int(self.settings.get('retention_per_day',2)));rf.addRow('Latest backups per day',self.perday)
        sl.addWidget(retention)
        startup=QGroupBox('Windows'); sf=QFormLayout(startup)
        self.startup=QCheckBox('Start automatically when I sign in');self.startup.setChecked(self.settings.get('start_with_windows',True));sf.addRow(self.startup)
        self.minimize=QCheckBox('Close window to system tray');self.minimize.setChecked(self.settings.get('minimize_to_tray',True));sf.addRow(self.minimize)
        sl.addWidget(startup)
        self.button('Save settings',self.save,sl); sl.addStretch(); tabs.addTab(settings,'Settings')

    def log(self,msg):self.signals.message.emit(str(msg));self.signals.refresh.emit()
    def task(self, fn):
        def worker():
            if not self.lock.acquire(False):self.log('Another backup operation is running');return
            try:fn()
            except Exception as exc:self.log('Error: '+str(exc))
            finally:self.lock.release();self.signals.refresh.emit()
        threading.Thread(target=worker,daemon=True).start()

    def browse(self):
        p=QFileDialog.getExistingDirectory(self,'Select ETS2 profiles folder',self.source.text())
        if p:self.source.setText(p)

    def save(self):
        self.settings.update(source=self.source.text().strip(),interval_enabled=self.interval.isChecked(),
            interval_minutes=self.minutes.value(),changes_enabled=self.changes.isChecked(),
            retention_days=self.days.value(),retention_per_day=self.perday.value(),
            start_with_windows=self.startup.isChecked(),minimize_to_tray=self.minimize.isChecked())
        engine.save_settings(self.settings)
        try: engine.configure_windows_startup(self.startup.isChecked())
        except Exception as e: QMessageBox.warning(self,'Startup configuration',str(e))
        self.log('Settings saved')

    def refresh(self):
        with engine.database() as db:
            rows=db.execute('SELECT id,created,filename,status,remote_id FROM backups ORDER BY created DESC LIMIT 1000').fetchall()
        self.table.setRowCount(len(rows)); self.ids=[]
        total=0; queued=0
        for i,(uid,created,name,status,remote) in enumerate(rows):
            self.ids.append(uid); p=engine.BACKUPS/name
            size=p.stat().st_size if p.exists() else 0;total+=size
            if status in ('queued','failed'):queued+=1
            for j,v in enumerate((created,name,status,f'{size/1048576:.1f} MB' if size else '—','Yes' if remote else 'No')):
                item=QTableWidgetItem(v);item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable);self.table.setItem(i,j,item)
        self.summary.setText(f'{len(rows)} backup records   •   {queued} pending uploads')
        self.usage_label.setText(f'Local archive storage: {total/1048576:.1f} MB (Google Drive quota is managed by Google)')
        self.usage.setValue(min(100,int(total/1048576/1024*100)))

    def selected(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self.ids):raise RuntimeError('Select a backup first')
        with engine.database() as db:
            result=db.execute('SELECT id,filename,remote_id,digest FROM backups WHERE id=?',(self.ids[row],)).fetchone()
        if not result:raise RuntimeError('Backup not found')
        return result

    def backup_now(self):
        def run():
            fp=engine.fingerprint(self.settings['source'])
            if not fp:raise RuntimeError('No save files found in selected folder')
            name=engine.snapshot_if_changed(self.settings['source'],fp)
            self.log('Created '+name if name else 'No changes since the last backup')
        self.task(run)

    def download(self):
        try:uid,name,remote,digest=self.selected()
        except Exception as e:QMessageBox.information(self,'Select backup',str(e));return
        if not remote:QMessageBox.information(self,'Download','No cloud copy exists');return
        def run():
            from googleapiclient.http import MediaIoBaseDownload
            temp=engine.BACKUPS/(name+'.download')
            try:
                with temp.open('wb') as out:
                    loader=MediaIoBaseDownload(out,engine.drive_service().files().get_media(fileId=remote))
                    done=False
                    while not done:_,done=loader.next_chunk()
                if hashlib.sha256(temp.read_bytes()).hexdigest()!=digest:raise RuntimeError('Checksum mismatch')
                import zipfile
                with zipfile.ZipFile(temp) as z:
                    if z.testzip():raise RuntimeError('Damaged ZIP')
                temp.replace(engine.BACKUPS/name)
                with engine.database() as db:db.execute("UPDATE backups SET status='uploaded' WHERE id=?",(uid,))
                self.log('Downloaded and verified '+name)
            finally:temp.unlink(missing_ok=True)
        self.task(run)

    def restore(self):
        try:_,name,_,_=self.selected()
        except Exception as e:QMessageBox.information(self,'Select backup',str(e));return
        target=QFileDialog.getExistingDirectory(self,'Choose empty restore folder')
        if target:self.task(lambda:self.log('Restored to '+str(engine.restore_backup(name,target))))

    def revert(self):
        try:_,name,_,_=self.selected()
        except Exception as e:QMessageBox.information(self,'Select backup',str(e));return
        if QMessageBox.question(self,'Revert save','Close ETS2 first. Replace the current folder with this backup? A safety copy will be preserved.')==QMessageBox.StandardButton.Yes:
            self.task(lambda:self.log('Previous save preserved at '+engine.revert_backup(name,self.settings['source'])))

    def delete(self):
        try:uid,name,remote,_=self.selected()
        except Exception as e:QMessageBox.information(self,'Select backup',str(e));return
        cloud=False
        if remote:
            answer=QMessageBox.question(self,'Delete cloud copy','Delete this backup from Google Drive too? Select No to remove only the local copy.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel)
            if answer==QMessageBox.StandardButton.Cancel:return
            cloud=answer==QMessageBox.StandardButton.Yes
        if QMessageBox.question(self,'Confirm deletion','Delete selected backup?')==QMessageBox.StandardButton.Yes:
            self.task(lambda:(engine.delete_backup(uid,cloud),self.log('Backup deleted')))

    def monitor(self):
        last_fp=None;change_at=time.monotonic();last_snap=None;next_retry=0;last_check=time.monotonic()
        while not self.stop.wait(3):
            if self.lock.locked():continue
            try:fp=engine.fingerprint(self.settings['source'])
            except OSError:fp=None
            now=time.monotonic()
            if fp!=last_fp:last_fp=fp;change_at=now
            scheduled=self.settings['interval_enabled'] and now-last_check>=int(self.settings['interval_minutes'])*60
            changed=self.settings['changes_enabled'] and fp!=last_snap
            if fp and (scheduled or changed) and now-change_at>=int(self.settings.get('stable_seconds',12)):
                if self.lock.acquire(False):
                    try:
                        name=engine.snapshot_if_changed(self.settings['source'],fp)
                        last_snap=fp;last_check=now
                        if name:self.log('Saved '+name)
                    except Exception as e:self.log('Backup delayed: '+str(e))
                    finally:self.lock.release()
            if now>=next_retry and self.lock.acquire(False):
                try:
                    with engine.database() as db:
                        count=db.execute("SELECT COUNT(*) FROM backups WHERE status IN ('queued','failed')").fetchone()[0]
                    if count:
                        try:engine.upload_pending(self.settings,self.log)
                        except Exception:pass
                finally:self.lock.release()
                next_retry=now+60

    def closeEvent(self,event):
        if self.settings.get('minimize_to_tray',True) and not self.quitting and self.tray.isVisible():self.hide();event.ignore()
        else:self.stop.set();event.accept()
    def quit_app(self):
        self.quitting=True;self.stop.set();self.tray.hide();QApplication.quit()

def main():
    engine.database().close()
    if sys.platform=='win32':
        import ctypes
        kernel=ctypes.windll.kernel32
        mutex=kernel.CreateMutexW(None,False,'Local\\TruckSaveCloud_SingleInstance')
        if kernel.GetLastError()==183:
            ctypes.windll.user32.MessageBoxW(None,'TruckSave Cloud is already running.','TruckSave Cloud',0x40)
            return
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False)
    window=Window();window.show()
    sys.exit(app.exec())

if __name__=='__main__':main()
