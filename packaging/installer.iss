#define MyAppName "Tube_Ultimate_Player"
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef OutputSuffix
  #define OutputSuffix ""
#endif

[Setup]
AppName={#MyAppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir={#OutputDir}
OutputBaseFilename=Tube_Ultimate_Player_setup_v{#AppVersion}{#OutputSuffix}
SetupIconFile={#ProjectRoot}\docs\assets\icons\app-icon.ico
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\Tube_Ultimate_Player.exe

[Files]
Source: "{#ProjectRoot}\dist\Tube_Ultimate_Player\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "{#ProjectRoot}\3rdpart\*"; DestDir: "{app}\3rdpart"; Flags: recursesubdirs ignoreversion
Source: "{#ProjectRoot}\docs\assets\icons\*"; DestDir: "{app}\docs\assets\icons"; Flags: recursesubdirs ignoreversion
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\app_version.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Tube_Ultimate_Player"; Filename: "{app}\Tube_Ultimate_Player.exe"; IconFilename: "{app}\Tube_Ultimate_Player.exe"
Name: "{autodesktop}\Tube_Ultimate_Player"; Filename: "{app}\Tube_Ultimate_Player.exe"; IconFilename: "{app}\Tube_Ultimate_Player.exe"

[Run]
Filename: "{app}\Tube_Ultimate_Player.exe"; Description: "启动 Tube_Ultimate_Player"; Flags: nowait postinstall skipifsilent
