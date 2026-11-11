# Job Hunter laptop: never sleep or hibernate by itself - on charger AND on battery - and keep running
# when the lid is closed. The screen may still turn off; that does not stop Job Hunter.
#
# Run once (PowerShell):
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Documents\JobHunter\tools\no_sleep.ps1"
# Undo (back to Windows defaults):
#   powercfg /restoredefaultschemes

$ErrorActionPreference = 'Stop'

powercfg /change standby-timeout-ac 0      # sleep on charger: never
powercfg /change standby-timeout-dc 0      # sleep on battery: never
powercfg /change hibernate-timeout-ac 0    # hibernate on charger: never
powercfg /change hibernate-timeout-dc 0    # hibernate on battery: never
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0   # lid closed on charger: do nothing
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0   # lid closed on battery: do nothing
powercfg /setactive SCHEME_CURRENT

Write-Host ""
Write-Host "Done. This laptop will not sleep by itself any more (charger or battery, lid open or closed)." -ForegroundColor Green
Write-Host "It still sleeps/shuts down when you choose Sleep or Shut down, or when the battery runs out."
Write-Host "Keep it on the charger. To undo: powercfg /restoredefaultschemes"
