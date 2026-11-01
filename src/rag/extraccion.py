"""Paso 1 de la indexación: EXTRACCIÓN.

Convierte cada tipo de documento de EcoMarket en objetos `Document` de LangChain
(texto + metadatos). Cada formato necesita un cargador distinto:

    PDF  (políticas)          -> PyPDFLoader + reconstrucción de secciones
    CSV  (catálogo/inventario)-> un Document por producto
    JSON (preguntas frecuentes)-> un Document por pregunta-respuesta

Los pedidos (data/pedidos.json) NO se extraen aquí: son datos transaccionales
que cambian a cada minuto y se consultan con una herramienta (src/herramientas.py).
"""

import csv
import json
import re
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document

from src.config import CARPETA_DOCUMENTOS

ARCHIVO_POLITICAS = CARPETA_DOCUMENTOS / "politicas_ecomarket.pdf"
ARCHIVO_CATALOGO = CARPETA_DOCUMENTOS / "catalogo_inventario.csv"
ARCHIVO_FAQ = CARPETA_DOCUMENTOS / "preguntas_frecuentes.json"

RE_SUBSECCION = re.compile(r"^(\d+\.\d+)\s+(.+)$")
LINEAS_ENCABEZADO = ("EcoMarket S.A.S.", "Versión ")  # encabezado del PDF, no aporta al RAG

# Título de cada política en el PDF -> identificador corto que se guarda como metadato.
POLITICAS = {
    "DEVOLUCIONES": "devoluciones",
    "ENVÍOS": "envios",
    "GARANTÍAS": "garantias",
    "PAGO": "pagos",
    "PRIVACIDAD": "privacidad",
}

NOMBRES_CATEGORIA = {
    "hogar_cocina": "Hogar y cocina", "higiene_personal": "Higiene personal",
    "alimentos_perecederos": "Alimentos perecederos", "hogar": "Hogar", "accesorios": "Accesorios",
    "tecnologia": "Tecnología", "jardin": "Jardín", "textil_hogar": "Textiles para el hogar",
    "personalizados": "Personalizados", "tarjetas_regalo": "Tarjetas regalo",
}


# ----------------------------------------------------------------------------
# PDF de políticas
# ----------------------------------------------------------------------------
def texto_pdf(ruta: Path = ARCHIVO_POLITICAS) -> str:
    """Extrae el texto del PDF conservando los saltos de párrafo.

    El modo "layout" de pypdf respeta los espacios verticales del documento, así que
    los párrafos quedan separados por una línea en blanco. Luego se unen las líneas
    de un mismo párrafo (el PDF corta las líneas por ancho de página, no por sentido).
    """
    paginas = PyPDFLoader(str(ruta), extraction_mode="layout").load()
    texto = "\n\n".join(pagina.page_content for pagina in paginas)

    parrafos = []
    for bloque in re.split(r"\n\s*\n", texto):
        lineas = [linea.strip() for linea in bloque.splitlines() if linea.strip()]
        if not lineas or lineas[0].startswith(LINEAS_ENCABEZADO):
            continue
        parrafos.append(re.sub(r"\s+", " ", " ".join(lineas)))
    return "\n\n".join(parrafos)


def _identificar_politica(titulo: str) -> str | None:
    if not (titulo.isupper() and len(titulo) < 60):
        return None
    return next((clave for palabra, clave in POLITICAS.items() if palabra in titulo), None)


