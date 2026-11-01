"""Panel de operación: monitoreo del agente a partir del registro de acciones (Fase 3)."""

import json
import urllib.request

from src import config
from src.herramientas.registro import obtener_registro

COLUMNAS_ACCIONES = ["fecha", "hilo", "herramienta", "ok", "codigo_error", "duracion_ms", "argumentos", "mensaje"]
ENCABEZADOS_ACCIONES = ["fecha", "conversación", "herramienta", "ok", "error", "ms", "argumentos", "mensaje"]


def diagnostico_ollama() -> tuple[bool, str]:
    """Comprueba que Ollama responda y que los modelos estén descargados."""
    try:
        with urllib.request.urlopen(f"{config.OLLAMA_URL}/api/tags", timeout=3) as r:  # noqa: S310 (URL de config)
            modelos = {m["name"] for m in json.load(r).get("models", [])}
    except Exception:  # noqa: BLE001
        return False, f"No se pudo conectar con Ollama en `{config.OLLAMA_URL}`."
    faltan = [m for m in {config.MODELO_AGENTE, config.MODELO_LLM, config.MODELO_EMBEDDINGS}
              if m not in modelos and f"{m}:latest" not in modelos]
    if faltan:
        return False, "Faltan modelos en Ollama: " + ", ".join(f"`ollama pull {m}`" for m in faltan)
    return True, "Ollama disponible."


def alertas(m: dict) -> list[str]:
    """Reglas simples de alerta sobre las métricas."""
    lista = []
    if config.MODO_SOLO_LECTURA:
        lista.append("🛑 **Modo solo lectura activo**: el agente no puede generar etiquetas.")
    if m["acciones"] >= 5 and m["tasa_error"] > 0.3:
        lista.append(f"⚠️ Tasa de error alta: **{m['tasa_error']:.0%}** de las llamadas a herramientas fallan.")
    errores = m["por_error"]
    if errores.get("SIN_ELEGIBILIDAD_PREVIA"):
        lista.append(f"🔒 El candado bloqueó **{errores['SIN_ELEGIBILIDAD_PREVIA']}** intento(s) de generar una "
                     "etiqueta sin verificación previa. Revisar si fue un error del modelo o una manipulación.")
    if errores.get("TRANSPORTADORA_NO_DISPONIBLE"):
        lista.append(f"🚚 La transportadora falló **{errores['TRANSPORTADORA_NO_DISPONIBLE']}** vez/veces. "
                     "Revisar la integración.")
    if errores.get("IDENTIDAD_NO_COINCIDE", 0) >= 3:
        lista.append(f"🕵️ **{errores['IDENTIDAD_NO_COINCIDE']}** intentos con un correo que no coincide: "
                     "posible intento de suplantación.")
    return lista or ["✅ Sin alertas."]


def resumen_markdown() -> str:
    m = obtener_registro().metricas()
    tarjetas = (f"| Acciones | Errores | Tasa de error | Devoluciones | Tickets |\n|---|---|---|---|---|\n"
                f"| {m['acciones']} | {m['errores']} | {m['tasa_error']:.0%} | {m['devoluciones']} | {m['tickets']} |")
    por_herramienta = "\n".join(
        f"| {h['herramienta']} | {h['llamadas']} | {h['errores']} | {h['ms_promedio']} ms |" for h in m["por_herramienta"])
    tabla = ("\n\n**Por herramienta**\n\n| Herramienta | Llamadas | Errores | Duración promedio |\n|---|---|---|---|\n"
             + por_herramienta) if por_herramienta else ""
    return "### Alertas\n" + "\n".join(f"- {a}" for a in alertas(m)) + "\n\n### Métricas\n" + tarjetas + tabla


def tablas() -> tuple[list[list], list[list], list[list]]:
    r = obtener_registro()
    acciones = [[a[c] if c != "ok" else ("✅" if a["ok"] else "❌") for c in COLUMNAS_ACCIONES] for a in r.acciones(100)]
    devoluciones = [[d["codigo_devolucion"], d["numero_seguimiento"], d["producto"], d["transportadora"], d["guia"],
                     d["fecha_creacion"], d["fecha_limite_envio"], d.get("creada_por", "")] for d in r.devoluciones()]
    tickets = [[t["codigo_ticket"], t["fecha"], t["prioridad"], t["numero_seguimiento"] or "", t["motivo"],
                t["resumen"][:200]] for t in r.tickets()]
    return acciones, devoluciones, tickets
