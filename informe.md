# Informe · ¿Cuántas cifras del BCRP acierta Claude sin su MCP y cuántas con él?

Evaluación con `evaluar.py`: 12 preguntas (las 8 del curso y 4 propias sobre oro, exportaciones, crédito y empleo),
hechas a Claude Haiku con `claude -p` el 28 de octubre de 2026. Para cada una, Claude debía responder un número o
`null` si no lo sabía con certeza.

## Resultados

**Corrida base** (`resultados_base.csv`, memoria contra MCP):

| Modo | Acierta | Se equivoca | No sabe | Segundos promedio |
|---|---:|---:|---:|---:|
| memoria | 0 | 0 | 12 | 5.2 |
| mcp | **12** | 0 | 0 | 14.2 |

**Experimento extra: contra la búsqueda web** (`resultados.csv`, `uv run evaluar.py --modos memoria web mcp`):

| Modo | Acierta | Se equivoca | No sabe | Segundos promedio |
|---|---:|---:|---:|---:|
| memoria | 0 | 0 | 12 | 5.5 |
| web | 8 | 0 | 4 | 22.0 |
| mcp | **11** | 0 | 1 | 15.4 |

![Aciertos por modo y crecimiento del PBI regional](grafico.png)

A la izquierda, el porcentaje de aciertos por modo en la corrida con web. A la derecha, la serie que devuelve la
herramienta propia `comparar_paises`.

## Qué falló cada modo y por qué

**Memoria (0 de 12).** Las 12 preguntas son de 2025 y 2026, después del corte de entrenamiento del modelo. Con la
instrucción «si no puedes saberlo con certeza, pon null» Claude dijo «no sé» en todas. No inventó ninguna cifra, pero
tampoco respondió ninguna.

**Web (8 de 12).** Ninguna respuesta fue un error: cuando no encontró el dato, dijo que no sabía.

- Acertó usando fuentes secundarias: noticias e informes del INEI (IPC, PBI) y las tablas HTML del BCRP cuando pudo
  leerlas (oro, exportaciones, reservas, tipo de cambio). En el PBI 2025 respondió 3.44 tomando la cifra de Forbes Perú
  y RPP. Entra en la tolerancia, pero no es el dato del BCRP.
- `referencia_set26`: la página de la tasa de referencia del BCRP devolvió una verificación anti-bots.
- `cobre_ago26`: no pudo abrir la serie mensual. La prensa solo tenía cotizaciones semanales y Claude, con razón, no las
  usó como promedio mensual.
- `empleo_set26` y `credito_set26`: el buscador le mostró versiones viejas de las series, con datos hasta mayo o junio,
  y no encontró el valor de setiembre.

**MCP (12 de 12 en la base, 11 de 12 con web).** Cada respuesta cita el código de la serie, el periodo y la URL de la
API. El único fallo fue `credito_set26` en la segunda corrida. `buscar_series` devolvió primero otras series de crédito
(PN00530MM y PN00520MM), que se actualizaron por última vez en junio de 2026, y Claude no llegó a PN00504MM, la que
usó en la corrida base para acertar 10.01. El problema está en la búsqueda, no en los datos: en el catálogo hay muchas
series con nombres parecidos, y `buscar_series` solo deja al final las que no se actualizan desde antes de 2025. Una
mejora sería ordenar por fecha de actualización reciente, o que `serie` avise cuando la serie no llega al periodo
pedido. También muestra que el resultado de Claude cambia de una corrida a otra, aun con las mismas herramientas.

## Dos cifras de mi MCP comprobadas a mano en la web del BCRP

| Pregunta | Mi MCP | Web del BCRP | Tabla |
|---|---:|---:|---|
| Oro LME, promedio de setiembre de 2026 (US$/oz troy) | 4,319.21 | 4,319 | [PN01654XM](https://estadisticas.bcrp.gob.pe/estadisticas/series/mensuales/resultados/PN01654XM/html/2026-8/2026-9/) |
| Exportaciones FOB, agosto de 2026 (millones de US$) | 10,815.4 | 10,815 | [PN38714BM](https://estadisticas.bcrp.gob.pe/estadisticas/series/mensuales/resultados/PN38714BM/html/2026-7/2026-8/) |

Las otras dos preguntas propias también se sacaron de la web del BCRP:
[PN00504MM](https://estadisticas.bcrp.gob.pe/estadisticas/series/mensuales/resultados/PN00504MM/html/2026-8/2026-9/)
(crédito, 10.0 %) y
[PN38051GM](https://estadisticas.bcrp.gob.pe/estadisticas/series/mensuales/resultados/PN38051GM/html/2026-8/2026-9/)
(PEA ocupada de Lima, 6,013 miles). La web redondea a los decimales de la serie, por eso las tolerancias de
`preguntas.csv` cubren ese redondeo.

## Herramienta propia: `comparar_paises`

El BCRP solo publica datos del Perú. `comparar_paises` agrega el contexto regional con la API del Banco Mundial, que
no pide clave: crecimiento del PBI, inflación, desempleo, PBI per cápita, exportaciones o deuda, en cualquier grupo de
países. Devuelve la tabla año por país, el promedio y el ranking del periodo, y la URL de la fuente. Por ejemplo, entre
2015 y 2025 el PBI del Perú creció en promedio 2.53 % al año, más que Chile (2.10 %) y casi igual que Colombia
(2.55 %), pero en 2023 fue el único de los cinco países con
recesión (-0.35 %).

## Conclusión

Sin herramientas, Claude no sirve para cifras recientes. Al menos reconoce que no sabe en vez de inventar. La búsqueda
web acierta dos de cada tres, pero depende de que la página se deje leer y de lo que publique la prensa, y es la más
lenta (22 s). El MCP acertó 23 de 24 respuestas entre las dos corridas, en 15 s, siempre con la fuente oficial. Vale
la pena construir un MCP cuando las mismas fuentes se consultan seguido, cuando hace falta la cifra exacta y su
procedencia, o cuando hay que calcular (variaciones, promedios). Para una pregunta suelta sobre un dato muy citado,
basta la búsqueda web. Aun con el MCP, la calidad de la búsqueda de series decide si Claude llega a la serie correcta.

## Nota técnica

En Windows, `evaluar.py` fallaba con `UnicodeDecodeError`: `subprocess` leía la salida de `claude` en cp1252. Se
corrigió agregando `encoding="utf-8"` a `subprocess.run`. `test_server.py` no se modificó.
