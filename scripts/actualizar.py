"""Actualiza data.json con las captaciones y cierres de SkyOne Home.

Lee la API pública de SkyOne y guarda SOLO datos públicos: nada de emails,
teléfonos, propietarios, catastro, número de casa, comisiones ni contratos.
Uso: python scripts/actualizar.py   (desde la raíz del repositorio)
"""
import concurrent.futures, datetime, io, json, os, re, unicodedata, urllib.request

OFICINA = "agencia_0000000011"  # SkyOne Home
API = "https://api.skyone.group/api"
H = {"User-Agent": "Mozilla/5.0 (SkyOne Home catalogo)", "Accept": "application/json"}
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(RAIZ, "cache.json")
FOTOS = os.path.join(RAIZ, "fotos")
AMEN = ["piscina", "quincho", "gimnasio", "parrilla", "balcon", "pet_friendly", "ascensor", "condominio",
        "cocina_equipada", "area_servicio", "deposito", "generador", "esquina"]


def get(url, binario=False, metodo="GET"):
    req = urllib.request.Request(url, headers=H, method=metodo)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read() if binario else (r.status if metodo == "HEAD" else json.load(r))


def uno(v):
    return v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v)


def nombre(n):
    return re.sub(r"\s+\d+$", "", str(n or "").strip()).strip(" .")


