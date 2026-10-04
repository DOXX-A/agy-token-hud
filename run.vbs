Set oShell = CreateObject("WScript.Shell")
userProfile = oShell.ExpandEnvironmentStrings("%USERPROFILE%")
oShell.Run "node """ & userProfile & "\.antigravity-tokens-hud\index.js""", 0, False
