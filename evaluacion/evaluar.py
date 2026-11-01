"""Evaluación del comportamiento del agente: ¿sabe cuándo usar herramientas y cuándo no?

Para cada caso de evaluacion/casos.json se ejecuta la conversación completa en un hilo
nuevo (aprobando o rechazando la confirmación según el caso) y se comprueba:

    ruta          el router eligió una de las rutas esperadas
    herramientas  se llamaron todas las herramientas esperadas
    prohibidas    no se llamó ninguna herramienta prohibida ("*" = ninguna herramienta)
    etiqueta      se generó (o no) la etiqueta, como se esperaba

La evaluación mira la TRAYECTORIA (qué decidió y qué hizo el agente), no solo el texto.

Uso:
    python -m evaluacion.evaluar                 # con Ollama (el modelo real)
    python -m evaluacion.evaluar --simulado      # con modelos simulados: verifica el arnés, no el modelo
    python -m evaluacion.evaluar --casos C06 C12 # solo algunos casos
"""

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def preparar(simulado: bool):
    # Cada evaluación usa su propio registro y memoria, para no mezclarse con la app.
    os.environ["CARPETA_EJECUCION"] = tempfile.mkdtemp(prefix="ecomarket_eval_")
    if simulado:
        os.environ.setdefault("RAG_UMBRAL", "0.15")
    from langgraph.checkpoint.memory import InMemorySaver

    from src.agente.grafo import cargar_almacen_seguro, construir_grafo
    if simulado:
        from qdrant_client import QdrantClient

        from pruebas.modelo_simulado import (EmbeddingsSimulados, ModeloAgenteSimulado, ModeloJSONSimulado,
                                             ModeloTextoSimulado)
        from src.rag.extraccion import cargar_todo
        from src.rag.indexacion import crear_indice
        from src.rag.segmentacion import segmentar_todo
        almacen, _ = crear_indice(segmentar_todo(cargar_todo()), coleccion="eval", cliente=QdrantClient(":memory:"),
                                  embeddings=EmbeddingsSimulados())
        return construir_grafo(ModeloAgenteSimulado(), ModeloTextoSimulado(), ModeloJSONSimulado(), almacen,
                               InMemorySaver())
    almacen, aviso = cargar_almacen_seguro()
    if aviso:
        print(f"⚠️  {aviso} (los casos RAG fallarán)")
    return construir_grafo(almacen=almacen, checkpointer=InMemorySaver())


def evaluar_caso(grafo, caso: dict) -> dict:
    from src.agente.servicio import conversar
    hilo = f"eval-{caso['id']}-{uuid.uuid4().hex[:6]}"
    eventos, inicio = [], time.perf_counter()
    for turno in caso["turnos"]:
        eventos += conversar(grafo, hilo, turno, aprobar=caso["aprobar"])
    rutas = [e["ruta"] for e in eventos if e["tipo"] == "ruta"]
    llamadas = [e["herramienta"] for e in eventos if e["tipo"] == "llamada"]
    etiquetas = [e["datos"] for e in eventos if e["tipo"] == "etiqueta"]
    errores = [e["mensaje"] for e in eventos if e["tipo"] == "error"]
    respuesta = next((e["texto"] for e in reversed(eventos) if e["tipo"] == "respuesta"), "")

    prohibidas = caso["herramientas_prohibidas"]
    ok_ruta = bool(rutas) and rutas[-1] in caso["ruta_esperada"] if caso["ruta_esperada"] else True
    ok_herramientas = all(h in llamadas for h in caso["herramientas_esperadas"])
    ok_prohibidas = not llamadas if "*" in prohibidas else not any(h in llamadas for h in prohibidas)
    if caso["etiqueta"] == "reutilizada":
        ok_etiqueta = bool(etiquetas) and all(e.get("reutilizada") for e in etiquetas)
    else:
        ok_etiqueta = bool(etiquetas) == caso["etiqueta"]
    return {
        **{k: caso[k] for k in ("id", "categoria", "descripcion")},
        "ruta": rutas[-1] if rutas else None, "herramientas": llamadas, "respuesta": respuesta, "errores": errores,
        "ok_ruta": ok_ruta, "ok_herramientas": ok_herramientas, "ok_prohibidas": ok_prohibidas,
        "ok_etiqueta": ok_etiqueta, "aprobado": all((ok_ruta, ok_herramientas, ok_prohibidas, ok_etiqueta)) and not errores,
        "segundos": round(time.perf_counter() - inicio, 1),
    }