def slug(n):
    s = unicodedata.normalize("NFD", nombre(n)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "asesor"


def lista(extra=""):
    j = get(f"{API}/propiedades?page=1&per_page=2000&agencia_id={OFICINA}{extra}")
    return j["data"]


def contar_fotos(img):
    m = re.match(r"(.*-)01(_\d+)-small\.jpg$", img or "")
    if not m:
        return 1 if img else 0
    n = 1
    while n < 30:
        try:
            get(f"{m.group(1)}{n + 1:02d}{m.group(2)}-small.jpg", metodo="HEAD")
            n += 1
        except Exception:
            break
    return n


def detalle(pid):
    """Devuelve solo lo público de una propiedad + datos internos mínimos para fotos de asesor."""
    r = {k: uno(v) for k, v in get(f"{API}/propiedad/{pid}").items()}
    img = r.get("img") or ""
    m = re.match(r"(.*-)01(_\d+)-small\.jpg$", img)
    return {
        "id": int(str(pid).split("_")[-1]), "k": pid,
        "t": (r.get("titulo") or "").strip(), "d": (r.get("descripcion") or "").strip(),
        "tp": r.get("tipoPropiedad"), "op": r.get("venta_alquiler"), "p": r.get("precio") or 0, "m": r.get("moneda_contrato"),
        "dr": r.get("dormitorios"), "b": r.get("banios"), "g": r.get("garajes"), "m2": r.get("m2"), "mc": r.get("m2_cons"),
        "ba": r.get("distrito"), "c": r.get("ciudad"), "a": [k for k in AMEN if str(r.get(k)).lower() == "si"],
        "i": [m.group(1), m.group(2)] if m else img, "nf": contar_fotos(img),
        "u": r.get("link") or f"https://portal.skyone.group/propiedad/{pid}", "v": r.get("video") or "",
        "ag": slug(r.get("agent_name")), "agn": nombre(r.get("agent_name")),
        "_img_asesor": r.get("agent_img") or "",
    }


def fondo_blanco(im):
    """Recorta a la persona y la pone sobre fondo blanco, en cuadrado de 480 px."""
    from PIL import Image
    try:
        from rembg import remove, new_session
        cut = remove(im, session=new_session("isnet-general-use"), post_process_mask=True)
    except Exception:
        w, h = im.size; m = min(w, h); top = (h - m) // 4 if h > w else 0
        return im.crop(((w - m) // 2, top, (w - m) // 2 + m, top + m)).resize((480, 480))
    w, h = im.size
    x0, y0, x1, y1 = cut.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox() or (0, 0, w, h)
    side = int(max(x1 - x0, (y1 - y0) * 0.82) * 1.12); cx = (x0 + x1) // 2; top = max(0, y0 - int(side * 0.06))
    blanco = Image.new("RGBA", cut.size, (255, 255, 255, 255)); blanco.alpha_composite(cut)
    big = Image.new("RGB", (w + 2 * side, h + 2 * side), "white"); big.paste(blanco.convert("RGB"), (side, side))
    return big.crop((cx - side // 2 + side, top + side, cx + side // 2 + side, top + 2 * side)).resize((480, 480), Image.LANCZOS)


def foto_asesor(s, url):
    destino = os.path.join(FOTOS, s + ".jpg")
    if os.path.exists(destino) or not url:
        return
    for u in (url.replace("-small.jpg", ".jpg"), url):
        try:
            b = get(u, binario=True)
            try:
                from PIL import Image
                im = Image.open(io.BytesIO(b)).convert("RGB")
                fondo_blanco(im).save(destino, quality=88)
            except ImportError:
                open(destino, "wb").write(b)
            return
        except Exception:
            continue


def main():
    os.makedirs(FOTOS, exist_ok=True)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    activas, cerradas = lista(), lista("&cerradas=1")
    meta = {x["id"]: x for x in activas + cerradas}
    faltan = [pid for pid in meta if pid not in cache]
    hoy = datetime.date.today().isoformat()
    # Las activas se vuelven a leer completas una vez por día (precio, descripción, fotos).
    refrescar = [pid for pid in (x["id"] for x in activas) if cache.get(pid, {}).get("_dia") != hoy]

    def bajar(pid):
        try:
            d = detalle(pid); d["_dia"] = hoy; return pid, d
        except Exception as e:
            print("No se pudo leer", pid, e); return pid, None

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for pid, d in ex.map(bajar, list(dict.fromkeys(faltan + refrescar))):
            if d:
                cache[pid] = d
    cache = {k: v for k, v in cache.items() if k in meta}

    asesores = {}
    def asesor(p):
        a = asesores.setdefault(p["ag"], {"e": p["ag"], "n": p["agn"], "c": 0, "z": 0, "_img": p.get("_img_asesor")})
        if not a.get("_img"): a["_img"] = p.get("_img_asesor")
        return a

    props, cierres = [], []
    ids_activas = {x["id"] for x in activas}
    for pid, x in meta.items():
        p = cache.get(pid)
        if not p:
            continue
        # Lo liviano (precio, título) se toma siempre de la lista, que está al minuto.
        p.update({"t": (x.get("titulo") or p["t"]).strip(), "p": x.get("precio") or p["p"], "m": x.get("moneda_contrato") or p["m"]})
        f = x.get("fecha_creacion") or x.get("fecha")
        a = asesor(p)
        pub = {k: v for k, v in p.items() if not k.startswith("_") and k not in ("k", "agn")}
        if pid in ids_activas:
            a["c"] += 1; pub["f"] = f; props.append(pub)
        else:
            a["z"] += 1
            img = p["i"]
            cierres.append({"id": p["id"], "tp": p["tp"], "op": p["op"], "ba": p["ba"], "c": p["c"], "ag": p["ag"],
                            "f": (x.get("fecha") or "")[:10], "img": f"{img[0]}01{img[1]}-small.jpg" if isinstance(img, list) else img})

    for s, a in asesores.items():
        foto_asesor(s, a.pop("_img", ""))
        a["foto"] = os.path.exists(os.path.join(FOTOS, s + ".jpg"))

    props.sort(key=lambda p: (p.get("f") or "", p["id"]), reverse=True)
    cierres.sort(key=lambda c: (c["f"], c["id"]), reverse=True)
    ahora = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))
    data = {"actualizado": ahora.strftime("%Y-%m-%d %H:%M"),
            "asesores": sorted(asesores.values(), key=lambda a: (-a["c"], -a["z"], a["n"])),
            "props": props, "cierres": cierres}
    txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    assert "@" not in json.dumps(data["asesores"]), "No se publican emails"
    open(os.path.join(RAIZ, "data.json"), "w", encoding="utf-8").write(txt)
    limpio = {k: {kk: vv for kk, vv in v.items() if kk != "_img_asesor"} for k, v in cache.items()}
    json.dump(limpio, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(props)} captaciones activas · {len(cierres)} cierres · {len(asesores)} asesores")


if __name__ == "__main__":
    main()
