# Instalar

Instala primero [FreeCAD 1.1](https://www.freecad.org/downloads.php) y Claude Code. Inicia sesión en Claude Code una vez.

## Instalador de Windows

1. Descarga [cotaforge-setup.exe](https://github.com/Jomruizgo/cotaforge/releases/latest/download/cotaforge-setup.exe). Para otra versión, usa la [página de Releases](https://github.com/Jomruizgo/cotaforge/releases).
2. Ejecútalo. Si Windows muestra "Windows protegió su PC", pulsa **Más información** y luego **Ejecutar de todas formas**. El instalador no está firmado.
3. Elige español o inglés. Es también el idioma del agente y de tus planos.
4. Sigue el asistente. Se instala para tu usuario, sin permisos de administrador.

## Tu primer proyecto

1. En el menú Inicio, abre **Crear proyecto nuevo (CotaForge)** y elige una carpeta vacía.
2. Abre esa carpeta en Claude Code: pestaña **Code** de la aplicación de escritorio, o `claude` en una terminal.
3. Escribe `/cotaforge:`. Si aparecen los comandos, funciona.

La próxima vez, abre la misma carpeta. Para un proyecto anterior, pulsa una vez **Abrir proyecto existente (CotaForge)**.

## Actualizar y desinstalar

- **Actualizar:** guarda tu trabajo en FreeCAD y ejecuta el instalador nuevo.
- **Desinstalar:** Configuración de Windows > Aplicaciones. Tus proyectos se quedan.

## Desde el código fuente

Necesitas Python 3.12, Git y FreeCAD 1.1.

```bash
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e kernel -e mcp-server
cotaforge plugin register
cotaforge init ~/mis-disenos
```

Abre `~/mis-disenos` en Claude Code. Detalles: [manual](manual.md#4-instalación-paso-a-paso). Si algo falla: [preguntas](faq.md).
