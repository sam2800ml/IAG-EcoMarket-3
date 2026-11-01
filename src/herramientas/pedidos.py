"""Herramienta: consultar un pedido, verificando la identidad del cliente.

Parte de `src/herramientas.py` del Taller 2 (búsqueda exacta por número, sin RAG)
y agrega la verificación del correo: ahora que el agente puede ejecutar acciones,
no basta con conocer un número de seguimiento para actuar sobre un pedido.
"""

import json
import re
import unicodedata
from datetime import date

from src import config
from src.herramientas.contrato import error, exito
from src.rag.extraccion import productos_por_id

RE_SEGUIMIENTO = re.compile(r"\bECO[-\s]?(\d{5})\b", re.IGNORECASE)
RE_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_pedidos: list[dict] | None = None
_productos: dict[str, dict] | None = None


def pedidos() -> list[dict]:
    global _pedidos
    if _pedidos is None:
        _pedidos = json.loads(config.ARCHIVO_PEDIDOS.read_text(encoding="utf-8"))
    return _pedidos


def catalogo() -> dict[str, dict]:
    global _productos
    if _productos is None:
        _productos = productos_por_id()
    return _productos


# ---------------------------------------------------------------- normalización
def normalizar_numero(texto: str | None) -> str | None:
    """Acepta 'eco 10005', 'ECO10005', 'ECO-10005' -> 'ECO-10005'."""
    coincidencia = RE_SEGUIMIENTO.search(texto or "")
    return f"ECO-{coincidencia.group(1)}" if coincidencia else None


def normalizar_correo(texto: str | None) -> str | None:
    coincidencia = RE_CORREO.search(texto or "")
    return coincidencia.group(0).lower() if coincidencia else None


def _sin_tildes(texto: str) -> str:
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-z0-9]+", _sin_tildes(texto)) if len(p) > 3}


# ---------------------------------------------------------------- consultas
def buscar_pedido(numero: str) -> dict | None:
    return next((p for p in pedidos() if p["numero_seguimiento"] == numero), None)


def productos_del_pedido(pedido: dict) -> list[dict]:
    resultado = []
    for item in pedido["productos"]:
        p = catalogo().get(item["id_producto"])
        if p:
            resultado.append({"id_producto": p["id_producto"], "nombre": p["nombre"], "categoria": p["categoria"],
                              "precio_cop": int(p["precio_cop"]), "cantidad": item["cantidad"]})
    return resultado


def dias_desde_entrega(pedido: dict) -> int | None:
    if not pedido.get("fecha_entrega"):
        return None
    return (config.FECHA_HOY - date.fromisoformat(pedido["fecha_entrega"])).days


def identificar_producto(pedido: dict, texto: str | None) -> tuple[dict | None, list[dict]]:
    """Encuentra el producto que menciona el cliente dentro del pedido.

    Devuelve (producto, candidatos). Si el pedido tiene un solo producto, ese es.
    Acepta el id ('P07') o palabras del nombre ('cubiertos', 'kit de bambú').
    """
    productos = productos_del_pedido(pedido)
    if len(productos) == 1 and not texto:
        return productos[0], productos
    texto = texto or ""
    for p in productos:
        if p["id_producto"].lower() == texto.strip().lower():
            return p, productos
    consulta = _palabras(texto)
    puntuados = sorted(((len(consulta & _palabras(p["nombre"])), p) for p in productos),
                       key=lambda par: par[0], reverse=True)
    if puntuados and puntuados[0][0] > 0 and (len(puntuados) == 1 or puntuados[0][0] > puntuados[1][0]):
        return puntuados[0][1], productos
    if len(productos) == 1:
        return productos[0], productos
    return None, productos


def validar_acceso(numero: str | None, correo: str | None) -> tuple[dict | None, dict | None]:
    """Valida número y correo. Devuelve (pedido, None) o (None, respuesta_de_error)."""
    if not numero or not correo:
        faltan = [n for n, v in (("número de seguimiento", numero), ("correo de la compra", correo)) if not v]
        return None, error("DATOS_INCOMPLETOS", f"Falta: {', '.join(faltan)}.", faltan=faltan)
    numero_n, correo_n = normalizar_numero(numero), normalizar_correo(correo)
    if not numero_n:
        return None, error("FORMATO_INVALIDO", "El número de seguimiento debe tener el formato ECO-XXXXX.")
    if not correo_n:
        return None, error("FORMATO_INVALIDO", "El correo no tiene un formato válido.")
    pedido = buscar_pedido(numero_n)
    if pedido is None:
        return None, error("PEDIDO_NO_EXISTE", f"No existe ningún pedido con el número {numero_n}.",
                           numero_seguimiento=numero_n)
    if pedido["correo"].lower() != correo_n:
        # Mensaje genérico a propósito: no revela si el pedido existe para otro correo.
        return None, error("IDENTIDAD_NO_COINCIDE",
                           "El correo no coincide con el registrado en ese pedido. Verifica el correo con el que "
                           "hiciste la compra.", numero_seguimiento=numero_n)
    return pedido, None


def consultar_pedido(numero_seguimiento: str | None, correo: str | None) -> dict:
    """Herramienta: estado y contenido de un pedido, solo si el correo coincide."""
    pedido, fallo = validar_acceso(numero_seguimiento, correo)
    if fallo:
        return fallo
    datos = {
        "numero_seguimiento": pedido["numero_seguimiento"],
        "cliente": pedido["cliente"].split()[0],  # solo el nombre: minimización de datos
        "estado": pedido["estado"],
        "fecha_compra": pedido["fecha_compra"],
        "fecha_estimada_entrega": pedido["fecha_estimada_entrega"],
        "fecha_entrega": pedido["fecha_entrega"],
        "dias_desde_entrega": dias_desde_entrega(pedido),
        "transportadora": pedido["transportadora"],
        "enlace_rastreo": pedido["enlace_rastreo"],
        "nota": pedido["nota"] or None,
        "productos": productos_del_pedido(pedido),
    }
    return exito(datos, f"Pedido {pedido['numero_seguimiento']} encontrado: {pedido['estado']}.")
