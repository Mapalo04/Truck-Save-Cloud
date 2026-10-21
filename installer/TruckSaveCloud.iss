#define AppName "TruckSave Cloud"
#define AppVersion "0.5.0"
[Setup]
AppId={{7ABEEB98-2131-43D9-8CF2-7B9A970D3E1A}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\TruckSaveCloud
DefaultGroupName={#AppName}
OutputDir=output
OutputBaseFilename=TruckSaveCloud-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\TruckSaveCloud.exe
[Files]
Source: "..\dist\TruckSaveCloud\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{group}\TruckSave Cloud"; Filename: "{app}\TruckSaveCloud.exe"
Name: "{autodesktop}\TruckSave Cloud"; Filename: "{app}\TruckSaveCloud.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\TruckSaveCloud.exe"; Description: "Launch TruckSave Cloud"; Flags: nowait postinstall skipifsilent
