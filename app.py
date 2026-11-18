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

import requests
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

# Precarga en el hilo principal de módulos que, si no, se importarían por primera vez
# desde hilos en paralelo (evita bloqueos de importación en servidores lentos).
import encodings.idna  # noqa: E402,F401
import json  # noqa: E402,F401
import zipfile  # noqa: E402,F401

import bs4  # noqa: E402,F401
import lxml.etree  # noqa: E402,F401

from scraper import extractor  # noqa: E402,F401
from scraper.descargador import Descargador
from scraper.categorias import SUPERMERCADOS, categorias_de
from scraper.servicio import comprimir_a_fichero, guardar, scrapear, url_publica

BASE = Path(__file__).parent
TRABAJOS_DIR = Path(os.environ.get("BOT_DATOS", Path(tempfile.gettempdir()) / "superbot"))
CLAVE = os.environ.get("BOT_CLAVE")
MAX_PRODUCTOS = int(os.environ.get("BOT_MAX_PRODUCTOS", "40"))
MAX_ESCANEO = int(os.environ.get("BOT_MAX_ESCANEO", "5000"))
CADUCIDAD = 12 * 3600  # se borran los trabajos de más de 12 h

# Orígenes web que pueden usar esta API desde otra página (p. ej. la app en GitHub Pages).
# Por defecto: cualquier *.github.io y localhost. "*" = todos.
ORIGENES = os.environ.get("BOT_ORIGENES", "https://*.github.io,http://localhost:*,http://127.0.0.1:*")

app = Flask(__name__, static_folder=str(BASE / "docs" / "app"), static_url_path="/static")


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
            (TRABAJOS_DIR / f"{tid}.zip").unlink(missing_ok=True)


def ejecutar(tid: str, urls, categorias, opciones):
    j = trabajos[tid]

    def log(msg):
        j["log"].append(msg)
        del j["log"][:-200]  # solo las últimas líneas (los escaneos generan miles)
        if msg.startswith("["):
            try:
                j["hecho"], j["total"] = (int(x) for x in msg[1:msg.index("]")].split("/"))
            except ValueError:
                pass

    d = Descargador(pausa=opciones["pausa"], usar_navegador=opciones["navegador"])
    try:
        productos, errores = scrapear(
            urls, categorias, j["carpeta"], max_por_categoria=opciones["max"],
            descargar_imagenes=opciones["imagenes"], max_imagenes=opciones["max_imagenes"],
            config=str(BASE / "selectores.json"), descargador=d, log=log,
            sitios=opciones.get("sitios"), max_escaneo=opciones["max"], filtro=opciones.get("filtro"),
            parar=j["parar"])
        guardar(productos, j["carpeta"])
        j.update(productos=productos, errores=errores, estado="terminado")
    except Exception as e:  # noqa: BLE001
        j.update(estado="error", errores=j["errores"] + [str(e)])
    finally:
        d.cerrar()


