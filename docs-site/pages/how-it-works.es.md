# Cómo funciona

## Primero el plan

`/cotaforge:plan` inicia un diseño. El agente pregunta el sector y el proceso con que se fabrica la pieza. La lista de pasos siempre se puede leer: pide el árbol de operaciones.

## Tres formas de empezar

`/cotaforge:plan` pregunta una vez cómo quieres trabajar, y recuerda tu respuesta.

- **Primero el boceto.** El agente investiga, propone un boceto rápido en FreeCAD, lo acuerda contigo, guarda una copia y solo entonces construye el detalle.
- **Directo.** Para arquitecturas conocidas: casi directo al diseño final.
- **Diseño sistemático**: el método clásico de los libros de diseño. Primero funciones y alternativas, luego el concepto y el detalle.

Recomendamos empezar por el boceto. Ves el concepto pronto y puedes cambiar de rumbo antes de gastar más tokens en el detalle.

## Pasos pequeños

Una operación por llamada: un croquis, una extrusión, un taladro. Cada una devuelve volumen, tamaño y validez medidos. Todo croquis está totalmente acotado, así que al cambiar un número cambia la pieza.

## Te cuestiona

Si una cota, una referencia o una decisión parece mala, el agente lo dice primero, con la razón. Si insistes, lo hace y anota la decisión en `assumptions-and-decisions.md`.

Antes de modelar una pieza normalizada, pregunta si puedes comprarla.

## Memoria

La memoria son archivos de texto pequeños, una idea cada uno.

- **Memoria del proyecto:** la carpeta `memory` de tu proyecto.
- **Memoria de usuario:** `.cotaforge` en tu carpeta de usuario. Aplica a todos tus diseños.

El agente propone y tú apruebas. Te pregunta si una regla vale para todos tus diseños o solo para este proyecto. Puedes editar los archivos a mano.

> `/cotaforge:fix` En un taladro roscado, el chaflán se hace después de roscar, nunca antes. Vale para todos mis diseños.

## Colores de los taladros

| Color | Significado |
|---|---|
| Azul | Taladro roscado. |
| Rojo | Taladro de precisión (ajuste H6, H7 u H8). |
| Verde | Taladro liso. |

## Planos

1. El diseñador (el agente con quien hablas) termina la pieza.
2. El dibujante hace la hoja en su propia ventana y pregunta lo que no sabe.
3. Un revisor lee la hoja: tú, el agente fabricante, o ambos.
4. Cada punto se corrige o se responde. Entonces se escribe el PDF.

## Comandos

| Comando | Qué hace |
|---|---|
| `/cotaforge:plan <problema>` | Inicia un diseño. |
| `/cotaforge:fix <texto>` | Guarda una corrección en memoria, cuando la apruebas. |
| `/cotaforge:declare <texto>` | Declara los materiales o procesos permitidos. |
| `/cotaforge:resume <archivo>` | Retoma un archivo de FreeCAD y dice su estado. |
| `/cotaforge:review <foco>` | Pide a un revisor independiente que mida el diseño. |
| `/cotaforge:friction <texto>` | Escribe una nota en `friction.md` de tu proyecto. |

Todo lo demás: [manual](manual.md).
