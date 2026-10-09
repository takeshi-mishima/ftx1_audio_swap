' start_ftx1_audio_swap.vbs : start ftx1_audio_swap.py without a console window
'
' rigctld for the FTX-1 must be running (or started later: the tool keeps retrying until it connects).
'
' (c) 2026 Takeshi Mishima JK1VUZ

Set shell = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)

' --- Setting ---
' MODE : fixed  = MAIN on LEFT (WSJT-X #1) and SUB on RIGHT (WSJT-X #2), always
'        follow = side selected on the panel (VS) on LEFT
'        main   = MAIN on LEFT
'        sub    = SUB on LEFT
MODE = "fixed"

' Extra options, e.g. "--port 4534 --interval 2" (leave empty for defaults)
EXTRA_OPTS = ""

' pythonw.exe must be in PATH (python.org installer "Add python.exe to PATH")
cmd = "pythonw """ & currentDir & "\ftx1_audio_swap.py"" --mode " & MODE & " " & EXTRA_OPTS

shell.CurrentDirectory = currentDir
shell.Run cmd, 0, False
