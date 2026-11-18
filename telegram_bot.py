#!/usr/bin/env python3
"""Bot de Telegram para el scraper de supermercado.

Uso:
  1. Crea un bot con @BotFather en Telegram y copia el token.
  2. TELEGRAM_TOKEN=123:ABC python telegram_bot.py
  3. Escribe a tu bot: pega un enlace de producto o usa /categoria <enlace>.

Variables de entorno:
  TELEGRAM_TOKEN      (obligatoria) token de @BotFather
  TELEGRAM_USUARIOS   ids de usuario permitidos, separados por comas (recomendado).
                      Si no se pone, cualquiera que encuentre el bot podrá usarlo.
                      Tu id te lo dice el propio bot con /yo.
  BOT_MAX_PRODUCTOS   máximo de productos por categoría (40)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from collections import deque
from pathlib import Path

import requests

from scraper.categorias import SUPERMERCADOS, categorias_de
from scraper.descargador import Descargador
from scraper.servicio import comprimir_por_partes, guardar, scrapear, url_publica

API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
MAX_PRODUCTOS = int(os.environ.get("BOT_MAX_PRODUCTOS", "40"))
LIMITE_TRABAJO = int(os.environ.get("BOT_LIMITE_TRABAJO", "240"))  # segundos
LIMITE_ESCANEO = int(os.environ.get("BOT_LIMITE_ESCANEO", str(8 * 3600)))  # segundos
MAX_ESCANEO = int(os.environ.get("BOT_MAX_ESCANEO", "5000"))
ESCANEO_POR_DEFECTO = 200
CATEGORIA_POR_DEFECTO = 200
FICHAS_MAXIMAS = 10  # con más productos se envía un resumen y las tablas
URL_RE = re.compile(r"https?://[^\s<>\"']+")

AYUDA = (
    "🛒 *SuperBot*\n"
    "Descargo título, descripción, precio e imágenes de productos de supermercado.\n\n"
    "*Elegir categorías*\n"
    "`/categorias` → eliges supermercado y categoría con botones y la descargas.\n\n"
    "*Escanear una web entera*\n"
    "`/web <enlace> [cantidad] [filtro]`\n"
    "  · `/web https://www.ahorramas.com` → 200 productos\n"
    "  · `/web https://www.lidl.es 1000`\n"
    "  · `/web https://www.ahorramas.com todos leche` → todos los que contengan «leche»\n"
    "`/parar` detiene el escaneo y te envía lo descargado hasta ese momento.\n\n"
    "*Otras formas*\n"
    "• Pega uno o varios *enlaces de producto* y te envío cada ficha.\n"
    "• `/categoria <enlace> [cantidad|todos]`: *todos* los productos de una categoría, oferta o "
    "búsqueda (recorre sus páginas y subcategorías; 200 por defecto).\n"
    "• `/opciones`: imágenes, modo navegador…\n"
    "• `/yo`: tu id de usuario.\n\n"
    "Te envío los resultados en CSV (Excel) y JSON, y las imágenes en ZIP."
)


class Telegram:
    def __init__(self, token: str):
        self.base = f"{API}/bot{token}"
        self.s = requests.Session()

    def llamar(self, metodo: str, files=None, timeout=70, **datos):
        if files:
            datos = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in datos.items()}
            r = self.s.post(f"{self.base}/{metodo}", data=datos, files=files, timeout=timeout)
        else:
            r = self.s.post(f"{self.base}/{metodo}", json=datos, timeout=timeout)
        res = r.json()
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {res.get('description')}")
        return res["result"]

    def texto(self, chat, txt, markdown=False, **kw):
        if markdown:
            kw["parse_mode"] = "Markdown"
        for i in range(0, len(txt), 4000):  # límite de 4096 caracteres
            ultimo = self.llamar("sendMessage", chat_id=chat, text=txt[i:i + 4000],
                                 disable_web_page_preview=True, **kw)
        return ultimo

    def editar(self, chat, msg_id, txt):
        try:
            self.llamar("editMessageText", chat_id=chat, message_id=msg_id, text=txt[-4000:],
                        disable_web_page_preview=True)
        except RuntimeError:
            pass  # "message is not modified", etc.


def _duracion(segundos: float) -> str:
    m = int(segundos // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else (f"{m} min" if m else f"{int(segundos)} s")


def resumen(productos: list[dict], muestra: int = 8) -> str:
    con_precio = [p for p in productos if p.get("precio") is not None]
    lineas = [f"*Resumen:* {len(productos)} productos, {len(con_precio)} con precio."]
    if con_precio:
        precios = sorted(p["precio"] for p in con_precio)
        lineas.append(f"Precio mínimo {precios[0]:.2f} · medio {sum(precios) / len(precios):.2f} · "
                      f"máximo {precios[-1]:.2f}".replace(".", ","))
    lineas.append("\n*Algunos productos:*")
    for p in productos[:muestra]:
        precio = f"{p['precio']:.2f} €".replace(".", ",") if p.get("precio") is not None else "sin precio"
        lineas.append(f"• {escapar_md((p.get('titulo') or '(sin título)')[:70])} — {precio}")
    lineas.append("\nLa lista completa va en los ficheros de abajo.")
    return "\n".join(lineas)


def escapar_md(t: str) -> str:
    return re.sub(r"([_*`\[])", r"\\\1", t or "")


def ficha(p: dict) -> str:
    precio = (f"{p['precio']:.2f}".replace(".", ",") + (" €" if p.get("moneda") == "EUR"
              else f" {p.get('moneda') or ''}")) if p.get("precio") is not None else "Precio no encontrado"
    desc = p.get("descripcion") or ""
    if len(desc) > 600:
        desc = desc[:600].rsplit(" ", 1)[0] + "…"
    partes = [f"*{escapar_md(p.get('titulo') or '(sin título)')}*", f"💶 *{escapar_md(precio.strip())}*"]
    if desc:
        partes.append(escapar_md(desc))
    partes.append(escapar_md(p["url"]))
    return "\n\n".join(partes)


class Bot:
    def __init__(self, token: str, permitidos: set[int]):
        self.tg = Telegram(token)
        self.permitidos = permitidos
        self.opciones: dict[int, dict] = {}
        self.ocupado: dict[int, tuple[float, int, int]] = {}  # chat -> (inicio, id, límite en s)
        self.paradas: dict[int, threading.Event] = {}
        self.webs: list[str] = []  # webs abiertas con /categorias (índice en los botones)
        self._siguiente_id = 0
        self.carpeta = Path(tempfile.gettempdir()) / "superbot-telegram"
        self.errores: deque[str] = deque(maxlen=10)  # se ven en /api/diagnostico
        self.ultimos_mensajes: deque[str] = deque(maxlen=10)

    def registrar_error(self, donde: str, e: Exception, chat=None):
        detalle = "".join(traceback.format_exception_only(type(e), e)).strip()
        linea = f"{time.strftime('%H:%M:%S')} {donde}: {detalle}"
        self.errores.append(linea)
        print(linea, flush=True)
        traceback.print_exc()
        if chat is not None:
            try:
                self.tg.texto(chat, f"❌ Error interno ({donde}): {detalle[:300]}")
            except Exception:  # noqa: BLE001
                pass

    def ops(self, chat):
        return self.opciones.setdefault(chat, {"imagenes": True, "imagenes_web": False,
                                               "navegador": False, "max_imagenes": 5})

    # ---------- Arranque ----------
    def preparar(self):
        yo = self.tg.llamar("getMe")
        if not self.permitidos:
            print("AVISO: sin TELEGRAM_USUARIOS, cualquiera puede usar el bot.")
        self.tg.llamar("setMyCommands", commands=[
            {"command": "categorias", "description": "Elegir categorías de un supermercado"},
            {"command": "web", "description": "Escanear una web entera: /web <enlace> [cantidad] [filtro]"},
            {"command": "categoria", "description": "Una categoría por enlace: /categoria <enlace> [cantidad]"},
            {"command": "parar", "description": "Detener la descarga en curso"},
            {"command": "opciones", "description": "Ver o cambiar opciones"},
            {"command": "ayuda", "description": "Cómo usar el bot"},
            {"command": "yo", "description": "Mi id de usuario"},
        ])
        return yo

    def activar_webhook(self, url: str, secreto: str):
        """Modo servidor: Telegram envía cada mensaje a `url` (y despierta servidores dormidos)."""
        yo = self.preparar()
        self.tg.llamar("setWebhook", url=url, secret_token=secreto, allowed_updates=["message", "callback_query"],
                       drop_pending_updates=False)
        print(f"Bot de Telegram @{yo['username']} activo (webhook).")

    def procesar(self, update: dict):
        if cq := update.get("callback_query"):
            chat = (cq.get("message") or {}).get("chat", {}).get("id")
            self.ultimos_mensajes.append(f"{time.strftime('%H:%M:%S')} chat {chat}: [botón] {cq.get('data')!r}")
            try:
                self.pulsado(cq)
            except Exception as e:  # noqa: BLE001
                self.registrar_error("botón", e, chat)
            return
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        texto = (msg.get("text") or msg.get("caption") or "")
        self.ultimos_mensajes.append(f"{time.strftime('%H:%M:%S')} chat {msg.get('chat', {}).get('id')}: {texto[:80]!r}")
        try:
            self.atender(msg)
        except Exception as e:  # noqa: BLE001
            self.registrar_error("atender", e, msg.get("chat", {}).get("id"))

    def ejecutar(self):
        """Modo local: consulta a Telegram cada pocos segundos (long polling)."""
        yo = self.preparar()
        self.tg.llamar("deleteWebhook")  # por si antes estuvo en un servidor
        print(f"Bot @{yo['username']} en marcha. Ctrl+C para parar.")
        desplazamiento = None
        while True:
            try:
                updates = self.tg.llamar("getUpdates", offset=desplazamiento, timeout=50,
                                         allowed_updates=["message", "callback_query"])
            except (requests.RequestException, RuntimeError) as e:
                print("Error de conexión con Telegram:", e)
                time.sleep(5)
                continue
            for u in updates:
                desplazamiento = u["update_id"] + 1
                self.procesar(u)

    def atender(self, msg):
        chat = msg["chat"]["id"]
        usuario = msg.get("from", {}).get("id")
        texto = (msg.get("text") or msg.get("caption") or "").strip()
        comando = texto.split()[0].split("@")[0].lower() if texto.startswith("/") else ""

        if comando == "/yo":
            return self.tg.texto(chat, f"Tu id de usuario es: {usuario}")
        if self.permitidos and usuario not in self.permitidos:
            return self.tg.texto(chat, f"⛔ No tienes permiso para usar este bot. (Tu id: {usuario})")
        if comando in ("/start", "/ayuda", "/help"):
            return self.tg.texto(chat, AYUDA, markdown=True)
        if comando == "/opciones":
            return self.cambiar_opciones(chat, texto)
        if comando in ("/categorias", "/cats", "/supermercados"):
            urls = URL_RE.findall(texto)
            if urls:
                if not url_publica(urls[0]):
                    return self.tg.texto(chat, f"URL no válida: {urls[0]}")
                return self.abrir_web(chat, urls[0])
            return self.elegir_supermercado(chat)
        if comando in ("/parar", "/stop", "/cancelar"):
            if chat in self.ocupado and chat in self.paradas:
                self.paradas[chat].set()
                return self.tg.texto(chat, "⏹ Parando… en unos segundos te envío lo descargado hasta ahora.")
            return self.tg.texto(chat, "No hay nada en marcha.")

        urls = [u.rstrip(".,;)") for u in URL_RE.findall(texto)]
        for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
            if ent.get("type") == "text_link" and ent.get("url"):
                urls.append(ent["url"])
        urls = list(dict.fromkeys(urls))
        if malas := [u for u in urls if not url_publica(u)]:
            return self.tg.texto(chat, f"URL no válida: {malas[0]}")
        if not urls:
            return self.tg.texto(chat, "Envíame un enlace de producto, o usa /web <enlace> para escanear "
                                       "una web entera. /ayuda para más info.")
        if self.esta_ocupado(chat):
            return

        extra = {}
        limite = LIMITE_TRABAJO
        if comando in ("/web", "/escanear", "/todo"):
            resto = URL_RE.sub(" ", texto).split()[1:]
            maximo = ESCANEO_POR_DEFECTO
            palabras = []
            for t in resto:
                if t.isdigit():
                    maximo = int(t)
                elif t.lower() in ("todo", "todos", "all"):
                    maximo = MAX_ESCANEO
                else:
                    palabras.append(t)
            maximo = max(1, min(maximo, MAX_ESCANEO))
            extra = {"sitios": urls[:1], "max_escaneo": maximo, "filtro": " ".join(palabras) or None}
            args = ([], [], maximo)
            limite = LIMITE_ESCANEO
        elif comando in ("/categoria", "/cat"):
            resto = URL_RE.sub(" ", texto).split()[1:]
            maximo = CATEGORIA_POR_DEFECTO
            for t in resto:
                if t.isdigit():
                    maximo = int(t)
                elif t.lower() in ("todo", "todos", "all"):
                    maximo = MAX_ESCANEO
            args = ([], urls[:1], max(1, min(maximo, MAX_ESCANEO)))
            limite = LIMITE_ESCANEO
        else:
            args = (urls[:MAX_PRODUCTOS], [], MAX_PRODUCTOS)
        self.lanzar(chat, args, extra, limite)

    def esta_ocupado(self, chat) -> bool:
        """True (y avisa) si el chat ya tiene una descarga en marcha."""
        if chat not in self.ocupado:
            return False
        inicio, _, limite = self.ocupado[chat]
        if time.time() - inicio < limite:
            self.tg.texto(chat, "⏳ Todavía estoy con tu petición anterior. Espera a que termine o usa /parar.")
            return True
        self.registrar_error("trabajo", TimeoutError(
            f"la petición anterior lleva más de {limite // 60} min; se descarta"))
        self.tg.texto(chat, "⚠️ La petición anterior se había quedado atascada; la descarto y sigo con esta.")
        if chat in self.paradas:
            self.paradas[chat].set()
        self.ocupado.pop(chat, None)
        return False

    def lanzar(self, chat, args, extra=None, limite=LIMITE_TRABAJO):
        self._siguiente_id += 1
        self.ocupado[chat] = (time.time(), self._siguiente_id, limite)
        self.paradas[chat] = threading.Event()
        threading.Thread(target=self.trabajo_seguro, args=(chat, self._siguiente_id, *args),
                         kwargs=extra or {}, daemon=True, name=f"trabajo-chat-{chat}").start()

    # ---------- Selector de categorías con botones ----------
    def _web_id(self, web: str) -> int:
        if web not in self.webs:
            self.webs.append(web)
        return self.webs.index(web)

    def elegir_supermercado(self, chat, msg_id=None):
        filas = [[{"text": s["nombre"], "callback_data": f"s:{self._web_id(s['url'])}"}] for s in SUPERMERCADOS]
        texto = ("🏪 ¿De qué supermercado quieres ver las categorías?\n\n"
                 "Para otra web: /categorias <enlace de la web>")
        if msg_id:
            return self._editar_botones(chat, msg_id, texto, filas)
        self.tg.llamar("sendMessage", chat_id=chat, text=texto, reply_markup={"inline_keyboard": filas})

    def abrir_web(self, chat, web, msg_id=None):
        aviso = "⏳ Cargando las categorías… (la primera vez tarda unos segundos)"
        if msg_id:
            self._editar_botones(chat, msg_id, aviso, [])
        else:
            msg_id = self.tg.texto(chat, aviso)["message_id"]
        try:
            arbol = categorias_de(web)
        except Exception as e:  # noqa: BLE001
            return self._editar_botones(chat, msg_id, f"❌ No pude leer las categorías: {e}", [])
        if not arbol.nodos:
            return self._editar_botones(chat, msg_id, "No he encontrado categorías en esa web. Prueba con "
                                        "/categoria <enlace de una categoría>.", [])
        self.mostrar_nodo(chat, msg_id, self._web_id(web), -1, 0)

    def mostrar_nodo(self, chat, msg_id, wid: int, nid: int, pagina: int):
        arbol = categorias_de(self.webs[wid])
        hijos = arbol.hijos_de(None if nid < 0 else nid)
        por_pagina = 12
        trozo = hijos[pagina * por_pagina:(pagina + 1) * por_pagina]
        nombre_web = next((s["nombre"] for s in SUPERMERCADOS if s["url"] == self.webs[wid]),
                          self.webs[wid].split("/")[2])
        filas = []
        if nid >= 0:
            nodo = arbol.nodos[nid]
            urls = arbol.urls_de(nid)
            if urls:
                filas.append([{"text": "⬇ Descargar (200)", "callback_data": f"d:{wid}:{nid}:200"},
                              {"text": "⬇ Todos", "callback_data": f"d:{wid}:{nid}:0"}])
            texto = f"🗂 {nombre_web} › {arbol.ruta(nid)}"
            texto += (f"\n\n{len(hijos)} subcategorías. Elige una o descarga esta entera."
                      if hijos else "\n\nPulsa «Descargar» para bajar sus productos.")
        else:
            texto = f"🗂 {nombre_web}: elige una categoría ({len(hijos)})"
        for h in trozo:
            etiqueta = h.nombre[:40] + (f" ({len(h.hijos)}) ›" if h.hijos else "")
            filas.append([{"text": etiqueta, "callback_data": f"n:{wid}:{h.id}:0"}])
        nav = []
        if pagina > 0:
            nav.append({"text": "◀ Anteriores", "callback_data": f"n:{wid}:{nid}:{pagina - 1}"})
        if (pagina + 1) * por_pagina < len(hijos):
            nav.append({"text": f"Más ▶ ({pagina + 2}/{-(-len(hijos) // por_pagina)})",
                        "callback_data": f"n:{wid}:{nid}:{pagina + 1}"})
        if nav:
            filas.append(nav)
        if nid >= 0:
            padre = arbol.nodos[nid].padre
            filas.append([{"text": "⬅ Volver", "callback_data": f"n:{wid}:{-1 if padre is None else padre}:0"}])
        else:
            filas.append([{"text": "⬅ Supermercados", "callback_data": "s:-1"}])
        self._editar_botones(chat, msg_id, texto, filas)

    def _editar_botones(self, chat, msg_id, texto, filas):
        try:
            self.tg.llamar("editMessageText", chat_id=chat, message_id=msg_id, text=texto[:4000],
                           reply_markup={"inline_keyboard": filas})
        except RuntimeError as e:
            if "not modified" not in str(e):
                raise

    def pulsado(self, cq: dict):
        chat = cq["message"]["chat"]["id"]
        msg_id = cq["message"]["message_id"]
        usuario = cq.get("from", {}).get("id")
        try:
            self.tg.llamar("answerCallbackQuery", callback_query_id=cq["id"])
        except Exception:  # noqa: BLE001
            pass
        if self.permitidos and usuario not in self.permitidos:
            return self.tg.texto(chat, f"⛔ No tienes permiso para usar este bot. (Tu id: {usuario})")
        partes = (cq.get("data") or "").split(":")
        tipo = partes[0]
        if tipo == "s":
            wid = int(partes[1])
            if wid < 0 or wid >= len(self.webs):
                return self.elegir_supermercado(chat, msg_id)
            return self.abrir_web(chat, self.webs[wid], msg_id)
        if tipo in ("n", "d") and len(partes) == 4:
            wid, nid, n = int(partes[1]), int(partes[2]), int(partes[3])
            if wid >= len(self.webs):  # el servidor se reinició: se perdió la lista
                return self._editar_botones(chat, msg_id, "Esta lista ha caducado. Vuelve a usar /categorias.", [])
            if tipo == "n":
                return self.mostrar_nodo(chat, msg_id, wid, nid, n)
            arbol = categorias_de(self.webs[wid])
            urls = arbol.urls_de(nid)
            if not urls or self.esta_ocupado(chat):
                return
            maximo = MAX_ESCANEO if n == 0 else n
            self.tg.texto(chat, f"⬇ Descargando «{arbol.ruta(nid)}»" + (" (todos)" if n == 0 else f" (hasta {n})"))
            self.lanzar(chat, ([], urls, maximo), None, LIMITE_ESCANEO)

    def trabajo_seguro(self, chat, tid, *args, **kwargs):
        try:
            self.trabajo(chat, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            self.registrar_error("trabajo", e, chat)
        finally:
            if self.ocupado.get(chat, (0, None, 0))[1] == tid:  # no liberar un trabajo más nuevo
                self.ocupado.pop(chat, None)
                self.paradas.pop(chat, None)

    def cambiar_opciones(self, chat, texto):
        o = self.ops(chat)
        partes = texto.split()[1:]
        if len(partes) == 2 and partes[0] in ("imagenes", "imagenes_web", "navegador"):
            o[partes[0]] = partes[1].lower() in ("si", "sí", "on", "1", "true")
        elif len(partes) == 2 and partes[0] == "max_imagenes" and partes[1].isdigit():
            o["max_imagenes"] = max(1, min(int(partes[1]), 10))
        si = lambda v: "sí" if v else "no"  # noqa: E731
        self.tg.texto(chat,
            "⚙️ Opciones actuales:\n"
            f"• imagenes: {si(o['imagenes'])} (enlaces y categorías)\n"
            f"• imagenes_web: {si(o['imagenes_web'])} (escaneos con /web; con miles de productos ocupan mucho)\n"
            f"• navegador: {si(o['navegador'])}\n"
            f"• max_imagenes: {o['max_imagenes']}\n\n"
            "Para cambiarlas, por ejemplo:\n/opciones imagenes no\n/opciones imagenes_web si\n"
            "/opciones navegador si\n/opciones max_imagenes 1\n\n"
            "Sin imágenes descargadas, el CSV incluye igualmente los enlaces a las fotos.")

    # ---------- Scraping ----------
    def trabajo(self, chat, urls, categorias, maximo, sitios=None, max_escaneo=None, filtro=None):
        o = self.ops(chat)
        escaneo = bool(sitios)
        salida = self.carpeta / f"{chat}-{int(time.time())}"
        progreso = self.tg.texto(chat, "⏳ Buscando los productos de la web…" if escaneo else "⏳ Empezando…")
        lineas: list[str] = []
        ultima_edicion = [0.0]
        estado = {"hecho": 0, "total": 0, "inicio_descarga": None}

        def log(m):
            lineas.append(m)
            if m.startswith("["):
                try:
                    hecho, total = m[1:m.index("]")].split("/")
                    estado.update(hecho=int(hecho), total=int(total))
                    estado["inicio_descarga"] = estado["inicio_descarga"] or time.time()
                except ValueError:
                    pass
            if time.time() - ultima_edicion[0] > 4:  # no saturar la API de Telegram
                ultima_edicion[0] = time.time()
                cabecera = "⏳ Trabajando…"
                if estado["total"]:
                    h, t = estado["hecho"], estado["total"]
                    cabecera = f"⏳ Descargando {h}/{t} productos"
                    if h > 3:
                        quedan = (time.time() - estado["inicio_descarga"]) / (h - 1) * (t - h)
                        cabecera += f" · quedan ~{_duracion(quedan)}"
                    if escaneo or categorias:
                        cabecera += "\n(/parar para detener y recibir lo descargado)"
                del lineas[:-6]
                self.tg.editar(chat, progreso["message_id"], cabecera + "\n\n" + "\n".join(l[:120] for l in lineas))

        d = Descargador(usar_navegador=o["navegador"])
        try:
            imagenes = o["imagenes_web"] if escaneo else o["imagenes"]
            productos, errores = scrapear(urls, categorias, salida, max_por_categoria=maximo,
                                          descargar_imagenes=imagenes, max_imagenes=o["max_imagenes"],
                                          descargador=d, log=log, sitios=sitios,
                                          max_escaneo=max_escaneo or maximo, filtro=filtro,
                                          parar=self.paradas.get(chat))
            guardar(productos, salida)
            n = len(productos)
            parado = bool(self.paradas.get(chat) and self.paradas[chat].is_set())
            self.tg.editar(chat, progreso["message_id"],
                           (f"{'⏹' if parado else '✅'} {n} producto{'s' if n != 1 else ''} "
                            f"descargado{'s' if n != 1 else ''}") if n else "⚠️ No se obtuvo ningún producto")
            if n <= FICHAS_MAXIMAS:
                for p in productos:
                    self.enviar_producto(chat, p, salida)
            else:
                self.tg.texto(chat, resumen(productos), markdown=True)
            if errores:
                self.tg.texto(chat, f"⚠️ Problemas ({len(errores)}):\n" + "\n".join(f"• {e[:200]}" for e in errores[:8]))
            if n:
                self.enviar_resultados(chat, salida, n, imagenes)
        except Exception as e:  # noqa: BLE001
            self.registrar_error("descarga", e, chat)
        finally:
            d.cerrar()
            shutil.rmtree(salida, ignore_errors=True)

    def enviar_resultados(self, chat, salida: Path, n: int, con_imagenes: bool):
        for nombre, desc in (("productos.csv", "📊 Tabla para Excel"), ("productos.json", "🗂 Datos en JSON")):
            with open(salida / nombre, "rb") as f:
                self.tg.llamar("sendDocument", chat_id=chat, caption=f"{desc} · {n} productos",
                               files={"document": (nombre, f)}, timeout=300)
        if con_imagenes and (salida / "imagenes").exists():
            partes = comprimir_por_partes(salida / "imagenes", salida.parent / f"{salida.name}-imagenes.zip")
            for i, parte in enumerate(partes, 1):
                with open(parte, "rb") as f:
                    nombre = "imagenes.zip" if len(partes) == 1 else f"imagenes-parte{i}de{len(partes)}.zip"
                    self.tg.llamar("sendDocument", chat_id=chat, caption=f"🖼 Imágenes ({i}/{len(partes)})",
                                   files={"document": (nombre, f)}, timeout=600)
                parte.unlink(missing_ok=True)

    def enviar_producto(self, chat, p, salida: Path):
        texto = ficha(p)
        locales = [salida / r for r in p.get("imagenes_locales", [])][:10]
        try:
            if len(locales) == 1 or (locales and len(texto) > 1000):
                # Una foto con pie (máx. 1024 caracteres); si el texto es largo, va aparte
                corto = len(texto) <= 1000
                with open(locales[0], "rb") as f:
                    self.tg.llamar("sendPhoto", chat_id=chat, files={"photo": f},
                                   caption=texto if corto else None, parse_mode="Markdown")
                if not corto:
                    self.tg.texto(chat, texto, markdown=True)
            elif len(locales) > 1:
                ficheros = {f"img{i}": open(r, "rb") for i, r in enumerate(locales)}
                try:
                    media = [{"type": "photo", "media": f"attach://img{i}"} for i in range(len(locales))]
                    media[0].update(caption=texto, parse_mode="Markdown")
                    self.tg.llamar("sendMediaGroup", chat_id=chat, media=media, files=ficheros)
                finally:
                    for f in ficheros.values():
                        f.close()
            else:
                self.tg.texto(chat, texto, markdown=True)
        except RuntimeError:
            # Si falla la foto (formato no admitido, Markdown raro...), al menos el texto plano
            self.tg.texto(chat, texto.replace("\\", "").replace("*", ""))


def usuarios_permitidos() -> set[int]:
    return {int(x) for x in re.split(r"[,\s]+", os.environ.get("TELEGRAM_USUARIOS", "")) if x.strip().isdigit()}


def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        sys.exit("Falta TELEGRAM_TOKEN. Créalo con @BotFather en Telegram.\n"
                 "Ejemplo: TELEGRAM_TOKEN=123456:ABC... python telegram_bot.py")
    try:
        Bot(token, usuarios_permitidos()).ejecutar()
    except KeyboardInterrupt:
        print("\nBot detenido.")


if __name__ == "__main__":
    main()