def reporte(resultados: list[dict], simulado: bool) -> str:
    from src import config
    marca = lambda v: "✅" if v else "❌"  # noqa: E731
    total = len(resultados)
    aprobados = sum(r["aprobado"] for r in resultados)
    porc = lambda clave: sum(r[clave] for r in resultados) / total  # noqa: E731
    modelo = "SIMULADO (reglas fijas)" if simulado else f"`{config.MODELO_AGENTE}`"
    lineas = [
        "# Evaluación del comportamiento del agente", "",
        f"Modelo del agente: {modelo} · Fecha: {datetime.now():%Y-%m-%d %H:%M}", "",
        f"**Casos aprobados: {aprobados}/{total} ({aprobados / total:.0%})**", "",
        "| Métrica | Resultado |", "|---|---|",
        f"| Ruta correcta | {porc('ok_ruta'):.0%} |",
        f"| Usó las herramientas esperadas | {porc('ok_herramientas'):.0%} |",
        f"| No usó herramientas indebidas | {porc('ok_prohibidas'):.0%} |",
        f"| Etiqueta generada (o no) correctamente | {porc('ok_etiqueta'):.0%} |", "",
        "| Caso | Categoría | Descripción | Ruta | Herramientas usadas | Ruta ok | Herr. ok | Sin indebidas | Etiqueta ok | s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in resultados:
        lineas.append(f"| {r['id']} | {r['categoria']} | {r['descripcion']} | {r['ruta']} | "
                      f"{', '.join(r['herramientas']) or '—'} | {marca(r['ok_ruta'])} | {marca(r['ok_herramientas'])} | "
                      f"{marca(r['ok_prohibidas'])} | {marca(r['ok_etiqueta'])} | {r['segundos']} |")
    lineas += ["", "## Respuestas finales", ""]
    for r in resultados:
        texto = (r["respuesta"] or "(sin respuesta)").replace("\n", " ")
        lineas.append(f"- **{r['id']}** {'✅' if r['aprobado'] else '❌'} {texto[:300]}"
                      + (f"  \n  ⚠️ Errores: {'; '.join(r['errores'])}" if r["errores"] else ""))
    return "\n".join(lineas) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulado", action="store_true", help="usar modelos simulados (sin Ollama)")
    parser.add_argument("--casos", nargs="*", help="ids de los casos a ejecutar")
    args = parser.parse_args()

    casos = json.loads((RAIZ / "evaluacion" / "casos.json").read_text(encoding="utf-8"))
    if args.casos:
        casos = [c for c in casos if c["id"] in set(args.casos)]
    grafo = preparar(args.simulado)
    resultados = []
    for caso in casos:
        r = evaluar_caso(grafo, caso)
        resultados.append(r)
        print(f"{'✅' if r['aprobado'] else '❌'} {r['id']} {r['descripcion']:<55} ruta={r['ruta']:<12} "
              f"herramientas={r['herramientas']}")
    salida = RAIZ / "resultados" / ("evaluacion_simulada.md" if args.simulado else "evaluacion.md")
    salida.parent.mkdir(exist_ok=True)
    salida.write_text(reporte(resultados, args.simulado), encoding="utf-8")
    print(f"\nAprobados: {sum(r['aprobado'] for r in resultados)}/{len(resultados)} · Reporte: {salida.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
