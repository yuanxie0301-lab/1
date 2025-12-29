\
; Inno Setup script (compile in Inno Setup)
#define MyAppName "AIReception"
#define MyAppVersion "4.0"
#define MyAppExeName "AIReception.exe"

[Setup]
AppId={{B1E2B3A4-AC00-4E55-9D90-9E7D8B1F2A3B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={pf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AIReception_Setup
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

; IMPORTANT:
; User data is stored in %LOCALAPPDATA%\AIReception\user_data (kept on uninstall).
; So uninstall will remove program files + dependencies only, and keep your local knowledge base.

