; Installateur d'AGOA Launcher (construit par GitHub Actions : makensis /DVERSION=x.y.z installer.nsi)
Unicode true
!include "MUI2.nsh"

!define APPNAME "AGOA Launcher"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\AGOALauncher"
!ifndef VERSION
  !define VERSION "0.0.0"
!endif

Name "${APPNAME}"
OutFile "dist\AGOA-Launcher-Setup-${VERSION}.exe"
InstallDir "C:\AGOA\Launcher"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "AGOA - Agence Rémi Thollet"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1036 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=1036 "FileDescription" "Installateur ${APPNAME}"
VIAddVersionKey /LANG=1036 "CompanyName" "Agence Rémi Thollet Architecte"
VIAddVersionKey /LANG=1036 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1036 "LegalCopyright" "Agence Rémi Thollet Architecte"

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Installation d'${APPNAME}"
!define MUI_WELCOMEPAGE_TEXT "Ce programme installe ${APPNAME}, qui installe et met à jour toutes les applications AGOA de l'agence dans C:\AGOA.$\r$\n$\r$\nVos applications déjà installées sont conservées."
!define MUI_FINISHPAGE_RUN "$INSTDIR\AGOA-Launcher.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Lancer ${APPNAME}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "French"

Section "Install"
  SetOutPath "$INSTDIR"
  File "dist\AGOA-Launcher.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; ancienne version « exe seul » posée directement dans C:\AGOA
  Delete "$INSTDIR\..\AGOA-Launcher.exe"

  CreateShortcut "$SMPROGRAMS\${APPNAME}.lnk" "$INSTDIR\AGOA-Launcher.exe"
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\AGOA-Launcher.exe"

  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${REGKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${REGKEY}" "Publisher" "Agence Rémi Thollet Architecte"
  WriteRegStr HKCU "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\AGOA-Launcher.exe"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; ne touche pas aux applications AGOA installées dans C:\AGOA\<programme>
  Delete "$INSTDIR\AGOA-Launcher.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  DeleteRegKey HKCU "${REGKEY}"
SectionEnd
