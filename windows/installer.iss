; installer.iss - Inno Setup script for Whisper Toggle v2.2.0
;
; Build:
;   1. powershell -ExecutionPolicy Bypass -File windows\build-installer.ps1
;   2. ISCC compiles this into WhisperToggle-Setup-2.2.0.exe

[Setup]
AppId={{A7D3F2E1-B8C4-4F5A-9E6D-1C2B3A4F5E6D}
AppName=Whisper Toggle
AppVersion=2.2.0
AppVerName=Whisper Toggle 2.2.0
AppPublisher=Tim Lewis
AppPublisherURL=https://github.com/tolewis/Whisper-Toggle
AppSupportURL=https://github.com/tolewis/Whisper-Toggle/issues
DefaultDirName={localappdata}\Whisper Toggle
DefaultGroupName=Whisper Toggle
OutputBaseFilename=WhisperToggle-Setup-2.2.0
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Whisper Toggle
DisableProgramGroupPage=yes
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
Source: "..\build\stage\tray_app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\stage\settings_gui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\stage\settings_web.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\stage\settings_web.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\stage\whisper_toggle\*"; DestDir: "{app}\whisper_toggle"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\stage\vendor\*"; DestDir: "{app}\vendor"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\build\stage\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\stage\disable-win-voice-typing.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Whisper Toggle"; \
    Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\whisper-toggle-tray.pyw"""; \
    WorkingDir: "{app}"; \
    IconFilename: "{app}\assets\icon.ico"; \
    Comment: "Voice dictation - press Ctrl+Shift+H to talk"

Name: "{group}\Uninstall Whisper Toggle"; \
    Filename: "{uninstallexe}"

Name: "{userdesktop}\Whisper Toggle"; \
    Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\whisper-toggle-tray.pyw"""; \
    WorkingDir: "{app}"; \
    IconFilename: "{app}\assets\icon.ico"; \
    Comment: "Voice dictation - press Ctrl+Shift+H to talk"; \
    Tasks: desktopicon

[InstallDelete]
Type: files; Name: "{userstartup}\Whisper Toggle.lnk"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "Whisper Toggle"; \
    ValueData: """{app}\python\pythonw.exe"" ""{app}\whisper-toggle-tray.pyw"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\whisper-toggle-tray.pyw"""; \
    WorkingDir: "{app}"; \
    Description: "Launch Whisper Toggle now"; \
    Flags: postinstall nowait skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PythonExe: String;
  SmokeLog: String;
  Args: String;
begin
  if CurStep = ssPostInstall then begin
    PythonExe := ExpandConstant('{app}\python\python.exe');
    SmokeLog := ExpandConstant('{app}\logs\install-smoke.json');
    Args := '-m whisper_toggle.smoke --from-config --log "' + SmokeLog + '"';
    if not Exec(PythonExe, Args, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
      MsgBox('Whisper Toggle validation could not start. Setup will stop before launching the app.', mbError, MB_OK);
      Abort;
    end;
    if ResultCode <> 0 then begin
      MsgBox('Whisper Toggle validation failed. Setup will stop before launching the app. See: ' + SmokeLog, mbError, MB_OK);
      Abort;
    end;
  end;
end;

[UninstallRun]
; Stop ONLY the python(w) processes that are running from THIS install dir
; ({app}), matched by their real ExecutablePath. Never an image-wide taskkill
; by image name -- that would kill every unrelated python/pythonw on the box.
; NB: literal PowerShell "{" is escaped as "{{" for Inno; "{app}" stays a
; single-brace Inno constant so it expands to the install path.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-CimInstance Win32_Process | Where-Object {{ ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.ExecutablePath -like '{app}\*' } | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } """; \
    Flags: runhidden; \
    RunOnceId: "KillWhisperToggle"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\python\__pycache__"
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\python\Lib\site-packages\*.pyc"
