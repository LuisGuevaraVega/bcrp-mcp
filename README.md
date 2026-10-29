# bcrp-mcp · Un MCP con las series oficiales del BCRP

Servidor [MCP](https://modelcontextprotocol.io) hecho con [FastMCP 4](https://gofastmcp.com) que le da a Claude acceso
a las 17,167 series estadísticas del Banco Central de Reserva del Perú (BCRP) y, para el contexto regional, a los
indicadores del Banco Mundial. Las cuentas las hace Python, no el modelo: cada cifra sale de una llamada que se puede
repetir y viene con la URL de su fuente.

Tarea de la sesión 04 del curso [Data Science con Python](https://github.com/d2cml-ai/Data-Science-Python/tree/main/sessions/04-mcp-claude-code)
(el enunciado original está en [`ENUNCIADO.md`](ENUNCIADO.md)). Los resultados están en [`informe.md`](informe.md).

![Aciertos por modo y crecimiento del PBI regional](grafico.png)

## Qué expone

| Tipo | Nombre | Qué hace |
|---|---|---|
| Herramienta | `buscar_series(texto, frecuencia, limite)` | Busca códigos de serie por palabras en el catálogo del BCRP |
| Herramienta | `serie(codigo, desde, hasta)` | Descarga los datos de una serie (diaria, mensual, trimestral o anual) con la URL de la fuente |
| Herramienta | `resumen(codigo, desde, hasta)` | Primero, último, cambio, variación %, promedio, máximo y mínimo, para que el modelo no calcule a mano |
| Herramienta | `comparar_paises(indicador, paises, desde, hasta)` | **Herramienta propia.** Compara al Perú con otros países en PBI, inflación, desempleo, PBI per cápita, exportaciones o deuda, con la API del Banco Mundial |
| Recurso | `bcrp://serie/{codigo}` | La ficha de una serie: nombre, categoría, frecuencia, inicio y última actualización |
| Prompt | `informe(tema)` | Pide un informe breve que cite el código y el periodo de cada cifra |

Ninguna de las dos APIs pide clave.

## Uso

Necesitas [`uv`](https://docs.astral.sh/uv/) y [Claude Code](https://claude.com/claude-code).

```bash
git clone https://github.com/LuisGuevaraVega/bcrp-mcp && cd bcrp-mcp
uv run pytest -q        # 8 passed; la primera vez baja el catálogo del BCRP (~7 MB) a datos/
claude                  # aprueba los servidores bcrp y fetch de .mcp.json
```

En Claude Code:

- *«¿Cuál fue la inflación de Lima a 12 meses en setiembre de 2026?»*
- *«¿Creció el Perú más que Chile y Colombia desde 2015?»* (usa `comparar_paises`)
- `@bcrp:bcrp://serie/PN01273PM ¿desde cuándo hay datos de esta serie?`
- `/mcp__bcrp__informe exportaciones`

Para usarlo en otro proyecto: `claude mcp add bcrp -- uv run --directory /ruta/a/bcrp-mcp server.py`.

## Archivos

| Archivo | Qué es |
|---|---|
| `server.py` | El servidor MCP |
| `test_server.py` | Las 8 pruebas del curso, sin cambios |
| `.mcp.json` | Los servidores `bcrp` y `fetch` para Claude Code |
| `preguntas.csv` | 12 preguntas con la cifra oficial (8 del curso y 4 propias) |
| `evaluar.py` | Hace cada pregunta a Claude (`claude -p`) sin herramientas, con búsqueda web y con el MCP, y califica |
| `resultados.csv`, `resultados_base.csv` | La corrida con web y la corrida base (memoria contra MCP) |
| `graficar.py` | Genera `grafico.png` |
| `informe.md` | El informe de la evaluación |

## Licencia

[MIT](LICENSE). Los datos son del [BCRP](https://estadisticas.bcrp.gob.pe) y del
[Banco Mundial](https://data.worldbank.org).
