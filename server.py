"""Servidor MCP del BCRP. Empiecen aquí: buscar_series ya funciona; completen los TODO hasta que pasen las pruebas.

    uv run pytest                                           # el contrato: las 8 pruebas deben pasar
    uv run server.py                                        # el servidor, por stdio
    claude mcp add --scope project bcrp -- uv run server.py # conectarlo a Claude Code

Las cuentas las hace Python, no el modelo: así cada cifra sale de una llamada que se puede repetir.
"""
import json
import re
import time
import unicodedata
from datetime import date
from functools import cache
from pathlib import Path

import httpx
import pandas as pd
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Image
from matplotlib.dates import num2date
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from matplotlib.transforms import offset_copy
from openpyxl.styles import Alignment, Font

AQUI = Path(__file__).parent
CATALOGO = AQUI / "datos" / "catalogo_bcrp.csv"
SALIDAS = AQUI / "salidas"
URL_CATALOGO = "https://estadisticas.bcrp.gob.pe/estadisticas/series/metadata"
API = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"
SOLO_LECTURA = {"readOnlyHint": True, "openWorldHint": True}
GUARDA_ARCHIVO = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}

mcp = FastMCP("bcrp", instructions=(
    "Series oficiales del Banco Central de Reserva del Perú (BCRP). Primero busca el código con buscar_series; "
    "luego usa resumen para variaciones, promedios y extremos, transformar para variaciones interanuales, "
    "acumuladas, índices o valores reales, y serie para los datos. grafico guarda un PNG y exportar un Excel en "
    "salidas/. No calcules a mano: cita siempre el código de la serie y el periodo."))


def _sin_tildes(s):
    return unicodedata.normalize("NFD", str(s).lower()).encode("ascii", "ignore").decode()


@cache
def catalogo():
    """El catálogo oficial: 17 mil series en unos 7 MB. Queda en datos/ y se renueva si tiene más de una semana."""
    if not CATALOGO.exists() or time.time() - CATALOGO.stat().st_mtime > 7 * 86400:
        try:
            CATALOGO.parent.mkdir(exist_ok=True)
            r = httpx.get(URL_CATALOGO, timeout=120)
            r.raise_for_status()
            CATALOGO.write_text(r.content.decode("latin-1"), encoding="utf-8")
        except httpx.HTTPError:
            if not CATALOGO.exists():  # sin catálogo no hay servidor; con uno viejo, seguimos
                raise
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
    # primero las series al día (actualizadas en los últimos 100 días del catálogo), luego las de 2025 en adelante,
    # al final las viejas y las descontinuadas; entre iguales, las de nombre más corto (las más generales)
    sel = df[m]
    al_dia = (pd.Timestamp(df.actualizada.max()) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    vigencia = ((sel.actualizada < al_dia).astype(int) + (sel.actualizada < "2025").astype(int)
                + 2 * sel.texto.str.contains("descontinuada").astype(int))
    orden = sel.assign(vigencia=vigencia, largo=(sel.grupo + sel.nombre).str.len())
    return [_ficha(c) for c in orden.sort_values(["vigencia", "largo"]).head(limite).codigo]


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


FRECUENCIAS = {"Diaria": "D", "Mensual": "M", "Trimestral": "Q", "Anual": "Y"}
NOMBRE_FRECUENCIA = {f: nombre for nombre, f in FRECUENCIAS.items()}


def _a_periodo(texto, frecuencia):
    """Pasa "2025", "2025-T1", "2025-1", "2025-01" o "2025-01-15" a un pd.Period ("Mensual", "M" o "Q-DEC")."""
    p = [int(x) for x in re.findall(r"\d+", str(texto))] + [1, 1]
    f = FRECUENCIAS.get(frecuencia, frecuencia)[0]
    if f == "Y":
        return pd.Period(year=p[0], freq="Y")
    if f == "Q":
        return pd.Period(year=p[0], quarter=p[1], freq="Q")
    if f == "M":
        return pd.Period(year=p[0], month=p[1], freq="M")
    return pd.Period(year=p[0], month=p[1], day=p[2], freq="D")


def _texto(p):
    """Un pd.Period escrito como lo devuelve serie: "2025", "2025-T1", "2025-01" o "2025-01-15"."""
    return {"Y": f"{p.year}", "Q": f"{p.year}-T{p.quarter}", "M": f"{p:%Y-%m}", "D": f"{p:%Y-%m-%d}"}[p.freqstr[0]]


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
    frec, fin = ficha["frecuencia"], _fecha_api(hasta, ficha["frecuencia"], True)
    fuente = f"{API}/{codigo}/json/{_fecha_api(desde, frec)}/{fin}"
    datos = [{"periodo": _periodo(p["name"]), "valor": float(p["values"][0])}
             for p in _descargar(fuente).get("periods", []) if p["values"][0] not in ("n.d.", "")]
    # avisar si la serie no llega a lo pedido: es la señal de que conviene buscar otra (o de que está descontinuada)
    avisos = []
    if not datos:
        avisos.append(f"La serie no tiene datos entre {desde} y {hasta}.")
    elif (_a_periodo(fin, frec) - _a_periodo(datos[-1]["periodo"], frec)).n > (7 if frec == "Diaria" else 0):
        avisos.append(f"El último dato es de {datos[-1]['periodo']} y se pidió hasta {hasta} (última actualización "
                      f"de la serie: {ficha['actualizada']}). Si necesitan un dato más reciente, busquen otra serie.")
    if "descontinuada" in ficha["nombre"]:
        avisos.append("La serie está descontinuada: busquen su reemplazo con buscar_series.")
    return {**ficha, "datos": datos, "fuente": fuente, **({"aviso": " ".join(avisos)} if avisos else {})}


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
            "fuente": s["fuente"], **({"aviso": s["aviso"]} if "aviso" in s else {})}


