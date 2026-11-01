"""Pasos 2 y 3 de la indexación: SEGMENTACIÓN (chunking) y ENRIQUECIMIENTO de metadatos.

Estrategia elegida para EcoMarket: segmentación RECURSIVA CONSCIENTE DE LA ESTRUCTURA.

1. Documentos narrativos (políticas en PDF):
   - Primero se respeta la estructura del documento: cada subsección (1.1, 1.2, ...)
     es una unidad independiente (ver src/extraccion.py).
   - Si una subsección supera TAMANO_CHUNK, RecursiveCharacterTextSplitter la divide
     probando separadores de mayor a menor: párrafo -> línea -> oración -> coma -> palabra.
   - Se conserva un solapamiento (~15 %) para no perder el contexto en los bordes.
   - A cada fragmento se le antepone un encabezado con la política y la subsección
     ("contextual chunk header"), para que un fragmento suelto siga teniendo sentido.

2. Documentos estructurados (catálogo CSV y preguntas frecuentes JSON):
   - Cada registro (un producto, un par pregunta-respuesta) ya es una unidad de sentido
     corta, así que se indexa completo. Cortarlo mezclaría el precio de un producto con
     la descripción de otro, o separaría una pregunta de su respuesta.

Este módulo también contiene las estrategias alternativas (tamaño fijo, por párrafos y
recursiva sin estructura) que se comparan en experimentos.py.
"""

import hashlib
from datetime import date

from langchain_core.documents import Document
from langchain_text_splitters import CharacterTextSplitter, RecursiveCharacterTextSplitter

from src.config import SOLAPAMIENTO, TAMANO_CHUNK

SEPARADORES = ["\n\n", "\n", ". ", "; ", ", ", " ", ""]


def crear_splitter(tamano: int = TAMANO_CHUNK, solapamiento: int = SOLAPAMIENTO) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=tamano,
        chunk_overlap=solapamiento,
        separators=SEPARADORES,
        keep_separator="end",      # el punto queda al final de la oración, no al inicio de la siguiente
        strip_whitespace=True,
    )


def _encabezado(metadata: dict) -> str:
    return f"[{metadata['politica']} > {metadata['seccion']} {metadata['titulo_seccion']}]"


def segmentar_politicas(secciones: list[Document], tamano: int = TAMANO_CHUNK,
                        solapamiento: int = SOLAPAMIENTO) -> list[Document]:
    splitter = crear_splitter(tamano, solapamiento)
    fragmentos = []
    for seccion in secciones:
        partes = splitter.split_text(seccion.page_content)
        for indice, parte in enumerate(partes, start=1):
            fragmentos.append(Document(
                page_content=f"{_encabezado(seccion.metadata)}\n{parte}",
                metadata={**seccion.metadata, "parte": indice, "total_partes": len(partes)},
            ))
    return fragmentos


def segmentar_registros(registros: list[Document], tamano: int = TAMANO_CHUNK,
                        solapamiento: int = SOLAPAMIENTO) -> list[Document]:
    """Registros estructurados: se dejan completos salvo que sean anormalmente largos."""
    splitter = crear_splitter(tamano * 2, solapamiento)  # tolerancia mayor: partirlos es el último recurso
    fragmentos = []
    for registro in registros:
        partes = splitter.split_text(registro.page_content)
        for indice, parte in enumerate(partes, start=1):
            fragmentos.append(Document(page_content=parte,
                                       metadata={**registro.metadata, "parte": indice, "total_partes": len(partes)}))
    return fragmentos


def enriquecer_metadatos(fragmentos: list[Document]) -> list[Document]:
    """Agrega metadatos comunes: identificador estable, longitud y fecha de indexación.

    El identificador se calcula a partir del contenido (hash), así que volver a indexar
    el mismo fragmento lo actualiza en lugar de duplicarlo.
    """
    hoy = date.today().isoformat()
    for fragmento in fragmentos:
        huella = hashlib.sha1(
            (fragmento.metadata["fuente"] + fragmento.page_content).encode("utf-8")
        ).hexdigest()
        fragmento.metadata.update({
            "chunk_id": huella[:16],
            "num_caracteres": len(fragmento.page_content),
            "fecha_indexacion": hoy,
        })
    return fragmentos


def segmentar_todo(documentos: dict[str, list[Document]], tamano: int = TAMANO_CHUNK,
                   solapamiento: int = SOLAPAMIENTO) -> list[Document]:
    fragmentos = (
        segmentar_politicas(documentos["politicas"], tamano, solapamiento)
        + segmentar_registros(documentos["catalogo"], tamano, solapamiento)
        + segmentar_registros(documentos["faq"], tamano, solapamiento)
    )
    return enriquecer_metadatos(fragmentos)


# ----------------------------------------------------------------------------
# Estrategias alternativas (para el experimento de comparación)
# ----------------------------------------------------------------------------
def estrategia_tamano_fijo(texto: str, tamano: int = TAMANO_CHUNK) -> list[str]:
    """Corta cada N caracteres exactos, sin solapamiento y sin mirar el contenido."""
    return CharacterTextSplitter(separator="", chunk_size=tamano, chunk_overlap=0,
                                 strip_whitespace=False).split_text(texto)


def estrategia_parrafos(texto: str) -> list[str]:
    """Un fragmento por párrafo, sin importar si es muy largo o muy corto."""
    return [p.strip() for p in texto.split("\n\n") if p.strip()]


def estrategia_recursiva(texto: str, tamano: int = TAMANO_CHUNK, solapamiento: int = SOLAPAMIENTO) -> list[str]:
    """Recursiva sobre el texto completo, sin usar la estructura de secciones."""
    return crear_splitter(tamano, solapamiento).split_text(texto)


def estrategia_estructural_recursiva(secciones: list[Document], tamano: int = TAMANO_CHUNK,
                                     solapamiento: int = SOLAPAMIENTO) -> list[str]:
    """La estrategia elegida (la misma que usa la indexación real)."""
    return [d.page_content for d in segmentar_politicas(secciones, tamano, solapamiento)]
