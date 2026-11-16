"""Descarga de páginas (requests o Playwright) e imágenes, respetando robots.txt."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SuperBot/1.0"
)


class BloqueoAntiBot(RuntimeError):
    """La web devolvió una página de protección (Cloudflare, captcha...)."""


def comprobar_bloqueo(estado: int, html: str, url: str):
    cabecera = html[:3000].lower()
    if ("just a moment" in cabecera or "cf_chl_opt" in html[:20000] or "captcha" in cabecera
            or (estado in (403, 429, 503) and "cloudflare" in html.lower())):
        raise BloqueoAntiBot(
            f"{urlparse(url).netloc} está protegida por un sistema anti-bots (Cloudflare) "
            "y ha bloqueado la petición desde esta conexión. Prueba desde otra red "
            "(por ejemplo, ejecutando el bot en tu móvil con Termux o en tu PC de casa).")


class Descargador:
    def __init__(self, pausa: float = 1.5, usar_navegador: bool = False,
                 respetar_robots: bool = True, timeout: int = 20):
        self.pausa = pausa
        self.usar_navegador = usar_navegador
        self.respetar_robots = respetar_robots
        self.timeout = timeout
        self.sesion = requests.Session()
        self.sesion.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        })
        self._robots: dict[str, RobotFileParser | None] = {}
        self._ultima = 0.0
        self._pw = self._navegador = None

    # ---- control ----
    def _esperar(self):
        resto = self.pausa - (time.time() - self._ultima)
        if resto > 0:
            time.sleep(resto)
        self._ultima = time.time()

    def permitido(self, url: str) -> bool:
        if not self.respetar_robots:
            return True
        base = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                r = self.sesion.get(base + "/robots.txt", timeout=self.timeout)
                if not r.ok:
                    comprobar_bloqueo(r.status_code, r.text, url)
                rp.parse(r.text.splitlines() if r.ok else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base].can_fetch(USER_AGENT, url)

    # ---- HTML ----
    def html(self, url: str) -> str:
        if not self.permitido(url):
            raise PermissionError(f"robots.txt no permite acceder a {url}")
        self._esperar()
        if self.usar_navegador:
            return self._html_navegador(url)
        r = self.sesion.get(url, timeout=self.timeout)
        comprobar_bloqueo(r.status_code, r.text, url)
        r.raise_for_status()
        r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
        return r.text

    def _html_navegador(self, url: str) -> str:
        if self._navegador is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as e:
                raise RuntimeError("Instala Playwright: pip install playwright && playwright install chromium") from e
            self._pw = sync_playwright().start()
            self._navegador = self._pw.chromium.launch()
        pagina = self._navegador.new_page(user_agent=USER_AGENT, locale="es-ES")
        try:
            resp = pagina.goto(url, wait_until="networkidle", timeout=self.timeout * 1000 * 2)
            comprobar_bloqueo(resp.status if resp else 200, pagina.content(), url)
            # Hacer scroll para forzar la carga perezosa de imágenes/productos
            for _ in range(5):
                pagina.mouse.wheel(0, 2500)
                pagina.wait_for_timeout(400)
            return pagina.content()
        finally:
            pagina.close()

    # ---- Imágenes ----
    def imagen(self, url: str, carpeta: Path, prefijo: str) -> str | None:
        carpeta.mkdir(parents=True, exist_ok=True)
        try:
            self._esperar()
            r = self.sesion.get(url, timeout=self.timeout, stream=True)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"   ! No se pudo descargar la imagen {url}: {e}")
            return None
        tipo = r.headers.get("Content-Type", "").split(";")[0]
        ext = mimetypes.guess_extension(tipo) or os.path.splitext(urlparse(url).path)[1] or ".jpg"
        ext = ".jpg" if ext == ".jpe" else ext
        nombre = f"{prefijo}_{hashlib.md5(url.encode()).hexdigest()[:8]}{ext}"
        ruta = carpeta / nombre
        with open(ruta, "wb") as f:
            for trozo in r.iter_content(8192):
                f.write(trozo)
        return str(ruta)

    def cerrar(self):
        if self._navegador:
            self._navegador.close()
            self._pw.stop()


def slug(texto: str, largo: int = 60) -> str:
    import unicodedata
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^\w\s-]", "", texto.lower())
    return re.sub(r"[\s_-]+", "-", texto).strip("-")[:largo] or "producto"
