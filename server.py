"""Servidor MCP del BCRP. Empiecen aquí: buscar_series ya funciona; completen los TODO hasta que pasen las pruebas.

    uv run pytest                                           # el contrato: las 8 pruebas deben pasar
    uv run server.py                                        # el servidor, por stdio
    claude mcp add --scope project bcrp -- uv run server.py # conectarlo a Claude Code

Las cuentas las hace Python, no el modelo: así cada cifra sale de una llamada que se puede repetir.
"""
import re
import time
import unicodedata
from functools import cache
from pathlib import Path

import httpx
import pandas as pd
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

AQUI = Path(__file__).parent
CATALOGO = AQUI / "datos" / "catalogo_bcrp.csv"
URL_CATALOGO = "https://estadisticas.bcrp.gob.pe/estadisticas/series/metadata"
API = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"
SOLO_LECTURA = {"readOnlyHint": True, "openWorldHint": True}

mcp = FastMCP("bcrp", instructions=(
    "Series oficiales del Banco Central de Reserva del Perú (BCRP). Primero busca el código con buscar_series; "
    "luego usa resumen para variaciones, promedios y extremos, y serie para los datos. No calcules a mano: "
    "cita siempre el código de la serie y el periodo."))


def _sin_tildes(s):
    return unicodedata.normalize("NFD", str(s).lower()).encode("ascii", "ignore").decode()


@cache
def catalogo():
    """El catálogo oficial: 17 mil series en unos 7 MB. Se baja una vez y queda en datos/."""
    if not CATALOGO.exists():
        CATALOGO.parent.mkdir(exist_ok=True)
        r = httpx.get(URL_CATALOGO, timeout=120)
        r.raise_for_status()
        CATALOGO.write_text(r.content.decode("latin-1"), encoding="utf-8")
    df = pd.read_csv(CATALOGO, sep=";", dtype=str).fillna("")
    df = df.rename(columns={"Código de serie": "codigo", "Categoría de serie": "categoria", "Grupo de serie": "grupo",
                            "Nombre de serie": "nombre", "Frecuencia": "frecuencia",
                            "Fecha de actualización": "actualizada", "Fecha de inicio": "inicio"})
    df["texto"] = (df.categoria + " " + df.grupo + " " + df.nombre).map(_sin_tildes)
    return df


def _ficha(codigo):
    fila = catalogo().query("codigo == @codigo")
    if fila.empty:
        raise ToolError(f"No existe el código de serie {codigo!r}. Búsquenlo con buscar_series.")
    f = fila.iloc[0]
    return {"codigo": f.codigo, "nombre": f"{f.grupo} - {f.nombre}", "categoria": f.categoria,
            "frecuencia": f.frecuencia, "inicio": f.inicio, "actualizada": f.actualizada}


@mcp.tool(annotations=SOLO_LECTURA)
def buscar_series(texto: str, frecuencia: str | None = None, limite: int = 10) -> list[dict]:
    """Busca series del BCRP por palabras: todas deben aparecer en la categoría, el grupo o el nombre.

    Ejemplos: "ipc 12 meses", "tipo de cambio interbancario venta", "reservas internacionales netas",
    "tasa de referencia", "cobre precio". frecuencia: "Diaria", "Mensual", "Trimestral" o "Anual".
    Devuelve el código, el nombre completo, la frecuencia y desde cuándo hay datos. Si no hay resultados,
    prueben con menos palabras.
    """
    df = catalogo()
    m = pd.Series(True, index=df.index)
    for palabra in _sin_tildes(texto).split():
        m &= df.texto.str.contains(re.escape(palabra))
    if frecuencia:
        m &= df.frecuencia.str.lower() == frecuencia.lower()
    # primero las series vigentes y, entre ellas, las de nombre más corto (las más generales)
    sel = df[m]
    orden = sel.assign(vieja=sel.actualizada < "2025", largo=(sel.grupo + sel.nombre).str.len())
    return [_ficha(c) for c in orden.sort_values(["vieja", "largo"]).head(limite).codigo]


MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def _fecha_api(fecha, frecuencia, fin=False):
    """Pasa "2025", "2025-01", "2025-T1" o "2025-01-15" al formato que pide la API según la frecuencia."""
    partes = [int(p) for p in re.findall(r"\d+", str(fecha))]
    if not partes:
        raise ToolError(f"Fecha inválida {fecha!r}. Usen 2025, 2025-01 o 2025-01-15.")
    anio = partes[0]
    if frecuencia == "Anual":
        return f"{anio}"
    if frecuencia == "Trimestral":
        # "2025-T1" es el trimestre 1; "2025-05" (un mes) cae en el trimestre 2
        trim = partes[1] if "T" in str(fecha).upper() else (partes[1] - 1) // 3 + 1 if len(partes) > 1 else None
        return f"{anio}-{trim or (4 if fin else 1)}"
    mes = partes[1] if len(partes) > 1 else (12 if fin else 1)
    if frecuencia == "Mensual":
        return f"{anio}-{mes}"
    dia = partes[2] if len(partes) > 2 else (pd.Period(f"{anio}-{mes}", "M").days_in_month if fin else 1)
    return f"{anio}-{mes:02d}-{dia:02d}"


def _periodo(nombre):
    """Pasa el nombre que devuelve la API ("Ene.2025", "01.Oct.26", "T1.25", "2025") a "2025-01", "2026-10-01"..."""
    if m := re.fullmatch(r"(\d{2})\.(\w{3})\.(\d{2})", nombre):
        return f"20{m[3]}-{MESES[m[2].lower()]:02d}-{m[1]}"
    if m := re.fullmatch(r"(\w{3})\.(\d{4})", nombre):
        return f"{m[2]}-{MESES[m[1].lower()]:02d}"
    if m := re.fullmatch(r"T(\d)\.(\d{2})", nombre):
        return f"20{m[2]}-T{m[1]}"
    return nombre


@cache
def _descargar(url):
    """Pide una URL a la API; si responde HTML (límite de pedidos) espera y reintenta. Queda en caché."""
    for intento in range(4):
        try:
            r = httpx.get(url, timeout=60)
            return r.json()
        except (ValueError, httpx.HTTPError):
            time.sleep(2 * (intento + 1))
    raise ToolError(f"El BCRP no respondió con datos para {url}. Reintenten en unos segundos.")


@mcp.tool(annotations=SOLO_LECTURA)
def serie(codigo: str, desde: str, hasta: str) -> dict:
    """Descarga los datos de una serie del BCRP entre dos fechas, con la URL de la fuente.

    Usen primero buscar_series para conseguir el código. Fechas: "2025-01" para series mensuales,
    "2026-10-01" para diarias, "2025-T1" para trimestrales y "2025" para anuales (un año suelto también vale).
    Ejemplo: serie("PN01273PM", "2025-01", "2025-12"). Para variaciones o promedios usen resumen.
    """
    ficha = _ficha(codigo)
    fuente = f"{API}/{codigo}/json/{_fecha_api(desde, ficha['frecuencia'])}/{_fecha_api(hasta, ficha['frecuencia'], True)}"
    datos = [{"periodo": _periodo(p["name"]), "valor": float(p["values"][0])}
             for p in _descargar(fuente).get("periods", []) if p["values"][0] not in ("n.d.", "")]
    return {**ficha, "datos": datos, "fuente": fuente}


@mcp.tool(annotations=SOLO_LECTURA)
def resumen(codigo: str, desde: str, hasta: str) -> dict:
    """Resume una serie del BCRP en un periodo: primer y último valor, cambio, variación %, promedio, máximo y mínimo.

    Úsenla siempre que la pregunta pida una variación, un promedio o un extremo, en vez de calcular a mano.
    Mismas fechas que serie. Ejemplo: resumen("PD04638PD", "2026-01-01", "2026-10-15").
    """
    s = serie(codigo, desde, hasta)
    if not s["datos"]:
        raise ToolError(f"La serie {codigo} no tiene datos entre {desde} y {hasta}.")
    d = pd.DataFrame(s["datos"])
    primero, ultimo = s["datos"][0], s["datos"][-1]
    cambio = ultimo["valor"] - primero["valor"]
    return {"codigo": codigo, "nombre": s["nombre"], "frecuencia": s["frecuencia"], "observaciones": len(d),
            "primero": primero, "ultimo": ultimo, "cambio": round(cambio, 6),
            "variacion_pct": round(100 * cambio / primero["valor"], 4) if primero["valor"] else None,
            "promedio": round(d.valor.mean(), 6),
            "maximo": d.loc[d.valor.idxmax()].to_dict(), "minimo": d.loc[d.valor.idxmin()].to_dict(),
            "fuente": s["fuente"]}