@app.get("/")
def inicio():
    return send_from_directory(BASE / "docs" / "app", "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(BASE / "docs" / "app", "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.get("/icono.svg")
def icono():
    return send_from_directory(BASE / "docs" / "app", "icono.svg")


@app.get("/api/estado")
def estado():
    return jsonify(clave=bool(CLAVE), max_productos=MAX_PRODUCTOS, max_escaneo=MAX_ESCANEO,
                   max_categoria=MAX_ESCANEO)


@app.post("/api/trabajos")
def crear():
    comprobar_clave()
    limpiar_antiguos()
    datos = request.get_json(force=True, silent=True) or {}
    lineas = [l.strip() for l in str(datos.get("urls", "")).splitlines() if l.strip()]
    modo = datos.get("modo")
    if not lineas:
        return jsonify(error="Escribe al menos una URL"), 400
    if len(lineas) > MAX_PRODUCTOS:
        return jsonify(error=f"Máximo {MAX_PRODUCTOS} URLs por vez"), 400
    malas = [u for u in lineas if not url_publica(u)]
    if malas:
        return jsonify(error=f"URL no válida: {malas[0]}"), 400

    tope = MAX_ESCANEO if modo in ("web", "categoria") else MAX_PRODUCTOS
    opciones = {
        "max": max(1, min(int(datos.get("max") or 20), tope)),
        "imagenes": bool(datos.get("imagenes", True)),
        "max_imagenes": max(1, min(int(datos.get("max_imagenes", 5)), 10)),
        "navegador": bool(datos.get("navegador", False)),
        "pausa": 1.5,
    }
    urls, categorias = lineas, []
    if modo == "categoria":
        urls, categorias = [], lineas
    elif modo == "web":
        urls = []
        opciones.update(sitios=lineas[:1], filtro=(str(datos.get("filtro") or "").strip() or None))
    tid = uuid.uuid4().hex[:12]
    carpeta = TRABAJOS_DIR / tid
    carpeta.mkdir(parents=True, exist_ok=True)
    trabajos[tid] = {"estado": "trabajando", "log": [], "productos": [], "errores": [],
                     "carpeta": carpeta, "creado": time.time(), "hecho": 0, "total": 0,
                     "parar": threading.Event()}
    threading.Thread(target=ejecutar, daemon=True, args=(tid, urls, categorias, opciones),
                     name=f"trabajo-web-{tid}").start()
    return jsonify(id=tid)


@app.get("/api/supermercados")
def supermercados():
    return jsonify(SUPERMERCADOS)


@app.get("/api/categorias")
def categorias():
    """Categorías de una web, por niveles para no cargar miles de golpe en el móvil.
    ?web=URL                -> categorías principales
    ?web=URL&padre=ID       -> subcategorías de ID
    ?web=URL&q=texto        -> búsqueda por nombre
    Cada nodo: {id, n(ombre), u(rl), p(adre), h(nº de subcategorías), r(uta)}"""
    comprobar_clave()
    web = (request.args.get("web") or "").strip()
    if not web.startswith("http"):
        web = "https://" + web
    if not url_publica(web):
        return jsonify(error=f"Dirección no válida: {web}"), 400
    try:
        arbol = categorias_de(web)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)), 502
    if not arbol.nodos:
        return jsonify(error="No he encontrado categorías en esa web. Puedes pegar el enlace "
                             "de una categoría a mano."), 404
    q = (request.args.get("q") or "").strip()
    padre = request.args.get("padre")
    if q:
        nodos = arbol.buscar(q)
    else:
        try:
            nodos = arbol.hijos_de(int(padre) if padre not in (None, "") else None)
        except (ValueError, IndexError):
            abort(404)
    salida = []
    for n in nodos:
        d = n.a_dict()
        d["r"] = arbol.ruta(n.id)
        if not n.url:
            d["us"] = arbol.urls_de(n.id)  # sin página propia: sus subcategorías
        salida.append(d)
    return jsonify(web=web, total=len(arbol.nodos), nodos=salida)


@app.post("/api/trabajos/<tid>/parar")
def parar(tid):
    comprobar_clave()
    j = trabajos.get(tid) or abort(404)
    j["parar"].set()
    return jsonify(ok=True)


@app.get("/api/trabajos/<tid>")
def consultar(tid):
    comprobar_clave()
    j = trabajos.get(tid) or abort(404)
    datos = {k: v for k, v in j.items() if k not in ("carpeta", "parar", "productos")}
    datos["log"] = j["log"][-20:]
    datos["total_productos"] = len(j["productos"])
    # Con miles de productos, la página solo necesita una muestra (el resto va en los ficheros)
    datos["productos"] = j["productos"][:100]
    return jsonify(datos)


@app.get("/api/trabajos/<tid>/imagen/<path:ruta>")
def imagen(tid, ruta):
    comprobar_clave()
    j = trabajos.get(tid) or abort(404)
    return send_from_directory(j["carpeta"], ruta)


