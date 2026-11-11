# Install

Install [FreeCAD 1.1](https://www.freecad.org/downloads.php) and Claude Code first. Sign in to Claude Code once.

## Windows installer

1. Download [cotaforge-setup.exe](https://github.com/Jomruizgo/cotaforge/releases/latest/download/cotaforge-setup.exe). For another version, use the [Releases page](https://github.com/Jomruizgo/cotaforge/releases).
2. Run it. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. The installer is not signed.
3. Choose Spanish or English. It is also the language of the agent and of your drawing sheets.
4. Follow the wizard. It installs for your user, with no administrator rights.

## Your first project

1. In the Start menu, open **Create new project (CotaForge)** and choose an empty folder.
2. Open that folder in Claude Code: **Code** tab of the desktop app, or `claude` in a terminal.
3. Type `/cotaforge:`. If the commands appear, it works.

Next time, open the same folder. For an older project, click **Open existing project (CotaForge)** once.

## Update and uninstall

- **Update:** save your work in FreeCAD, then run the new installer.
- **Uninstall:** Windows Settings > Apps. Your projects stay.

## From source

You need Python 3.12, Git and FreeCAD 1.1.

```bash
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e kernel -e mcp-server
cotaforge plugin register
cotaforge init ~/my-designs
```

Open `~/my-designs` in Claude Code. Details: [manual](manual.md#4-step-by-step-installation). If something fails: [FAQ](faq.md).
