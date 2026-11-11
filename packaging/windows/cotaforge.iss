; Inno Setup script of the cotaforge installer for Windows (Inno Setup 6.3 or newer).
;
; Build it with packaging\windows\build.ps1 (it stages the files and calls ISCC), or by hand:
;   ISCC /DAppVersion=0.7.0 /DSourceDir=<staged folder> /DOutputDir=<folder for the .exe> cotaforge.iss
;
; Per user, no administrator rights, fixed folder {localappdata}\Programs\cotaforge (so the projects keep working
; after an update). FreeCAD and Claude Code are detected, never bundled.
; This file must stay saved as UTF-8 with BOM: the texts for the user have accents.

#ifndef AppVersion
  #error AppVersion is missing: pass /DAppVersion=x.y.z
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\stage"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
; Never change the AppId: it is how a newer installer recognises (and replaces) an older install.
AppId={{8D2F6A94-3C71-4E58-A1B7-5F90C2E4D613}
AppName=CotaForge
AppVersion={#AppVersion}
AppVerName=CotaForge {#AppVersion}
AppPublisher=CotaForge
DefaultDirName={localappdata}\Programs\cotaforge
DisableDirPage=yes
UsePreviousAppDir=no
DefaultGroupName=cotaforge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=cotaforge-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Not the Restart Manager: what keeps files in use is a cad server started by a Claude session (often running in the
; background after its window is closed). [Code] finds those processes by their path inside {app}, says what they
; are, asks before closing them (a FreeCAD design may be unsaved) and closes them by PID. Nothing else is closed.
CloseApplications=no
RestartApplications=no
UninstallDisplayName=CotaForge {#AppVersion}
UninstallDisplayIcon={app}\cotaforge.ico
SetupIconFile=cotaforge.ico

[Languages]
; The wizard asks which language to use and offers the one of Windows. The choice is also the engineer's language in
; his projects: [Code] passes it to install-info, which keeps it in install.json (cotaforge init reads it from there).
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
spanish.ManualFile=manual-usuario.md
english.ManualFile=user-manual.md
spanish.ShortcutNew=Crear proyecto nuevo (CotaForge)
english.ShortcutNew=Create new project (CotaForge)
spanish.ShortcutNewHint=Crea un proyecto de diseño nuevo
english.ShortcutNewHint=Creates a new design project
spanish.ShortcutOpen=Abrir proyecto existente (CotaForge)
english.ShortcutOpen=Open existing project (CotaForge)
spanish.ShortcutOpenHint=Abre y actualiza un proyecto existente
english.ShortcutOpenHint=Opens and refreshes an existing project
spanish.ShortcutManual=Manual de CotaForge
english.ShortcutManual=CotaForge manual
spanish.ShortcutManualHint=Manual de usuario
english.ShortcutManualHint=User manual
spanish.ShortcutUninstall=Desinstalar CotaForge
english.ShortcutUninstall=Uninstall CotaForge
spanish.BrowseFreeCADTitle=Elige la carpeta de instalación de FreeCAD
english.BrowseFreeCADTitle=Choose the FreeCAD install folder
spanish.ClaudeMissingTitle=Claude Code no está instalado en este equipo
english.ClaudeMissingTitle=Claude Code is not installed on this computer
spanish.ClaudeMissingWhy=CotaForge se usa desde Claude Code, que cada persona instala e inicia con su propia cuenta. ¿Qué quieres hacer?
english.ClaudeMissingWhy=CotaForge is used from Claude Code, which each person installs and signs in to with their own account. What do you want to do?
spanish.ClaudeInstallNow=Instalar Claude Code ahora (se abre una ventana con el instalador oficial)
english.ClaudeInstallNow=Install Claude Code now (a window opens with the official installer)
spanish.ClaudeSkip=Continuar sin instalarlo (lo instalaré yo; luego los accesos directos de CotaForge registrarán el plugin)
english.ClaudeSkip=Continue without it (I will install it; the CotaForge shortcuts will then register the plugin)
spanish.FreeCADMissingTitle=No se encontró FreeCAD 1.1
english.FreeCADMissingTitle=FreeCAD 1.1 was not found
spanish.FreeCADWhy=CotaForge usa FreeCAD 1.1 como software de CAD, y no lo incluye (pesa más de 500 MB). Si ya lo tienes en otra carpeta, elígela aquí. Si lo instalas después en su carpeta de siempre, CotaForge lo encontrará solo.
english.FreeCADWhy=CotaForge uses FreeCAD 1.1 as its CAD software and does not include it (it weighs over 500 MB). If you already have it in another folder, choose it here. If you install it later in its usual folder, CotaForge will find it on its own.
spanish.FreeCADFolder=Carpeta de FreeCAD (la que contiene bin\FreeCADCmd.exe). Déjala vacía para continuar sin FreeCAD:
english.FreeCADFolder=FreeCAD folder (the one that holds bin\FreeCADCmd.exe). Leave it empty to continue without FreeCAD:
spanish.Browse=Examinar...
english.Browse=Browse...
spanish.OpenFreeCADDownload=Abrir la página de descarga de FreeCAD
english.OpenFreeCADDownload=Open the FreeCAD download page
spanish.NoFreeCADConfirm=Sin FreeCAD no podrás diseñar hasta instalarlo. ¿Continuar sin FreeCAD?
english.NoFreeCADConfirm=Without FreeCAD you cannot design until you install it. Continue without FreeCAD?
spanish.NotFreeCADRoot=La carpeta elegida no contiene bin\FreeCADCmd.exe. Elige la carpeta de instalación de FreeCAD.
english.NotFreeCADRoot=The chosen folder does not hold bin\FreeCADCmd.exe. Choose the FreeCAD install folder.
spanish.NotFreeCAD11=CotaForge está probado con FreeCAD 1.1. Esta carpeta no parece de esa versión; se usará de todos modos.
english.NotFreeCAD11=CotaForge is tested with FreeCAD 1.1. This folder does not look like that version; it will be used anyway.
spanish.NotFound=no encontrado (CotaForge lo buscará cuando lo instales)
english.NotFound=not found (CotaForge will look for it once you install it)
spanish.ClaudeWillInstall=se instalará con el instalador oficial
english.ClaudeWillInstall=will be installed with the official installer
spanish.ClaudeNotFound=no encontrado (el plugin se registrará desde los accesos directos)
english.ClaudeNotFound=not found (the plugin will be registered from the shortcuts)
spanish.InUse=CotaForge está en uso: una sesión de Claude (la app de escritorio o Claude Code) tiene abierto su servidor del CAD. Pasa aunque hayas cerrado la ventana de Claude, porque la app sigue corriendo en segundo plano.
english.InUse=CotaForge is in use: a Claude session (the desktop app or Claude Code) has its CAD server open. This happens even if you closed the Claude window, because the app keeps running in the background.
spanish.InUseSave=Si tienes un diseño abierto en FreeCAD, guárdalo antes de seguir.
english.InUseSave=If you have a design open in FreeCAD, save it before going on.
spanish.InUseClose=¿Cierro ese servidor y continúo? Tu sesión de Claude seguirá abierta; para volver a usar el CAD en ella, reconecta el servidor cad con /mcp o abre una sesión nueva.
english.InUseClose=Shall I close that server and go on? Your Claude session stays open; to use the CAD in it again, reconnect the cad server with /mcp or open a new session.
spanish.CloseClaudeAndRetry=Cierra la app de Claude por completo (también desde su ícono junto al reloj) y %1.
english.CloseClaudeAndRetry=Close the Claude app completely (also from its icon next to the clock) and %1.
spanish.CouldNotClose=No se pudo cerrar el servidor de CotaForge. Cierra la app de Claude por completo y %1.
english.CouldNotClose=The CotaForge server could not be closed. Close the Claude app completely and %1.
spanish.RetryInstall=vuelve a ejecutar el instalador
english.RetryInstall=run the installer again
spanish.RetryUninstall=vuelve a desinstalar
english.RetryUninstall=uninstall again
spanish.InstallingClaude=Instalando Claude Code...
english.InstallingClaude=Installing Claude Code...
spanish.ClaudeInstallFailed=No se pudo instalar Claude Code. Instálalo desde https://claude.com/product/claude-code; luego los accesos directos de CotaForge registrarán el plugin.
english.ClaudeInstallFailed=Claude Code could not be installed. Install it from https://claude.com/product/claude-code; the CotaForge shortcuts will then register the plugin.
spanish.FinalCheckFailed=CotaForge se copió, pero la comprobación final falló. Vuelve a ejecutar el instalador; si persiste, avisa con el código %1.
english.FinalCheckFailed=CotaForge was copied, but the final check failed. Run the installer again; if it persists, report the code %1.
spanish.RegisteringPlugin=Registrando el plugin en Claude Code...
english.RegisteringPlugin=Registering the plugin in Claude Code...
spanish.PluginRegisterFailed=No se pudo registrar el plugin en Claude Code. No es grave: se registrará al usar "Crear proyecto nuevo (CotaForge)" o "Abrir proyecto existente (CotaForge)".
english.PluginRegisterFailed=The plugin could not be registered in Claude Code. It is not serious: it will be registered when you use "Create new project (CotaForge)" or "Open existing project (CotaForge)".

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; An update must not leave files of an older python\ or plugin behind.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\marketplace"
Type: filesandordirs; Name: "{app}\docs"
; The shortcuts of 0.8.x had other names: an update must not leave them next to the new ones.
Type: files; Name: "{autoprograms}\cotaforge\Nuevo proyecto cotaforge.lnk"
Type: files; Name: "{autoprograms}\cotaforge\Abrir proyecto cotaforge.lnk"
; The language may have changed since the last install: the shortcuts of the other one go too.
Type: files; Name: "{autoprograms}\cotaforge\Crear proyecto nuevo (CotaForge).lnk"
Type: files; Name: "{autoprograms}\cotaforge\Abrir proyecto existente (CotaForge).lnk"
Type: files; Name: "{autoprograms}\cotaforge\Manual de CotaForge.lnk"
Type: files; Name: "{autoprograms}\cotaforge\Desinstalar CotaForge.lnk"
Type: files; Name: "{autoprograms}\cotaforge\Create new project (CotaForge).lnk"
Type: files; Name: "{autoprograms}\cotaforge\Open existing project (CotaForge).lnk"
Type: files; Name: "{autoprograms}\cotaforge\CotaForge manual.lnk"
Type: files; Name: "{autoprograms}\cotaforge\Uninstall CotaForge.lnk"

[Icons]
; Each launcher has its own icon (a green plus to create, an amber folder to open): they are easy to confuse.
Name: "{autoprograms}\cotaforge\{cm:ShortcutNew}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m cotaforge_mcp.cli new"; WorkingDir: "{userdocs}"; IconFilename: "{app}\cotaforge-new.ico"; Comment: "{cm:ShortcutNewHint}"
Name: "{autoprograms}\cotaforge\{cm:ShortcutOpen}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m cotaforge_mcp.cli open"; WorkingDir: "{userdocs}"; IconFilename: "{app}\cotaforge-open.ico"; Comment: "{cm:ShortcutOpenHint}"
Name: "{autoprograms}\cotaforge\{cm:ShortcutManual}"; Filename: "{win}\notepad.exe"; Parameters: """{app}\docs\{cm:ManualFile}"""; IconFilename: "{app}\cotaforge.ico"; Comment: "{cm:ShortcutManualHint}"
Name: "{autoprograms}\cotaforge\{cm:ShortcutUninstall}"; Filename: "{uninstallexe}"

[UninstallRun]
; Takes the plugin out of Claude Code. Not fatal: Claude Code may be gone already.
Filename: "{app}\python\python.exe"; Parameters: "-I -m cotaforge_mcp.cli plugin unregister"; Flags: runhidden skipifdoesntexist; RunOnceId: "UnregisterPlugin"

[UninstallDelete]
; Files made after the install (byte-code caches, install.json) are not in the uninstall log. The projects are
; elsewhere and are never touched.
Type: filesandordirs; Name: "{app}"

[Code]
const
  FreeCADUrl = 'https://www.freecad.org/downloads.php';
  ClaudeInstallArgs = '-NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"';

var
  FreeCADRoot: String;
  ClaudePath: String;
  InstallClaudeNow: Boolean;
  ClaudePage: TInputOptionWizardPage;
  { A query page, not an input-dir page: the dir page refuses an empty folder, and empty means "continue without". }
  FreeCADPage: TInputQueryWizardPage;

{ --- detection ----------------------------------------------------------------------------------------------- }

function IsFreeCADRoot(const Dir: String): Boolean;
begin
  Result := (Dir <> '') and FileExists(AddBackslash(Dir) + 'bin\FreeCADCmd.exe');
end;

procedure CollectFromFolders(const Pattern: String; Found: TStringList);
var
  Rec: TFindRec;
  Base: String;
begin
  Base := ExtractFilePath(Pattern);
  if FindFirst(Pattern, Rec) then
  begin
    try
      repeat
        if (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          if IsFreeCADRoot(Base + Rec.Name) then
            Found.Add(Base + Rec.Name);
      until not FindNext(Rec);
    finally
      FindClose(Rec);
    end;
  end;
end;

procedure CollectFromRegistry(const RootKey: Integer; Found: TStringList);
var
  Keys: TArrayOfString;
  I: Integer;
  SubKey, DisplayName, Location: String;
begin
  if RegGetSubkeyNames(RootKey, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', Keys) then
  begin
    for I := 0 to GetArrayLength(Keys) - 1 do
    begin
      SubKey := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\' + Keys[I];
      if RegQueryStringValue(RootKey, SubKey, 'DisplayName', DisplayName) then
      begin
        if Pos('freecad', Lowercase(DisplayName)) = 1 then
        begin
          if RegQueryStringValue(RootKey, SubKey, 'InstallLocation', Location) then
          begin
            Location := RemoveBackslashUnlessRoot(Location);
            if IsFreeCADRoot(Location) then
              Found.Add(Location);
          end;
        end;
      end;
    end;
  end;
end;

{ The FreeCAD folder to use: the newest one, preferring a 1.1 install. Empty when there is none. }
function DetectFreeCAD(): String;
var
  Found: TStringList;
  I: Integer;
begin
  Result := '';
  Found := TStringList.Create;
  try
    Found.Sorted := True;
    CollectFromFolders(ExpandConstant('{commonpf64}') + '\FreeCAD*', Found);
    CollectFromFolders(ExpandConstant('{localappdata}') + '\Programs\FreeCAD*', Found);
    CollectFromRegistry(HKLM, Found);
    CollectFromRegistry(HKCU, Found);
    for I := Found.Count - 1 downto 0 do
    begin
      if (Result = '') and (Pos('1.1', Found[I]) > 0) then
        Result := Found[I];
    end;
    if (Result = '') and (Found.Count > 0) then
      Result := Found[Found.Count - 1];
  finally
    Found.Free;
  end;
end;

{ claude.exe on the PATH or in the native installer's folder. Empty when there is none. }
function DetectClaude(): String;
begin
  Result := FileSearch('claude.exe', GetEnv('PATH'));
  if Result = '' then
  begin
    Result := ExpandConstant('{%USERPROFILE}') + '\.local\bin\claude.exe';
    if not FileExists(Result) then
      Result := '';
  end;
end;

{ --- wizard -------------------------------------------------------------------------------------------------- }

procedure OpenFreeCADDownload(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('open', FreeCADUrl, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure BrowseFreeCAD(Sender: TObject);
var
  Dir: String;
begin
  Dir := FreeCADPage.Values[0];
  if BrowseForFolder(CustomMessage('BrowseFreeCADTitle'), Dir, False) then
    FreeCADPage.Values[0] := Dir;
end;

procedure InitializeWizard();
var
  BrowseButton, DownloadButton: TNewButton;
begin
  { /FREECAD=<folder> lets a scripted install name the FreeCAD folder. }
  FreeCADRoot := RemoveBackslashUnlessRoot(ExpandConstant('{param:FREECAD|}'));
  if not IsFreeCADRoot(FreeCADRoot) then
    FreeCADRoot := DetectFreeCAD();
  ClaudePath := DetectClaude();
  InstallClaudeNow := False;

  ClaudePage := CreateInputOptionPage(wpWelcome, 'Claude Code', CustomMessage('ClaudeMissingTitle'),
    CustomMessage('ClaudeMissingWhy'), True, False);
  ClaudePage.Add(CustomMessage('ClaudeInstallNow'));
  ClaudePage.Add(CustomMessage('ClaudeSkip'));
  ClaudePage.SelectedValueIndex := 0;

  FreeCADPage := CreateInputQueryPage(ClaudePage.ID, 'FreeCAD 1.1', CustomMessage('FreeCADMissingTitle'),
    CustomMessage('FreeCADWhy'));
  FreeCADPage.Add(CustomMessage('FreeCADFolder'), False);
  FreeCADPage.Values[0] := '';

  BrowseButton := TNewButton.Create(FreeCADPage);
  BrowseButton.Parent := FreeCADPage.Surface;
  BrowseButton.Caption := CustomMessage('Browse');
  BrowseButton.Left := 0;
  BrowseButton.Top := FreeCADPage.Edits[0].Top + FreeCADPage.Edits[0].Height + ScaleY(8);
  BrowseButton.Width := ScaleX(100);
  BrowseButton.Height := ScaleY(25);
  BrowseButton.OnClick := @BrowseFreeCAD;

  DownloadButton := TNewButton.Create(FreeCADPage);
  DownloadButton.Parent := FreeCADPage.Surface;
  DownloadButton.Caption := CustomMessage('OpenFreeCADDownload');
  DownloadButton.Left := 0;
  DownloadButton.Top := BrowseButton.Top + BrowseButton.Height + ScaleY(8);
  DownloadButton.Width := ScaleX(260);
  DownloadButton.Height := ScaleY(25);
  DownloadButton.OnClick := @OpenFreeCADDownload;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { A silent install never asks: without FreeCAD it continues without it, without Claude Code it installs nothing. }
  Result := False;
  if PageID = ClaudePage.ID then
    Result := (ClaudePath <> '') or WizardSilent()
  else if PageID = FreeCADPage.ID then
    Result := (FreeCADRoot <> '') or WizardSilent();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: String;
begin
  Result := True;
  if CurPageID = ClaudePage.ID then
  begin
    InstallClaudeNow := (ClaudePage.SelectedValueIndex = 0);
  end
  else if CurPageID = FreeCADPage.ID then
  begin
    Dir := Trim(FreeCADPage.Values[0]);
    if Dir = '' then
    begin
      Result := (MsgBox(CustomMessage('NoFreeCADConfirm'),
        mbConfirmation, MB_YESNO) = IDYES);
    end
    else if not IsFreeCADRoot(Dir) then
    begin
      MsgBox(CustomMessage('NotFreeCADRoot'),
        mbError, MB_OK);
      Result := False;
    end
    else
    begin
      FreeCADRoot := RemoveBackslashUnlessRoot(Dir);
      if Pos('1.1', FreeCADRoot) = 0 then
        MsgBox(CustomMessage('NotFreeCAD11'),
          mbInformation, MB_OK);
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
var
  Summary: String;
begin
  Summary := MemoDirInfo + NewLine + NewLine;
  if FreeCADRoot <> '' then
    Summary := Summary + 'FreeCAD:' + NewLine + Space + FreeCADRoot + NewLine
  else
    Summary := Summary + 'FreeCAD:' + NewLine + Space + CustomMessage('NotFound') + NewLine;
  Summary := Summary + NewLine;
  if ClaudePath <> '' then
    Summary := Summary + 'Claude Code:' + NewLine + Space + ClaudePath + NewLine
  else if InstallClaudeNow then
    Summary := Summary + 'Claude Code:' + NewLine + Space + CustomMessage('ClaudeWillInstall') + NewLine
  else
    Summary := Summary + 'Claude Code:' + NewLine + Space + CustomMessage('ClaudeNotFound') + NewLine;
  Result := Summary;
end;

{ --- programs that keep the install folder in use ------------------------------------------------------------- }

{ Adds to Pids every python.exe / pythonw.exe whose executable is under Prefix (lowercase, ends with a backslash). }
procedure CollectRunning(const Prefix: String; var Pids: TArrayOfInteger);
var
  Locator, Service, Items, Item: Variant;
  I, N: Integer;
  Path: String;
begin
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT ProcessId, ExecutablePath FROM Win32_Process ' +
      'WHERE Name = ''python.exe'' OR Name = ''pythonw.exe''');
    N := Items.Count;
    for I := 0 to N - 1 do
    begin
      Item := Items.ItemIndex(I);
      if not VarIsNull(Item.ExecutablePath) then
      begin
        Path := Item.ExecutablePath;  { a Variant: into a String first, Lowercase does not take a Variant }
        Path := Lowercase(Path);
        if Pos(Prefix, Path) = 1 then
        begin
          SetArrayLength(Pids, GetArrayLength(Pids) + 1);
          Pids[GetArrayLength(Pids) - 1] := Item.ProcessId;
        end;
      end;
    end;
  except
    Log('Could not list the running processes: ' + GetExceptionMessage);
  end;
end;

{ The cad servers (and launchers) running from the python folder of the install: a Claude session keeps one alive,
  sometimes in the background after its window is closed, and Windows refuses to replace or delete a running file. }
procedure RunningFromApp(var Pids: TArrayOfInteger);
var
  LongPrefix, ShortPrefix: String;
begin
  SetArrayLength(Pids, 0);
  LongPrefix := Lowercase(AddBackslash(ExpandConstant('{app}')) + 'python\');
  CollectRunning(LongPrefix, Pids);
  ShortPrefix := Lowercase(AddBackslash(GetShortName(ExpandConstant('{app}'))) + 'python\');
  if ShortPrefix <> LongPrefix then
    CollectRunning(ShortPrefix, Pids);
end;

procedure CloseByPid(const Pids: TArrayOfInteger);
var
  I, Code: Integer;
begin
  for I := 0 to GetArrayLength(Pids) - 1 do
  begin
    Log('Closing process ' + IntToStr(Pids[I]) + ': it runs from the install folder');
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/PID ' + IntToStr(Pids[I]) + ' /F', '', SW_HIDE,
      ewWaitUntilTerminated, Code);
  end;
end;

{ Empty when nothing runs from the install folder any more; otherwise why the install or uninstall has to stop.
  Asks first unless Silent (a scripted run asked for it): closing the server also ends the FreeCAD session it drives. }
function FreeInstallFolder(Silent: Boolean; const Retry: String): String;
var
  Pids: TArrayOfInteger;
  I: Integer;
  Gap: String;
begin
  Result := '';
  RunningFromApp(Pids);
  if GetArrayLength(Pids) = 0 then
    exit;
  { No line may start with '#': the preprocessor would read it as a directive. }
  Gap := #13#10 + #13#10;
  if not Silent then
  begin
    if MsgBox(CustomMessage('InUse') + Gap + CustomMessage('InUseSave') + Gap + CustomMessage('InUseClose'),
      mbConfirmation, MB_YESNO) <> IDYES then
    begin
      Result := FmtMessage(CustomMessage('CloseClaudeAndRetry'), [Retry]);
      exit;
    end;
  end;
  { A Claude session may start the server again right away: close whatever appears, for a few seconds. }
  for I := 1 to 20 do
  begin
    CloseByPid(Pids);
    Sleep(500);
    RunningFromApp(Pids);
    if GetArrayLength(Pids) = 0 then
      exit;
  end;
  Result := FmtMessage(CustomMessage('CouldNotClose'), [Retry]);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := FreeInstallFolder(WizardSilent(), CustomMessage('RetryInstall'));
end;

function InitializeUninstall(): Boolean;
var
  Reason: String;
begin
  Reason := FreeInstallFolder(UninstallSilent(), CustomMessage('RetryUninstall'));
  Result := (Reason = '');
  if not Result then
    SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
end;

{ --- install ------------------------------------------------------------------------------------------------- }

{ install.json is written by cotaforge itself (the command line carries any character of a path, a file written from
  here would not). }
function InstallInfoArgs(): String;
begin
  { The language of the wizard is the engineer's language: Inno names it 'spanish' or 'english', and cotaforge reads both. }
  Result := '-I -m cotaforge_mcp.cli install-info --version {#AppVersion} --language ' + ActiveLanguage;
  if FreeCADRoot <> '' then
    Result := Result + ' --freecad-root "' + FreeCADRoot + '"';
  if ClaudePath <> '' then
    Result := Result + ' --claude "' + ClaudePath + '"';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Python: String;
begin
  if CurStep = ssPostInstall then
  begin
    Python := ExpandConstant('{app}\python\python.exe');

    if (ClaudePath = '') and InstallClaudeNow then
    begin
      WizardForm.StatusLabel.Caption := CustomMessage('InstallingClaude');
      Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), ClaudeInstallArgs, '', SW_SHOW,
        ewWaitUntilTerminated, ResultCode);
      ClaudePath := DetectClaude();
      if ClaudePath = '' then
        SuppressibleMsgBox(CustomMessage('ClaudeInstallFailed'), mbInformation, MB_OK, IDOK);
    end;

    if not Exec(Python, InstallInfoArgs(), '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
       or (ResultCode <> 0) then
    begin
      Log('cotaforge install-info failed, exit code ' + IntToStr(ResultCode));
      SuppressibleMsgBox(FmtMessage(CustomMessage('FinalCheckFailed'), [IntToStr(ResultCode)]), mbError, MB_OK, IDOK);
    end
    else if ClaudePath <> '' then
    begin
      WizardForm.StatusLabel.Caption := CustomMessage('RegisteringPlugin');
      if not Exec(Python, '-I -m cotaforge_mcp.cli plugin register', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
         or (ResultCode <> 0) then
      begin
        Log('plugin register failed, exit code ' + IntToStr(ResultCode));
        SuppressibleMsgBox(CustomMessage('PluginRegisterFailed'), mbInformation, MB_OK, IDOK);
      end;
    end;
  end;
end;
