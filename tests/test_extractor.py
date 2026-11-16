from scraper.extractor import extraer_producto, extraer_enlaces_productos, parsear_precio

JSONLD = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"BreadcrumbList"},
{"@type":"Product","name":"Leche entera 1L","description":"<p>Leche de vaca</p>",
"image":["/img/leche1.jpg","https://cdn.x.com/leche2.jpg"],
"offers":{"@type":"Offer","price":"1.05","priceCurrency":"EUR"}}]}
</script></head><body><h1>Otro</h1></body></html>"""

HEUR = """<html><head><meta property="og:image" content="https://cdn.x.com/a.png">
<meta name="description" content="Aceite de oliva virgen extra"></head>
<body><h1> Aceite  AOVE 1L </h1><span class="product-price">4,59 €</span></body></html>"""


def test_jsonld():
    p = extraer_producto(JSONLD, "https://super.es/p/leche")
    assert p.titulo == "Leche entera 1L"
    assert p.descripcion == "Leche de vaca"
    assert p.precio == 1.05 and p.moneda == "EUR"
    assert p.imagenes == ["https://super.es/img/leche1.jpg", "https://cdn.x.com/leche2.jpg"]


def test_heuristicas():
    p = extraer_producto(HEUR, "https://super.es/p/aceite")
    assert p.titulo == "Aceite AOVE 1L"
    assert p.precio == 4.59 and p.moneda == "EUR"
    assert p.descripcion.startswith("Aceite de oliva")
    assert p.imagenes == ["https://cdn.x.com/a.png"]


def test_selectores():
    html = '<div class="t">Pan</div><b class="pr">$ 1,234.50</b>'
    p = extraer_producto(html, "https://s.com/x", {"titulo": ".t", "precio": ".pr"})
    assert p.titulo == "Pan" and p.precio == 1234.5 and p.moneda == "USD"


def test_precios():
    assert parsear_precio("1.234,56 €") == 1234.56
    assert parsear_precio("2,99") == 2.99
    assert parsear_precio("$1,299") == 1299
    assert parsear_precio("1.299") == 1299
    assert parsear_precio(3) == 3.0


def test_enlaces():
    html = '<a href="/producto/leche">a</a><a href="/contacto">b</a><a href="/producto/leche#x">c</a>'
    assert extraer_enlaces_productos(html, "https://s.es/cat") == ["https://s.es/producto/leche"]
