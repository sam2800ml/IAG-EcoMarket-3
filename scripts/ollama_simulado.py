"""Servidor HTTP que imita la API de Ollama (/api/chat, /api/embed, /api/tags) con los modelos simulados.

Prueba el camino REAL de la app: ChatOllama y OllamaEmbeddings -> HTTP -> herramientas -> resultados,
sin GPU ni modelos descargados. Así se encontró que el cliente de Ollama descarta el tool_call_id.

    python scripts/ollama_simulado.py                      # escucha en el puerto 11500
    OLLAMA_HOST=http://127.0.0.1:11500 RAG_UMBRAL=0.15 python indexar.py
    OLLAMA_HOST=http://127.0.0.1:11500 RAG_UMBRAL=0.15 python -m app.main

Las decisiones vienen de reglas fijas (pruebas/modelo_simulado.py): no evalúa la calidad de un modelo real.
"""
import json
import sys
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pruebas.modelo_simulado import EmbeddingsSimulados, ModeloAgenteSimulado, ModeloJSONSimulado, ModeloTextoSimulado

AGENTE, TEXTO, JSONM, EMB = ModeloAgenteSimulado(), ModeloTextoSimulado(), ModeloJSONSimulado(), EmbeddingsSimulados()
LLAMADAS = {"chat_con_tools": 0, "chat_json": 0, "chat_texto": 0, "embed": 0}


def a_langchain(mensajes):
    nombres, salida = {}, []
    for m in mensajes:
        rol, contenido = m.get("role"), m.get("content") or ""
        if rol == "system":
            salida.append(SystemMessage(contenido))
        elif rol == "user":
            salida.append(HumanMessage(contenido))
        elif rol == "assistant":
            tcs = []
            for i, tc in enumerate(m.get("tool_calls") or []):
                f = tc["function"]; args = f["arguments"]
                args = json.loads(args) if isinstance(args, str) else args
                ident = tc.get("id") or f"x{i}"
                nombres[ident] = f["name"]
                tcs.append({"name": f["name"], "args": args, "id": ident, "type": "tool_call"})
            salida.append(AIMessage(contenido, tool_calls=tcs))
        elif rol == "tool":
            ident = m.get("tool_call_id") or ""
            try:
                nombre = json.loads(contenido).get("herramienta")
            except Exception:
                nombre = None
            salida.append(ToolMessage(contenido, tool_call_id=ident or "?", name=m.get("tool_name") or nombre))
    return salida


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, obj, code=200):
        cuerpo = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo))); self.end_headers(); self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            return self._json({"models": [{"name": n} for n in ("qwen3:4b", "bge-m3:latest")]})
        if self.path.startswith("/api/version"):
            return self._json({"version": "0.99.0"})
        self._json({"error": "no"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path.startswith("/api/embed"):
            LLAMADAS["embed"] += 1
            entradas = body.get("input"); entradas = [entradas] if isinstance(entradas, str) else entradas
            return self._json({"model": body.get("model"), "embeddings": EMB.embed_documents(entradas)})
        if self.path.startswith("/api/show"):
            return self._json({"capabilities": ["completion", "tools"], "model_info": {}})
        if self.path.startswith("/api/chat"):
            msgs = a_langchain(body.get("messages", []))
            if body.get("tools"):
                LLAMADAS["chat_con_tools"] += 1; r = AGENTE.invoke(msgs)
            elif body.get("format") == "json":
                LLAMADAS["chat_json"] += 1; r = JSONM.invoke(msgs)
            else:
                LLAMADAS["chat_texto"] += 1; r = TEXTO.invoke(msgs)
            mensaje = {"role": "assistant", "content": r.content or ""}
            if r.tool_calls:
                mensaje["tool_calls"] = [{"function": {"name": tc["name"], "arguments": tc["args"]}} for tc in r.tool_calls]
            base = {"model": body.get("model"), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
            if body.get("stream", True):
                lineas = [json.dumps({**base, "message": mensaje, "done": False}),
                          json.dumps({**base, "message": {"role": "assistant", "content": ""}, "done": True,
                                      "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5})]
                cuerpo = ("\n".join(lineas) + "\n").encode()
                self.send_response(200); self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(cuerpo))); self.end_headers(); self.wfile.write(cuerpo)
                return
            return self._json({**base, "message": mensaje, "done": True, "done_reason": "stop"})
        self._json({"error": "no"}, 404)


if __name__ == "__main__":
    print("Ollama simulado escuchando en http://127.0.0.1:11500", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 11500), H).serve_forever()
