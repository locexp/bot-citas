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

from scraper.descargador import Descargador
from scraper.servicio import comprimir, guardar, scrapear, url_publica

API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
MAX_PRODUCTOS = int(os.environ.get("BOT_MAX_PRODUCTOS", "40"))
URL_RE = re.compile(r"https?://[^\s<>\"']+")

AYUDA = (
    "🛒 *SuperBot*\n"
    "Descargo título, descripción, precio e imágenes de productos de supermercado.\n\n"
    "• Pega uno o varios *enlaces de producto* y te envío cada ficha.\n"
    "• `/categoria <enlace> [máx]`: todos los productos de una categoría, oferta o búsqueda.\n"
    "• `/opciones`: ver o cambiar opciones (imágenes, modo navegador).\n"
    "• `/yo`: tu id de usuario.\n\n"
    "Al terminar te envío un ZIP con las imágenes, el CSV (Excel) y el JSON."
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
        self.ocupado: set[int] = set()
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
        return self.opciones.setdefault(chat, {"imagenes": True, "navegador": False, "max_imagenes": 5})

    # ---------- Arranque ----------
    def preparar(self):
        yo = self.tg.llamar("getMe")
        if not self.permitidos:
            print("AVISO: sin TELEGRAM_USUARIOS, cualquiera puede usar el bot.")
        self.tg.llamar("setMyCommands", commands=[
            {"command": "categoria", "description": "Productos de una categoría: /categoria <enlace> [máx]"},
            {"command": "opciones", "description": "Ver o cambiar opciones"},
            {"command": "ayuda", "description": "Cómo usar el bot"},
            {"command": "yo", "description": "Mi id de usuario"},
        ])
        return yo

    def activar_webhook(self, url: str, secreto: str):
        """Modo servidor: Telegram envía cada mensaje a `url` (y despierta servidores dormidos)."""
        yo = self.preparar()
        self.tg.llamar("setWebhook", url=url, secret_token=secreto, allowed_updates=["message"],
                       drop_pending_updates=False)
        print(f"Bot de Telegram @{yo['username']} activo (webhook).")

    def procesar(self, update: dict):
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
                                         allowed_updates=["message"])
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

        urls = [u.rstrip(".,;)") for u in URL_RE.findall(texto)]
        for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
            if ent.get("type") == "text_link" and ent.get("url"):
                urls.append(ent["url"])
        urls = list(dict.fromkeys(urls))
        if malas := [u for u in urls if not url_publica(u)]:
            return self.tg.texto(chat, f"URL no válida: {malas[0]}")
        if not urls:
            return self.tg.texto(chat, "Envíame un enlace de producto, o usa /categoria <enlace>. /ayuda para más info.")
        if chat in self.ocupado:
            return self.tg.texto(chat, "⏳ Todavía estoy con tu petición anterior, espera a que termine.")

        if comando == "/categoria":
            m = re.search(r"\s(\d+)\s*$", texto)
            maximo = min(int(m.group(1)), MAX_PRODUCTOS) if m else 20
            args = ([], urls[:1], maximo)
        else:
            args = (urls[:MAX_PRODUCTOS], [], MAX_PRODUCTOS)
        self.ocupado.add(chat)
        threading.Thread(target=self.trabajo_seguro, args=(chat, *args), daemon=True).start()

    def trabajo_seguro(self, chat, *args):
        try:
            self.trabajo(chat, *args)
        except Exception as e:  # noqa: BLE001
            self.registrar_error("trabajo", e, chat)
        finally:
            self.ocupado.discard(chat)

    def cambiar_opciones(self, chat, texto):
        o = self.ops(chat)
        partes = texto.split()[1:]
        if len(partes) == 2 and partes[0] in ("imagenes", "navegador"):
            o[partes[0]] = partes[1].lower() in ("si", "sí", "on", "1", "true")
        elif len(partes) == 2 and partes[0] == "max_imagenes" and partes[1].isdigit():
            o["max_imagenes"] = max(1, min(int(partes[1]), 10))
        si = lambda v: "sí" if v else "no"
        self.tg.texto(chat,
            "⚙️ Opciones actuales:\n"
            f"• imagenes: {si(o['imagenes'])}\n• navegador: {si(o['navegador'])}\n"
            f"• max_imagenes: {o['max_imagenes']}\n\n"
            "Para cambiarlas, por ejemplo:\n/opciones imagenes no\n/opciones navegador si\n/opciones max_imagenes 3")

    # ---------- Scraping ----------
    def trabajo(self, chat, urls, categorias, maximo):
        o = self.ops(chat)
        salida = self.carpeta / f"{chat}-{int(time.time())}"
        progreso = self.tg.texto(chat, "⏳ Empezando…")
        lineas: list[str] = []
        ultima_edicion = [0.0]

        def log(m):
            lineas.append(m)
            if time.time() - ultima_edicion[0] > 3:  # no saturar la API
                ultima_edicion[0] = time.time()
                self.tg.editar(chat, progreso["message_id"], "⏳ Trabajando…\n" + "\n".join(lineas[-8:]))

        d = Descargador(usar_navegador=o["navegador"])
        try:
            productos, errores = scrapear(urls, categorias, salida, max_por_categoria=maximo,
                                          descargar_imagenes=o["imagenes"],
                                          max_imagenes=o["max_imagenes"], descargador=d, log=log)
            guardar(productos, salida)
            n = len(productos)
            self.tg.editar(chat, progreso["message_id"],
                           f"✅ {n} producto{'s' if n != 1 else ''} descargado{'s' if n != 1 else ''}"
                           if n else "⚠️ No se obtuvo ningún producto")
            for p in productos:
                self.enviar_producto(chat, p, salida)
            if errores:
                self.tg.texto(chat, "⚠️ Problemas:\n" + "\n".join(f"• {e}" for e in errores[:10]))
            if n:
                zip_bytes = comprimir(salida)
                if len(zip_bytes) < 48 * 1024 * 1024:  # límite de Telegram para bots: 50 MB
                    self.tg.llamar("sendDocument", chat_id=chat, caption="📦 Imágenes + CSV + JSON",
                                   files={"document": ("productos.zip", zip_bytes, "application/zip")},
                                   timeout=300)
                else:
                    self.tg.llamar("sendDocument", chat_id=chat, caption="El ZIP supera 50 MB; te envío el CSV",
                                   files={"document": ("productos.csv", (salida / "productos.csv").read_bytes())})
        except Exception as e:  # noqa: BLE001
            self.registrar_error("descarga", e, chat)
        finally:
            d.cerrar()
            self.ocupado.discard(chat)
            shutil.rmtree(salida, ignore_errors=True)

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
