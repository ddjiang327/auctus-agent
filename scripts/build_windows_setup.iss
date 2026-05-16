; Auctus Agent Windows Installer Script (Inno Setup)
; This script creates a standard Windows .exe installer
;
; Requirements:
; - Inno Setup 6.0+ (https://jrsoftware.org/isinfo.php)
; - Python 3.10+ embedded (downloaded during build)
;
; To build:
;   iscc build_windows_setup.iss

#define MyAppName "Auctus Agent"
#define MyAppVersion "VERSION_PLACEHOLDER"
#define MyAppPublisher "Auctus"
#define MyAppURL "https://github.com/ddjiang327/auctus-agent"
#define MyAppExeName "Auctus Agent.exe"

[Setup]
; NOTE: The value of AppId uniquely identifies this application.
AppId={{B5A8D7E3-F2C1-4B9A-8E6F-3D2C1B4A9E7F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output settings
OutputDir=..\..\release
OutputBaseFilename=Auctus-Agent-windows-v{#MyAppVersion}-setup
; Compression
Compression=lzma2
SolidCompression=yes
; Modern wizard style
WizardStyle=modern
; Privileges
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Appearance
SetupIconFile=..\app\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; Architecture
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
; Main application files
Source: "..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\auctus"

[Code]
// Pascal Script for custom install logic

var
  PythonDownloadPage: string;
  PythonVersion: string;

function IsPythonInstalled: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('python', '-c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  PythonVersion := '3.11.8';
  PythonDownloadPage := 'https://www.python.org/ftp/python/' + PythonVersion + '/python-' + PythonVersion + '-embed-amd64.zip';
end;

procedure InitializeWizard;
begin
  // Custom initialization if needed
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Post-installation tasks
    // Create data directories
    ForceDirectories(ExpandConstant('{localappdata}\Auctus Agent\data'));
    ForceDirectories(ExpandConstant('{localappdata}\Auctus Agent\inputs'));
    ForceDirectories(ExpandConstant('{localappdata}\Auctus Agent\outputs'));
    ForceDirectories(ExpandConstant('{localappdata}\Auctus Agent\logs'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Stop the running server if any
    Exec('taskkill', '/F /IM python.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec('taskkill', '/F /IM "Auctus Agent.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
