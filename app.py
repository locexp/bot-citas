#!/usr/bin/env python3
"""Interfaz web (pensada para el móvil) del bot scraper.

  python app.py                 -> http://localhost:8000
  BOT_CLAVE=secreta python app.py   (pide clave; recomendable si la publicas en Internet)
"""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from scraper.descargador import Descargador
from scraper.servicio import comprimir, guardar, scrapear, url_publica

BASE = Path(__file__).parent
TRABAJOS_DIR = Path(os.environ.get("BOT_DATOS", Path(tempfile.gettempdir()) / "superbot"))
CLAVE = os.environ.get("BOT_CLAVE")
MAX_PRODUCTOS = int(os.environ.get("BOT_MAX_PRODUCTOS", "40"))
CADUCIDAD = 6 * 3600  # se borran los trabajos de más de 6 h

# Orígenes web que pueden usar esta API desde otra página (p. ej. la app en GitHub Pages).
# Por defecto: cualquier *.github.io y localhost. "*" = todos.
ORIGENES = os.environ.get("BOT_ORIGENES", "https://*.github.io,http://localhost:*,http://127.0.0.1:*")

app = Flask(__name__, static_folder=str(BASE / "web"), static_url_path="/static")


def origen_permitido(origen: str) -> bool:
    import fnmatch
    return any(p.strip() == "*" or fnmatch.fnmatch(origen, p.strip()) for p in ORIGENES.split(","))


@app.after_request
def cors(resp):
    origen = request.headers.get("Origin")
    if origen and origen_permitido(origen):
        resp.headers["Access-Control-Allow-Origin"] = origen
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Clave"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        # Chrome: permitir que una web pública hable con el bot en tu propia red/móvil
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


@app.route("/api/<path:_ruta>", methods=["OPTIONS"])
def preflight(_ruta):
    return "", 204
trabajos: dict[str, dict] = {}
cerrojo = threading.Lock()


def comprobar_clave():
    if CLAVE and request.headers.get("X-Clave") != CLAVE and request.args.get("clave") != CLAVE:
        abort(401)


def limpiar_antiguos():
    ahora = time.time()
    with cerrojo:
        for tid in [t for t, j in trabajos.items() if ahora - j["creado"] > CADUCIDAD]:
            shutil.rmtree(trabajos.pop(tid)["carpeta"], ignore_errors=True)


def ejecutar(tid: str, urls, categorias, opciones):
    j = trabajos[tid]
    log = lambda msg: j["log"].append(msg)
    d = Descargador(pausa=opciones["pausa"], usar_navegador=opciones["navegador"])
    try:
        productos, errores = scrapear(
            urls, categorias, j["carpeta"], max_por_categoria=opciones["max"],
            descargar_imagenes=opciones["imagenes"], max_imagenes=opciones["max_imagenes"],
            config=str(BASE / "selectores.json"), descargador=d, log=log)
        guardar(productos, j["carpeta"])
        j.update(productos=productos, errores=errores, estado="terminado")
    except Exception as e:  # noqa: BLE001
        j.update(estado="error", errores=j["errores"] + [str(e)])
    finally:
        d.cerrar()


@app.get("/")
def inicio():
    return send_from_directory(BASE / "web", "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(BASE / "web", "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.get("/icono.svg")
def icono():
    return send_from_directory(BASE / "web", "icono.svg")


@app.get("/api/estado")
def estado():
    return jsonify(clave=bool(CLAVE), max_productos=MAX_PRODUCTOS)


@app.post("/api/trabajos")
def crear():
    comprobar_clave()
    limpiar_antiguos()
    datos = request.get_json(force=True, silent=True) or {}
    lineas = [l.strip() for l in str(datos.get("urls", "")).splitlines() if l.strip()]
    if not lineas:
        return jsonify(error="Escribe al menos una URL"), 400
    if len(lineas) > MAX_PRODUCTOS:
        return jsonify(error=f"Máximo {MAX_PRODUCTOS} URLs por vez"), 400
    malas = [u for u in lineas if not url_publica(u)]
    if malas:
        return jsonify(error=f"URL no válida: {malas[0]}"), 400

    es_categoria = datos.get("modo") == "categoria"
    opciones = {
        "max": max(1, min(int(datos.get("max", 20)), MAX_PRODUCTOS)),
        "imagenes": bool(datos.get("imagenes", True)),
        "max_imagenes": max(1, min(int(datos.get("max_imagenes", 5)), 10)),
        "navegador": bool(datos.get("navegador", False)),
        "pausa": 1.5,
    }
    tid = uuid.uuid4().hex[:12]
    carpeta = TRABAJOS_DIR / tid
    carpeta.mkdir(parents=True, exist_ok=True)
    trabajos[tid] = {"estado": "trabajando", "log": [], "productos": [], "errores": [],
                     "carpeta": carpeta, "creado": time.time()}
    threading.Thread(
        target=ejecutar, daemon=True,
        args=(tid, [] if es_categoria else lineas, lineas if es_categoria else [], opciones),
    ).start()
    return jsonify(id=tid)


@app.get("/api/trabajos/<tid>")
def consultar(tid):
    comprobar_clave()
    j = trabajos.get(tid) or abort(404)
    return jsonify({k: v for k, v in j.items() if k != "carpeta"})


@app.get("/api/trabajos/<tid>/imagen/<path:ruta>")
def imagen(tid, ruta):
    comprobar_clave()
    j = trabajos.get(tid) or abort(404)
    return send_from_directory(j["carpeta"], ruta)


@app.get("/api/trabajos/<tid>/zip")
def descargar_zip(tid):
    comprobar_clave()
    j = trabajos.get(tid) or abort(404)
    import io
    return send_file(io.BytesIO(comprimir(j["carpeta"])), mimetype="application/zip",
                     as_attachment=True, download_name=f"productos-{tid}.zip")


@app.get("/api/trabajos/<tid>/<fichero>")
def descargar_fichero(tid, fichero):
    comprobar_clave()
    if fichero not in ("productos.json", "productos.csv"):
        abort(404)
    j = trabajos.get(tid) or abort(404)
    return send_from_directory(j["carpeta"], fichero, as_attachment=True)


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", "8000"))
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "TU-IP"
    print(f"\n  Abre en este dispositivo:  http://localhost:{puerto}")
    print(f"  Desde otro móvil en tu wifi: http://{ip}:{puerto}\n")
    app.run(host="0.0.0.0", port=puerto, threaded=True)
