# Tu primer diseño

Un diseño pequeño a partir de una idea: una placa para montar un motor en un bastidor. Tus palabras y las del agente cambian de una sesión a otra. El flujo no.

Necesitas un proyecto abierto: ver [Instalar](install.md#tu-primer-proyecto).

## 1. Plantea la necesidad

> `/cotaforge:plan` Necesito una placa para montar un motor en un bastidor: de unos 100 x 60 mm, S235JR, cuatro tornillos M8, con los taladros a 15 mm de los bordes.

## 2. Responde las preguntas

La primera vez, el agente pregunta cómo quieres trabajar (ver [tres formas de empezar](how-it-works.md#tres-formas-de-empezar)) y lo recuerda. También pregunta el sector y el proceso con que se fabrica la pieza. Responde en palabras simples.

## 3. Dirige el concepto

Con el boceto primero, el agente investiga, busca componentes comerciales como el motor y los tornillos, y propone un boceto rápido en FreeCAD. Si tienes archivos 3D de los componentes comerciales, entrégaselos.

Dile qué cambiar: "más gruesa", "taladros más cerca del borde". Guarda una copia del boceto que acuerdes y luego se detiene a preguntar qué supuso.

## 4. Mira el detalle

La placa crece paso a paso: croquis, extrusión, taladros, chaflanes en las esquinas. Cada paso devuelve las medidas que da FreeCAD.

Comprueba una a mano. Cada taladro Ø9 quita π × 4,5² × 8 ≈ 509 mm³, y cada esquina de 4 × 45° quita 4 × 4 / 2 × 8 = 64 mm³. Di "para" para interrumpir, o "deshaz el último paso".

## 5. Dibuja

> Haz el plano de taller en A3 con vista frontal, vista superior e isométrica. Ubica el primer taladro desde el borde izquierdo y el borde frontal, y los otros desde él por distancia entre centros.

El **dibujante** se abre en su propia ventana y pregunta lo que no sabe, como desde qué bordes se cuentan las posiciones.

![El plano de taller de la placa: vista frontal, vista superior e isométrica. El primer taladro se ubica desde los bordes izquierdo y frontal, los otros desde él por distancia entre centros. El espesor está acotado en la vista frontal.](assets/img/plate-sheet-es.png)

*Una placa sintética, hecha con CotaForge y FreeCAD. Modo de revisión `own`: el agente respondió sus propios puntos de revisión.*

## 6. Revisa

El PDF no se escribe hasta que alguien distinto del dibujante haya mirado la hoja. El agente te pregunta si la revisas tú o el agente **fabricante**. El fabricante lee solo el plano, nunca el modelo 3D, y lista lo que falta o no está claro. Si no respondes en 15 minutos, revisa él. Cada punto se corrige o se responde.

## 7. Revisa antes de producir

Pide el PDF. La casilla "Aprobó" queda vacía hasta que le digas al agente que apruebe. Lee tú la hoja: como con cualquier trabajo hecho con IA, revísala antes de producir.

Siguiente: [cómo funciona](how-it-works.md).