BANCO_MUNDIAL = "https://api.worldbank.org/v2"
INDICADORES = {"pbi": "NY.GDP.MKTP.KD.ZG", "inflacion": "FP.CPI.TOTL.ZG", "desempleo": "SL.UEM.TOTL.ZS",
               "pbi_per_capita": "NY.GDP.PCAP.CD", "exportaciones": "NE.EXP.GNFS.ZS", "deuda": "GC.DOD.TOTL.GD.ZS"}


@mcp.tool(annotations=SOLO_LECTURA)
def comparar_paises(indicador: str = "pbi", paises: str = "PER,CHL,COL,MEX,BRA",
                    desde: int = 2015, hasta: int = 2025) -> dict:
    """Compara al Perú con otros países en un indicador anual del Banco Mundial (el BCRP solo tiene datos del Perú).

    Úsenla cuando la pregunta compare países o pida contexto regional: "¿creció el Perú más que Chile?",
    "inflación de la Alianza del Pacífico". indicador: "pbi" (crecimiento real %), "inflacion" (IPC %),
    "desempleo" (% de la fuerza laboral), "pbi_per_capita" (US$ corrientes), "exportaciones" (% del PBI),
    "deuda" (deuda del gobierno central, % del PBI), o un código del Banco Mundial como "NY.GDP.MKTP.KD.ZG".
    paises: códigos ISO3 separados por comas. Devuelve una tabla año por país, el promedio y el ranking del
    periodo, y la URL de la fuente.
    """
    codigo = INDICADORES.get(indicador.lower(), indicador)
    iso = ";".join(p.strip().upper() for p in paises.replace(";", ",").split(",") if p.strip())
    fuente = f"{BANCO_MUNDIAL}/country/{iso}/indicator/{codigo}?format=json&date={desde}:{hasta}&per_page=1000"
    r = httpx.get(fuente, timeout=60).json()
    if len(r) < 2 or not r[1]:
        raise ToolError(f"El Banco Mundial no tiene datos de {codigo!r} para {iso}. Revisen el indicador y los países.")
    df = pd.DataFrame([{"pais": x["country"]["value"], "anio": x["date"], "valor": x["value"]} for x in r[1]])
    tabla = df.pivot(index="anio", columns="pais", values="valor").sort_index()
    promedio = tabla.mean().sort_values(ascending=False).round(3)
    return {"indicador": r[1][0]["indicator"]["value"], "codigo": codigo,
            "tabla": {anio: {p: (None if pd.isna(v) else round(v, 3)) for p, v in fila.items()}
                      for anio, fila in tabla.iterrows()},
            "promedio_periodo": promedio.to_dict(), "ranking": list(promedio.index),
            "fuente": fuente.replace("&per_page=1000", "")}


@mcp.resource("bcrp://serie/{codigo}", mime_type="application/json")
def ficha_serie(codigo: str) -> dict:
    """Ficha de una serie del BCRP: nombre, categoría, frecuencia, desde cuándo hay datos y última actualización."""
    return _ficha(codigo)


@mcp.prompt
def informe(tema: str) -> str:
    """Pide un informe breve sobre un tema de la economía peruana con cifras oficiales del BCRP."""
    return (f"Escribe un informe breve sobre «{tema}» en el Perú con las series oficiales del BCRP.\n\n"
            "1. Usa buscar_series para encontrar las 2 o 3 series más relevantes.\n"
            "2. Usa resumen para el último año disponible: no calcules a mano.\n"
            "3. Si ayuda, usa comparar_paises para dar contexto regional.\n"
            "4. Cita el código de la serie y el periodo de cada cifra, por ejemplo (PN01273PM, 2026-09), y "
            "termina con la lista de fuentes (URL).\n"
            "Máximo 250 palabras, en español.")


if __name__ == "__main__":
    mcp.run()
