; Windows installer for Ignite (Inno Setup 6). Built by packaging/build.py:
;   iscc /DAppVersion=0.1.0 packaging\installer.iss
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\Ignite.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; Folder holding the PyInstaller output (Ignite\); the installer goes there too.
#ifndef DistDir
  #define DistDir "..\dist"
#endif

[Setup]
AppId={{6F1D2C3B-8A47-4E5B-9C0D-5A1E7B3F2D84}
AppName=Ignite
AppVersion={#AppVersion}
AppPublisher=Ignite
DefaultDirName={localappdata}\Programs\Ignite
; Same AppId as ShiftyTool, so this upgrades it: always into ...\Ignite, not the old ...\ShiftyTool folder.
UsePreviousAppDir=no
DefaultGroupName=Ignite
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#DistDir}
OutputBaseFilename=Ignite-Setup-{#AppVersion}
SetupIconFile=ignite.ico
UninstallDisplayIcon={app}\Ignite.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#DistDir}\Ignite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; ShiftyTool's program folder and shortcuts, from before the rename: Ignite installs into its own folder instead.
Type: filesandordirs; Name: "{localappdata}\Programs\ShiftyTool"
Type: files; Name: "{autoprograms}\ShiftyTool.lnk"
Type: files; Name: "{autodesktop}\ShiftyTool.lnk"

[Icons]
Name: "{autoprograms}\Ignite"; Filename: "{app}\Ignite.exe"
Name: "{autodesktop}\Ignite"; Filename: "{app}\Ignite.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Ignite.exe"; Description: "Launch Ignite"; Flags: nowait postinstall skipifsilent
; Ignite's own updater runs this setup silently with /RELAUNCH=1 ("Restart and update"): start the new version.
Filename: "{app}\Ignite.exe"; Flags: nowait; Check: RelaunchAfterUpdate

; Settings, the log, and keychain entries are kept on uninstall, so a reinstall
; picks up where you left off. Delete %APPDATA%\Ignite to reset.

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;
