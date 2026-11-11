# Qué necesitas

| Necesitas | Detalle |
|---|---|
| Windows 10 u 11, de 64 bits | En Linux funciona desde el código fuente. |
| [FreeCAD 1.1](https://www.freecad.org/downloads.php) | No se admiten otras versiones. |
| Claude Code | Con un plan de Claude y una sesión iniciada. |
| Git para Windows | Claude Code lo pide. |

CotaForge es un plugin y un servidor de herramientas para Claude Code. No tiene ventana propia. Úsalo en la terminal o en la pestaña **Code** de la aplicación de escritorio de Claude.

## Qué ve Claude

FreeCAD, CotaForge y todos tus archivos se quedan en tu computador. Claude es un servicio en línea, así que lo que ve llega a Anthropic, como en cualquier sesión de Claude Code. No abre el archivo de tu pieza. Ve:

- lo que escribes;
- las respuestas de las herramientas: medidas, listas de pasos, listas de taladros, errores;
- imágenes de la vista 3D o de la hoja, cuando las pide;
- tus notas de memoria, cuando aplican.

Claude Code también puede leer otros archivos de la carpeta del proyecto, después de preguntar.

!!! warning "Piezas confidenciales"
    Revisa las normas de tu empresa antes de usar Claude con ellas.
