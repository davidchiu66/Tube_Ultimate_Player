#define MyAppName "Tube_Ultimate_Player"
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppName={#MyAppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir={#OutputDir}
OutputBaseFilename=Tube_Ultimate_Player_setup_v{#AppVersion}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin

[Files]
Source: "{#ProjectRoot}\dist\Tube_Ultimate_Player\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "{#ProjectRoot}\3rdpart\yt-dlp.exe"; DestDir: "{app}\3rdpart"; Flags: ignoreversion
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\app_version.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Tube_Ultimate_Player"; Filename: "{app}\Tube_Ultimate_Player.exe"
Name: "{autodesktop}\Tube_Ultimate_Player"; Filename: "{app}\Tube_Ultimate_Player.exe"

[Run]
Filename: "{app}\Tube_Ultimate_Player.exe"; Description: "启动 Tube_Ultimate_Player"; Flags: nowait postinstall skipifsilent
