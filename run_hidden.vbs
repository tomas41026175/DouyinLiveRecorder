' run_hidden.vbs
' Runs the given command line with a truly hidden window (style 0),
' unlike a .lnk shortcut's WindowStyle=Minimized (7) which briefly shows
' the window before minimizing it -- that brief show is the "flash then
' vanish" cmd window users see at login when autostart is enabled.
' Usage: wscript.exe run_hidden.vbs "<full command line to run>"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & WScript.Arguments(0) & """", 0, False