IPC_LIMA = "PN38705PM"  # IPC de Lima Metropolitana, índice dic. 2021 = 100: el deflactor de "real"
POR_ANIO = {"M": 12, "Q": 4, "Y": 1}
OPERACIONES = {"var_interanual": "var% interanual", "var_periodo": "var% respecto al periodo anterior",
               "var_acumulada": "var% acumulada en el año", "promedio_anual": "promedio anual",
               "indice": "índice", "real": "a precios constantes"}


def _unidad(nombre):
    """La unidad es el último paréntesis del nombre: "(US$ por onzas troy)", "(var% 12 meses)"..."""
    partes = [p for p in re.findall(r"\(([^()]*)\)", nombre) if p != "descontinuada"]
    if partes:
        return partes[-1]
    return "%" if re.search(r"tasa|inflacion", _sin_tildes(nombre)) else ""  # tasa de referencia, expectativas


def _en_pandas(codigo, desde, hasta):
    """La serie como pd.Series con índice de periodos, más su URL."""
    s = serie(codigo, desde, hasta)
    if not s["datos"]:
        raise ToolError(f"La serie {codigo} no tiene datos entre {desde} y {hasta}.")
    indice = pd.PeriodIndex([_a_periodo(d["periodo"], s["frecuencia"]) for d in s["datos"]])
    return pd.Series([d["valor"] for d in s["datos"]], index=indice), s["fuente"]


def _base(s, base, ultimo=False):
    """El valor de referencia: el del periodo base, el promedio del año base ("2025") o, sin base, el primero/último."""
    s = s.dropna()
    if not base:
        p = s.index[-1] if ultimo else s.index[0]
        return _texto(p), s[p]
    if len(re.findall(r"\d+", str(base))) == 1 and s.index.freqstr[0] != "Y":
        del_anio = s[s.index.year == int(base)]
        if del_anio.empty:
            raise ToolError(f"No hay datos en el año base {base}.")
        return str(base), del_anio.mean()
    p = _a_periodo(base, s.index.freqstr)
    if p not in s.index:
        raise ToolError(f"No hay dato en el periodo base {base}.")
    return _texto(p), s[p]


