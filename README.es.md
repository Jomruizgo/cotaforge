# CotaForge

[English](README.md) · **Español** · [Sitio web](https://jomruizgo.github.io/cotaforge/es/)

Diseño mecánico en FreeCAD, desde Claude Code. El agente construye piezas, ensambles y planos de taller en pasos pequeños y medidos. Puedes interrumpirlo, deshacer cualquier paso, y guardar y retomar el archivo nativo.

**Versión temprana de desarrollo.** Espera errores y cambios. Solo funciona con Claude Code y FreeCAD 1.1.x. Windows está soportado; en Linux funciona desde el código fuente.

Sitio web: <https://jomruizgo.github.io/cotaforge/es/>

## Instalar

Necesitas [FreeCAD 1.1](https://www.freecad.org/downloads.php) y [Claude Code](https://claude.com/claude-code), con la sesión iniciada. En Windows, Claude Code también pide Git para Windows.

**Instalador de Windows.** Descarga [cotaforge-setup.exe](https://github.com/Jomruizgo/cotaforge/releases/latest/download/cotaforge-setup.exe) y ejecútalo. Para otra versión, usa la página de [Releases](https://github.com/Jomruizgo/cotaforge/releases). El instalador no está firmado: Windows muestra "Windows protegió su PC". Pulsa **Más información** y luego **Ejecutar de todas formas**.

**Desde el código fuente.** Necesitas Python 3.12, Git y FreeCAD 1.1.

```bash
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e kernel -e mcp-server
cotaforge init ~/mis-disenos
cd ~/mis-disenos
claude --plugin-dir /ruta/a/cotaforge/plugin
```

Usa una instalación editable (`-e`): `cotaforge init` encuentra la carpeta `plugin/` a través de ella. Si FreeCAD no está en su carpeta habitual, pasa `--freecad-root`. Añade `--visible` para ver crecer la pieza en la ventana de FreeCAD.

## Primer uso

Con el instalador: menú Inicio > **Crear proyecto nuevo (CotaForge)**, elige una carpeta vacía y ábrela en Claude Code. Luego:

```text
/cotaforge:plan una placa de acero de 100 x 60 x 8 mm con cuatro taladros pasantes de 9 mm
```

## Documentación

- [Manual de usuario](docs/manual-usuario.md), también en inglés: [user manual](docs/user-manual.md).
- [Sitio web](https://jomruizgo.github.io/cotaforge/es/).

## Qué hay aquí

| Carpeta | Qué es |
|---|---|
| `kernel/` | Paquete de Python: adaptadores de CAD, ejecutor de pasos, medición, restricciones, memoria. |
| `mcp-server/` | El servidor MCP `cad` y el comando `cotaforge`. |
| `plugin/` | El plugin de Claude Code: comandos y agentes. |
| `templates/` | La plantilla del espacio de diseño que copia `cotaforge init`. |
| `packaging/windows/` | El instalador de Windows. |

## Límites

- Solo FreeCAD 1.1.x y solo Claude Code.
- Sin la ventana de FreeCAD no hay captura 3D ni selección señalando.
- `check` compara la pieza con el material, el proceso y el tamaño que declaraste. No verifica tolerancias ni esfuerzos.
- El criterio del agente no se verifica.
- `run_native` no es un entorno aislado.

## Comentarios

[Abre un issue](https://github.com/Jomruizgo/cotaforge/issues/new/choose). Adjunta las versiones de CotaForge, Claude Code y FreeCAD, lo que pediste y el error exacto. Nunca adjuntes un archivo de un cliente.

## Descargo de responsabilidad

Se entrega tal como está, con licencia MIT. Los autores no se hacen responsables del mal uso, de los errores de la herramienta, ni del software de CAD o del modelo de IA que use. Tú revisas el diseño y los planos, y eres responsable de lo que pasa a producción.

## Contribuir y licencia

[CONTRIBUTING.md](CONTRIBUTING.md), [AI_POLICY.md](AI_POLICY.md), [SECURITY.md](SECURITY.md) (en inglés). Licencia MIT: [LICENSE](LICENSE). Software incluido: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
