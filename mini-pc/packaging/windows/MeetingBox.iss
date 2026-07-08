; Inno Setup script for the MeetingBox Windows desktop port.
; Build with:
;   "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" packaging\windows\MeetingBox.iss
; Produces: packaging\windows\Output\MeetingBoxSetup.exe
;
; Installs the PyInstaller one-dir payload (MeetingBox.exe + meetingbox-audio.exe
; + _internal\) into Program Files, seeds a per-machine device-ui.env under
; %PROGRAMDATA%\MeetingBox (only if absent, so upgrades keep the user's edits),
; and creates Start Menu / optional desktop shortcuts.

#define MyAppName "MeetingBox"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Lucratech Solutions"
#define MyAppExeName "MeetingBox.exe"
#define MyAppURL "https://meetingboxai.lucratechsol.com/"

[Setup]
AppId={{8B6E2C44-2E2C-49A2-9C9F-7F2E1B3A6D11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=MeetingBoxSetup
SetupIconFile=meetingbox.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller one-dir output.
Source: "dist\MeetingBox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Per-machine config seed (only copied if it does not already exist).
Source: "device-ui.env"; DestDir: "{commonappdata}\MeetingBox"; Flags: onlyifdoesntexist uninsneveruninstall

[Dirs]
Name: "{commonappdata}\MeetingBox"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Edit MeetingBox configuration"; Filename: "notepad.exe"; Parameters: """{commonappdata}\MeetingBox\device-ui.env"""
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
