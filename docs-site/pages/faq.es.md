# Preguntas y límites

## Descargo de responsabilidad

CotaForge es una versión temprana de desarrollo, entregada tal como está, con licencia MIT. Los autores no se hacen responsables del mal uso, de los errores de la herramienta, ni del software de CAD o del modelo de IA que use. Tú revisas el diseño y los planos, y eres responsable de lo que pasa a producción.

## Costo y privacidad

CotaForge es gratuito y de código abierto (licencia MIT). Para usarlo necesitas Claude Code con un plan de Claude.

Tus archivos están en tu computador. Para lo que ve Claude, lee [qué necesitas](what-you-need.md#qué-ve-claude).

## Versiones

Solo FreeCAD 1.1. Windows 10 y 11; Linux desde el código fuente. Solo Claude Code.

## Límites

- Sin la ventana de FreeCAD no hay imagen 3D ni selección señalando.
- No hay solevación (loft).
- Los planos no cubren símbolos de soldadura, vistas interrumpidas, cortes parciales ni líneas de doblez.
- Los ensambles no tienen subensambles. Las uniones son fija, de revolución y deslizante.
- La chapa metálica necesita el complemento SheetMetal de FreeCAD.
- `check` compara la pieza con el material, el proceso y el tamaño que declaraste. No calcula esfuerzos ni verifica tolerancias.
- El criterio del agente no se verifica. Decidir si una pieza es buena idea te corresponde a ti.

Algunos límites son de FreeCAD, no de CotaForge:

- La velocidad de cada paso depende de tu máquina (procesador y memoria).
- FreeCAD puede volverse lento o cerrarse con ensambles grandes o con muchas piezas abiertas. Si se cierra, se pierde lo que no se haya guardado.
- No abras el mismo archivo en dos sesiones a la vez: la última que guarde pisa a la otra.

Más en el [manual](manual.md#12-límites).

## Problemas frecuentes

| Qué ves | Qué hacer |
|---|---|
| "Windows protegió su PC" | **Más información** y luego **Ejecutar de todas formas**. |
| `/cotaforge:` no muestra comandos | Pulsa cualquier acceso directo de CotaForge en el menú Inicio y reinicia Claude Code. |
| El servidor `cad` no está conectado (`/mcp`) | Abriste otra carpeta. Abre la carpeta de tu proyecto. |
| No se encuentra FreeCAD | Instala FreeCAD 1.1 en su carpeta habitual y abre el proyecto de nuevo. |

Más en el [manual](manual.md#14-problemas-frecuentes).

## Reportar un problema

[Abre un issue](https://github.com/Jomruizgo/cotaforge/issues/new/choose). Adjunta las versiones de CotaForge, Claude Code y FreeCAD, lo que pediste y el error exacto. Nunca adjuntes un archivo de un cliente.

Los problemas de seguridad se reportan en privado: [SECURITY.md](https://github.com/Jomruizgo/cotaforge/blob/main/SECURITY.md).
