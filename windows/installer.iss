; installer.iss — Inno Setup script for Whisper Toggle
;
; This installs the FULL bundle: embedded Python + all dependencies +
; application scripts. The user does NOT need Python installed.
;
; Build process:
;   1. Run build-installer.ps1 (downloads Python, installs deps, stages files)
;   2. Inno Setup compiles this script into WhisperToggle-Setup-1.0.exe
;
; The build script calls ISCC automatically if Inno Setup is installed.

[Setup]
AppId={{A7D3F2E1-B8C4-4F5A-9E6D-1C2B3A4F5E6D}
AppName=Whisper Toggle
AppVersion=1.0
AppVerName=Whisper Toggle 1.0
AppPublisher=Tim Lewis
AppPublisherURL=https://github.com/tolewis/Whisper-Toggle
AppSupportURL=https://github.com/tolewis/Whisper-Toggle/issues
DefaultDirName={localappdata}\Whisper Toggle
DefaultGroupName=Whisper Toggle
OutputBaseFilename=WhisperToggle-Setup-1.0
SetupIconFile=compiler:SetupClassicIcon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Whisper Toggle
DisableProgramGroupPage=yes

; Show a welcome message
InfoBeforeFile=installer-welcome.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Launch Whisper Toggle when I sign in"; GroupDescription: "Options:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"

[Files]
; Embedded Python with all dependencies pre-installed
Source: "..\build\stage\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; Application files
Source: "..\build\stage\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\stage\whisper-toggle-tray.pyw"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu
Name: "{group}\Whisper Toggle"; \
    Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\whisper-toggle-tray.pyw"""; \
    WorkingDir: "{app}"; \
    Comment: "Voice dictation — press Ctrl+` to talk"

Name: "{group}\Uninstall Whisper Toggle"; \
    Filename: "{uninstallexe}"

; Desktop shortcut (optional)
Name: "{userdesktop}\Whisper Toggle"; \
    Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\whisper-toggle-tray.pyw"""; \
    WorkingDir: "{app}"; \
    Comment: "Voice dictation — press Ctrl+` to talk"; \
    Tasks: desktopicon

; Auto-start on login (optional)
Name: "{userstartup}\Whisper Toggle"; \
    Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\whisper-toggle-tray.pyw"""; \
    WorkingDir: "{app}"; \
    Tasks: autostart

[Run]
; Launch after install
Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\whisper-toggle-tray.pyw"""; \
    WorkingDir: "{app}"; \
    Description: "Launch Whisper Toggle now"; \
    Flags: postinstall nowait skipifsilent

[UninstallRun]
; Kill the app before uninstalling
Filename: "taskkill.exe"; \
    Parameters: "/F /IM pythonw.exe"; \
    Flags: runhidden; \
    RunOnceId: "KillWhisperToggle"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\python\__pycache__"
Type: files; Name: "{app}\python\Lib\site-packages\*.pyc"