@app.get("/api/trabajos/<tid>/zip")
def descargar_zip(tid):
    comprobar_clave()
    j = trabajos.get(tid) or abort(404)
    destino = TRABAJOS_DIR / f"{tid}.zip"
    if not destino.exists() or j["estado"] == "trabajando":
        comprimir_a_fichero(j["carpeta"], destino)
    return send_file(destino, mimetype="application/zip", as_attachment=True,
                     download_name=f"productos-{tid}.zip")


@app.get("/api/trabajos/<tid>/<fichero>")
def descargar_fichero(tid, fichero):
    comprobar_clave()
    if fichero not in ("productos.json", "productos.csv"):
        abort(404)
    j = trabajos.get(tid) or abort(404)
    return send_from_directory(j["carpeta"], fichero, as_attachment=True)


# ---------- Bot de Telegram integrado (opcional) ----------
# Si hay TELEGRAM_TOKEN, el mismo servidor atiende también el bot de Telegram:
#  - con dirección pública (RENDER_EXTERNAL_URL o BOT_URL_PUBLICA): modo webhook
#  - sin ella (PC, Termux): consulta a Telegram en segundo plano
TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_TOKEN") or "").strip().strip('"').strip("'")
URL_PUBLICA = (os.environ.get("BOT_URL_PUBLICA") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
bot_telegram = None
SECRETO_TG = ""
ESTADO_TG = {"configurado": bool(TELEGRAM_TOKEN), "modo": None, "arrancado": False, "error": None,
             "bot": None, "mensajes_recibidos": 0}

if TELEGRAM_TOKEN:
    import hashlib

    from telegram_bot import Bot, usuarios_permitidos

    bot_telegram = Bot(TELEGRAM_TOKEN, usuarios_permitidos())
    SECRETO_TG = hashlib.sha256(("superbot:" + TELEGRAM_TOKEN).encode()).hexdigest()[:32]

    def _arrancar_telegram():
        ESTADO_TG["modo"] = "webhook" if URL_PUBLICA else "polling"
        for intento in range(5):
            try:
                ESTADO_TG["bot"] = "@" + bot_telegram.tg.llamar("getMe")["username"]
                if URL_PUBLICA:
                    bot_telegram.activar_webhook(f"{URL_PUBLICA}/telegram/{SECRETO_TG}", SECRETO_TG)
                    ESTADO_TG.update(arrancado=True, error=None)
                    return
                ESTADO_TG.update(arrancado=True, error=None)
                bot_telegram.ejecutar()
                return
            except Exception as e:  # noqa: BLE001
                ESTADO_TG["error"] = str(e)
                print(f"No se pudo arrancar el bot de Telegram (intento {intento + 1}/5):", e, flush=True)
                time.sleep(10)



_telegram_iniciado = threading.Lock()
_telegram_listo = False


def iniciar_telegram():
    """Arranca el bot de Telegram UNA vez, cuando el servidor ya ha terminado de cargar.

    Lanzar hilos con red mientras el módulo aún se está importando puede dejarlos
    bloqueados para siempre (bloqueo de importación de Python), sobre todo en
    servidores lentos. Por eso se hace en la primera petición (el health check de
    Render llega a los pocos segundos) y no al importar.
    """
    global _telegram_listo
    if not bot_telegram or _telegram_listo:
        return
    with _telegram_iniciado:
        if _telegram_listo:
            return
        _telegram_listo = True
        threading.Thread(target=_arrancar_telegram, daemon=True, name="arranque-telegram").start()


def hay_trabajo_en_marcha() -> bool:
    return (any(j["estado"] == "trabajando" for j in list(trabajos.values()))
            or bool(bot_telegram and bot_telegram.ocupado))


def _mantener_despierto():
    """Render (plan gratuito) duerme el servidor tras 15 min sin peticiones de fuera, aunque
    esté trabajando. Mientras haya un escaneo en marcha, se hace una petición a sí mismo
    a través de su dirección pública cada 5 minutos. Sin trabajo, se deja dormir."""
    while True:
        time.sleep(300)
        if URL_PUBLICA and hay_trabajo_en_marcha():
            try:
                requests.get(f"{URL_PUBLICA}/salud", timeout=30)
            except requests.RequestException:
                pass


_despertador_iniciado = False


@app.before_request
def _antes_de_cada_peticion():
    global _despertador_iniciado
    iniciar_telegram()
    if URL_PUBLICA and not _despertador_iniciado:
        _despertador_iniciado = True
        threading.Thread(target=_mantener_despierto, daemon=True, name="mantener-despierto").start()


@app.post("/telegram/<secreto>")
def webhook_telegram(secreto):
    if (not bot_telegram or secreto != SECRETO_TG
            or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRETO_TG):
        abort(404)
    ESTADO_TG["mensajes_recibidos"] += 1
    update = request.get_json(force=True, silent=True) or {}
    # Responder a Telegram enseguida y procesar en segundo plano
    threading.Thread(target=bot_telegram.procesar, args=(update,), daemon=True).start()
    return "", 200


@app.get("/salud")
def salud():
    return "ok"


def hilos_en_espera() -> dict:
    """Dónde está esperando cada hilo (solo archivo:línea y función, sin datos)."""
    import sys
    import traceback as tb
    nombres = {t.ident: t.name for t in threading.enumerate()}
    salida = {}
    for ident, marco in sys._current_frames().items():
        nombre = nombres.get(ident, str(ident))
        if nombre.startswith(("MainThread", "ThreadPool")) or "gthread" in nombre:
            continue
        pila = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in tb.extract_stack(marco)]
        propias = [p for p in pila if p.split(":")[0] in
                   ("app.py", "telegram_bot.py", "servicio.py", "descargador.py", "extractor.py")]
        if propias:
            salida[nombre] = propias[-4:] + [pila[-1]]
    return salida


