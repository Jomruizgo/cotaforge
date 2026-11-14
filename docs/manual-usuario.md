# Manual de usuario de CotaForge

CotaForge te permite diseñar piezas mecánicas en 3D pidiéndoselo a Claude Code en lenguaje natural. Claude no dibuja a ojo: maneja un software de CAD real (FreeCAD) a través de un servidor MCP, y cada medida que informa viene de ese software.

Este manual supone que ya usas Claude Code (terminal, prompts, permisos, plugins, servidores MCP). Al final hay un [glosario](#13-glosario) con los términos propios del producto.

## Contenido

1. [Qué es y qué no es](#1-qué-es-y-qué-no-es)
2. [Declarar en vez de dibujar](#2-declarar-en-vez-de-dibujar)
3. [Requisitos](#3-requisitos)
4. [Instalación paso a paso](#4-instalación-paso-a-paso)
5. [Crear tu espacio de diseño](#5-crear-tu-espacio-de-diseño)
6. [Tu primer diseño](#6-tu-primer-diseño)
7. [Cómo trabaja el agente](#7-cómo-trabaja-el-agente)
8. [Guardar y retomar](#8-guardar-y-retomar)
9. [Materiales, procesos y restricciones](#9-materiales-procesos-y-restricciones)
10. [Memoria: decisiones y correcciones](#10-memoria-decisiones-y-correcciones)
11. [`run_native`, la salida de emergencia](#11-run_native-la-salida-de-emergencia)
12. [Límites](#12-límites)
13. [Glosario](#13-glosario)
14. [Problemas frecuentes](#14-problemas-frecuentes)
15. [Cómo dar feedback](#15-cómo-dar-feedback)
16. [Hoja de referencia rápida](#16-hoja-de-referencia-rápida)

---

## 1. Qué es y qué no es

CotaForge son tres piezas instaladas en tu equipo:

- Un **servidor MCP** llamado `cad`. Conecta Claude Code con el software de CAD y expone un conjunto pequeño de herramientas, cada una con varias acciones (sección 7).
- Un **plugin de Claude Code** llamado `cotaforge`, con los comandos `/cotaforge:plan`, `/cotaforge:resume`, `/cotaforge:fix`, `/cotaforge:declare`, `/cotaforge:review` y `/cotaforge:friction`, y los agentes `reviewer` (revisa el diseño), `fabricator` (revisa planos) y `drafter` (hace planos).
- Un **espacio de diseño**: una carpeta tuya, creada con el acceso directo "Crear proyecto nuevo (CotaForge)" o con `cotaforge init`. Contiene las instrucciones del agente, los permisos y la memoria de tu proyecto.

**Cómo funciona.** El agente construye la pieza en pasos pequeños llamados *operaciones* (un croquis, una extrusión, un agujero). Tras cada una, el software de CAD devuelve hechos medidos: volumen, caja envolvente, validez. El agente los lee antes del siguiente paso. Si un paso falla, no cambia nada. Todo paso se puede deshacer.

**Ningún número viene del modelo de lenguaje.** Los volúmenes, las medidas y los conteos vienen del software de CAD. El `CLAUDE.md` de tu espacio de diseño prohíbe al agente estimar cifras, y debe decir lo que no puede verificar.

**Qué no es.**

- No reemplaza tu criterio de ingeniería. Comprueba la geometría y lo que declaraste. El agente revisor separa lo que verificó de lo que es juicio (sección 9).
- No es un software de CAD nuevo. FreeCAD construye la geometría.
- No es un entorno aislado (sandbox). `run_native` ejecuta código real dentro de FreeCAD (sección 11).
- Solo funciona con FreeCAD 1.1.x y desde Claude Code (terminal o pestaña Code de la app de escritorio). Mira los [Límites](#12-límites).

---

## 2. Declarar en vez de dibujar

Tú declaras *qué* debe ser la pieza. El agente decide *cómo* construirla.

Tú declaras:

- La intención y las dimensiones.
- El material y los procesos de fabricación permitidos.
- Los límites de esos procesos (tamaño de la máquina, diámetro máximo del torno).
- Las decisiones y correcciones que se deben recordar (sección 10).

**Procedimental** (evítalo): "Dibuja un rectángulo de 60 por 40 en XY. Extrúyelo 10. Dibuja un círculo de 8 en la cara superior. Córtalo de lado a lado."

**Declarativo** (úsalo):

> Placa de 60 x 40 x 10 mm en aluminio 6061, mecanizada en la fresadora de 3 ejes del taller (volumen de trabajo 100 x 80 x 60 mm), con un agujero pasante Ø8 centrado en la cara superior. Verifica con `check` antes de exportar.

El primer prompt solo describe gestos y no da nada contra lo que verificar. El segundo permite que `check` compare la pieza medida con el material, el proceso y los límites que declaraste. Los límites pertenecen a un proceso: el tamaño que cabe en una fresadora es `build_volume_mm`; el diámetro máximo (`max_diameter_mm`) pertenece a un torno (`turning`).

| Tú declaras | Se registra con | Efecto |
|---|---|---|
| Dimensiones e intención | `op` (campo `intent`) | Cada paso guarda una línea con el "porqué" y devuelve el volumen y la caja medidos. |
| Material | `memory` (`save`, tipo `material`) y `doc` (`set_material`) | Se asigna al documento. |
| Procesos permitidos | `memory` (`save`, tipo `process`) y `doc` (`set_route`) | La ruta de procesos se asigna al documento. |
| Límites | capacidades del proceso en `memory` | `check` compara la pieza contra ellas. |
| Decisiones y correcciones | `memory` (`save`) | Se cargan en sesiones futuras. |
| "¿Cumple lo que declaré?" | `check` | Informa y bloquea la exportación si hay violaciones. |

**Mundo cerrado.** Si declaras al menos un material, solo se pueden asignar materiales declarados. Lo mismo vale para los procesos. Si no declaras nada de un tipo, ese tipo no restringe. Con "Aluminio 6061" declarado, pedir "Titanio" devuelve:

```
error: outside_declared
material 'Titanio' is not declared; declared: ['Aluminio 6061']
```

**El plan es legible.** Lo que construye el agente es una lista de operaciones con sus parámetros. `inspect` con `action="tree"` la muestra y `doc` con `save` la guarda junto al archivo. No hay un script oculto.

---

## 3. Requisitos

| Requisito | Detalle |
|---|---|
| Sistema operativo | Windows 10 u 11 de 64 bits con el instalador `.exe` (4.1). Linux desde un clon del repositorio (4.2). |
| Python | 3.12 o superior, solo para el clon. El instalador de Windows trae el suyo. |
| FreeCAD | Versión **1.1.x**. Descarga: <https://www.freecad.org/downloads.php>. El instalador no lo incluye. |
| Claude Code | Instalado y con sesión iniciada (app de escritorio, pestaña Code, o `claude` en una terminal). CotaForge no gestiona tu cuenta. |
| Git | En Windows, Claude Code ejecuta los hooks con Git Bash, así que instala Git para Windows. |

### Cómo se encuentra FreeCAD

CotaForge necesita la **carpeta raíz** de FreeCAD, la que contiene el ejecutable de línea de comandos.

- **Windows:** `bin\FreeCADCmd.exe` dentro de la raíz. Si no configuras nada, busca en la carpeta `C:\Program Files\FreeCAD*` más reciente y luego en `%LOCALAPPDATA%\Programs\FreeCAD*`. El `C:\Program Files\FreeCAD 1.1` del instalador oficial se encuentra sin configurar nada.
- **Linux:** `usr/bin/freecadcmd` dentro de la raíz. Por defecto busca en `~/Applications/freecad-1.1.3/squashfs-root`, la carpeta que crea `./<archivo>.AppImage --appimage-extract`.

Para cualquier otra ubicación, usa `--freecad-root` en `cotaforge init` o define `COTAFORGE_FREECAD_ROOT`. Prioridad: el argumento, luego la variable, luego las ubicaciones por defecto.

---

## 4. Instalación paso a paso

Si solo diseñas, usa el instalador de Windows (4.1). El clon (4.2) es para desarrollo o para Linux.

### 4.1 Con el instalador de Windows

El instalador instala por usuario, **sin permisos de administrador**, en `%LOCALAPPDATA%\Programs\cotaforge`. Trae su propio Python y sus dependencias.

**1. Descarga** el último instalador: <https://github.com/Jomruizgo/cotaforge/releases/latest/download/cotaforge-setup.exe>. Para otra versión, mira la [página de Releases](https://github.com/Jomruizgo/cotaforge/releases).

**2. Ejecútalo.** El instalador no está firmado con un certificado de pago, así que Windows SmartScreen muestra "Windows protegió su PC". Haz clic en **Más información** y luego en **Ejecutar de todas formas**.

**3. Sigue el asistente.** Busca dos programas que no incluye:

| Programa | Qué hace el instalador |
|---|---|
| **FreeCAD 1.1** | Busca en `C:\Program Files\FreeCAD*`, `%LOCALAPPDATA%\Programs\FreeCAD*` y el registro. Si no encuentra nada, abre la página de descarga si se lo pides, te deja elegir la carpeta (la que tiene `bin\FreeCADCmd.exe`) o continuar sin él. Si instalas FreeCAD después en su carpeta habitual, CotaForge lo encuentra. |
| **Claude Code** | Busca `claude.exe` en el `PATH` y en `%USERPROFILE%\.local\bin`. Si falta, ofrece lanzar el instalador oficial o continuar sin él. Tú inicias sesión. |

Al final registra el plugin `cotaforge` en Claude Code, si Claude Code está instalado. Si no, el plugin se registra la primera vez que uses un acceso directo.

**4. Usa los accesos directos del menú Inicio** (carpeta *CotaForge*):

| Acceso directo | Qué hace |
|---|---|
| **Crear proyecto nuevo (CotaForge)** | Pide una carpeta (puedes crearla ahí), prepara el espacio de diseño con la ventana de FreeCAD visible, registra el plugin si falta y te dice cómo abrirlo. |
| **Abrir proyecto existente (CotaForge)** | Pide la carpeta de un proyecto existente y refresca su configuración, como `cotaforge init` sin `--force`. Tu memoria no se toca. Tu `CLAUDE.md` se actualiza solo si nunca lo editaste. Si la carpeta no es un proyecto de CotaForge, no cambia nada. |
| **Manual de CotaForge** | Abre este manual. |
| **Desinstalar CotaForge** | Elimina el programa. |

**5. Abre el proyecto en Claude Code:**

- **App de escritorio:** pestaña **Code**, elige la carpeta del proyecto. Escribe `/cotaforge:` para ver los comandos.
- **Terminal:** haz `cd` a la carpeta del proyecto y ejecuta `claude`.

No necesitas `--plugin-dir`: el plugin está registrado en tu configuración de usuario de Claude Code.

**Actualizar.** Ejecuta el nuevo `.exe` sobre el anterior. Reemplaza el programa y el plugin y nunca toca tus proyectos. Guarda antes tu trabajo en FreeCAD. Una sesión de Claude que usó CotaForge mantiene su servidor de CAD en marcha, incluso después de cerrar la ventana, y Windows no puede reemplazar un programa en ejecución. Si el instalador encuentra ese servidor, te pregunta si debe cerrarlo. Responde que sí para cerrar solo los procesos de CotaForge, y luego reconecta el servidor `cad` con `/mcp` o abre una sesión nueva. Responde que no, cierra por completo la app de Claude (también desde el icono de la bandeja) y ejecuta el instalador otra vez.

**Desinstalar.** Usa *Configuración > Aplicaciones* o el acceso directo. Elimina el programa y el registro del plugin. **Tus proyectos y tu memoria (`%USERPROFILE%\.cotaforge`) se quedan.**

**Comprobar la instalación:**

```powershell
& "$env:LOCALAPPDATA\Programs\cotaforge\python\python.exe" -m cotaforge_mcp.cli version
& "$env:LOCALAPPDATA\Programs\cotaforge\python\python.exe" -m cotaforge_mcp.cli plugin status
```

Lo que el instalador detectó (carpeta de FreeCAD, ruta de `claude`) está en `%LOCALAPPDATA%\Programs\cotaforge\install.json`.

El instalador no modifica el `PATH`. Para el comando `cotaforge` en una terminal, usa la forma `python.exe -m cotaforge_mcp.cli ...` de arriba (por ejemplo `... init <carpeta>`, `... plugin register`).

### 4.2 Desde un clon del repositorio

CotaForge no está en PyPI. Tiene dos paquetes de Python: `kernel` y `mcp-server`.

```bash
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e kernel -e mcp-server
```

En Windows (PowerShell). Clona en una ruta corta como `C:\dev`: una ruta de 210 caracteres o más se rechaza (sección 14).

```powershell
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e kernel -e mcp-server
```

Usa `-e`: `cotaforge init` encuentra la carpeta `plugin/` del clon solo con una instalación editable. Si no, pasa `--plugin-dir` (sección 5).

El clon también es un marketplace de Claude Code. Registra el plugin una vez para no necesitar `--plugin-dir`:

```bash
cotaforge plugin register
```

`cotaforge plugin status` muestra si está registrado y `cotaforge plugin unregister` lo elimina. El registro apunta al clon: si lo mueves, regístralo de nuevo.

La instalación añade dos comandos al entorno virtual: `cotaforge` (`init`, `plugin`, `version`) y `cotaforge-mcp` (el servidor MCP, que lanza Claude Code). Compruébalo:

```bash
cotaforge version
```

Para las pruebas, instala los extras: `python -m pip install -e "kernel[dev]" -e "mcp-server[dev]"`. Con FreeCAD instalado, `cd mcp-server && python -m pytest tests/acceptance` ejecuta un recorrido contra FreeCAD real (define antes `COTAFORGE_FREECAD_ROOT` si FreeCAD no está en una ubicación por defecto). Para construir el instalador de Windows: `packaging\windows\build.ps1` (mira `packaging/windows/`).

**El entorno virtual (o la instalación con el `.exe`) queda fijado en los archivos que escribe `cotaforge init`.** Si lo mueves, lo borras o lo recreas, ejecuta `init` de nuevo en tu espacio de diseño. Con el instalador, el acceso directo **Abrir proyecto existente (CotaForge)** lo hace.

---

## 5. Crear tu espacio de diseño

Un **espacio de diseño** es una carpeta tuya (el comando la crea si no existe) que contiene tus piezas, la memoria de tu proyecto y tu sesión de Claude Code.

**Con el instalador de Windows**, usa **Crear proyecto nuevo (CotaForge)**. Hace lo que `cotaforge init --visible` en la carpeta que elijas. **Abrir proyecto existente (CotaForge)** repite `init` sin `--force`, por ejemplo después de una actualización.

**Desde una terminal:**

```bash
cotaforge init ~/design
```

```
Espacio de diseño listo en /home/you/design
  creado       CLAUDE.md
  creado       memory/.gitkeep
  creado       .mcp.json (servidor cad, adaptador freecad)
  creado       .claude/settings.local.json (hook de run_native, 1 CLAUDE.md excluidos)

Siguiente paso: el plugin ya está registrado en Claude Code. Abre esa carpeta:
  - en la app de escritorio de Claude: pestaña Code, y elige la carpeta; o
  - en una terminal:
      cd /home/you/design
      claude
```

Si el plugin no está registrado, el siguiente paso sugiere `claude --plugin-dir <ruta del plugin>` y te recuerda `cotaforge plugin register`.

### Qué es cada archivo generado

| Archivo | Para qué sirve |
|---|---|
| `CLAUDE.md` | El comportamiento del agente. Puedes editarlo. Si nunca lo editaste, CotaForge lo actualiza con las versiones nuevas y guarda la anterior como `CLAUDE.md.<versión>-<huella>.bak`. Si lo editaste, CotaForge deja la plantilla nueva en `CLAUDE.md.new`; el agente te lo recuerda al inicio de cada sesión mientras ese archivo exista y te ofrece fusionarlos. Solo `--force` lo reemplaza. |
| `.mcp.json` | Declara el servidor `cad`: el intérprete de Python y las variables `COTAFORGE_ADAPTER`, `COTAFORGE_WORKSPACE`, `COTAFORGE_FREECAD_ROOT` y, si lo pediste, `COTAFORGE_VISIBLE`. |
| `.claude/settings.local.json` | Permite las herramientas del servidor (`mcp__cad`), aprueba el servidor `cad`, excluye los `CLAUDE.md` heredados y registra el hook de `run_native` (sección 11). |
| `memory/` | La memoria de tu proyecto: un archivo `.md` por entrada (sección 10). |
| `assumptions-and-decisions.md` | Registro de supuestos, decisiones y cálculos, cada uno con su fuente. |
| `calculations/` | Scripts de Python que producen los números del diseño. |

Tus archivos `.FCStd` (con su `.cotaforge.json`) y los registros aparecen a medida que trabajas.

Los nombres de carpetas y archivos están en inglés. Un proyecto creado antes de la 0.10 conserva sus nombres en español (`memoria/`, `planos/`, `calculos/`, `supuestos-y-decisiones.md`, `friccion.md`, `<pieza>-intencion.md`). CotaForge los lee donde están y nunca los renombra. El contenido que escribes o lees (registro, entradas de memoria, hojas) queda en el idioma del proyecto.

**Los `CLAUDE.md` heredados se excluyen.** `init` busca archivos `CLAUDE.md` en las carpetas por encima de tu espacio y en `~/.claude` y los añade a `claudeMdExcludes`, para que tu configuración personal no afecte al diseño. Comprueba que existen; no los lee. Por eso tus instrucciones globales no se aplican en un espacio de diseño.

### Opciones de `cotaforge init`

| Opción | Efecto |
|---|---|
| `--adapter freecad` (por defecto) | Usa FreeCAD. |
| `--adapter fake` | Un simulador para las pruebas del proyecto. No produce geometría real. |
| `--freecad-root PATH` | Carpeta raíz de FreeCAD, escrita en `.mcp.json`. |
| `--visible` / `--no-visible` | Abre FreeCAD **con su ventana** (`COTAFORGE_VISIBLE=1`) para que veas crecer el diseño. `--no-visible` lo desactiva. |
| `--force` | Reemplaza `CLAUDE.md` aunque lo hayas editado. |
| `--plugin-dir PATH` | Solo cambia el mensaje del "siguiente paso" a `claude --plugin-dir PATH`. |
| `--language es\|en` | Tu idioma en este proyecto: mensajes de `init`, hojas de planos, preguntas del dibujante, notas de decisión. Se guarda como `language` en `brief.yaml`. Sin la opción, un proyecto existente conserva su idioma, uno nuevo toma el idioma elegido al instalar y uno más antiguo que no declaraba ninguno sigue en español. Cámbialo después con `memory(action=brief, language=...)` o pidiéndoselo al agente. |

Si no se encuentra FreeCAD, `init` igualmente crea todo y avisa. Pasa `--freecad-root` y ejecútalo otra vez.

**Es seguro repetirlo.** `init` nunca toca la memoria, conserva un `CLAUDE.md` editado (verás `conservado`) y fusiona los archivos JSON sin borrar las claves que no son suyas. Úsalo para cambiar a `--visible`, corregir la ruta de FreeCAD o refrescar el intérprete.

### Abrir Claude Code con el plugin

Con el plugin registrado (el instalador de Windows lo hace; desde un clon, `cotaforge plugin register`):

- **App de escritorio:** pestaña **Code**, elige la carpeta del espacio de diseño.
- **Terminal:** `cd ~/design` y luego `claude`.

Escribe `/cotaforge:` para ver los comandos. Sin registrar, carga el plugin para una sesión desde un clon: `claude --plugin-dir /ruta/al/clon/plugin`.

### Lo que Claude Code puede preguntarte la primera vez

- **Confiar en la carpeta.** La pregunta habitual de Claude Code para una carpeta nueva.
- **Aprobar el servidor `cad`.** `init` ya lo aprobó. Si pregunta de todos modos, apruébalo. `/mcp` muestra si `cad` está conectado.
- **Permisos de herramientas.** `mcp__cad` está permitido, así que no debería preguntar nada. La excepción es `run_native` con código riesgoso (sección 11).
- **Escribir archivos.** Comandos como `/cotaforge:friction` escriben en tu carpeta, así que Claude Code puede pedir permiso de escritura.

---

## 6. Tu primer diseño

1. Pídela con `/cotaforge:plan`. Por ejemplo: "Una placa de 60 x 40 x 10 mm llamada `plate`, con un agujero pasante Ø8 centrado en la cara superior y un redondeo de radio 5 en la esquina del origen. Dime el volumen después de cada paso."
2. Responde las preguntas del agente. Pregunta solo lo que no puede decidir.
3. Lee el plan: `inspect` con `action="tree"`, o pide "muéstrame el árbol de operaciones".
4. Míralo construir. Con la ventana de FreeCAD visible (`--visible`) ves llegar cada operación. Cada paso informa el volumen y la caja envolvente medidos.
5. Comprueba: pide `check` contra lo que declaraste, o ejecuta `/cotaforge:review`.
6. Haz el plano. El agente construye la hoja, alguien distinto del agente la revisa y luego exportas el PDF (sección 7, "Dibujos").
7. Guarda con `doc` (`action="save"`). El `.FCStd` queda en tu espacio de diseño.

Si un paso falla, el servidor lo dice y el modelo queda como estaba. El agente lee el motivo y reintenta. Para deshacer un paso, pide: "deshaz el redondeo".

El sitio web tiene el recorrido completo: <https://jomruizgo.github.io/cotaforge/es/first-part/>.

---

## 7. Cómo trabaja el agente

### Las herramientas

El agente habla con el software de CAD solo a través de estas herramientas del servidor `cad` (los permisos las muestran con el prefijo `mcp__cad__`). Cada herramienta tiene varias acciones; `cad_guide` y las descripciones de las herramientas le dan al agente los parámetros exactos.

| Herramienta | Para qué sirve | Acciones principales |
|---|---|---|
| `doc` | Ciclo de vida del documento (pueden estar abiertos varios) | `new`, `open`, `import` (STEP o IGES), `save`, `status`, `close`, `switch`, `set_material`, `set_route`, `set_purpose`, `convention`, `reload` |
| `op` | Una operación de modelado, con hechos medidos | se elige con `name`; `edit` cambia un paso o rasgo; `delete` elimina un rasgo |
| `undo` | Deshacer | `undo()`, `undo(to="<step>")`, `undo(to="start")` |
| `inspect` | Leer hechos medidos, sin cambiar nada | `tree`, `faces`, `edges`, `face`, `edge`, `properties`, `measure`, `bom`, `validity`, `interference`, `sketch_status`, `sweep`, `holes`, `draft`, `parting` |
| `view` | Mostrarte cosas y leer tu selección | `focus`, `show`; con ventana: `selection`, `capture`, `set_view`, `style`, `section` |
| `drawing` | Planos de producción | `create`, `add`, `remove`, `remove_sheet`, `update`, `list`, `review`, `capture`, y las acciones de revisión (mira "Dibujos") |
| `channel` | Canal de preguntas entre la sesión del dibujante y la del diseñador | `ask`, `reply`, `dispute`, `decide`, `wait`, `done`, `list`, `open_drawing`, `reopen`, `close` |
| `export` | Salida a otros formatos | `step`, `mesh`, `macro`, `pdf`, `dxf` |
| `check` | Comparar la pieza medida con lo que declaraste | sin argumentos |
| `memory` | La memoria del ingeniero | `index`, `get`, `search`, `save`, `deactivate`, `approve`, `answer`, `review`, `brief` |
| `catalog` | Piezas comerciales, tu catálogo y la biblioteca técnica | `search`, `get`, `add`, `file` |
| `run_native` | Salida de emergencia: código de FreeCAD como un paso deshacible | sección 11 |
| `cad_guide` | Conocimiento del software de CAD bajo demanda | temas como `profile`, `ops`, `op:<name>`, `terms`, `traps`, `native`, `recipe`, `drawing`, `assembly`, `memory`, `pointing` |

### Una operación por llamada

Una llamada a `op` ejecuta **una** operación y devuelve volumen, caja envolvente y validez. Operaciones con FreeCAD:

- **Croquis y referencias:** `sketch`, `ref_plane`, `ref_axis`.
- **Rasgos:** `extrude`, `cut`, `revolve`, `sweep`, `hole`, `linear_pattern`, `circular_pattern`, `chamfer`, `fillet`, `shell`, `draft`, `mirror`.
- **Cuerpos:** `box`, `cylinder`, `boolean_union`, `boolean_subtract`, `boolean_intersect`, `translate`, `rotate`, `copy_body`, `mirror_body`, `shrink_body`, `mould_split`, `split`.
- **Ensamble:** `insert_component`, `place_component`, `fix_component`, `add_joint`, `set_joint`, `mate_concentric`, `mate_coincident`, `mate_distance`, `check_interference`.
- **Chapa metálica** (necesita un complemento, mira [Chapa metálica](#chapa-metálica-opcional)): `base_flange`, `edge_flange`, `unfold`.

Antes de usar una operación, el agente lee `cad_guide` con `topic="op:<name>"`. `topic="terms"` traduce el vocabulario comercial de CAD ("saliente extruido", "asistente de taladro", "relación concéntrica") a estas operaciones.

Todo va en milímetros, mm³ y grados. Los nombres de paso son libres (letras minúsculas, dígitos, `_`); sin nombre, el agente usa `s1`, `s2`... Cada paso lleva una línea `intent` con el porqué.

### Agujeros

`hole` sigue el asistente de agujeros de FreeCAD (`PartDesign::Hole`) con los mismos nombres y valores por defecto.

| Campo | Qué hace |
|---|---|
| `standard`, `size` | `simple` (con `diameter_mm`), `iso_clearance` (holgura ISO 273, M8 da Ø9) o `iso_tapped` (broca de machuelo, M6 da Ø5; la rosca es cosmética). `size` acepta roscas métricas gruesas hasta M68 y roscas finas ISO 261 (`M45x1.5`). La broca de machuelo se calcula (diámetro mayor menos paso); M8 y M12 usan sus brocas estándar. La holgura ISO 273 está tabulada hasta M36; más allá, da `diameter_mm`. Las tablas se transcribieron sin el texto de la norma y se contrastaron talla por talla con las del propio FreeCAD. |
| `end_condition` | `blind` (`depth_mm` desde la cara de entrada, incluido el cajeado), `through_all` o `up_to_face`. |
| `counterbore` | `{diameter_mm, depth_mm}`. Un tornillo de cabeza cilíndrica M8 es una sola llamada: `iso_clearance`, `size="M8"`, `counterbore={diameter_mm: 14, depth_mm: 9}`. |
| `countersink` | `{diameter_mm, angle_deg}` (90 por defecto). No se combina con `counterbore`. |
| `drill_point` | `angled` (por defecto, cono de 118° añadido después de `depth_mm`) o `flat`. Un agujero pasante lo ignora. Los diseños guardados antes de este valor por defecto conservan el fondo plano. |
| `up_to_face` | La cara final debe ser plana, normal al eje y delante de la entrada. FreeCAD no tiene esa condición, así que el servidor mide la distancia cada vez que se ejecuta el paso y taladra ciego hasta esa profundidad. La respuesta lo dice. Si la cara se mueve, edita o repite el paso. |
| `fit` | `"H7"` y similares: el ajuste del agujero, usado para su color y para los planos. |

Los errores aparecen antes de tocar la pieza y dicen qué número no cuadra. Contra una cara cilíndrica solo funcionan los agujeros simples.

**Cómo se verifica.** Antes de un `hole`, un `cut` o un `extrude` fusionado, el servidor le pide a FreeCAD una operación booleana: el sólido de la herramienta contra la pieza tal como está. El cambio de volumen esperado es la intersección real, no "área x profundidad". Un agujero que desemboca en una cavidad existente pasa. Una herramienta en el aire, del lado equivocado o con un volumen distinto del de la booleana sigue fallando. La respuesta dice qué se comparó. Sin FreeCAD (el adaptador de pruebas) se usa una fórmula cerrada.

**Colores de los agujeros.** Una convención del harness, sin nada que configurar: un agujero roscado es azul claro, uno de precisión (ajuste IT8 o más fino, como H7) es rojo suave y cualquier otro es verde. Los agujeros hechos con `hole` quedan pintados. Un color que cambies a mano se respeta. En los planos solo la vista isométrica va en color. Para cambiar los valores, escribe `convenciones.yaml` en la carpeta de datos (`COTAFORGE_HOME`, por defecto `~/.cotaforge`) para todos los proyectos, o en el espacio de diseño para un proyecto (gana el archivo del proyecto). También puedes pedírselo al agente: usa `doc` con `action="convention"`.

```yaml
agujeros:
  activa: true                  # false: no se pinta ningún agujero
  colores:
    preciso: [255, 128, 128]    # o "#ff8080"
    liso: [0, 192, 0]
    roscado: [128, 128, 255]
  grado_it_preciso: 8           # un ajuste de este grado IT o más fino es de precisión
  isometrico_en_color: true     # false: el isométrico queda en línea
```

`inspect` con `holes` lista los agujeros cilíndricos agrupados por diámetro, con eje, profundidad y si son pasantes o ciegos. Un cajeado aparece como dos agujeros sobre un mismo eje.

**Piezas moldeadas.** `inspect` con `draft` y `pull=[x, y, z]` (la dirección en que se aparta una mitad del molde) mide el ángulo de desmoldeo de cada cara y lista las caras por debajo del mínimo (`min_draft_deg`; sin él, 1 grado, una regla práctica, no una norma: pregunta al moldeador o a la hoja técnica de la resina), las caras sin desmoldeo y las contrasalidas, que son caras a las que no se llega siguiendo la dirección (un agujero transversal, un saliente sobre una pared). Un plano es exacto; una cara curva se muestrea y la respuesta dice cómo. Con `paint=true` las caras se colorean en pantalla: verde cumple el mínimo, amarillo es bajo, rojo no tiene desmoldeo o es contrasalida. `shrink_body` deriva la cavidad: un cuerpo nuevo, ligado a la pieza (la sigue cuando la pieza cambia), escalado por la contracción del plástico (`shrinkage_percent`, un número o `[x, y, z]`) respecto al origen de la pieza. `factor_source` (hoja técnica, moldeador, ingeniero) es obligatorio y la intención del paso lo guarda junto al número. La caja de la cavidad se comprueba contra la de la pieza al 0,01 %. Una contracción distinta por eje convierte las caras curvas en superficies B-spline y el volumen que da el software de CAD para ese cuerpo es solo numérico (hasta un 0,05 % de error).

**Partir el molde.** `inspect` con `parting` y `pull=[x, y, z]` (la dirección en que abre el macho, la mitad móvil: las caras que miran hacia ella son del macho, las que miran en contra son de la cavidad) busca la línea de partición: los lazos de aristas entre una cara del macho y una de la cavidad. Lista cada lazo (exterior o interior, cerrado o no, plano o escalonado), las paredes sin desmoldeo que tocan las dos mitades (el ingeniero decide cuál las forma: `id=core` o `id=cavity`; hasta entonces los lazos se calculan con la propuesta de la línea más corta y se marcan como provisionales), las caras que la silueta atraviesa (FreeCAD no parte una cara por su silueta: con esa dirección no se puede partir) y una propuesta de corredera por cada región de contrasalida (una dirección por la que nada de la pieza la bloquea). `mould_split` corta entonces la pieza contraída de un bloque de postizo y parte lo que queda por la superficie de partición en dos cuerpos nuevos, `cavity` y `core`, más un cuerpo por cada corredera aceptada (`slides=all` o los números de región; una pieza con una contrasalida que ninguna corredera forma se rechaza, quedaría atrapada). El bloque es `block_size_mm` o la caja de la pieza crecida `margin_mm`; sin ninguno crece lo mayor entre 25 mm y una quinta parte del lado más largo, una regla práctica de primera pasada, no una norma. La superficie de partición es un plano cuando la línea está en uno; si escalona, es una superficie de salida (cada arista empujada hacia fuera, de través a la dirección, con las esquinas a inglete y los escalones como paredes); un lazo interior (una abertura que atraviesa la pieza) recibe un cierre plano. El paso comprueba que cada mitad es un sólido válido, que macho + cavidad + correderas + pieza suman el bloque al 0,01 % y que cada mitad y cada corredera salen por su propia dirección. La intención del paso guarda la dirección, el bloque, la decisión sobre las paredes y las correderas; la respuesta da la línea de partición, el método y las comprobaciones. La corredera es solo el primer paso: un cuerpo que forma la contrasalida y sale por su propia dirección, sin guía, cuña ni retención (eso es trabajo posterior). El macho y las correderas no tienen nombre de paso propio: el nombre del paso es la cavidad.

### Croquis de concepto o detalle

`doc` con `action="set_purpose"`, `purpose="sketch"` o `"detail"` (por defecto). Un documento `sketch` es un modelo de concepto: `box`, `cylinder` y las booleanas sirven para sondear volúmenes. Un documento `detail` se construye con rasgos (croquis + `extrude`/`cut`, `hole`, `fillet`) que siguen siendo editables. En un documento `detail`, restar un cilindro de frente contra una cara plana genera una advertencia con una llamada `hole` lista para usar. El propósito se guarda en `<name>.cotaforge.json`.

### Deshacer y editar

`undo` deshace el último paso; `undo(to="s2")` deshace todo lo posterior a `s2`; `undo(to="start")` deshace todo. Los rasgos que ya estaban en un archivo cuando se abrió no se pueden deshacer (sección 8).

Para cambiar un valor de un paso anterior, no deshagas: `op(edit="s3", params={"diameter_mm": 10})` cambia solo ese valor y regenera lo que depende de él. Si un paso posterior ya no encaja, la respuesta dice cuál y el modelo queda como estaba. Dimensiones de croquis: `params={"dimensions": {"largo": 80}}`.

### Rasgos de un archivo abierto

Un archivo hecho a mano en FreeCAD, por `run_native` o por otra sesión trae su historial de rasgos. El agente ve los rasgos con sus **nombres de FreeCAD** y puede cambiarlos sin `run_native`:

- `inspect` con `action="tree"` lista los rasgos de cada cuerpo en el orden de la cadena, con sus parámetros principales, la punta y qué paso del plan hizo cada uno. Con `id="Chamfer"` da un rasgo en detalle.
- `op(edit="Fillet", params={"Radius": 4})` cambia propiedades por nombre de FreeCAD. El valor se valida y el resultado se recalcula y se comprueba. Si el resultado no es válido, no se aplica nada. `params={"edges": [...]}` reasigna las aristas de un redondeo o chaflán.
- `op(delete="Chamfer")` elimina un rasgo como lo hace FreeCAD. Si otros objetos lo necesitan, se rechaza nombrándolos; `params={"cascade": true}` los elimina también. Eliminar el último rasgo sólido de un cuerpo se rechaza (usa `undo`).
- Un rasgo hecho por un paso del plan (`extrude`, `fillet`...) se edita con ese paso: `op(edit="s3", ...)`.

Cada cambio es un paso y `undo` lo revierte. `view` con `action="focus"` muestra el último rasgo de cada cuerpo y oculta los croquis y los rasgos intermedios.

### Seleccionar caras y aristas

Una **cara** es una superficie de la pieza. El agente la nombra por descripción: "la cara de normal +Z creada por el paso `s2`". Una referencia debe coincidir con exactamente una cara; si coincide con varias, el error lista los candidatos y el agente afina.

- Las posiciones sobre una cara se miden **desde el centro de la cara**, no desde el origen del modelo. "Centrado en la cara superior" es la posición `(0, 0)`. Un agujero también acepta `at=[x, y, z]` (proyectado sobre la cara), y un croquis sobre una cara acepta `frame="world"`.
- En los planos base, las coordenadas del croquis son las del mundo: XY es `u=X, v=Y`; YZ es `u=Y, v=Z`; XZ es `u=X, v=-Z`.
- Los ids internos de las caras cambian al aplicar operaciones, así que el agente pide las caras por el paso que las creó.

`chamfer` y `fillet` también aceptan un **conjunto de aristas**: describes qué son las aristas y el servidor las resuelve sobre la pieza actual.

```json
{"kind": "edges", "from_op": "plate", "where": {"parallel_to": "z", "convex": true}}
```

Criterios de `where`: `parallel_to`, `length_mm`, `convex`, `planar`, `curve` (`line`, `circle`, `arc`), `on_face`, `exclude_holes`, `except`. Todos los criterios dados deben cumplirse. El paso informa cuántas aristas coincidieron y las lista. El agente puede previsualizar un conjunto con `inspect` (`action="edges"`, `op=..., where={...}`). Si FreeCAD no puede manejar alguna arista, el paso falla y nombra las aristas; con `skip_failing=true` aplica las que encajan e informa del resto.

### Importar STEP e IGES

`doc` con `action="import"`, `path` (`.step`, `.stp`, `.iges`, `.igs`) y `name` trae la pieza de un cliente a un documento de pieza nuevo.

- La geometría queda donde el archivo la pone. No se recentra.
- FreeCAD convierte a milímetros. El resultado indica la unidad que declara el archivo.
- Crea un cuerpo por sólido, guardado como `<name>.FCStd` en el espacio de diseño. Si ese archivo existe, la importación se rechaza salvo con `overwrite=true`.
- El resultado informa sólidos, volumen, caja envolvente y si la geometría es válida y cerrada. Un archivo con solo superficies (común en IGES) genera una advertencia, porque el volumen no es significativo. Las caras sueltas van a un cuerpo de superficies; `sew=true` intenta coserlas en sólidos.
- Los cuerpos importados no tienen historial: `op` con `edit` no puede cambiarlos. Aun así puedes construir sobre ellos (croquis sobre una cara, `cut`, `hole`, `fillet`) y guardar.
- Los archivos grandes tardan minutos y el servidor envía avisos de progreso. Una importación se corta tras `COTAFORGE_IMPORT_STALL_S` segundos (120 por defecto) sin que FreeCAD use CPU, o tras `COTAFORGE_IMPORT_MAX_S` (3600 por defecto).
- Los cuerpos importados reciben la comprobación de validez barata de FreeCAD. La comprobación profunda es `inspect` con `action="validity"`.
- Parasolid (`.x_t`, `.x_b`) no se puede leer. Pide STEP.

Los resultados largos de `inspect` que no caben se cortan y el texto completo se guarda en `reports/`; el resultado nombra el archivo.

### Ensambles

Cada pieza se modela en su propio documento con sus propios ejes y luego se coloca en un documento de ensamble.

1. Pieza: `doc` `new`, operaciones, `save`. Las piezas siguen abiertas; `doc` `status` lista los documentos abiertos y `doc` `switch` elige sobre cuál actúan `op`, `undo` e `inspect`.
2. Ensamble: `doc` `new` con `kind="assembly"`.
3. `op` `insert_component`: `part` es el nombre de una pieza guardada (o el archivo de una pieza guardada que no está abierta) y `placement` es su origen y rotación. Para dos tornillos idénticos, inserta la misma pieza dos veces.
4. `place_component` mueve una instancia. La pose es absoluta.
5. `inspect` `interference` informa cada par de instancias como solapadas, tocándose o libres (con la distancia mínima).

**Juntas.** `add_joint` crea una junta `fixed`, `revolute` o `slider` entre instancias (`b` se mueve, `a` es de lo que cuelga; sin `a`, el suelo). La junta se crea en la posición actual, que es el valor 0, y `min` y `max` deben incluir el 0. `set_joint` la lleva a un valor absoluto. Una instancia con una junta se mueve con `set_joint`, no a mano. `inspect` con `sweep`, `id` y `range=[from, to, step]` recorre la junta por posiciones e informa el primer solape. Solo comprueba esas posiciones, así que el agente debe decir "comprobado cada 1°", no "sin colisión".

Los cambios en una pieza llegan al ensamble cuando vuelves a él con `switch`. Una pieza sin guardar no es lo que ve el ensamble: enlaza con el archivo guardado. El archivo del ensamble nombra sus piezas por ruta relativa, así que mantenlas juntas cuando lo copies o lo muevas. Una pieza usada por un ensamble abierto no se puede cerrar hasta que se cierre el ensamble.

**Windows, con la ventana visible.** Un enlace de FreeCAD exige que el documento enlazado esté cargado, no visible. Una pieza que hizo y guardó el agente pierde su ventana al insertarse (sigue abierta). Para editarla, haz `doc` `switch` a la pieza, edita, `save` y `switch` de vuelta.

### La ventana visible (`--visible`)

Por defecto FreeCAD corre sin ventana. Con `--visible` (o `COTAFORGE_VISIBLE=1`) abre su ventana y ves crecer el modelo. Necesita una pantalla (`DISPLAY` o `WAYLAND_DISPLAY` en Linux). FreeCAD visible usa su propia carpeta de preferencias, no la tuya. Esto habilita:

- `view` `selection`: el agente lee lo que seleccionaste en 3D o en una hoja de plano.
- `view` `show`: el agente señala caras, aristas, elementos del plano o vistas.
- `view` `set_view`: `iso`, `front`, `top`, `right`, `rear`, `left`, `bottom`, `dimetric`, `trimetric`.
- `view` `style`: transparencia, color, mostrar u ocultar cuerpos.
- `view` `section`: un plano de corte por eje y posición, para ver el interior.
- `view` `capture`: un PNG de la vista 3D.

`style` y `section` solo cambian lo que ves, no el modelo.

**Lo que cambias a mano.** Si editas el modelo en FreeCAD (una propiedad, una cota de croquis, un rasgo que añades o eliminas, tu propio deshacer), la **siguiente respuesta de cualquier herramienta** empieza con un aviso corto, por ejemplo:

```text
hand edits in the CAD since your last call (the engineer changed the model; re-read what you rely on):
- Pad (step base): Length: 16.0 -> 18.0
```

Se entrega una sola vez y no despierta al agente. Los cambios de cámara, visibilidad, color y selección nunca se notifican. La selección se lee solo si se pide. Los cambios a mano en una hoja de plano tampoco se notifican. Sin ventana no hay ediciones a mano.

Sin ventana, `view` ofrece solo `show` y `focus`.

**Croquis.** Un croquis que una operación ya usó se oculta cuando la operación termina. Ocultarlo es solo de visualización y no deja el documento sin guardar.

**Señalar en el plano.** Con la ventana visible puedes seleccionar en la ventana de la hoja una cota, una llamada, una vista, o una línea o círculo de una vista (Ctrl añade varios) y preguntar por ello. El agente recibe su id y tipo, el texto que imprime, qué mide y las caras y agujeros del modelo a los que se refiere. Una línea o círculo de una vista corresponde a una arista del modelo y a sus caras. También funciona al revés: selecciona una cara en 3D y pregunta "¿qué cotas se refieren a esta cara?". El agente puede señalarte con `view` `show` e `ids` (elementos del plano `d3`, vistas `view:top`, caras o aristas), con `on="sheet"`, `"3d"` o `"both"`; `clear=true` quita las marcas. No cambia nada en el documento.

### Exportar

`export` con `step` escribe un archivo STEP, `mesh` un STL y `macro` un script de FreeCAD que reconstruye el modelo. Las rutas relativas son relativas al espacio de diseño. **Si `check` tiene violaciones, la exportación se bloquea** (sección 9). Guardar y editar siguen permitidos. `export` nunca sobrescribe: responde `file_exists`; añade `overwrite=true` para reemplazar.

- `pdf` es siempre el plano: una hoja, un archivo. Con varias hojas, nombra una con `sheet=`.
- `dxf` con `face` o `flat` es un contorno de corte (abajo). Sin ellos es la hoja del plano.
- El PDF y el DXF de una hoja se escriben solo cuando el plano está terminado (mira "Plano terminado"). Los DXF de corte no pasan por esa puerta.

**DXF para corte láser o plasma.** `export` `dxf` con `face` (una cara plana, por id como `face:3` o por la misma referencia que usan las operaciones) escribe el contorno exterior y todos los vacíos a escala real en mm, proyectados sobre el plano de la cara con la esquina inferior izquierda en (0, 0). Los arcos siguen siendo arcos. Una cara curva se rechaza. El DXF no lleva espesor ni material: confirma la unidad y el espesor con tu taller. Con `flat="<unfold step>"` en vez de `face`, escribe el patrón plano de una pieza de chapa, sin líneas de doblez.

### Dibujos

`drawing` construye los planos de taller de la pieza. Cada plano es una **hoja**; un documento puede tener varias, cada una exportada a su propio archivo (sin PDF de varias páginas). El flujo: `plan` (lo que el agente pone solo en la hoja y lo que te pregunta), `compose` (la primera propuesta de la hoja entera, en una sola orden), `capture` y `review`, luego correcciones locales de un ítem cada vez con `update`, `add` o `remove` (nada más de la hoja se mueve), `review`, exportar. Un segundo `compose` es para un cambio que afecta a toda la hoja (papel, escala, vistas, esquema de acotación). Para construir la hoja en pasos pequeños (`create` y unos pocos ítems por `add`), usa `memory(action="brief", drawing_flow="steps")`.

**Construido en vivo.** Con la ventana visible, la hoja de TechDraw se abre en `create` y cada elemento aparece a medida que el agente lo añade, así que puedes interrumpir o sugerir. El agente trabaja en pasos pequeños. Sin ventana, el plano se construye igual; lo ves con `capture` o al exportar.

**`create`.** Parámetros: `template` (`A4`, `A3`, `A2`, `A1`, `A0`, `Letter`; horizontal), `angle` (`first` por defecto, o `third`), `title`, `author`, `date`, `material`, `scale`, `drawing_number`, `revision`, `general_tolerances` (por defecto `ISO 2768-mK`), `company`, `title_block` (más campos por nombre), `views`, `isometric`, `projection` y `fresh`. El cuadro de rotulación sigue la ISO 7200; su idioma es el español salvo que pases `language="en"`. La escala se elige automáticamente salvo que des una (`scale="1:2"`).

- Sin `views`, una pieza de revolución con todos sus rasgos coaxiales y algo por dentro (arandela, casquillo, anillo escalonado) recibe un solo corte por el eje en la vista principal, más la isométrica: sin vista de extremo, y sin línea de corte ni letras (el plano de corte es el eje de simetría; ISO 128-44). Si algún rasgo no es coaxial (agujeros en un círculo de paso, una cara plana, un chavetero) recibe la vista a lo largo de su eje y un corte A-A por el eje; una pieza maciza recibe una sola vista exterior. Cualquier otra pieza recibe cuatro vistas (frontal, superior, derecha, isométrica). `remove_view` y `add_view` con `views=["right"]` quitan o restauran una vista.
- `create` sobre una hoja existente la reconstruye y conserva lo que sigue siendo válido: elementos, respuestas de revisión, el revisor elegido, cuadro de rotulación, papel, ángulo, idioma y escala. Lista los elementos que ya no se resuelven en vez de borrarlos. `fresh=true` construye desde cero.
- Para otra hoja del mismo documento usa un nombre de `sheet` nuevo. `body` dibuja un cuerpo por hoja.
- **`projection`** la elige el ingeniero; el agente nunca la elige. `techdraw` (por defecto) crea vistas nativas de TechDraw, que pueden tardar horas por vista en un ensamble de cientos de cuerpos. `hlr` proyecta las vistas estándar con `TechDraw.projectEx` y tarda segundos o minutos. El plano es el mismo, pero las vistas de la ventana son símbolos dibujados, no vistas editables de TechDraw. En un ensamble grande y lento, el agente puede ofrecer `hlr` y esperar tu respuesta.

**Qué es nativo en TechDraw.** Objetos nativos, que puedes editar en FreeCAD: las vistas (con `projection="techdraw"`), `hole_dim`, `dim` (FreeCAD mide el valor), `datum_dims` en modo `ordinate`, `note`, las cotas generales y el cuadro de rotulación. Símbolos dibujados: `datum`, `gdt`, `finish`, marcas y líneas de centro, arcos de rosca, `section`, `detail`, `aux_view`, la vista explosionada, la lista de piezas y la tabla de agujeros. La razón es que TechDraw 1.1.3 no tiene un objeto equivalente, o no dibuja en estilo ISO. Si una cota nativa no coincide con el modelo, el elemento se dibuja como símbolo y `review` lo informa. Un texto o una posición que edites a mano se respeta hasta que el elemento cambie. El PDF y el DXF son lo que entregas: con ventana, TechDraw escribe el PDF; sin ella, lo hace el compositor propio de CotaForge, y la respuesta de `export` dice cuál.

**Elementos (`add`).** `drawing` `add` con `items=[...]`; cada uno tiene un `type` y, opcionalmente, un `id`. La respuesta dice por elemento: `ok`, `warning`, `error` (guardado, no se puede dibujar ahora) o `refused` (no guardado). **El agente nunca escribe números de cotas: se miden sobre el modelo** cada vez que se dibuja la hoja.

| Elemento | Qué coloca |
|---|---|
| `hole_dim` | Llamada de agujero con línea de referencia, en la vista donde se ve el círculo (`4× Ø8 pasante`, `M6 ↧ 12`). Los agujeros idénticos comparten una llamada. Elige los agujeros con `at`, `face` o `all=true`. `dowel=true` marca los agujeros de posicionamiento. `text` da tu redacción de la llamada; se contrasta con el modelo y `review` avisa si lo contradice. `thread` declara la rosca de un agujero no hecho con `hole`. `view="A-A"` lo pone en un corte. |
| `dim` | `kind="distance"` entre dos referencias (un punto o eje de agujero, una cara, una arista), `radius`, `angle`, `chamfer`, `diameter`. |
| `datum_dims` | Posiciones de agujeros desde dos aristas de referencia: `mode="ordinate"` o `mode="table"`. Cada hoja con agujeros ya lleva la suya (`loc`, `auto=true`). |
| `line` | Una línea ISO 128 (`chain`, `dashed`, `continuous`): un `axis` de un cilindro o una línea `from` y `to`. |
| `note` | Una nota numerada en el bloque `NOTAS:`. |
| `revision` | Una fila de la tabla de revisiones. |
| `overall` | Las tres cotas generales (las hace `create`). |
| `view_lines` | Líneas ocultas y ejes en las vistas estándar. Sin él no se muestran. Añádelo en toda pieza con agujeros. |
| `section` | Un corte completo con línea de corte, letras y rayado a 45°. Da `holes="all"` o `at` y `normal`. No se admiten medios cortes. |
| `detail` | Un círculo ampliado de una vista, rotulado `B (5:1)`. |
| `aux_view` | La pieza vista de frente a una cara inclinada. |
| `datum` | Un triángulo de referencia ISO 5459 con una letra, sobre una `face` o sobre el eje de un `hole_dim` o de un `dim` de diámetro (`on`). |
| `gdt` | Un marco de tolerancia geométrica ISO 1101 (`char`, `value`, `datums`...). Citar una referencia que no existe en la hoja es un error. Una tolerancia geométrica escrita como texto de una nota se señala (`gdt_as_note`), igual que una referencia que ningún marco cita (`datum_unused`). Un marco de posición cuelga bajo la llamada de la medida; las cotas que ubican su rasgo van enmarcadas (teóricamente exactas; las que añades llevan `exact=true`) y su primer datum debe fijar el eje (la cara a la que el agujero es perpendicular). |
| `finish` | Acabado superficial (ISO 21920-1): `ra`, `machining`, o `general=true` para el símbolo de "todas las demás superficies". |

Los elementos con línea de referencia aceptan `offset_mm` y `side`. Sin ellos, la llamada busca espacio libre. Con ellos va exactamente donde se pide, con un `warning` si ese lugar está ocupado. `at_mm` es una posición en la hoja desde su esquina inferior izquierda.

`remove` con `items=["h2"]` elimina solo esos elementos (los ids son obligatorios). `remove_sheet` elimina una hoja. `list` muestra cada hoja y lo que hace cada elemento frente a la pieza. Si cambiaste la pieza, `update` reorganiza las vistas y conserva todos los elementos. Un elemento que apunta a una cara que ya no existe se informa como `error`; nunca se dibuja con el número viejo.

**Ajustes y tolerancias.** `hole_dim` y `dim` aceptan `fit` (`"H7"`, `"g6"`, `"H7/g6"`; ISO 286, grados IT5 a IT11, hasta 500 mm), `tol` (`0.1` o `[upper, lower]`) y `show` (`symbol`, `deviations`, `both`, `limits`). El tamaño nominal es el del modelo. Una cota sin tolerancia propia informa la tolerancia general ISO 2768 de la hoja.

Un ajuste imprime su clase y, si es una sola, sus límites (`Ø20 H7 (+0,021/0)`). La hoja no lleva ninguna nota que pida al taller verificarlos.

> **No verificado.** Las tablas ISO 286 e ISO 2768 se transcribieron a mano de manuales y se contrastaron con los ajustes habituales, no con el texto de la norma.

Las roscas (ISO 6410) se dibujan automáticamente para los agujeros hechos con `hole`, como un arco de tres cuartos en el diámetro mayor.

**Revisar y escribir.** `drawing` `capture` devuelve la hoja como imagen (con `region=[x0, y0, x1, y1]` en mm de hoja para ampliar). Con ventana, la imagen sale de la misma escena de hoja que el PDF, incluido lo que un `run_native` puso en la hoja. Sin ventana, los objetos nativos que no se pueden pintar se listan como `NOT DRAWN`. `review` no lee objetos nativos; avisa con `native_unread`, y comprobar esa parte significa mirar `capture`. `export` `pdf` valida el PDF que acaba de escribir y no lo guarda si está mal formado.

El DXF es R12, con capas por rol (`CONTORNO`, `OCULTAS`, `COTAS`, `TEXTO`, `ROTULO`, `EJES`, `SOMBREADO`, `ROSCAS`), y el cuadro de rotulación dentro. Los grosores de línea ISO 128 se aplican en el PDF; en el DXF los lleva el color de la capa.

**Planos de ensamble.** `create` sobre un ensamble dibuja todos los componentes en su pose, sin líneas ocultas, y deja espacio para una lista de piezas. Estos elementos existen solo en ensambles: `parts_list` (ISO 7573; una fila por pieza distinta, cantidades calculadas), `balloons` (ISO 6433; uno por posición, sobre una cara visible de una instancia en la `view` elegida) y `exploded` (componentes separados, con `mode="radial"` o `"axis"`, `moves` para fijar el desplazamiento de un componente, y `trails` de trayectoria). La vista explosionada es una transformación del plano; el ensamble no se mueve. La soldadura queda fuera de alcance.

**`drawing` `review`.** No dibuja nada ni cambia nada. Lista, sin modelo de lenguaje, lo que marcaría un revisor, cada hallazgo con un `id` y el elemento implicado: agujeros sin llamada o posición, rasgos sin cota, sobreacotado, referencias sin definir, referencias a caras que ya no existen, colisiones de disposición y (en ensambles) problemas de lista de piezas y globos. `warning` es un error probable e `info` algo por confirmar. Úsalo mientras construyes la hoja; no reemplaza mirar `capture`.

### Plano terminado: quién lo revisa y qué bloquea la exportación

`export` `pdf` (o `dxf` sin `face` ni `flat`) **se rechaza hasta que alguien distinto del agente que dibujó la hoja la haya revisado y no quede nada sin resolver**. La respuesta dice en qué caso está la hoja y qué falta. El servidor no decide que un plano esté bien. Obliga al agente a que otro mire la hoja y a que responda cada punto.

No hay PDF borrador. El borrador es la hoja de TechDraw dentro del `.FCStd`; la ves en vivo y con `capture`.

**Quién revisa: `drawing_review`.** Se guarda en `brief.yaml` junto al sector y los procesos. Cámbialo con `memory(action="brief", drawing_review="<mode>", text="<tus palabras>")` o editando el archivo. Salir del valor por defecto es decisión tuya: el agente debe citar tus palabras en `text`.

| Modo | Quién revisa | Qué abre la exportación |
|---|---|---|
| `engineer_or_fabricator` (por defecto) | Tú eliges revisar o que lo haga el agente `fabricator`. Si no respondes en `drawing_review_wait_min` minutos (15 por defecto), cuenta el fabricator. | El informe de quien cuente, con todos sus puntos respondidos. |
| `fabricator` | Solo el agente `fabricator`. | Su informe, con todos los puntos respondidos. |
| `engineer` | Solo tú, sin límite de tiempo. | Tu informe, con todos los puntos respondidos. |
| `own` | El agente de dibujo se hace a sí mismo una lista de comprobación. | La lista, cerrada. |
| `none` | Nadie. | Nada. Ni siquiera se piden los hallazgos automáticos. |

Los valores antiguos en español `ingeniero_o_fabricante`, `fabricante`, `ingeniero`, `propio` y `ninguno` se siguen aceptando como entrada. Fija la espera junto con el modo (`drawing_review="engineer_or_fabricator:30"`) o sola (`drawing_review="30"`).

**El agente `fabricator`** hace de maquinista, soldador o ensamblador que recibe solo el plano: la imagen de la hoja y sus textos impresos, nunca el modelo 3D. Responde si puede fabricar y ensamblar la pieza, qué falta, qué es ambiguo y qué es costoso tal como está dibujado. Usa las entradas de memoria de dibujo de tu sector como lista de comprobación. Registra su informe con `drawing(action="review_report", by="fabricator", items=[...])`. Cada punto tiene un `kind` (`missing`, `ambiguous`, `costly`, `other`), `where`, `text` y `fix`, y recibe un id como `fab1.1`. No puede modificar la hoja.

**El flujo por defecto.**

1. El agente llama a `drawing(action="request_review", sheet)`, que anota la hora en la hoja. Te hace una pregunta: ¿revisarás la hoja tú o que lo haga el fabricator? Inicia un temporizador en segundo plano para que Claude Code lo despierte en el plazo, y mientras tanto sigue.
2. Si dices "yo", el agente lo registra (`review_choice`, `by="engineer"`) y espera tu informe sin límite de tiempo. Dictas observaciones en el chat (o "sin observaciones"). Lo que señales en la hoja lo lee con `view` `selection`. Las registra con `review_report`, `by="engineer"`.
3. Si dices "fabricator", o no respondes a tiempo, el agente lanza el `fabricator` en ese momento.
4. Antes de exportar, todos los puntos del último informe que cuenta están respondidos. El agente corrige la hoja y responde con `drawing(action="answer", id="fab1.1", text="fixed: what changed")`, o responde `does not apply: why`. Si revisaste tú, incluso después del fabricator, cuenta tu último informe.
5. La elección del revisor pertenece a la hoja: un cambio posterior no vuelve a preguntar. Un informe sigue vigente después de las correcciones que pidió. El plano lleva un contador de cambios y muestra "revisado en el cambio 3, la hoja está en el 5". Solo hay una nueva revisión si la pides o si el agente decide que la hoja cambió más allá del informe.

**El servidor no puede probar quién escribió un informe.** Cualquier agente puede llamar a `review_report`. Garantiza el orden (el informe del revisor elegido, todos los puntos respondidos) y que las respuestas quedan en la hoja, donde las lees con `review`.

**Hallazgos automáticos.** En todos los modos salvo `none`, los hallazgos automáticos del CAD siguen reteniendo la exportación. Se calculan sobre la geometría real y sobre lo que dibuja la hoja, nunca se adivinan. Cubren las posiciones de los agujeros y sus referencias, agujeros con ajuste sin tolerancia, referencias sin definir, rasgos sin cota, marcas de centro y ejes que faltan, legibilidad y las notas estándar (material, aristas, tratamiento térmico, recubrimiento, rugosidad, marcado). Cada nota está presente o declarada "does not apply". `drawing` `review` los lista con sus ids.

**Solo en el modo `own`** el agente también responde las preguntas de juicio y las entradas de memoria que dispara la exportación del plano, que debe haber leído en esta sesión.

**Cómo se cierra un punto.** Corrígelo (añade la cota, la nota o el corte) o respóndelo una vez: `drawing(action="answer", sheet?, id, text)` con `text` = `applies: <cómo, en esta pieza>`, `does not apply: <por qué>` o `fixed: <qué cambió>`, con al menos tres palabras después de los dos puntos. Ejemplo: `id="notes_heat_treatment"`, `text="does not apply: aluminum 6061-T6 is delivered treated"`. Un hallazgo que no se corrige ni se responde detiene la exportación. Las advertencias de la ventana (`window_differs`, `native_fallback`) nunca la detienen.

Las respuestas viven en la hoja, dentro del `.FCStd`, y sobreviven al guardar y reabrir. Léelas: un "does not apply" sin una razón real es una afirmación falsa hacia ti. **Una respuesta no se imprime en el plano.** Lo que el taller deba leer va en una `note`.

**"Aprobado por" en el cuadro de rotulación es tuyo.** Solo tú lo llenas, con `drawing(action="approve", sheet="Plano", by="Ana", reason="Ana: apruébalo")`. El agente lo llama solo cuando tú lo ordenas, citando tus palabras. Cualquier cambio posterior en la hoja lo vacía otra vez. Aprobar no se salta la revisión.

**No verificado en una sesión real de Claude Code:** que el temporizador en segundo plano despierte al agente y que el agente lance el `fabricator` por sí mismo. El reloj, las puertas y los informes están probados con el servidor real y FreeCAD.

La guía del agente para esto es `cad_guide` con `topic="drawing:done"`.

#### Un dibujante aparte (`drafter`)

Los planos los puede hacer otro agente, el `drafter`, en su propia sesión de Claude Code para que lo veas y lo corrijas. Su contexto es solo las reglas de dibujo y la hoja. No conoce la intención del diseño, así que pregunta lo que no está en el archivo de intención ni en el modelo.

1. **Entrega.** El diseñador guarda la pieza, escribe `drawings/<part>-intent.md` y cierra la pieza. Un archivo está abierto en una sola sesión a la vez, porque la que guarda al final sobrescribe a la otra. Llama a `channel` `open_drawing`, que abre una terminal en la carpeta del diseño ejecutando `claude --agent cotaforge:drafter`. Si no se abre, el diseñador te da el comando para que lo escribas tú.
2. **Preguntas.** El dibujante pregunta al diseñador con `channel` `ask` y espera (`wait`). El diseñador responde con lo que sabe y te pregunta solo lo que no sabe. Puedes responder en la sesión de dibujo (`by=engineer`). Todo se guarda en `drawings/<part>-questions.jsonl` y en una copia legible `drawings/<part>-questions.md`.
3. **Una respuesta no es una decisión.** El diseñador contrasta lo que le dijiste al dibujante con la intención, los cálculos, las normas y el modelo. Si coincide, lo anota en `assumptions-and-decisions.md`. Si no, te dice por qué y el punto queda en `dispute`. La decisión final es tuya; se registra con `decide`, con la objeción al lado.
4. **Cambios al modelo.** El dibujante nunca cambia la pieza. Pregunta por el canal, guarda y cierra; el diseñador cambia y cierra; el dibujante reabre y ejecuta `update`.
5. **Revisión.** El dibujante pasa la puerta de revisión, luego exporta y avisa al diseñador con `done`.
6. **Cerrar la ventana.** Cuando el dibujante registra `done` (hoja guardada, ningún punto del fabricator abierto, ninguna pregunta abierta), el harness cierra esa ventana por su propio pid, nunca por nombre. Si alguien está a mitad de un turno o no se puede saber, te avisa y no la cierra. `close` con `confirmed=true` la cierra a petición tuya.
7. **Si cierras la ventana por error.** El dibujante guarda después de cada cambio. El diseñador se entera y ofrece `reopen`: la misma conversación con `claude --resume <id>` (el id está en `drawings/<part>-window.json`), o una sesión nueva que lee el canal y la hoja.

Los roles del canal son `drafter` y `designer`; el humano es `engineer`.

**No verificado en una sesión real:** dos sesiones sobre la misma carpeta, la ventana que abre `open_drawing` y su cierre por pid, `claude --resume` con `--agent`, leer la transcripción para saber si hay un turno en curso, y el comportamiento del software de CAD cuando una sesión guarda y cierra una pieza y la otra la abre.

#### Calidad mínima de los planos

Toda hoja recibe este mínimo sin que lo pidas. El papel, la proyección y el cuadro de rotulación siguen siendo tu elección.

- **Todo agujero localizado.** Una hoja con agujeros recibe un elemento `loc`. Nunca cuenta desde la esquina de la caja envolvente. En cada dirección cuenta desde tus referencias declaradas (cotas de línea base); si no, desde el plano de simetría de la pieza; si no, no dibuja nada y te pregunta desde qué cara o arista se cuentan las posiciones.
- **Llamadas compactas.** Los agujeros idénticos comparten una llamada. Las líneas de referencia van al lado más limpio y no se cruzan.
- **Agujeros de posicionamiento.** `dowel=true` termina la llamada en «registro» y pone un anillo en cada agujero. La función de un agujero es cosa tuya; `review` avisa de un probable agujero de posicionamiento sin marcar.
- **Notas numeradas.** El bloque `NOTAS` numera, en orden: el espesor (cuando la hoja tiene una vista estándar), y las notas del agente.
- **Cuadro de rotulación.** Una plantilla de TechDraw guardada en el documento. `create` o `update` con `title_block={"document_type": "...", ...}` fija campos por nombre. Todavía no hay logotipo.
- **Una sola vista.** Una placa cuyos agujeros atraviesan el espesor se puede dibujar en una vista con una nota de espesor (`views=["top"]`).

No se admiten: vistas interrumpidas, cortes parciales ni líneas de doblez en chapa.

### Chapa metálica (opcional)

CotaForge modela chapa doblada con `base_flange` (un contorno croquizado con agujeros dentro se vuelve una chapa), `edge_flange` (dobla una pared desde una arista recta) y `unfold` (patrón plano). Necesitan el complemento **SheetMetal**, que no viene con FreeCAD (<https://github.com/shaise/FreeCAD_SheetMetal>).

**Instálalo:** en FreeCAD, *Herramientas > Administrador de complementos (Addon Manager)*, busca `SheetMetal`, *Instalar*, y luego reinicia FreeCAD y Claude Code. El servidor ofrece las tres operaciones solo cuando detecta el complemento. Si falta o no carga, `cad_guide` y el error dicen cómo arreglarlo.

El servidor lee las carpetas `Mod` de usuario donde instala el Administrador de complementos (en Windows `%APPDATA%\FreeCAD\v1-1\Mod` y `%APPDATA%\FreeCAD\Mod`), porque la ventana visible usa un perfil privado. Si el tuyo está en otro lugar, define `COTAFORGE_FREECAD_MOD_DIRS` con las carpetas `Mod`, separadas por `;` (Windows) o `:` (Linux/macOS).

**Uso:**

1. `base_flange`: un croquis con el contorno y, dentro, agujeros y recortes, más el espesor. Una pieza de chapa no admite `cut`, `hole` ni `fillet`; los agujeros van en este croquis.
2. `edge_flange`: una arista entre una cara grande y su cara de espesor. Parámetros: `length_mm`, `angle_deg` (90 por defecto), `bend_radius_mm` (radio interior, **sin valor por defecto**: es el de tu herramental), `position` y `length_from`.
3. `unfold`: una cara plana grande (fija) y **`k_factor`, obligatorio y sin valor por defecto**. Es el factor K de ANSI (0 a 1, normalmente 0.3 a 0.5). Depende del material, el espesor, el radio y el herramental: **pregunta al taller**. El agente debe declarar el valor que asume en `intent`. Un K equivocado da un patrón plano equivocado.

`unfold` informa el tamaño del patrón plano, el espesor y el área del desarrollo. El volumen y la caja envolvente que siguen apareciendo describen la pieza doblada. CotaForge también calcula por fórmula los volúmenes de las alas y el tamaño del patrón plano y los compara con los de FreeCAD; si no coinciden, el paso se deshace. Esta comprobación se exige solo en el caso simple (contorno rectangular, un ala de largo completo por lado, ningún agujero dentro de un doblez) y en los demás se informa.

Para cortar, `export` `dxf` con `flat="<unfold step id>"`. Las líneas de doblez no se incluyen.

Límites: solo alas en aristas rectas; no se prueban alas sobre alas; no hay dobladillos ni alivios configurables. Verificado con el complemento 0.8.26 y FreeCAD 1.1.3 en Linux y Windows, sin ventana; con ventana no está verificado.

### Masa y lista de materiales

`inspect` `properties` informa masa = volumen medido x densidad y nombra la fuente de la densidad. La densidad viene de la propiedad `density_kg_m3` de la entrada `material` declarada en la memoria, o de una tabla corta incluida con su fuente citada (acero estructural 7850 kg/m³, aluminio 6061 2700; el inoxidable 304 y 316 están marcados como sin verificar). Sin una densidad conocida responde `mass: not available` con el motivo; el agente no adivina una.

`inspect` `bom` lista una línea por cuerpo distinto con cantidad, volumen, masa, material y ruta, y la masa total.

### Catálogo de piezas comerciales

El agente consulta `catalog` en vez de recordar dimensiones. Tiene dos partes:

- **Tablas estándar incluidas.** Tubos estructurales (NTC 4526:2021, catálogo de Ternium Colombia), ángulos de alas iguales (ASTM A36), calibres de lámina y tornillería métrica ISO (tornillos y pernos hexagonales ISO 4014/4017, tuercas ISO 4032, arandelas ISO 7089). Cada resultado indica sus tamaños y su fuente; los tamaños cubiertos están en los resultados.
- **Tu catálogo**, en `~/.cotaforge/catalog/`: un archivo JSON legible por entrada, cada una con su fecha. El rol `compra` (por defecto) es un proveedor al que compras, con disponibilidad, precio y unidad. El rol `medidas` es una tabla de dimensiones de un catálogo que es solo referencia. Lleva la norma y la fuente, y no tiene proveedor ni precio.

Pregunta con tus palabras: "masa de un tubo cuadrado de 40x40x2". El agente usa `search` y `get`. Para añadir algo, dile "guarda esta cotización" (nombre, proveedor, fecha, precio y unidad) o pásale una tabla y di "guarda estas dimensiones como referencia". Añade solo lo que diste o confirmaste y nunca inventa un precio. Un precio nuevo es una entrada nueva. Para corregir o quitar una entrada, edita o borra su archivo.

Cada resultado indica de dónde viene el número: `catálogo (NTC 4526:2021, ...)`, `catálogo del usuario (proveedor, fecha)`, `catálogo del usuario, solo medidas`, `calculado` o `SIN VERIFICAR` (confirma con el proveedor antes de usar).

El catálogo es pequeño y no es la única fuente. Si una pieza no está ahí, el agente busca en la web y la marca "web (URL, fecha)" o como supuesto. El calibre de una lámina nombra una tabla: confirma el espesor en mm con el proveedor. La masa publicada de los tubos puede superar la de la sección nominal hasta en un 13%; la herramienta muestra ambas.

### Comprar antes de dibujar

El agente se pregunta si una pieza que transmite, guía, sella, sujeta o soporta de forma normalizada (rodamientos, acoples, sellos, chavetas, resortes, motorreductores) es comercial, y busca en `catalog` y luego en la web.

- La búsqueda se registra en `assumptions-and-decisions.md`, una línea por pieza: qué, dónde, qué se encontró y la decisión (comprar, o fabricar y por qué). El revisor comprueba que cada cuerpo que parece comercial tenga su línea.
- Si compras y tienes el STEP, dáselo al agente: lo importa (`doc` `import`) y le da el rol `comercial`.
- Si no hay 3D, el agente modela una envolvente simplificada (id `env_...`, rol `comercial`, fuente en la intención) a partir de dimensiones que tú das o de una hoja de datos publicada que cita, nunca de memoria. Sirve para ubicar y comprobar choques; no es la pieza real.
- El agente nunca te pide registrarte en el sitio de un proveedor ni dar datos personales para obtener un STEP.

### Biblioteca técnica

La biblioteca es tu carpeta de normas (DIN, ISO, NTC), manuales y notas. Permite al agente encontrar y citar una página en vez de recordar una cifra.

- **Tú entregas los archivos; el agente los archiva.** Dale un PDF, una imagen, un `.txt` o un `.md` y di "archiva esta norma". Elige el tema, el nombre y el mapa de páginas, y la archiva con `catalog` `file`. No se aceptan Word, Excel ni PowerPoint: exporta a PDF. No hay OCR: un PDF escaneado o una imagen se encuentra solo por su ficha.
- **Dónde.** `~/.cotaforge/library/`, o la carpeta de `COTAFORGE_LIBRARY` (por ejemplo una carpeta de red de un equipo). Una carpeta por tema, ordenada por lo que buscas (`DIN 7979` va en pasadores). Los temas y sinónimos están en `topics.yaml` en su raíz.
- **Ficha.** `<name>.ficha.md` junto a cada documento: título, tema, norma, edición, páginas y un mapa de páginas. Puedes editarla. El documento se copia con un nombre estándar; tu original no se toca.
- **Búsqueda.** `catalog` `search` también busca en la biblioteca: primero las fichas, luego el texto de las páginas. El agente abre la página antes de citarla. Las citas se leen "DIN 7979, 1997, p. 3".
- **Los archivos que copias a mano** se indexan en la siguiente búsqueda. Pídele la ficha al agente.
- **El índice** vive en `COTAFORGE_HOME/indexes/` y se puede borrar; se reconstruye. El texto completo usa `pypdf`.
- **Nunca se distribuye.** CotaForge no incluye documentos, solo código y una lista inicial de temas.

### Problemas abiertos: flujos, registro y cálculos

Cuando un problema no tiene una arquitectura conocida, tú eliges el flujo de trabajo. `/cotaforge:plan` pregunta una vez y guarda tu preferencia en la memoria de usuario. Tres flujos iniciales (el agente puede proponer uno mejor y decir por qué):

- **Primero el boceto**: entender y buscar, proponer un boceto rápido en el software de CAD, acordarlo contigo, guardar una copia, pausar, y luego construir el detalle como un ensamble con piezas independientes.
- **Directo**: para arquitecturas conocidas, casi directo al modelo.
- **Diseño sistemático**: el método clásico de los libros de diseño. Primero funciones y alternativas, luego el concepto y el detalle.

Antes del detalle, el agente hace una pausa para decirte qué datos son supuestos y calcular lo que dimensiona el diseño.

**`assumptions-and-decisions.md`** es el registro del diseño: cada supuesto, decisión y cálculo con su fuente. El agente lo mantiene y `/cotaforge:resume` lo lee. Puedes editarlo.

**`calculations/`** contiene los scripts de Python que producen los números (`calculations/<topic>.py`); el registro dice qué script produjo cada cifra. La primera vez que el agente ejecuta uno, Claude Code pide permiso. Apruébalo para `calculations/` después de revisar el script. El espacio de diseño no lo permite por defecto porque un script escrito por el agente podría saltarse el harness.

---

## 8. Guardar y retomar

El archivo nativo de FreeCAD (`.FCStd`) es el avance de tu diseño. Guarda con `doc` `action="save"`, retoma con `action="open"` o `/cotaforge:resume <archivo>`.

### Qué se guarda

Guardar escribe dos archivos:

- `plate.FCStd`: el documento de FreeCAD, con el material y la ruta declarados.
- `plate.cotaforge.json`: el *registro de sesión* (sidecar): historial de pasos, identificadores y las medidas al momento de guardar.

Mantén ambos juntos cuando copies o muevas la pieza. Los cuerpos importados que ninguna operación cambió se guardan de forma compacta en el registro; sus caras y aristas se regeneran al reabrir con los mismos identificadores.

### Al reabrir

| Situación | Resultado |
|---|---|
| Guardado por CotaForge, sin tocar desde entonces | Se restaura la sesión **exacta**: mismos identificadores de paso y de cara, historial intacto. |
| Editado en otro lugar (por ejemplo en FreeCAD), o sin registro de sesión | **Adoptado**: se abre tal cual, cada cuerpo recibe un alias sobre el que construir. El informe avisa de *deriva* si el volumen o el tamaño cambiaron desde el último guardado. |
| Cualquier `.FCStd` no hecho con CotaForge | También se adopta. |

`doc` `reload` vuelve a leer el archivo del documento activo y descarta la copia de la sesión. Se niega si hay cambios sin guardar, salvo con `force=true`.

Los pasos anteriores a la reapertura no se pueden deshacer: el software de CAD no guarda el historial de deshacer. Puedes añadir pasos nuevos y deshacerlos. `/cotaforge:resume` abre el archivo, lee el informe, lista el árbol de operaciones, mide y ejecuta `check`, sin modificar nada.

### Protecciones al guardar

- Un archivo que no es de este documento no se sobrescribe (`file_exists`). Guarda con otro nombre.
- Un archivo modificado fuera desde que CotaForge lo leyó o guardó por última vez no se sobrescribe (`file_conflict`). Guarda con otro nombre, `doc` `save` con `force=true`, o `doc` `reload`.
- Un documento con cambios sin guardar no se cierra (`unsaved_changes`) salvo que pases `discard=true`.

Pueden estar abiertos varios documentos a la vez; solo uno está activo. Si FreeCAD se cae, el siguiente paso reabre el último documento activo guardado (con las piezas de un ensamble) y dice cuáles otros se perdieron.

### Si FreeCAD se cierra o se queda ocupado

Si FreeCAD se cierra en medio de una llamada (lo cierras tú, se cae), esa llamada responde en un par de segundos con `session_lost`: que ese FreeCAD está cerrado, qué se perdió y cómo continuar. La **siguiente** llamada relanza FreeCAD y reabre el último archivo guardado; esa llamada no se ejecuta, repítela.

Si cancelas una llamada larga, FreeCAD no puede interrumpir lo que está calculando. Mientras tanto, las llamadas que necesitan el software de CAD responden `cad_busy` tras unos segundos. Cerrar FreeCAD lo libera (la siguiente llamada lo relanza y reabre el último guardado; el trabajo sin guardar se pierde). `memory`, `catalog` y `cad_guide` siempre responden.

**Copia de recuperación.** Un `run_native` que usa TechDraw ha cerrado FreeCAD en uso real. Antes de ejecutar uno, si el documento tiene cambios sin guardar, CotaForge guarda una copia en `recovery/` sin tocar tu archivo. Un guardado limpio la borra. Si FreeCAD se cierra, la copia se conserva como `<name>.crash-<time>.FCStd` y la respuesta dice qué se estaba ejecutando y cómo continuar: `doc` `open` la copia, luego `doc` `save` a tu archivo con `overwrite=true`. No se hacen copias de ensambles.

**La revisión del plano no se pierde.** Las respuestas, el revisor elegido y los informes viven en la hoja. También se anotan en `recovery/<name>.revision.jsonl` y se vuelven a poner al abrir el archivo si a la hoja le faltan. La aprobación ("Aprobado por") nunca se restaura: es tuya y se vuelve a pedir.

---

## 9. Materiales, procesos y restricciones

Un **proceso de fabricación** es cómo se hará la pieza: fresado, torneado, corte láser, impresión 3D. Cada máquina tiene límites. Declararlos permite que `check` avise cuando la pieza no se puede hacer en tu taller.

### Declarar

Declarar es opcional. `/cotaforge:declare <lo que dices>` guía al agente:

1. Revisa lo declarado (`memory` `index`).
2. Guarda el material o proceso (`memory` `save`, tipo `material` o `process`).
3. Lo asigna a la pieza abierta: `doc` `set_material`, `doc` `set_route` (la lista ordenada de procesos).
4. Ejecuta `check`.

Una entrada de proceso tiene un `tag` y capacidades. Tags conocidos: `turning`, `milling_3axis`, `laser_cut`, `sheet_bending`, `fdm`, `sls`, `welding`. Se puede usar otro tag, sin comprobaciones automáticas. Ejemplo, `memory/fresadora-taller.md`:

```yaml
---
id: fresadora-taller
type: process
title: Fresadora 3 ejes del taller
applies_when: Piezas prismáticas
not_when: Piezas de revolución
provenance:
  source: shop_measurement
  reference: Ficha de la máquina
  by: Ana
  date: '2026-10-11'
process:
  tag: milling_3axis
  capabilities:
  - name: build_volume_mm
    value_set: [100, 80, 60]
---
```

**Procedencia.** Un número en la memoria es un hecho y necesita `provenance`: `source` (`shop_measurement`, `supplier_datasheet`, `standard`, `user_statement`, `correction`, `reference_book`, `manufacturer_guide`), una `reference`, quién lo afirma (`by`) y la fecha. Una entrada con números y sin procedencia se rechaza. El agente no puede guardar una cifra que estimó. Si falta un dato, pregunta o lo marca `placeholder` (un valor pendiente que nunca bloquea).

### Qué verifica `check`

`check` compara la pieza medida con lo que declaraste y da `pass`, `violation` o `unverified` por cada elemento:

- **Material:** el asignado está entre los declarados.
- **Ruta:** todo proceso asignado está declarado.
- **Envolvente:** la caja envolvente cabe en `max_diameter_mm`, `max_length_mm` o `build_volume_mm` del proceso. Con varios procesos en la ruta, basta uno que aguante la pieza.

```
check: 0 violation, 0 unverified, 3 pass
PASS: material | measured 6061 | limit Aluminio 6061 - ...
PASS: route | measured fresadora-taller | limit fresadora-taller - ...
PASS: envelope.build_volume_mm | measured 60 x 40 x 10 | limit 100 x 80 x 60 - ...
geometry: valid
export: allowed
```

Con una violación, `export` responde `export_blocked` y no escribe nada. Guardar sigue permitido:

```
check: 1 violation, 0 unverified, 2 pass
VIOLATION: envelope.max_diameter_mm | measured 250 | limit 200 - ...
export: BLOCKED by 1 violation(s); editing and doc save are still allowed
```

### Qué no hace `check`

- El diámetro y la longitud se deducen de la caja envolvente, suponiendo que el eje va a lo largo del lado más largo. Si los tres lados están a menos de un 5% entre sí, marca el elemento `unverified`. Una placa plana contra `max_diameter_mm` da "40": úsalo con piezas que realmente se tornean.
- Otras capacidades que puedes guardar (`min_wall_mm`, `min_internal_radius_mm`, `achievable_it_grade`...) se registran pero no se comprueban.
- Si la pieza tiene pasos de `run_native`, las comprobaciones de envolvente pasan a `unverified`.
- Las tolerancias no se verifican.

Si no hay nada declarado, `check` lo dice: nada restringe el diseño. Un fragmento de `run_native` que cambie el material o la ruta a algo no declarado se deshace.

### El revisor

`/cotaforge:review` lanza el agente `reviewer`, que mide por su cuenta y solo puede leer: no tiene herramientas para modificar la pieza ni guardar memoria. Su informe separa lo **Verificado** (respaldado por una medida o por `check`) del **Juicio** (observaciones que ninguna herramienta confirmó). Úsalo antes de dar una pieza por terminada o de exportarla. Para los planos, el revisor es el `fabricator` (sección 7).

---

## 10. Memoria: decisiones y correcciones

La memoria guarda lo que tú sabes y el agente no: tus procesos, materiales, reglas de la casa y correcciones. Cada entrada es un archivo Markdown con un encabezado YAML. Puedes editarla a mano.

### Dos ámbitos

| Ámbito | Dónde | Uso |
|---|---|---|
| `project` (por defecto) | `memory/` en tu espacio de diseño | Lo que aplica a este proyecto. |
| `user` | `~/.cotaforge/memory` (o `COTAFORGE_HOME/memory`) | Lo que aplica a todos tus diseños. |

Si el mismo `id` existe en ambos, gana el del proyecto. Cada carpeta tiene un `INDEX.md` generado: no lo edites. Los archivos se leen en cada llamada, así que un cambio manual se ve al instante; un archivo mal formado se omite y se informa.

### Tipos de entrada

`practice`, `principle`, `process`, `material`, `correction`, `cad_trap` (una trampa conocida del software de CAD) y `case` (un diseño real y lo que enseña). Todas llevan `title`, `applies_when` y `not_when` (obligatorios). Solo las entradas `process` y `material` las comprueba el código; las demás son texto que el agente carga cuando aplica.

### Cómo le llega al agente

Al arrancar, el servidor pone el **índice** de la memoria (ids de entradas, agrupados por ámbito y tipo) en las instrucciones del agente. Cuando una entrada aplica, el agente la carga con `memory` `get`. Las entradas son datos, no instrucciones: no pueden ampliar lo que el agente puede hacer. `memory` `index` siempre está al día.

### Quién decide qué se vuelve memoria

El agente propone; tú decides.

- `save` deja la entrada en `status: proposed`. Aparece en el índice como pendiente de aprobación y no se empuja ni se exige. Las declaraciones de `process` y `material` quedan activas al guardarse.
- Apruebas diciéndoselo al agente. Llama a `memory` `approve` (`by` tu nombre, `text` tus palabras). No debe aprobar por su cuenta.
- Todo lo que el agente guarda en `user` queda `proposed`, y el servidor lo obliga a hacerte una pregunta: ¿esto aplica a tus diseños futuros o solo a este proyecto? "Para mis diseños futuros" lo activa en `user` (`approve` con `scope=user`). "Solo este proyecto" lo mueve al proyecto y lo activa allí (`scope=project`).
- Un `principle` es general. `save` lo rechaza, y propone dividirlo, si contiene palabras de sector o proceso, marcas, códigos de pieza o medidas con unidades. Eso va en una `practice` enlazada mediante `related`. Un principio también necesita `recognize` y al menos un ejemplo.
- Una `practice` con una medida debe llevar `sources` o `provenance`; sin ellas, `save` la guarda con una advertencia.
- Antes de escribir, `save` busca entradas similares y no escribe hasta que el agente actualice una (mismo `id`) o confirme que es nueva.
- Un hecho de un solo diseño no es una regla. Va en el proyecto (una entrada `case` o `assumptions-and-decisions.md`). Si una entrada `user` cita solo casos de este proyecto, `save` avisa.

### Campos opcionales

Una entrada puede llevar: `recognize` (la situación tal como se ve en el modelo), `examples` (`{sector, text, validated}`), `typical_error`, `cases`, `related`, `replaces` (el id de una entrada base que la tuya reemplaza), `sources`, `sector` y `when`. `when` fija cuándo se dispara la entrada: `roles`, `stages` (`concept`, `layout`, `detail`) y `ops`. Si das varios campos, todos deben coincidir.

### Sector, roles y cuándo se empuja la memoria

- **Sector y procesos.** `memory` `brief` los declara en `brief.yaml` de tu espacio de diseño. El mismo archivo guarda `drawing_review` (sección 7). Declara tu sector una vez: el agente carga entonces primero los ejemplos de tu sector y marca los de otros sectores como analogías. Sin sector declarado, las prácticas se empujan de todos modos, y los ejemplos de varios sectores vienen marcados como analogías. Una práctica de un sector distinto del tuyo ni se empuja ni se exige (se puede leer con `get`).
- **Tabla de sectores.** `sectors.yaml` del conjunto base asocia lo que escribes con un id de sector (los alias funcionan en inglés y en español). Amplíala con tu propio `sectors.yaml` en la carpeta `memory/` de tu proyecto o de usuario; gana el del proyecto.

```yaml
sectors:
  - id: stamping
    name: Stamping and dies
    aliases: [estampado, troqueles, prensas]
  - id: fixtures
    aliases: [my welding jigs]
```

- **Roles.** Cada cuerpo o componente puede tener un rol: `estructura`, `soporte`, `contacto`, `piloto`, `apoyo`, `calza`, `actuador`, `sensor`, `soldado`, `comercial`, `ajustable`, `fijacion`. El agente lo fija en el parámetro `role` de la operación o con `op` `edit`. Se guarda en el archivo y colorea el cuerpo (`view` `style` con `color="roles"`). Añade roles en `roles.yaml` en tu espacio de diseño: `roles: {soldado: "#a0522d", bastidor: "#336699"}`.
- **En el momento de decidir.** Si una operación coincide con el `when` de una entrada activa, su resultado lleva una línea con el id de la entrada. Es una señal para cargarla.
- **En cada hito.** Al terminar concepto, disposición o detalle, el agente llama a `memory` `review` con la etapa. El servidor lista las entradas disparadas y se niega hasta que cada una tenga una respuesta (`memory` `answer`): `applies: how, on which part` o `does not apply: why`. Acepta una respuesta solo si el agente leyó esa entrada (`get`) en esta sesión. Las respuestas se guardan en el estado del diseño.
- **Una alerta medida no se despacha con una frase.** Dos entradas se activan por una medición del modelo: esquinas interiores vivas en una cavidad fresada, y un rodamiento sobre un tramo de eje mucho más largo que él. Mientras la medición siga disparándose, `review` las lista como pendientes y ninguna respuesta del agente las cierra, tampoco `does not apply: why`. Se cierran de dos maneras: se corrige el modelo (el servidor vuelve a medir y la alerta desaparece), o tú ordenaste mantenerla y el agente registra tu nombre y tus palabras (`memory` `answer` con `by` y tus palabras, citadas, en el texto). La orden cubre lo medido en ese momento; una violación nueva vuelve a quedar abierta. Un plano de una pieza con una violación abierta no se exporta: la hoja muestra un requisito «Alertas de diseño medidas, resueltas» que ninguna respuesta en la hoja cierra.

### El conjunto base

El producto trae un conjunto base de solo lectura (ámbito `base`) de principios generales y prácticas por sector. Los principios son preguntas de calidad para planos terminados y revisión de diseño. Las prácticas llevan las cifras que un principio no puede, cada una con su fuente. Algunas cifras vienen de guías de fabricantes concretos, no de normas: confírmalas con el taller que fabricará la pieza. Las entradas que citan una fuente que no se leyó lo dicen.

La mayoría de las prácticas base se leen bajo demanda con `memory` `index` o `search`. Unas pocas se disparan con operaciones o roles concretos. `memory` `index` lista solo ids, y una práctica base solo si pertenece a tu sector declarado. `memory` `index` con `sector=<id>` lista las prácticas de otro sector.

Para desactivar una entrada en tu proyecto, usa `memory` `deactivate` con su id. Para cambiar una conservando su id, guarda una entrada con ese id. Si tu entrada tiene un id distinto pero dice lo mismo, declara `replaces: <base id>`. `COTAFORGE_BASE_MEMORY=0` deja fuera el conjunto base.

### Los comandos del plugin

| Comando | Qué hace |
|---|---|
| `/cotaforge:declare <texto>` | Declara materiales o procesos como restricción dura (sección 9). |
| `/cotaforge:fix <texto>` | Guarda tu corrección como entrada `correction`. Busca antes entradas similares, usa solo lo que dijiste, exige procedencia para los números, guarda en el proyecto y propone el ámbito `user` (una pregunta) si la lección no depende de este proyecto. |
| `/cotaforge:plan <problema>` | Elige contigo el flujo de diseño (sección 7). |
| `/cotaforge:resume <archivo>` | Retoma un `.FCStd` y resume su estado (sección 8). |
| `/cotaforge:review <foco>` | Revisión independiente (sección 9). |
| `/cotaforge:friction <texto>` | Escribe una nota en `friction.md` de tu proyecto. |

Ejemplo:

> `/cotaforge:fix` El chaflán de un agujero roscado se hace después de roscar, nunca antes. Aplica a todos mis diseños.

El agente guarda una `correction` en `user` como propuesta, pregunta una vez si aplica a todos tus diseños y, con tu sí, la activa.

### Desactivar

`memory` `deactivate` marca una entrada como inactiva: el archivo se queda, pero deja de restringir y de aparecer en el índice. En el ámbito `user` se apaga para todos tus diseños. Para apagar una entrada de usuario en un proyecto, guarda una entrada inactiva con el mismo `id` en ese proyecto.

---

## 11. `run_native`, la salida de emergencia

Las operaciones de `op` están verificadas y son editables, pero no cubren todo. `run_native` ejecuta un fragmento de Python de FreeCAD como un paso deshacible. El agente debe leer antes `cad_guide` con `topic="native"` y dar el motivo en `intent`. El fragmento tiene disponibles `doc`, `App`/`FreeCAD`, `Part`, `Base`, `Vector`, `Placement`, `Rotation`, `Sketcher`, `PartDesign` y `math`. Todo lo demás se importa. No hay interfaz gráfica.

```
ok: thick native
modified: Body (PartDesign::Body), Pad (PartDesign::Pad)
volume change: +36000 mm3
bbox: size (60, 40, 25) mm, min (0, 0, 0), max (60, 40, 25)
```

`undo` lo deshace como cualquier paso.

### Deshacer automático ante fallos

El paso se revierte (`state: unchanged`) cuando el código lanza una excepción, supera `timeout_s` (60 s por defecto, 600 s máximo) o deja un objeto roto: forma nula o inválida, booleana vacía, sólido de volumen cero, restricciones de croquis en conflicto, un rasgo construido sobre un croquis no totalmente definido por cotas, o un error de recálculo.

```
s3 native was not applied
error: BrokenResult: the step left 1 object(s) broken: C: boolean produced an empty result
state: unchanged
```

Un fragmento que no cambió nada no se registra. Los objetos que crea un fragmento no son operaciones: las operaciones no pueden construir sobre ellos por referencia de paso.

### Los croquis detrás de un rasgo quedan totalmente definidos

Un rasgo se construye solo sobre un croquis **totalmente definido por cotas**. El croquis de `op` siempre lo está. Un croquis dibujado a mano o por `run_native` puede tener libertad.

- Un paso de `run_native` que construye un rasgo sobre un croquis así se revierte. La respuesta dice cuántos grados de libertad quedan y qué geometría está libre. Acota el croquis antes de crear el rasgo. Un croquis a medias sin rasgo encima se acepta.
- Una restricción `Block` no cuenta como definición.
- Un archivo que abres conserva sus croquis tal cual. `check` lista los croquis bajo un rasgo que no están totalmente definidos. Cambiar una propiedad de un rasgo existente está permitido. Construir un rasgo nuevo sobre un croquis así, o dejarlo con libertad, se rechaza hasta que se acote.
- Un croquis que ningún rasgo usa puede tener libertad.

### Por qué a veces te pregunta: el hook

Un hook de Claude Code es un programa que se ejecuta antes de una herramienta. `cotaforge init` registra uno para `mcp__cad__run_native` que escanea primero el código. Si encuentra algo, Claude Code te pregunta y muestra los motivos:

```
run_native: the fragment does something worth a look (advisory scan, not a sandbox)
line 1: imports 'os' (reaches the file system and processes)
line 2: calls os.system(...) (runs programs or removes/renames files)
```

Marca importar `os`, `subprocess`, `shutil`, `socket`, `urllib`, `http`, `requests`, `ctypes` o `importlib`; `eval`/`exec`/`compile` de texto no literal; `__import__`; y escrituras de archivos cuyo destino no está demostrablemente dentro de tu espacio de diseño. Leer archivos no se marca. El código inofensivo pasa sin preguntar.

**Es una heurística, no un entorno aislado.** Lee el texto del código; lo que Python hace de forma dinámica se le escapa. Un escaneo limpio **no** significa que el código sea seguro. El hook solo convierte los hallazgos en una pregunta. Nunca bloquea ni reemplaza tus reglas de permisos. Si apruebas, el código se ejecuta en el proceso de FreeCAD con los permisos de tu usuario. Si el hook mismo falla, no imprime nada y no bloquea el modelado.

### Scripts de TechDraw

`makeCosmeticLine` y `formatGeometricEdge` cerraron FreeCAD en uso real (código de salida `3221226356 = 0xC0000374`) y se perdió trabajo sin guardar. El cierre no se pudo reproducir, así que la causa se desconoce, y la advertencia de `run_native` lo dice. Estas llamadas no están prohibidas: un script que usa TechDraw recibe antes una copia de recuperación (sección 8). Para líneas de centro y discontinuas, usa en su lugar el elemento `line` de `drawing`.

### Registro de uso

Cada llamada a `run_native` se anota en `native-usage.jsonl` en tu espacio de diseño. El código en sí no se guarda.

---

## 12. Límites

- **Un solo software de CAD: FreeCAD 1.1.x.** El adaptador `fake` es un simulador de pruebas.
- **Solo desde Claude Code** (terminal o pestaña Code). No hay interfaz propia.
- **Sin ventana no hay captura ni selección 3D.** `view` `capture`, `set_view`, `style`, `section` y `selection` necesitan `--visible` y una pantalla. `drawing` `capture` funciona sin ventana.
- **Operaciones que no existen:** loft y corte de revolución (revoluciona un cuerpo y réstalo con `boolean_subtract`). Los croquis admiten rectángulo, cuadrado, círculo, `slot`, y líneas y arcos encadenados en perfiles cerrados (`arc3`, `arc_ends`, `arc`).
- **Planos:** vistas, cotas, llamadas de agujeros, cotas de posición, notas, revisiones y cuadro de rotulación; ajustes ISO 286 y tolerancias generales ISO 2768 (tablas transcritas, no verificadas), tolerancias geométricas ISO 1101, referencias, acabado superficial y roscas ISO 6410. **No se admiten:** soldadura, vistas interrumpidas, cortes parciales, líneas de doblez en chapa. El texto del cuadro de rotulación es en español por defecto.
- **Sin cálculo de esfuerzos.**
- **La chapa metálica** existe solo con el complemento SheetMetal y con los límites de [su sección](#chapa-metálica-opcional).
- **`check`** cubre material, ruta y envolvente (sección 9).
- **Ensambles:** sin subensambles (solo se insertan piezas). Las relaciones entre caras mueven la instancia sin dejar una relación viva en FreeCAD. Las juntas son `fixed`, `revolute` y `slider`; el valor lo fija CotaForge, no el solucionador de FreeCAD, y una instancia que cuelga de otra en movimiento no la sigue. El ensamble muestra solo lo que está guardado de cada pieza.
- **Los rasgos de un archivo abierto no se pueden deshacer.** Se adoptan como cuerpos y construyes sobre ellos.
- **Los resultados de las herramientas están en inglés.** El agente te los explica en tu idioma.
- **`run_native` no es un entorno aislado** (sección 11).
- **El juicio del agente no está verificado.** Los números vienen del software de CAD; que una pieza sea buena idea, no.
- **Las entradas de memoria son tus datos, no la verdad.** Una entrada mala se toma como dato.

---

## 13. Glosario

Términos del propio producto.

| Término | Significado |
|---|---|
| **Espacio de diseño** | La carpeta creada por `cotaforge init`. |
| **Operación** | Un paso de modelado. Cada llamada a `op` es una. |
| **Plan** | La lista de operaciones con sus parámetros (`inspect` `tree`). |
| **Intención** | La línea con el "porqué" guardada con cada paso. |
| **Adaptador** | El motor de CAD: `freecad`, o `fake` para pruebas. |
| **Declarado** | Material o proceso que registraste. Una declaración es una restricción dura. |
| **Mundo cerrado** | Si declaras algo de un tipo, solo lo declarado es válido. |
| **Ruta de procesos** | La lista ordenada de procesos asignados a una pieza. |
| **Procedencia** | El origen de un número: fuente, referencia, autor, fecha. |
| **Registro de sesión** (*sidecar*) | `<name>.cotaforge.json` junto al `.FCStd`. |
| **Deriva** | Diferencia entre lo que CotaForge guardó y lo que hay ahora en el archivo. |
| **Adoptar** | Abrir un archivo sin su historial y construir sobre sus cuerpos. |
| **Ámbito** | Dónde vive una entrada de memoria: `project`, `user` o `base`. |
| **Propuesta** (*proposed*) | Una entrada de memoria que espera tu aprobación. |
| **Fabricator** | El agente que revisa un plano como quien fabrica la pieza. |
| **Drafter** | El agente que hace planos en su propia sesión. |
| **Hoja** | Un plano. Una hoja se exporta a un archivo. |
| **Nativo** | Un objeto de TechDraw que puedes editar en FreeCAD, a diferencia de un símbolo dibujado. |
| **Hook** | Un programa de Claude Code que se ejecuta antes de una herramienta. |
| **`run_native`** | La salida de emergencia: un fragmento de Python de FreeCAD como un paso deshacible. |

---

## 14. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `init` avisa "FreeCAD not found", o `the CAD software did not start: ...`, o `FreeCAD root not found` | CotaForge no encuentra la raíz de FreeCAD. Pasa la carpeta con `bin\FreeCADCmd.exe` (Windows) o `usr/bin/freecadcmd` (Linux) mediante `--freecad-root`, o define `COTAFORGE_FREECAD_ROOT`. Ejecuta `cotaforge init` de nuevo y reinicia Claude Code. |
| Windows: `... is too long for FreeCAD on Windows: <path> (N characters; the limit is 209, ...)` | Windows limita las rutas a 260 caracteres y FreeCAD escribe archivos temporales junto al destino, así que CotaForge rechaza cualquier ruta de 210 caracteres o más. Usa un nombre de carpeta o de archivo más corto. Si la advertencia nombra la carpeta de FreeCAD, el script del worker o el registro del worker, el clon o la carpeta temporal están en una ruta demasiado larga: clona en una ruta corta como `C:\dev\cotaforge`, recrea allí el entorno virtual y ejecuta `cotaforge init` de nuevo. Activar las rutas largas de Windows no está verificado como solución. |
| Windows: `the CAD software did not start: worker failed to start: worker exited (code 0) while waiting for hello ...` | Visto con un clon en una ruta de unos 270 caracteres. Las versiones actuales muestran en su lugar la advertencia de ruta demasiado larga. Si aún lo ves, revisa el registro del worker (carpeta temporal, dentro de `cotaforge`) y clona en una ruta corta. |
| El servidor `cad` no aparece en `/mcp` | Abriste Claude Code en otra carpeta: debe ser el espacio de diseño (donde está `.mcp.json`). O el entorno virtual se movió o se borró: ejecuta `cotaforge init` de nuevo (instalador: **Abrir proyecto existente (CotaForge)**). Si Claude Code pide aprobar el servidor, apruébalo. |
| Windows dice "Windows protegió su PC" | El instalador no está firmado con un certificado de pago. Haz clic en **Más información** y luego en **Ejecutar de todas formas**. Hazlo solo con un instalador de una fuente de confianza. |
| `/cotaforge:` no aparece, o el plugin falta en la pestaña Code | El plugin no está registrado. Ejecuta `cotaforge plugin status` (instalador: `python.exe -m cotaforge_mcp.cli plugin status`, sección 4.1). Si dice "NOT registered", ejecuta `plugin register` o usa cualquier acceso directo de CotaForge. Si no encuentra Claude Code, instálalo e inicia sesión. Reinicia después la sesión de Claude Code. |
| Los accesos directos dicen "I could not find Claude Code" | Claude Code no está instalado, o está en un lugar que ni el `PATH` ni `%USERPROFILE%\.local\bin` cubren. Instálalo (<https://claude.com/product/claude-code>) y abre el acceso directo otra vez. |
| Tras actualizar, un proyecto antiguo no conecta | Ábrelo con **Abrir proyecto existente (CotaForge)**. Refresca `.mcp.json` y el hook. Tu memoria y tu `CLAUDE.md` no se tocan. |
| `no document is open` | Pide al agente que cree (`doc` `new`) o abra uno. |
| `a document named '...' is already open` | Usa otro nombre o `doc` `switch`. |
| `... was not applied`, `state: unchanged` | Normal: la operación no se pudo aplicar y no cambió nada. El agente lee el motivo y lo corrige. |
| `export_blocked` | La pieza viola lo que declaraste. Ejecuta `check`, corrige la pieza o cambia la declaración (`/cotaforge:declare`). |
| `outside_declared` | Pediste un material o proceso que no declaraste (mundo cerrado). Usa uno declarado o decláralo antes. |
| `file_exists` o `file_conflict` al guardar | El nombre existe o el archivo cambió fuera. Guarda con otro nombre. |
| `file_exists` al exportar | El archivo existe y por defecto no se sobrescribe. Usa otro nombre u `overwrite=true`. |
| Faltan `base_flange`, `edge_flange` y `unfold`, o `the SheetMetal add-on is not installed` | Instala el complemento desde el Administrador de complementos y reinicia FreeCAD y Claude Code (sección 7). Si dice `cannot be loaded`, actualízalo o reinstálalo. |
| Un `unfold` se deshace con un `flat_length_mm` distinto | El patrón plano que midió FreeCAD no coincide con la fórmula de doblez. Revisa el `k_factor`, el radio y el ángulo. Un patrón plano que no cuadra no se entrega. |
| `degraded_session` | La sesión de CAD es inutilizable. Guarda si puedes, cierra el documento y, si persiste, reinicia Claude Code. |
| `session_lost` | FreeCAD se cerró durante la llamada. La respuesta dice qué se perdió y dónde está la copia de recuperación (sección 8). Repite cualquier llamada: relanza FreeCAD y reabre el último guardado. |
| `cad_busy` | FreeCAD sigue en una llamada anterior (normalmente una que cancelaste). Espera, o cierra FreeCAD: la siguiente llamada lo relanza y reabre el último guardado (el trabajo sin guardar se pierde). |
| `run_native` no pregunta | Es lo esperado con código inofensivo. Si esperabas una pregunta, comprueba que `.claude/settings.local.json` aún tenga el hook (ejecuta `init` de nuevo si no). El escaneo es una heurística (sección 11). |
| El hook falla porque no encuentra Python | El entorno con el que se ejecutó `init` ya no existe. Ejecuta `init` de nuevo (instalador: **Abrir proyecto existente (CotaForge)**). En Windows los hooks se ejecutan con Git Bash, así que se requiere Git para Windows. Si la ruta de instalación tiene espacios, `init` escribe su forma corta 8.3 cuando existe. |
| "Not logged in" | Viene de Claude Code. Inicia sesión con `claude` y `/login`. |
| Mis instrucciones globales (`~/.claude/CLAUDE.md`) no se aplican en el espacio de diseño | Es intencional: `init` excluye los `CLAUDE.md` heredados. Quita la exclusión de `claudeMdExcludes` en `.claude/settings.local.json` si las quieres. |
| La ventana de FreeCAD no aparece | Aparece solo con `--visible` (ejecuta `init` de nuevo con la opción) y necesita una pantalla. La primera conexión puede tardar unos segundos. |
| Un archivo de memoria "could not be loaded" | `memory` `index` lista los archivos con problemas y los omite. Corrige el YAML o borra el archivo. |

---

## 15. Cómo dar feedback

Abre un issue: <https://github.com/Jomruizgo/cotaforge/issues/new/choose>.

Adjunta:

- Las versiones de CotaForge (`cotaforge version`), de Claude Code y de FreeCAD.
- Lo que le pediste al agente.
- El error exacto o la respuesta de la herramienta.

Nunca adjuntes un archivo de un cliente.

---

## 16. Hoja de referencia rápida

### Comandos de terminal

| Quiero... | Comando |
|---|---|
| Instalar (Windows) | Ejecutar `cotaforge-<version>-setup.exe` (sección 4.1) |
| Instalar (clon) | `python -m pip install -e kernel -e mcp-server` |
| Registrar, quitar o comprobar el plugin | `cotaforge plugin register` / `unregister` / `status` |
| Ver las versiones instaladas | `cotaforge version` |
| Crear un espacio de diseño | `cotaforge init <carpeta>` (Windows: el acceso directo "Crear proyecto nuevo (CotaForge)") |
| ...con FreeCAD en otra ruta | `cotaforge init <carpeta> --freecad-root <ruta>` |
| ...con una ventana de FreeCAD | `cotaforge init <carpeta> --visible` |
| ...sin ventana | `cotaforge init <carpeta> --no-visible` |
| Reemplazar `CLAUDE.md` | `cotaforge init <carpeta> --force` |
| Abrir Claude Code con el plugin registrado | App de escritorio: pestaña Code, elige la carpeta. O `cd <carpeta>` y `claude` |
| ...sin registrar el plugin | `cd <carpeta>` y `claude --plugin-dir <clon>/plugin` |

### Variables de entorno

| Variable | Efecto |
|---|---|
| `COTAFORGE_FREECAD_ROOT` | Raíz de FreeCAD. |
| `COTAFORGE_VISIBLE` | `1` abre FreeCAD con ventana. |
| `COTAFORGE_WORKSPACE` | Carpeta del espacio de diseño (la escribe `init`). |
| `COTAFORGE_ADAPTER` | `freecad` (por defecto) o `fake`. |
| `COTAFORGE_HOME` | Reemplaza `~/.cotaforge` (memoria de usuario, tu catálogo, índice de la biblioteca). |
| `COTAFORGE_LIBRARY` | La carpeta de tu biblioteca técnica, en vez de `~/.cotaforge/library`. |
| `COTAFORGE_BASE_MEMORY` | `0` deja fuera el conjunto base. |
| `COTAFORGE_FREECAD_MOD_DIRS` | Carpetas `Mod` donde buscar el complemento SheetMetal. |
| `COTAFORGE_IMPORT_STALL_S` | Segundos que una importación puede pasar sin que FreeCAD use CPU antes de cortarse (120 por defecto). |
| `COTAFORGE_IMPORT_MAX_S` | Tope total de una importación, en segundos (3600 por defecto). |

### Comandos dentro de Claude Code

| Comando | Para qué sirve |
|---|---|
| `/mcp` | Comprobar que el servidor `cad` está conectado. |
| `/cotaforge:declare <texto>` | Declarar materiales o procesos. |
| `/cotaforge:plan <problema>` | Elegir el flujo de diseño. |
| `/cotaforge:resume <archivo>` | Retomar un `.FCStd`. |
| `/cotaforge:review <foco>` | Revisión independiente. |
| `/cotaforge:fix <texto>` | Guardar una corrección en la memoria. |
| `/cotaforge:friction <texto>` | Escribe una nota en `friction.md` de tu proyecto. |

### Frases que funcionan

| Quiero... | Dile |
|---|---|
| Una pieza declarativa | "Placa de 60 x 40 x 10 en aluminio 6061, fresadora de 3 ejes, agujero pasante Ø8 centrado en la cara superior. Verifica con `check`." |
| Ver el plan | "Muéstrame el árbol de operaciones." |
| Medir | "Dame el volumen y la caja envolvente." |
| Deshacer | "Deshaz el último paso." / "Vuelve al paso `s2`." |
| Guardar | "Guarda como `plate.FCStd`." |
| Señalar una cara (con `--visible`) | Selecciónala en FreeCAD y di "agujero Ø6 en la cara que seleccioné". |
| Señalar una cota, vista o agujero del plano (con `--visible`) | Selecciónalo en la ventana de la hoja y di "¿qué mide esta cota?". Para hallar las cotas de una cara: selecciónala en 3D y pregunta "¿qué cotas se refieren a esta cara?". |
| Que el agente te muestre cuál (con `--visible`) | "Muéstrame la cota d3." |
| Exportar | "Exporta a STEP." |
| Un plano | "Arma el plano en A3 a 1:2 con el título Soporte, acota los agujeros y su posición desde la esquina, y exporta a PDF y DXF." |
| Ver el interior (con `--visible`) | "Pon la caja al 70% de transparencia." / "Corta un plano en z = 10." |
| Una revisión | `/cotaforge:review` |

Las herramientas del servidor `cad` y sus acciones están en la [sección 7](#7-cómo-trabaja-el-agente).
