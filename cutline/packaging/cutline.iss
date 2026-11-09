; Installateur Windows de Cutline (Inno Setup 6)
#define AppVersion GetEnv("CUTLINE_VERSION")
#if AppVersion == ""
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6F1C2B8E-4D2A-4C1B-9E55-CUTLINE00001}
AppName=Cutline
AppVersion={#AppVersion}
AppPublisher=Cutline
DefaultDirName={localappdata}\Programs\Cutline
DefaultGroupName=Cutline
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Cutline_Installation
Compression=lzma2/fast
SolidCompression=no
DisableProgramGroupPage=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "..\dist\Cutline\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Cutline"; Filename: "{app}\Cutline.exe"
Name: "{userdesktop}\Cutline"; Filename: "{app}\Cutline.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Cutline.exe"; Description: "Lancer Cutline"; Flags: nowait postinstall skipifsilent
