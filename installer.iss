; Script Inno Setup pour "Convertisseur d'images"
; Compile ce fichier avec Inno Setup Compiler (ISCC.exe ou l'interface graphique),
; ou lance simplement build.bat qui construit aussi l'exécutable.
; -> produit installer_output\ConvertisseurImages_Setup.exe
; Fichier enregistré en UTF-8 avec BOM, sinon les accents s'affichent mal dans l'installateur.

#define MyAppName "Convertisseur d'images"
#define MyAppVersion "1.0"
#define MyAppExeName "ConvertisseurImages.exe"

[Setup]
; AppId propre à cette application (différent de celui du Compresseur d'images)
AppId={{30A12602-E1E9-40EA-80A6-AD007FC3514A}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ConvertisseurImages_Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis supplémentaires :"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
