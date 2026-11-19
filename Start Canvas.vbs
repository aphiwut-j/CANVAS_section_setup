Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
python = root & "\.venv\Scripts\pythonw.exe"
If Not files.FileExists(python) Then
    MsgBox "The project Python environment is missing. Please have the project dependencies installed.", 16, "Canvas Section Planner"
    WScript.Quit 1
End If
shell.Run Chr(34) & python & Chr(34) & " -m app", 0, False
WScript.Sleep 2500
shell.Run "http://127.0.0.1:8010", 1, False
