"""Registro de operación en SQLite: acciones del agente, devoluciones y tickets.

Es la base del monitoreo (Fase 3): cada llamada a una herramienta queda
registrada con sus argumentos, su resultado y su duración, y el panel de
operación de la interfaz lee de aquí.

Los correos se guardan enmascarados (l***@correo.example): el registro sirve para
auditar, no necesita el dato completo (minimización de datos).
"""

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from src import config

_RE_CORREO = re.compile(r"([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+)")


def enmascarar(texto: str) -> str:
    return _RE_CORREO.sub(r"\1***\2", texto or "")


class Registro:
    def __init__(self, ruta: Path | str = config.BD_OPERACION):
        if str(ruta) != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(ruta), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._candado = threading.Lock()
        with self._candado, self._con:
            self._con.executescript("""
                CREATE TABLE IF NOT EXISTS acciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha TEXT, hilo TEXT, herramienta TEXT, argumentos TEXT,
                    ok INTEGER, codigo_error TEXT, mensaje TEXT, duracion_ms INTEGER);
                CREATE TABLE IF NOT EXISTS devoluciones (
                    codigo_devolucion TEXT PRIMARY KEY, numero_seguimiento TEXT, id_producto TEXT,
                    datos TEXT, fecha_creacion TEXT,
                    UNIQUE (numero_seguimiento, id_producto));
                CREATE TABLE IF NOT EXISTS tickets (
                    codigo_ticket TEXT PRIMARY KEY, fecha TEXT, hilo TEXT, prioridad TEXT,
                    motivo TEXT, numero_seguimiento TEXT, resumen TEXT);
            """)
        self._sembrar_devoluciones()

    def _sembrar_devoluciones(self) -> None:
        """Carga las devoluciones que ya existían antes de iniciar (caso de idempotencia)."""
        if not config.ARCHIVO_DEVOLUCIONES_INICIALES.exists():
            return
        for d in json.loads(config.ARCHIVO_DEVOLUCIONES_INICIALES.read_text(encoding="utf-8")):
            with self._candado, self._con:
                self._con.execute(
                    "INSERT OR IGNORE INTO devoluciones VALUES (?, ?, ?, ?, ?)",
                    (d["codigo_devolucion"], d["numero_seguimiento"], d["id_producto"],
                     json.dumps(d, ensure_ascii=False), d["fecha_creacion"]))

    # ------------------------------------------------------------------ acciones
    def registrar_accion(self, hilo: str, herramienta: str, argumentos: dict, resultado: dict,
                         duracion_ms: int) -> None:
        with self._candado, self._con:
            self._con.execute(
                "INSERT INTO acciones (fecha, hilo, herramienta, argumentos, ok, codigo_error, mensaje, duracion_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), hilo, herramienta,
                 enmascarar(json.dumps(argumentos, ensure_ascii=False)), int(bool(resultado.get("ok"))),
                 resultado.get("codigo_error"), enmascarar(resultado.get("mensaje", ""))[:300], duracion_ms))

    def acciones(self, limite: int = 200) -> list[dict]:
        filas = self._con.execute("SELECT * FROM acciones ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(f) for f in filas]

    # ------------------------------------------------------------------ devoluciones
    def buscar_devolucion(self, numero: str, id_producto: str) -> dict | None:
        fila = self._con.execute(
            "SELECT datos FROM devoluciones WHERE numero_seguimiento = ? AND id_producto = ?",
            (numero, id_producto)).fetchone()
        return json.loads(fila["datos"]) if fila else None

    def guardar_devolucion(self, datos: dict) -> None:
        with self._candado, self._con:
            self._con.execute("INSERT OR IGNORE INTO devoluciones VALUES (?, ?, ?, ?, ?)",
                              (datos["codigo_devolucion"], datos["numero_seguimiento"], datos["id_producto"],
                               json.dumps(datos, ensure_ascii=False), datos["fecha_creacion"]))

    def siguiente_codigo(self, prefijo: str, tabla: str, columna: str) -> str:
        total = self._con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 (nombres fijos)
        codigo = f"{prefijo}-{config.FECHA_HOY.year}-{total + 1:04d}"
        while self._con.execute(f"SELECT 1 FROM {tabla} WHERE {columna} = ?", (codigo,)).fetchone():  # noqa: S608
            total += 1
            codigo = f"{prefijo}-{config.FECHA_HOY.year}-{total + 1:04d}"
        return codigo

    def devoluciones(self) -> list[dict]:
        filas = self._con.execute("SELECT datos FROM devoluciones ORDER BY fecha_creacion DESC, codigo_devolucion DESC")
        return [json.loads(f["datos"]) for f in filas.fetchall()]

    # ------------------------------------------------------------------ tickets
    def guardar_ticket(self, ticket: dict) -> None:
        with self._candado, self._con:
            self._con.execute("INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (ticket["codigo_ticket"], ticket["fecha"], ticket["hilo"], ticket["prioridad"],
                               ticket["motivo"], ticket.get("numero_seguimiento"), enmascarar(ticket["resumen"])))

    def tickets(self) -> list[dict]:
        return [dict(f) for f in self._con.execute("SELECT * FROM tickets ORDER BY fecha DESC").fetchall()]

    # ------------------------------------------------------------------ métricas para el panel
    def metricas(self) -> dict:
        c = self._con
        total = c.execute("SELECT COUNT(*) FROM acciones").fetchone()[0]
        errores = c.execute("SELECT COUNT(*) FROM acciones WHERE ok = 0").fetchone()[0]
        por_herramienta = [dict(f) for f in c.execute(
            "SELECT herramienta, COUNT(*) AS llamadas, SUM(1 - ok) AS errores, "
            "CAST(AVG(duracion_ms) AS INTEGER) AS ms_promedio FROM acciones GROUP BY herramienta "
            "ORDER BY llamadas DESC").fetchall()]
        por_error = {f["codigo_error"]: f["n"] for f in c.execute(
            "SELECT codigo_error, COUNT(*) AS n FROM acciones WHERE ok = 0 GROUP BY codigo_error").fetchall()}
        return {
            "acciones": total, "errores": errores, "tasa_error": (errores / total) if total else 0.0,
            "devoluciones": c.execute("SELECT COUNT(*) FROM devoluciones").fetchone()[0],
            "tickets": c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
            "por_herramienta": por_herramienta, "por_error": por_error,
        }


_registro: Registro | None = None


def obtener_registro() -> Registro:
    global _registro
    if _registro is None:
        _registro = Registro()
    return _registro


def usar_registro(registro: Registro) -> None:
    """Permite inyectar otro registro (por ejemplo, uno en memoria para las pruebas)."""
    global _registro
    _registro = registro
