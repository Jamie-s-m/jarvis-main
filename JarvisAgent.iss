; Inno Setup installer script for the JARVIS AI Agent desktop app
; Build with: ISCC.exe JarvisAgent.iss

[Setup]
AppName=JARVIS AI Agent
AppVersion=1.0.0
DefaultDirName={autopf}\JARVIS AI Agent
DefaultGroupName=JARVIS AI Agent
OutputBaseFilename=JARVIS_AI_Agent_Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
UninstallDisplayIcon={app}\JarvisAgent.exe

[Files]
Source: "dist\JarvisAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\JARVIS AI Agent"; Filename: "{app}\JarvisAgent.exe"
Name: "{commondesktop}\JARVIS AI Agent"; Filename: "{app}\JarvisAgent.exe"

[Run]
Filename: "{app}\JarvisAgent.exe"; Description: "Launch JARVIS AI Agent"; Flags: nowait postinstall skipifsilent
