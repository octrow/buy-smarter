; Windows setup for the desktop app: per-user install (no admin rights), Start-menu entry, uninstaller.
; Built by desktop.yml after PyInstaller:
;   ISCC /DAppVersion=<version> /DSourceDir=<repo>\dist\buysmarter /O<repo>\out packaging\windows\buysmarter.iss
#ifndef AppVersion
  #error Pass /DAppVersion=<version>
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<one-dir build>
#endif

[Setup]
; Never change AppId: Windows matches upgrades and the uninstaller by it.
AppId={{6F3D2B8E-4C1A-4E7B-9D52-8A0C3F1B7E64}
AppName=buy-smarter
AppVersion={#AppVersion}
AppVerName=buy-smarter {#AppVersion}
AppPublisher=Daniil Petrov
AppPublisherURL=https://github.com/octrow/buy-smarter
AppSupportURL=https://github.com/octrow/buy-smarter/issues
PrivilegesRequired=lowest
DefaultDirName={autopf}\buysmarter
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=buysmarter-{#AppVersion}-windows-x64-setup
SetupIconFile=buysmarter.ico
UninstallDisplayIcon={app}\buysmarter.exe
UninstallDisplayName=buy-smarter
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An upgrade replaces the whole build: files a newer PyInstaller no longer ships must not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\buy-smarter"; Filename: "{app}\buysmarter.exe"
Name: "{autodesktop}\buy-smarter"; Filename: "{app}\buysmarter.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\buysmarter.exe"; Description: "{cm:LaunchProgram,buy-smarter}"; Flags: nowait postinstall skipifsilent
