; installer.iss — Inno Setup script for Whisper Toggle
;
; Prerequisites before compiling this installer:
;   1. Build dictate-toggle.exe (run build.ps1)
;   2. Install Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
;   3. Open this file in Inno Setup Compiler → Build → Compile
;
; The installer:
;   - Installs dictate-toggle.exe (standalone, no Python needed)
;   - Installs app.py and start-api.bat (API server, needs Python + venv)
;   - Creates Start Menu shortcuts
;   - Registers uninstaller
;   - Optionally sets up Python venv for the API server

[Setup]
AppId={{A7D3F2E1-B8C4-4F5A-9E6D-1C2B3A4F5E6D}
AppName=Whisper Toggle
AppVersion=1.0
AppPublisher=Tim Lewis
AppPublisherURL=https://github.com/tolewis/Whisper-Toggle
DefaultDirName={autopf}\Whisper Toggle
DefaultGroupName=Whisper Toggle
OutputBaseFilename=WhisperToggle-Setup-1.0
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Main executable (built by PyInstaller)
Source: "..\dist\dictate-toggle.exe"; DestDir: "{app}"; Flags: ignoreversion

; API server files
Source: "..\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "start-api.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Whisper Toggle"; Filename: "{app}\dictate-toggle.exe"; Parameters: "--hotkey ""ctrl+`"""; Comment: "Voice dictation toggle (Ctrl+`)"
Name: "{group}\Start Whisper API"; Filename: "{app}\start-api.bat"; Comment: "Start the Whisper transcription API server"
Name: "{group}\Uninstall Whisper Toggle"; Filename: "{uninstallexe}"
Name: "{userstartup}\Whisper Toggle"; Filename: "{app}\dictate-toggle.exe"; Parameters: "--hotkey ""ctrl+`"""; Comment: "Auto-start voice dictation"; Tasks: autostart

[Tasks]
Name: "autostart"; Description: "Start Whisper Toggle when I log in"; GroupDescription: "Startup:"

[Run]
; Offer to set up the API venv after install
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -Command ""& {{ python -m venv '{app}\venv'; & '{app}\venv\Scripts\pip' install faster-whisper fastapi uvicorn; Write-Host 'Done. Press Enter to close.'; Read-Host }}"""; \
    Description: "Set up Python environment for API server (requires Python 3.9+)"; \
    Flags: postinstall skipifsilent runascurrentuser; \
    StatusMsg: "Setting up Python environment..."

Filename: "{app}\start-api.bat"; \
    Description: "Start the API server now"; \
    Flags: postinstall skipifsilent nowait runascurrentuser unchecked

Filename: "{app}\dictate-toggle.exe"; \
    Parameters: "--hotkey ""ctrl+`"""; \
    Description: "Launch Whisper Toggle now"; \
    Flags: postinstall skipifsilent nowait runascurrentuser unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// Update start-api.bat to use the installed venv path
procedure CurStepChanged(CurStep: TSetupStep);
var
    BatFile: string;
    Content: string;
begin
    if CurStep = ssPostInstall then
    begin
        BatFile := ExpandConstant('{app}\start-api.bat');
        if LoadStringFromFile(BatFile, Content) then
        begin
            StringChangeEx(Content,
                '%LOCALAPPDATA%\whisper-venv\Scripts\python.exe',
                ExpandConstant('{app}\venv\Scripts\python.exe'),
                True);
            StringChangeEx(Content,
                '%LOCALAPPDATA%\whisper-venv\Scripts\python',
                ExpandConstant('{app}\venv\Scripts\python'),
                True);
            SaveStringToFile(BatFile, Content, False);
        end;
    end;
end;
