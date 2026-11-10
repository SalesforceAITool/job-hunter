' Job Hunter launcher (Desktop shortcut target): starts app.py with no console window.
' Order: data\python_path.txt written by install_windows.bat (pythonw.exe next to it) -> legacy data\pythonw.txt
'        -> pyw -3 -> pythonw -> Anaconda / Miniconda / python.org folders -> python (hidden).
Option Explicit
Dim fso, sh, dir, app, ok, exe, cand, pf, la, up, pd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
app = """" & dir & "\app.py"""

ok = False
exe = ReadFirstLine(dir & "\data\python_path.txt")
If Len(exe) > 0 And fso.FileExists(exe) Then ok = RunWith(exe)
If Not ok Then
  exe = ReadFirstLine(dir & "\data\pythonw.txt")
  If Len(exe) > 0 And fso.FileExists(exe) Then ok = RunWith(exe)
End If
If Not ok Then ok = TryRun("pyw -3 " & app)
If Not ok Then ok = TryRun("pythonw " & app)
If Not ok Then
  up = sh.ExpandEnvironmentStrings("%USERPROFILE%")
  la = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
  pd = sh.ExpandEnvironmentStrings("%ProgramData%")
  For Each cand In Array(up & "\anaconda3\python.exe", up & "\miniconda3\python.exe", _
                         la & "\anaconda3\python.exe", la & "\miniconda3\python.exe", _
                         pd & "\anaconda3\python.exe", pd & "\miniconda3\python.exe")
    If Not ok And fso.FileExists(cand) Then ok = RunWith(cand)
  Next
  pf = la & "\Programs\Python"
  If Not ok And fso.FolderExists(pf) Then
    For Each cand In fso.GetFolder(pf).SubFolders
      If Not ok And LCase(Left(cand.Name, 7)) = "python3" And fso.FileExists(cand.Path & "\python.exe") Then
        ok = RunWith(cand.Path & "\python.exe")
      End If
    Next
  End If
End If
If Not ok Then ok = TryRun("python " & app)
If Not ok Then
  MsgBox "Python was not found. Install Python 3 from https://www.python.org/downloads/windows/" & vbCrLf & _
         "(tick 'Add python.exe to PATH'), then run install_windows.bat again.", vbCritical, "Job Hunter"
End If

' Run app.py with the interpreter at path p (python.exe or pythonw.exe): prefers pythonw.exe in the same folder
' and puts that folder plus Anaconda's DLL folders (Library\bin) on PATH so ssl/sqlite3 load without "conda activate".
Function RunWith(p)
  Dim folder, w, env
  folder = fso.GetParentFolderName(p)
  w = folder & "\pythonw.exe"
  If Not fso.FileExists(w) Then w = p
  Set env = sh.Environment("Process")
  env("PATH") = folder & ";" & folder & "\Library\bin;" & folder & "\Scripts;" & env("PATH")
  RunWith = TryRun("""" & w & """ " & app)
End Function

Function ReadFirstLine(path)
  Dim f
  ReadFirstLine = ""
  If Not fso.FileExists(path) Then Exit Function
  Set f = fso.OpenTextFile(path, 1)
  If Not f.AtEndOfStream Then ReadFirstLine = Trim(f.ReadLine)
  f.Close
  If Left(ReadFirstLine, 1) = """" Then ReadFirstLine = Replace(ReadFirstLine, """", "")
End Function

Function TryRun(c)
  On Error Resume Next
  Err.Clear
  sh.Run c, 0, False
  TryRun = (Err.Number = 0)
  Err.Clear
  On Error GoTo 0
End Function
