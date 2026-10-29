# bcrp-mcp · Un MCP con las series oficiales del BCRP

Servidor [MCP](https://modelcontextprotocol.io) hecho con [FastMCP 4](https://gofastmcp.com) que le da a Claude acceso
a las 17,167 series estadísticas del Banco Central de Reserva del Perú (BCRP) y, para el contexto regional, a los
indicadores del Banco Mundial. Las cuentas las hace Python, no el modelo: cada cifra sale de una llamada que se puede
repetir y viene con la URL de su fuente.

Proyecto hecho en el diplomado de QLab de la PUCP, como tarea de la sesión 04 del curso
[Data Science con Python](https://github.com/d2cml-ai/Data-Science-Python/tree/main/sessions/04-mcp-claude-code)
(el enunciado original está en [`ENUNCIADO.md`](ENUNCIADO.md)). Los resultados están en [`informe.md`](informe.md).

![Aciertos por modo y crecimiento del PBI regional](grafico.png)

## Qué expone

| Tipo | Nombre | Qué hace |
|---|---|---|
| Herramienta | `buscar_series(texto, frecuencia, limite)` | Busca códigos de serie por palabras en el catálogo del BCRP. Primero muestra las series al día y al final las viejas o descontinuadas |
| Herramienta | `serie(codigo, desde, hasta)` | Descarga los datos de una serie (diaria, mensual, trimestral o anual) con la URL de la fuente. Avisa si la serie no llega al periodo pedido |
| Herramienta | `resumen(codigo, desde, hasta)` | Primero, último, cambio, variación %, promedio, máximo y mínimo, para que el modelo no calcule a mano |
| Herramienta | `transformar(codigo, operacion, desde, hasta, base)` | Variación interanual, respecto al periodo anterior o acumulada en el año, promedio anual, índice base 100 y valores reales (deflactados con el IPC de Lima) |
| Herramienta | `grafico(codigos, desde, hasta, transformacion, base, titulo, sombrear)` | Gráfico de hasta 5 series con estilo de publicación, guardado en `salidas/` |
| Herramienta | `exportar(codigos, desde, hasta, transformacion, base)` | Excel con los datos alineados por periodo y una hoja de metadatos, en `salidas/` |
| Herramienta | `comparar_paises(indicador, paises, desde, hasta)` | **Herramienta propia.** Compara al Perú con otros países en PBI, inflación, desempleo, PBI per cápita, exportaciones o deuda, con la API del Banco Mundial |
| Recurso | `bcrp://serie/{codigo}` | La ficha de una serie: nombre, categoría, frecuencia, inicio y última actualización |
| Prompt | `informe(tema)` | Pide un informe breve que cite el código y el periodo de cada cifra |
| Prompt | `coyuntura(periodo)` | Nota de coyuntura macro para economistas: inflación, tasa de referencia, PBI, tipo de cambio, términos de intercambio, reservas y crédito, con gráfico |

Ninguna de las dos APIs pide clave.

### Para economistas

- **Las transformaciones coinciden con las cifras del BCRP.** `transformar("PN38705PM", "var_interanual", ...)` da
  la misma inflación a 12 meses que publica el BCRP (PN01273PM), y `var_acumulada` da la misma variación acumulada
  (PN01272PM). Las pruebas lo verifican.
- **`real`** deflacta con el IPC de Lima (PN38705PM, dic. 2021 = 100) y expresa los valores a precios del periodo
  `base`. Si `base` es un año, usa el promedio de ese año. En series trimestrales o anuales, el deflactor es el
  promedio de los meses, y solo se usa si los meses están completos.
- **En series diarias**, como el tipo de cambio, las variaciones interanuales y acumuladas y los valores reales se
  calculan sobre el promedio de cada mes.
- **Avisos.** La herramienta avisa si se transforma una serie que ya es una tasa (por ejemplo, la variación
  interanual de una var%), si el último año de un promedio anual está incompleto, si se deflacta un monto en dólares
  con el IPC peruano o si la serie está descontinuada.
- **Gráficos.** Nunca usan dos ejes Y: las series con distinta unidad van en paneles separados y las tasas comparten
  panel. El último dato de cada serie se rotula al final de su línea. El pie cita los códigos y la fecha.

![Nota de coyuntura: inflación, expectativas y tasa de referencia](ejemplos/coyuntura.png)

## Instalación

Necesitas [`uv`](https://docs.astral.sh/uv/) y [Claude Code](https://claude.com/claude-code).

```bash
git clone https://github.com/LuisGuevaraVega/bcrp-mcp && cd bcrp-mcp
uv run pytest -q        # 20 passed; la primera vez baja el catálogo del BCRP (~7 MB) a datos/
claude                  # aprueba los servidores bcrp y fetch de .mcp.json
```

Para usarlo en otro proyecto: `claude mcp add bcrp -- uv run --directory /ruta/a/bcrp-mcp server.py`.

## Manual de uso en Claude

No hace falta saber los códigos ni el nombre de las herramientas: pregunta como le preguntarías a un analista.
Claude busca la serie en el catálogo, trae el dato y te dice de dónde salió. Si quieres comprobar qué hizo, abre el
detalle de las llamadas o pídele *«muéstrame el código de serie y la URL»*.

### 1. Preguntar por un dato

> ¿Cuál fue la inflación de Lima a 12 meses en setiembre de 2026?

Claude busca la serie con `buscar_series`, consulta el dato con `serie` y responde con el valor, el código de la serie
(PN01273PM) y el periodo. Para que acierte a la primera:

- **Di el periodo exacto** («setiembre de 2026», «el promedio de 2025»). Si no dices el periodo, Claude usa el último
  dato publicado.
- **Di la medida que quieres**: nivel, variación mensual, variación a 12 meses, promedio o fin de periodo. El BCRP
  tiene muchas series con nombres parecidos.
- **Si ya sabes el código, dáselo**: *«usa PN01207PM»*.

### 2. Pedir cálculos

Claude no hace las cuentas a mano: se las pide a las herramientas, que las calculan en Python.

| Si quieres… | Pídelo así | Herramienta |
|---|---|---|
| Cambio, promedio, máximo o mínimo de un periodo | *«¿Cuánto subió el tipo de cambio en 2026 y cuál fue su máximo?»* | `resumen` |
| Variación interanual o acumulada | *«Variación interanual del precio del cobre, mes a mes, en 2026»* | `transformar` |
| Promedio anual | *«Promedio anual del tipo de cambio de 2020 a 2025»* | `transformar` |
| Índice base 100 | *«Pon el oro y el cobre en índice, base enero de 2025 = 100»* | `transformar` |
| Valores reales | *«El crédito en soles a precios de 2025»* | `transformar` (deflacta con el IPC de Lima) |
| Comparar al Perú con otros países | *«¿Creció el Perú más que Chile y Colombia desde 2015?»* | `comparar_paises` (Banco Mundial, anual) |

### 3. Hacer gráficos

> Grafica la inflación, la subyacente y la tasa de referencia desde 2023, y sombrea 2025.

El PNG se guarda en `salidas/`. Claude te da la ruta del archivo y, como también recibe la imagen, puede comentarla.
Puedes pedir:

- **Hasta 5 series por gráfico.** Si tienen unidades distintas, como dólares y soles, cada una va en su propio panel.
- **Una transformación**, por ejemplo *«en variación interanual»* o *«en índice base 100»*. Sirve para comparar
  series de escalas distintas en un solo panel.
- **Un título propio y periodos sombreados**, como una recesión o un ciclo de alzas de la tasa.

### 4. Llevar los datos a Excel

> Pásame a Excel el tipo de cambio, las reservas y la tasa de referencia de 2025 a hoy.

El `.xlsx` se guarda en `salidas/`. Tiene una hoja con las series alineadas por periodo (las de distinta frecuencia
van en hojas separadas) y una hoja de metadatos con el nombre, la unidad, la transformación y la URL de cada serie.
También puedes pedirlo transformado: *«…en variación interanual»*.

### 5. Atajos: `@` para fichas y `/` para informes

- `@bcrp:bcrp://serie/PN01273PM ¿desde cuándo hay datos de esta serie?` adjunta la ficha de la serie: nombre,
  frecuencia, inicio y última actualización.
- `/mcp__bcrp__informe "precio de la plata"` escribe un informe breve con cifras y fuentes. **Pon el tema entre
  comillas** si tiene varias palabras: Claude Code separa los argumentos por espacios.
- `/mcp__bcrp__coyuntura 2026-09` escribe una nota de coyuntura macro con inflación, tasa de referencia, PBI, tipo de
  cambio, términos de intercambio, reservas y crédito, más un gráfico.

### 6. Lee los avisos

Las herramientas agregan un `aviso` cuando algo merece cuidado, y Claude debería mencionarlo:

- **«El último dato es de…»**: la serie no llega al periodo pedido. Puede ser el rezago normal de publicación (el
  IPC sale a inicios del mes siguiente) o una serie que ya no se actualiza: pide otra.
- **«La serie está descontinuada»**: pide su reemplazo.
- **«La serie ya es una tasa»**: calcular la variación interanual de una variación % rara vez tiene sentido. Pide
  la serie en niveles.
- **«Su promedio es parcial»**: el último año todavía no termina.

## Archivos

| Archivo | Qué es |
|---|---|
| `server.py` | El servidor MCP |
| `test_server.py` | Las 8 pruebas del curso, sin cambios |
| `test_extras.py` | 12 pruebas de `transformar`, `grafico`, `exportar`, los avisos y el prompt de coyuntura |
| `ejemplos/` | Un gráfico de ejemplo hecho con `grafico` |
| `salidas/` | Donde `grafico` y `exportar` guardan sus archivos (no se sube) |
| `.mcp.json` | Los servidores `bcrp` y `fetch` para Claude Code |
| `preguntas.csv` | 12 preguntas con la cifra oficial (8 del curso y 4 propias) |
| `evaluar.py` | Hace cada pregunta a Claude (`claude -p`) sin herramientas, con búsqueda web y con el MCP, y califica |
| `resultados.csv`, `resultados_base.csv` | La corrida con web y la corrida base (memoria contra MCP) |
| `graficar.py` | Genera `grafico.png` |
| `informe.md` | El informe de la evaluación |

## Licencia

[MIT](LICENSE). Los datos son del [BCRP](https://estadisticas.bcrp.gob.pe) y del
[Banco Mundial](https://data.worldbank.org).