def _transformar(codigo, operacion, desde, hasta, base=None):
    """Aplica una operación (o ninguna) a la serie; devuelve la ficha, la serie, su unidad, las fuentes y avisos."""
    ficha = _ficha(codigo)
    unidad, avisos = _unidad(ficha["nombre"]), []
    if operacion and operacion not in OPERACIONES:
        raise ToolError(f"Operación desconocida {operacion!r}. Usen una de: {', '.join(OPERACIONES)}.")
    if operacion not in (None, "", "promedio_anual") and ("%" in unidad or "porcentual" in unidad):
        avisos.append(f"La serie ya es una tasa ({unidad}): transformarla rara vez tiene sentido. "
                      "Busquen la serie en niveles (índice, monto o precio).")
    # las variaciones contra el año anterior necesitan un año más de historia
    pedir = str(int(re.findall(r"\d+", str(desde))[0]) - 1) if operacion in ("var_interanual", "var_acumulada") else desde
    s, fuente = _en_pandas(codigo, pedir, hasta)
    fuentes = [fuente]
    if ficha["frecuencia"] == "Diaria" and operacion in ("var_interanual", "var_acumulada", "real"):
        s = s.groupby(s.index.asfreq("M")).mean()
        avisos.append("Serie diaria: se usó el promedio de cada mes.")
    f = s.index.freqstr

    if operacion == "var_interanual":
        s = 100 * (s / s.reindex(s.index - POR_ANIO[f[0]]).to_numpy() - 1)
        unidad = OPERACIONES[operacion]
    elif operacion == "var_periodo":
        s = 100 * (s / s.shift(1) - 1)
        unidad = OPERACIONES[operacion]
    elif operacion == "var_acumulada":
        cierre = s.groupby(s.index.year).last()
        s = 100 * (s / cierre.reindex(s.index.year - 1).to_numpy() - 1)
        unidad = OPERACIONES[operacion]
    elif operacion == "promedio_anual":
        ultimo = s.index[-1]
        if ultimo.end_time < pd.Period(year=ultimo.year, freq="Y").end_time - pd.Timedelta(days=7):
            avisos.append(f"{ultimo.year} llega solo hasta {_texto(ultimo)}: su promedio es parcial.")
        s = s.groupby(s.index.year).mean()
        s.index = pd.PeriodIndex([pd.Period(year=a, freq="Y") for a in s.index])
    elif operacion == "indice":
        etiqueta, ref = _base(s, base)
        s = 100 * s / ref
        unidad = f"índice {etiqueta} = 100"
    elif operacion == "real":
        ipc, fuente_ipc = _en_pandas(IPC_LIMA, desde, hasta)
        fuentes.append(fuente_ipc)
        if f[0] in "QY":  # el IPC trimestral o anual es el promedio de sus meses, solo si están todos
            grupos = ipc.groupby(ipc.index.asfreq(f))
            ipc = grupos.mean()[grupos.size() == 12 // POR_ANIO[f[0]]]
        ipc = ipc.reindex(s.index)
        etiqueta, ref = _base(ipc, base, ultimo=True)
        s = s * ref / ipc
        if "US$" in unidad:
            avisos.append("El IPC de Lima deflacta montos en soles; para montos en dólares no corresponde.")
        unidad = f"{unidad} a precios de {etiqueta}".strip()

    inicio = _a_periodo(_fecha_api(desde, ficha["frecuencia"]), ficha["frecuencia"]).asfreq(s.index.freqstr)
    s = s[s.index >= inicio].dropna()
    if s.empty:
        raise ToolError(f"No quedan datos de {codigo} entre {desde} y {hasta} tras aplicar {operacion}.")
    return {"ficha": ficha, "s": s, "unidad": unidad, "fuentes": fuentes, "avisos": avisos}


@mcp.tool(annotations=SOLO_LECTURA)
def transformar(codigo: str, operacion: str, desde: str, hasta: str, base: str | None = None) -> dict:
    """Transforma una serie del BCRP como lo haría un economista, sin cuentas a mano.

    operacion:
    - "var_interanual": variación % contra el mismo periodo del año anterior.
    - "var_periodo": variación % contra el periodo anterior (día, mes, trimestre o año).
    - "var_acumulada": variación % contra el cierre del año anterior.
    - "promedio_anual": promedio de cada año; avisa si el último año está incompleto.
    - "indice": base 100 en `base` ("2025-01", o "2025" para el promedio del año); sin base, el primer dato.
    - "real": deflacta con el IPC de Lima (PN38705PM) a precios de `base`; sin base, del último mes con IPC.
      Solo para montos en soles.
    Úsenla sobre series en niveles (índices, montos, precios), no sobre las que ya son tasas (var%). En series
    diarias, var_interanual, var_acumulada y real usan el promedio de cada mes.
    Ejemplo: transformar("PN38705PM", "var_interanual", "2025-01", "2026-09") da la inflación a 12 meses.
    """
    t = _transformar(codigo, operacion, desde, hasta, base)
    r = {**t["ficha"], "frecuencia": NOMBRE_FRECUENCIA[t["s"].index.freqstr[0]], "operacion": operacion,
         "unidad": t["unidad"], "datos": [{"periodo": _texto(p), "valor": round(v, 4)} for p, v in t["s"].items()],
         "fuentes": t["fuentes"]}
    return {**r, "avisos": t["avisos"]} if t["avisos"] else r


# paleta categórica validada (orden fijo) y tintas para fondo claro
COLORES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
TINTA, TINTA_SUAVE, FONDO, REJILLA, SOMBRA = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df", "#ecebe6"
MESES_CORTOS = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"]


def _pedidas(codigos, desde, hasta, transformacion, base):
    lista = [c.strip().upper() for c in codigos.replace(";", ",").split(",") if c.strip()]
    if not lista:
        raise ToolError("Indiquen al menos un código de serie, separados por comas.")
    return [_transformar(c, transformacion, desde, hasta, base) for c in lista]


def _nombre_corto(ficha):
    """El nombre sin los paréntesis de unidad y, si el grupo es largo, sin el grupo: "Plata - H. Harman"."""
    f = catalogo().query("codigo == @ficha['codigo']").iloc[0]
    grupo, nombre = (re.sub(r"\s*\([^()]*\)", "", t).strip(" -") for t in (f.grupo, f.nombre))
    nombre = f"{grupo} - {nombre}" if len(grupo) <= 40 else nombre
    return nombre if len(nombre) <= 70 else nombre[:69] + "…"


def _archivo(codigos, transformacion, desde, hasta, extension):
    SALIDAS.mkdir(exist_ok=True)
    nombre = re.sub(r"[^\w-]+", "_", f"{'_'.join(codigos)}_{transformacion or 'niveles'}_{desde}_{hasta}")
    return SALIDAS / f"{nombre}.{extension}"


def _panel(unidad):
    """Las series en porcentaje (tasas, variaciones) comparten panel aunque su unidad se escriba distinto."""
    return "%" if "%" in unidad or "porcentual" in unidad else unidad


@mcp.tool(annotations=GUARDA_ARCHIVO)
def grafico(codigos: str, desde: str, hasta: str, transformacion: str | None = None, base: str | None = None,
            titulo: str | None = None, sombrear: str | None = None) -> ToolResult:
    """Grafica hasta 5 series del BCRP con estilo de publicación y guarda el PNG en salidas/.

    Úsenla cuando pidan un gráfico o cuando la evolución se entienda mejor viéndola. codigos: separados por comas,
    por ejemplo "PN01273PM,PD04722MM". transformacion y base: las mismas operaciones de transformar (por ejemplo
    "var_interanual" o "indice" para comparar series de distinta escala). Las series con la misma unidad comparten
    panel (todas las tasas y variaciones %, juntas); si las unidades difieren, cada una va en su panel, uno debajo
    de otro, nunca con dos ejes. sombrear: periodos a resaltar, por ejemplo "2020-03:2020-12,2023-01:2023-06".
    Devuelve la ruta del archivo, el último dato de cada serie, las fuentes y la imagen.
    """
    series = _pedidas(codigos, desde, hasta, transformacion, base)
    if len(series) > len(COLORES):
        raise ToolError(f"Máximo {len(COLORES)} series por gráfico: con más no se distinguen. Hagan varios.")
    paneles = {}
    for x, color in zip(series, COLORES):
        x["color"] = color
        paneles.setdefault(_panel(x["unidad"]), []).append(x)
    if len(paneles) > 3:
        raise ToolError("Hay más de tres unidades distintas: usen transformacion='indice' o hagan varios gráficos.")

    # alturas en pulgadas: título y subtítulo, una fila de leyenda por cada dos series, paneles y pie
    arriba = 0.85 + (0.24 * ((len(series) + 1) // 2) if len(series) > 1 else 0)
    alto = arriba + 3.2 * len(paneles) + 0.45
    fig = Figure(figsize=(10, alto), facecolor=FONDO)
    ejes = fig.subplots(len(paneles), 1, sharex=True, squeeze=False)[:, 0]
    fechas_todas = pd.concat([x["s"].index.to_timestamp().to_series() for x in series])
    largo = (fechas_todas.max() - fechas_todas.min()).days > 4 * 365
    for ax, grupo in zip(ejes, paneles.values()):
        ax.set_facecolor(FONDO)
        minimo, maximo = min(x["s"].min() for x in grupo), max(x["s"].max() for x in grupo)
        separacion, ocupado = 0.07 * ((maximo - minimo) or 1), None
        a_la_derecha = offset_copy(ax.transData, fig=fig, x=7, units="points")
        # el último dato de cada serie se rotula al final de su línea, empujando hacia arriba si dos chocan
        for x in sorted(grupo, key=lambda x: x["s"].iloc[-1]):
            fechas, valores = x["s"].index.to_timestamp(), x["s"].to_numpy()
            ax.plot(fechas, valores, color=x["color"], lw=1.8, label=_nombre_corto(x["ficha"]),
                    marker="o" if len(valores) <= 15 else None, ms=4)
            ax.plot(fechas[-1], valores[-1], "o", color=x["color"], ms=5)
            y = valores[-1] if ocupado is None else max(valores[-1], ocupado + separacion)
            ax.text(fechas[-1], y, f"{valores[-1]:,.2f}", transform=a_la_derecha, va="center", fontsize=9, color=TINTA)
            ocupado = y
        if minimo < 0 < maximo:
            ax.axhline(0, color=TINTA_SUAVE, lw=0.8)
        for tramo in (sombrear or "").split(","):
            if ":" in tramo:
                a, b = tramo.split(":")
                ax.axvspan(pd.Period(a.strip()).start_time, pd.Period(b.strip()).end_time, color=SOMBRA, zorder=0)
        unidades = list(dict.fromkeys(x["unidad"] for x in grupo if x["unidad"])) or ["sin unidad en el catálogo"]
        ax.set_title(" · ".join(unidades), loc="left", fontsize=9.5, color=TINTA_SUAVE)
        for lado in ("top", "right", "left"):
            ax.spines[lado].set_visible(False)
        ax.spines["bottom"].set_color(REJILLA)
        ax.grid(axis="y", color=REJILLA, lw=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors=TINTA_SUAVE, labelsize=9, length=0)
        ax.margins(x=0.07)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}" if abs(v) >= 1000 else f"{v:g}"))
    ejes[-1].xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"{num2date(v).year}" if largo else f"{MESES_CORTOS[num2date(v).month]} {num2date(v):%y}"))

    codigos_ok = [x["ficha"]["codigo"] for x in series]
    titulo = titulo or (_nombre_corto(series[0]["ficha"]) if len(series) == 1 else "Series del BCRP")
    subtitulo = f"{OPERACIONES[transformacion].capitalize()} · " if transformacion else ""
    fig.text(0.012, 1 - 0.32 / alto, titulo, fontsize=14, fontweight="bold", color=TINTA)
    fig.text(0.012, 1 - 0.58 / alto, f"{subtitulo}{desde} a {hasta}", fontsize=10, color=TINTA_SUAVE)
    if len(series) > 1:  # una sola leyenda, bajo el subtítulo, en el orden en que se pidieron las series
        lineas = {l.get_label(): l for ax in ejes for l in ax.get_lines() if not l.get_label().startswith("_")}
        etiquetas = [_nombre_corto(x["ficha"]) for x in series]
        fig.legend([lineas[e] for e in etiquetas], etiquetas, loc="upper left", bbox_to_anchor=(0.005, 1 - 0.72 / alto),
                   ncol=2, frameon=False, fontsize=9, labelcolor=TINTA, handlelength=1.6, columnspacing=2)
    fuente = f"Fuente: BCRP ({', '.join(codigos_ok)})" + (f" e IPC de Lima ({IPC_LIMA})" if transformacion == "real" else "")
    fig.text(0.012, 0.14 / alto, f"{fuente}. Elaborado con bcrp-mcp el {date.today():%d/%m/%Y}.", fontsize=8,
             color=TINTA_SUAVE)
    fig.tight_layout(rect=(0, 0.4 / alto, 1, 1 - arriba / alto))
    archivo = _archivo(codigos_ok, transformacion, desde, hasta, "png")
    fig.savefig(archivo, dpi=150, facecolor=FONDO)

    info = {"archivo": str(archivo.resolve()),
            "series": [{"codigo": x["ficha"]["codigo"], "nombre": x["ficha"]["nombre"], "unidad": x["unidad"],
                        "ultimo": {"periodo": _texto(x["s"].index[-1]), "valor": round(x["s"].iloc[-1], 4)}}
                       for x in series],
            "fuentes": [u for x in series for u in x["fuentes"]]}
    avisos = [a for x in series for a in x["avisos"]]
    if avisos:
        info["avisos"] = avisos
    return ToolResult(content=[json.dumps(info, ensure_ascii=False), Image(path=archivo)], structured_content=info)


