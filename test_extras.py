"""Pruebas de las herramientas para economistas: transformar, grafico, exportar, avisos y el prompt de coyuntura.

    uv run pytest -q

Como test_server.py, piden datos reales al BCRP y comparan con las series que el BCRP ya publica calculadas.
"""
import asyncio
from pathlib import Path

import pandas as pd
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from server import mcp


async def _con_cliente(f):
    async with Client(mcp) as c:
        return await f(c)


def llamar(herramienta, **args):
    return asyncio.run(_con_cliente(lambda c: c.call_tool(herramienta, args)))


def test_var_interanual_reproduce_la_inflacion_del_bcrp():
    # la variación a 12 meses del índice (PN38705PM) es la inflación que publica el BCRP (PN01273PM)
    r = llamar("transformar", codigo="PN38705PM", operacion="var_interanual", desde="2026-01", hasta="2026-09").data
    oficial = llamar("serie", codigo="PN01273PM", desde="2026-01", hasta="2026-09").data
    assert [d["periodo"] for d in r["datos"]] == [d["periodo"] for d in oficial["datos"]]
    for propio, bcrp in zip(r["datos"], oficial["datos"]):
        assert propio["valor"] == pytest.approx(bcrp["valor"], abs=0.01)


def test_var_acumulada_reproduce_la_del_bcrp():
    r = llamar("transformar", codigo="PN38705PM", operacion="var_acumulada", desde="2026-09", hasta="2026-09").data
    oficial = llamar("serie", codigo="PN01272PM", desde="2026-09", hasta="2026-09").data
    assert r["datos"][0]["valor"] == pytest.approx(oficial["datos"][0]["valor"], abs=0.01)


def test_indice_con_base_en_el_primer_dato():
    r = llamar("transformar", codigo="PN01655XM", operacion="indice", desde="2025-01", hasta="2025-12").data
    assert r["datos"][0]["valor"] == pytest.approx(100)
    assert r["unidad"] == "índice 2025-01 = 100"


def test_promedio_anual_de_una_serie_diaria_avisa_si_el_anio_esta_incompleto():
    r = llamar("transformar", codigo="PD04638PD", operacion="promedio_anual", desde="2025", hasta="2026-10-15").data
    assert [d["periodo"] for d in r["datos"]] == ["2025", "2026"]
    assert any("parcial" in a for a in r["avisos"])


def test_real_deflacta_con_el_ipc():
    r = llamar("transformar", codigo="PN00518MM", operacion="real", desde="2025-01", hasta="2025-12").data
    nominal = llamar("serie", codigo="PN00518MM", desde="2025-12", hasta="2025-12").data
    # sin base, los precios son los del último mes: ahí real y nominal coinciden
    assert r["datos"][-1]["valor"] == pytest.approx(nominal["datos"][0]["valor"])
    assert "PN38705PM" in r["fuentes"][1]


def test_transformar_avisa_si_la_serie_ya_es_una_tasa():
    r = llamar("transformar", codigo="PN01273PM", operacion="var_interanual", desde="2026-01", hasta="2026-09").data
    assert any("ya es una tasa" in a for a in r["avisos"])


def test_operacion_desconocida_da_un_error_claro():
    with pytest.raises(ToolError, match="var_interanual"):
        llamar("transformar", codigo="PN38705PM", operacion="crecimiento", desde="2025-01", hasta="2025-12")


def test_grafico_guarda_un_png_y_devuelve_la_imagen():
    r = llamar("grafico", codigos="PN01273PM,PD04722MM", desde="2025-01", hasta="2026-09")
    assert Path(r.structured_content["archivo"]).exists()
    assert any(c.type == "image" for c in r.content)
    assert [s["codigo"] for s in r.structured_content["series"]] == ["PN01273PM", "PD04722MM"]


def test_exportar_arma_un_excel_con_metadatos():
    r = llamar("exportar", codigos="PN01273PM,PD04638PD", desde="2026-01", hasta="2026-09").data
    assert r["hojas"] == ["Datos mensual", "Datos diaria", "Metadatos"]
    meta = pd.read_excel(r["archivo"], sheet_name="Metadatos")
    assert list(meta["Código"]) == ["PN01273PM", "PD04638PD"]
    assert meta["Fuente"].str.startswith("https://estadisticas.bcrp.gob.pe").all()


def test_serie_avisa_si_no_llega_al_periodo_pedido():
    # PN00530MM dejó de actualizarse en junio de 2026: fue la trampa de la pregunta de crédito
    r = llamar("serie", codigo="PN00530MM", desde="2026-01", hasta="2026-09").data
    assert "último dato" in r["aviso"]


def test_buscar_pone_primero_las_series_al_dia():
    r = llamar("buscar_series", texto="credito sector privado", frecuencia="Mensual", limite=5).data
    assert all(x["actualizada"] >= "2026-07" for x in r)


def test_prompt_de_coyuntura():
    async def pedir(c):
        return await c.get_prompt("coyuntura", {"periodo": "2026-09"})
    texto = asyncio.run(_con_cliente(pedir)).messages[0].content.text
    assert "2026-09" in texto and "PN01273PM" in texto
