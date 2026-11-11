# What you need

| You need | Detail |
|---|---|
| Windows 10 or 11, 64-bit | Linux works from source. |
| [FreeCAD 1.1](https://www.freecad.org/downloads.php) | Other versions are not supported. |
| Claude Code | With a Claude plan and a signed-in account. |
| Git for Windows | Claude Code asks for it. |

CotaForge is a plugin and a tool server for Claude Code. It has no window of its own. Use it in the terminal or in the **Code** tab of the Claude desktop app.

## What Claude sees

FreeCAD, CotaForge and all your files stay on your computer. Claude is an online service, so what it sees goes to Anthropic, as in any Claude Code session. It does not open your part file. It sees:

- what you type;
- tool answers: measures, step lists, hole lists, errors;
- pictures of the 3D view or the sheet, when it asks;
- your memory notes, when they apply.

Claude Code can also read other files in your project folder, after asking.

!!! warning "Confidential parts"
    Check your company's rules before using Claude on them.