def cargar_politicas(ruta: Path = ARCHIVO_POLITICAS) -> list[Document]:
    """Devuelve un Document por SUBSECCIÓN (1.1, 1.2...), con sus metadatos.

    Usar la estructura propia del documento (política > subsección) como primera
    división evita que un fragmento mezcle, por ejemplo, el plazo de devolución con
    los tiempos de envío.
    """
    documentos: list[Document] = []
    politica, titulo_politica, seccion, titulo_seccion, cuerpo = None, None, None, None, []

    def cerrar_seccion():
        if seccion and cuerpo:
            documentos.append(Document(
                page_content="\n\n".join(cuerpo),
                metadata={
                    "fuente": ruta.name,
                    "tipo_documento": "politica",
                    "tema": politica,
                    "politica": titulo_politica.capitalize(),
                    "seccion": seccion,
                    "titulo_seccion": titulo_seccion,
                },
            ))

    for parrafo in texto_pdf(ruta).split("\n\n"):
        nueva_politica = _identificar_politica(parrafo)
        subseccion = RE_SUBSECCION.match(parrafo)
        if nueva_politica:
            cerrar_seccion()
            politica, titulo_politica, seccion, cuerpo = nueva_politica, parrafo, None, []
        elif subseccion and len(parrafo) < 90:
            cerrar_seccion()
            seccion, titulo_seccion, cuerpo = subseccion.group(1), subseccion.group(2), []
        elif seccion:
            cuerpo.append(parrafo)
    cerrar_seccion()
    return documentos


# ----------------------------------------------------------------------------
# CSV de catálogo e inventario
# ----------------------------------------------------------------------------
def _texto_producto(fila: dict) -> str:
    precio = f"{int(fila['precio_cop']):,}".replace(",", ".")
    stock = int(fila["stock"])
    disponibilidad = "Agotado" if stock == 0 else f"Disponible ({stock} unidades)"
    garantia = f"{fila['garantia_meses']} meses" if int(fila["garantia_meses"]) else "No aplica"
    return (
        f"Producto: {fila['nombre']}\n"
        f"Categoría: {NOMBRES_CATEGORIA.get(fila['categoria'], fila['categoria'])}\n"
        f"Precio: {precio} pesos colombianos\n"
        f"Disponibilidad: {disponibilidad}\n"
        f"Material: {fila['material']}\n"
        f"Se puede devolver: {'Sí' if fila['retornable'] == 'si' else 'No'}\n"
        f"Garantía: {garantia}\n"
        f"Descripción: {fila['descripcion']}"
    )


def cargar_catalogo(ruta: Path = ARCHIVO_CATALOGO) -> list[Document]:
    """Un Document por producto: cada fila de la hoja de cálculo es una unidad de sentido."""
    with open(ruta, encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    return [
        Document(
            page_content=_texto_producto(fila),
            metadata={
                "fuente": ruta.name,
                "tipo_documento": "producto",
                "tema": "productos",
                "id_producto": fila["id_producto"],
                "nombre": fila["nombre"],
                "categoria": fila["categoria"],
                "precio_cop": int(fila["precio_cop"]),
                "stock": int(fila["stock"]),
                "retornable": fila["retornable"] == "si",
            },
        )
        for fila in filas
    ]


def productos_por_id(ruta: Path = ARCHIVO_CATALOGO) -> dict[str, dict]:
    """Acceso estructurado al catálogo (lo usa la herramienta de pedidos)."""
    with open(ruta, encoding="utf-8") as archivo:
        return {fila["id_producto"]: fila for fila in csv.DictReader(archivo)}


# ----------------------------------------------------------------------------
# JSON de preguntas frecuentes
# ----------------------------------------------------------------------------
def cargar_faq(ruta: Path = ARCHIVO_FAQ) -> list[Document]:
    """Un Document por pregunta-respuesta: separarlas rompería el par."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return [
        Document(
            page_content=f"Pregunta: {item['pregunta']}\nRespuesta: {item['respuesta']}",
            metadata={
                "fuente": ruta.name,
                "tipo_documento": "faq",
                "tema": item["tema"],
                "id_faq": item["id"],
                "actualizado": datos["actualizado"],
            },
        )
        for item in datos["preguntas"]
    ]


def cargar_todo() -> dict[str, list[Document]]:
    return {
        "politicas": cargar_politicas(),
        "catalogo": cargar_catalogo(),
        "faq": cargar_faq(),
    }