@app.get("/api/diagnostico")
def diagnostico():
    """Estado del servidor para encontrar problemas (no muestra claves ni tokens)."""
    info = {
        "servidor": "ok",
        "url_publica": URL_PUBLICA or None,
        "clave_activada": bool(CLAVE),
        "trabajos_en_memoria": len(trabajos),
        "telegram": dict(ESTADO_TG, usuarios_permitidos=len(bot_telegram.permitidos) if bot_telegram else 0,
                         ocupado={str(c): f"{int(time.time() - t)} s" for c, (t, *_) in bot_telegram.ocupado.items()}
                         if bot_telegram else {},
                         ultimos_mensajes=list(bot_telegram.ultimos_mensajes) if bot_telegram else [],
                         ultimos_errores=list(bot_telegram.errores) if bot_telegram else []),
    }
    if bot_telegram:
        try:
            w = bot_telegram.tg.llamar("getWebhookInfo", timeout=15)
            w = w if isinstance(w, dict) else {}
            info["telegram"]["webhook"] = {
                "registrado": bool(w.get("url")),
                "apunta_a_este_servidor": bool(URL_PUBLICA) and w.get("url", "").startswith(URL_PUBLICA),
                "mensajes_pendientes": w.get("pending_update_count"),
                "ultimo_error": w.get("last_error_message"),
            }
        except Exception as e:  # noqa: BLE001
            info["telegram"]["webhook"] = {"error_consultando": str(e)}
    info["hilos"] = hilos_en_espera()
    try:
        r = requests.get("https://www.ahorramas.com/robots.txt", timeout=15)
        info["internet"] = f"ok ({r.status_code})"
    except Exception as e:  # noqa: BLE001
        info["internet"] = f"error: {e}"
    return jsonify(info)


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", "8000"))
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "TU-IP"
    print(f"\n  Abre en este dispositivo:  http://localhost:{puerto}")
    print(f"  Desde otro móvil en tu wifi: http://{ip}:{puerto}\n")
    iniciar_telegram()
    app.run(host="0.0.0.0", port=puerto, threaded=True)