@mcp.tool(annotations=GUARDA_ARCHIVO)
def exportar(codigos: str, desde: str, hasta: str, transformacion: str | None = None,
             base: str | None = None) -> dict:
    """Exporta series del BCRP a un Excel en salidas/: una hoja con los datos alineados por periodo y otra de metadatos.

    Úsenla cuando pidan los datos para trabajarlos en Excel, Stata o R. codigos: separados por comas. Las series de
    distinta frecuencia van en hojas separadas. transformacion y base: las mismas operaciones de transformar.
    La hoja Metadatos guarda el nombre, la unidad, la transformación y la URL de cada serie.
    """
    series = _pedidas(codigos, desde, hasta, transformacion, base)
    codigos_ok = [x["ficha"]["codigo"] for x in series]
    archivo = _archivo(codigos_ok, transformacion, desde, hasta, "xlsx")
    por_frecuencia = {}
    for x in series:
        por_frecuencia.setdefault(x["s"].index.freqstr[0], []).append(x)

    with pd.ExcelWriter(archivo, engine="openpyxl") as w:
        for f, grupo in por_frecuencia.items():
            hoja = "Datos" if len(por_frecuencia) == 1 else f"Datos {NOMBRE_FRECUENCIA[f].lower()}"
            tabla = pd.concat({x["ficha"]["codigo"]: x["s"] for x in grupo}, axis=1).sort_index()
            tabla.index = tabla.index.to_timestamp() if f in "MD" else [_texto(p) for p in tabla.index]
            tabla.to_excel(w, sheet_name=hoja, startrow=3, header=False)
            ws = w.sheets[hoja]
            # tres filas de encabezado: código, nombre y unidad
            for fila, rotulo in enumerate(["Código", "Serie", "Unidad"], start=1):
                ws.cell(fila, 1, rotulo).font = Font(bold=True)
            for col, x in enumerate(grupo, start=2):
                ws.cell(1, col, x["ficha"]["codigo"]).font = Font(bold=True)
                ws.cell(2, col, x["ficha"]["nombre"]).alignment = Alignment(wrap_text=True, vertical="top")
                ws.cell(3, col, x["unidad"])
                ws.column_dimensions[ws.cell(1, col).column_letter].width = 20
            for fila in ws.iter_rows(min_row=4):
                if f in "MD":
                    fila[0].number_format = "mmm-yyyy" if f == "M" else "dd/mm/yyyy"
                for celda in fila[1:]:
                    celda.number_format = "#,##0.00"
            ws.column_dimensions["A"].width = 12
            ws.row_dimensions[2].height = 60
            ws.freeze_panes = "B4"
        pd.DataFrame([{
            "Código": x["ficha"]["codigo"], "Serie": x["ficha"]["nombre"], "Categoría": x["ficha"]["categoria"],
            "Frecuencia original": x["ficha"]["frecuencia"], "Unidad": x["unidad"],
            "Transformación": OPERACIONES.get(transformacion, "ninguna (niveles)"),
            "Desde": _texto(x["s"].index[0]), "Hasta": _texto(x["s"].index[-1]),
            "Última actualización": x["ficha"]["actualizada"], "Fuente": " ; ".join(x["fuentes"]),
            "Descargado": f"{date.today():%Y-%m-%d}"} for x in series]).to_excel(w, sheet_name="Metadatos", index=False)

    r = {"archivo": str(archivo.resolve()), "hojas": list(pd.ExcelFile(archivo).sheet_names),
         "series": [{"codigo": x["ficha"]["codigo"], "nombre": x["ficha"]["nombre"], "unidad": x["unidad"],
                     "observaciones": len(x["s"])} for x in series],
         "fuentes": [u for x in series for u in x["fuentes"]]}
    avisos = [a for x in series for a in x["avisos"]]
    return {**r, "avisos": avisos} if avisos else r


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


