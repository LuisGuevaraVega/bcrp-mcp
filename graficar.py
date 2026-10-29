"""grafico.png: aciertos por modo (de resultados.csv) y la herramienta propia, comparar_paises.

    uv run graficar.py
"""
import asyncio
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from fastmcp import Client

from server import mcp

AQUI = Path(__file__).parent
COLORES = {"acierta": "#2a9d8f", "se equivoca": "#e76f51", "no sabe": "#b0b0b0"}


async def pbi_region():
    async with Client(mcp) as c:
        r = await c.call_tool("comparar_paises", {"indicador": "pbi", "paises": "PER,CHL,COL,MEX,BRA",
                                                  "desde": 2015, "hasta": 2025})
    return pd.DataFrame(r.data["tabla"]).T.sort_index(), r.data["fuente"]


if __name__ == "__main__":
    res = pd.read_csv(AQUI / "resultados.csv")
    orden = [m for m in ["memoria", "web", "mcp"] if m in res.modo.unique()]
    tabla = (res.groupby("modo").resultado.value_counts(normalize=True).unstack(fill_value=0) * 100).reindex(orden)
    pbi, fuente = asyncio.run(pbi_region())

    fig, (a, b) = plt.subplots(1, 2, figsize=(13, 5), gridspec_kw={"width_ratios": [1, 1.6]})
    abajo = pd.Series(0.0, index=tabla.index)
    for resultado in ["acierta", "se equivoca", "no sabe"]:
        if resultado in tabla:
            a.bar(tabla.index, tabla[resultado], bottom=abajo, color=COLORES[resultado], label=resultado)
            abajo += tabla[resultado]
    for i, m in enumerate(tabla.index):
        a.text(i, 102, f"{tabla.loc[m].get('acierta', 0):.0f}% aciertos", ha="center", fontsize=10)
    a.set_ylim(0, 112)
    a.set_ylabel("% de las preguntas")
    a.set_title(f"Claude Haiku en {res.id.nunique()} preguntas del BCRP")
    a.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3, frameon=False)

    for pais in pbi.columns:
        b.plot(pbi.index, pbi[pais], marker="o", lw=3 if pais == "Peru" else 1.5,
               alpha=1 if pais == "Peru" else 0.6, label=pais)
    b.axhline(0, color="black", lw=0.6)
    b.set_title("Crecimiento del PBI real (%), con la herramienta comparar_paises")
    b.legend(frameon=False, ncol=3, loc="upper left")
    b.text(0, -0.13, f"Fuente: Banco Mundial, {fuente}", transform=b.transAxes, fontsize=7, color="gray")

    for ax in (a, b):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(AQUI / "grafico.png", dpi=150)
    print("grafico.png listo")
