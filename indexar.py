"""Construye la base de conocimiento (RAG del Taller 2): extracción → chunking → embeddings → Qdrant.

Uso:
    python indexar.py                # indexa (o reindexa) todo
    python indexar.py --si-no-existe # solo indexa si la colección no existe (lo usa Docker al arrancar)
"""

import sys
from collections import Counter

from src import config
from src.rag.extraccion import cargar_todo
from src.rag.indexacion import ErrorBaseConocimiento, coleccion_lista, crear_indice
from src.rag.segmentacion import segmentar_todo


def main() -> int:
    try:
        if "--si-no-existe" in sys.argv and coleccion_lista():
            print(f"✅ La colección '{config.COLECCION}' ya existe; no se reindexa.")
            return 0
        documentos = cargar_todo()
        fragmentos = segmentar_todo(documentos)
        print(f"Documentos: { {k: len(v) for k, v in documentos.items()} }")
        print(f"Fragmentos: {len(fragmentos)} {dict(Counter(f.metadata['tipo_documento'] for f in fragmentos))}")
        print(f"Generando embeddings con '{config.MODELO_EMBEDDINGS}' y cargando en Qdrant "
              f"({config.QDRANT_URL or config.CARPETA_QDRANT})...")
        almacen, segundos = crear_indice(fragmentos)
        print(f"✅ {almacen.client.count(config.COLECCION).count} vectores indexados en {segundos:.1f} s.")
        return 0
    except ErrorBaseConocimiento as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
