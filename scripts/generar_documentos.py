"""Genera el PDF de políticas a partir de su fuente en Markdown.

Solo hace falta ejecutarlo si se modifica data/fuentes/politicas_ecomarket.md.
El PDF resultante ya viene incluido en el repositorio.

Uso:
    python herramientas/generar_documentos.py
"""

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "data" / "fuentes" / "politicas_ecomarket.md"
DESTINO = RAIZ / "data" / "documentos" / "politicas_ecomarket.pdf"


def main() -> None:
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("titulo", parent=estilos["Heading1"], fontSize=15, spaceBefore=14, spaceAfter=8)
    subtitulo = ParagraphStyle("subtitulo", parent=estilos["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=4)
    cuerpo = ParagraphStyle("cuerpo", parent=estilos["BodyText"], fontSize=10, leading=14, spaceAfter=6)

    elementos = [
        Paragraph("EcoMarket S.A.S. · Políticas de servicio al cliente", estilos["Title"]),
        Paragraph("Versión 3.2 · Vigente desde el 1 de septiembre de 2026", cuerpo),
        Spacer(1, 0.4 * cm),
    ]
    for linea in FUENTE.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith("## "):
            elementos.append(Paragraph(linea[3:], subtitulo))
        elif linea.startswith("# "):
            elementos.append(Paragraph(linea[2:], titulo))
        else:
            elementos.append(Paragraph(linea, cuerpo))

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(DESTINO), pagesize=letter, title="Políticas EcoMarket",
                      leftMargin=2.2 * cm, rightMargin=2.2 * cm).build(elementos)
    print(f"PDF generado: {DESTINO.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
