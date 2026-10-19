; Tauri removes the autostart value (named after the product) on uninstall.
; Our settings, secrets and logs live in "%APPDATA%\GoXLR Discord Sync"
; (shared with the Python version), not in the folder named after the
; identifier, so delete it too when the user asks to delete the app data.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    RmDir /r "$APPDATA\${PRODUCTNAME}"
  ${EndIf}
!macroend