@mcp.prompt
def coyuntura(periodo: str = "") -> str:
    """Pide una nota de coyuntura macroeconómica del Perú para economistas, con un gráfico y cifras del BCRP."""
    cuando = f"de {periodo}" if periodo else "con el último dato disponible"
    return (f"Escribe una nota de coyuntura macroeconómica del Perú {cuando}, para un lector economista.\n\n"
            "Series (cita el código y el periodo de cada cifra, por ejemplo (PN01273PM, 2026-09)):\n"
            "- Inflación: IPC de Lima, var% 12 meses (PN01273PM); subyacente (PN01279PM); expectativa de inflación "
            "a 12 meses (PD12912AM).\n"
            "- Política monetaria: tasa de referencia (PD04722MM).\n"
            "- Actividad: PBI mensual, var% interanual (PN01728AM).\n"
            "- Sector externo: tipo de cambio interbancario promedio (PN01207PM), términos de intercambio var% "
            "12 meses (PN38926BM) y reservas internacionales netas en millones de US$ (PN00027MM).\n"
            "- Crédito al sector privado, var% 12 meses (PN00504MM).\n\n"
            "Pasos:\n"
            "1. Usa resumen en los últimos 13 meses de cada serie: último dato, el de un año antes y los extremos. "
            "Para el tipo de cambio y las reservas, usa transformar con var_interanual. No calcules a mano. Si una "
            "serie avisa que está descontinuada o que su último dato tiene más de tres meses, dilo.\n"
            "2. Usa grafico con \"PN01273PM,PN01279PM,PD12912AM,PD04722MM\" para los últimos tres años, y menciona "
            "la ruta del archivo.\n"
            "3. Estructura: un titular de una línea; Inflación y política monetaria; Actividad; Sector externo y "
            "crédito; Lo que hay que mirar (solo lo que muestran los datos, sin pronósticos propios); Fuentes "
            "(las URL).\n"
            "Máximo 400 palabras, en español, con el tono de un reporte de un banco central.")


if __name__ == "__main__":
    mcp.run()
