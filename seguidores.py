#!/usr/bin/env python3
"""
SEGUIDORES EXCHANGE - funciona en Termux (solo necesita python).

Instalar:   pkg install python
Servidor:   python seguidores.py servidor   (solo tú, en un dispositivo)
Panel:      python seguidores.py panel      (solo tú, en la MISMA carpeta del servidor)
Cliente:    python seguidores.py            (todos los usuarios)

Los usuarios siguen a los demás MANUALMENTE: el script abre el perfil y
la persona toca "Seguir". El script conecta, verifica y lleva la cuenta.
"""
import datetime, json, os, re, secrets, subprocess, sys, textwrap, threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, parse, request

# ================== CONFIGURA AQUÍ ==================
NOMBRE = "EFECTO KALABASSA"
SLOGAN = "Crece junto a otros creadores"
CONTACTO = "Escríbeme a:@ costa.sur7en tiktok"   # WhatsApp, Telegram, etc.
PRECIO = "PRO: $X al mes"                    # cámbialo cuando pongas precio
SERVIDOR = os.environ.get("SEGUIDORES_URL", "http://127.0.0.1:8080")

DB_FILE = "db.json"
KEY_FILE = "admin.key"
MAX_STRIKES = 3        # con 3 reportes deja de aparecer en las listas
LIMITE_GRATIS = 20     # seguimientos por día, plan gratis
LIMITE_PRO = 100       # seguimientos por día, plan PRO
LISTA_GRATIS = 10      # personas por lista, plan gratis
LISTA_PRO = 30         # personas por lista, plan PRO
# =====================================================

LOCK = threading.Lock()
ADMIN_KEY = ""
USER_RE = re.compile(r"^[a-z0-9._-]{1,40}$")
PLAT_RE = re.compile(r"^[a-z0-9_-]{1,20}$")

PLATAFORMAS = {
    "1": ("instagram", "https://instagram.com/"),
    "2": ("tiktok", "https://tiktok.com/@"),
    "3": ("x", "https://x.com/"),
    "4": ("youtube", "https://youtube.com/@"),
    "5": ("facebook", "https://facebook.com/"),
    "6": ("threads", "https://threads.net/@"),
    "7": ("telegram", "https://t.me/"),
    "8": ("twitch", "https://twitch.tv/"),
    "9": ("pinterest", "https://pinterest.com/"),
    "10": ("linkedin", "https://linkedin.com/in/"),
    "11": ("snapchat", "https://snapchat.com/add/"),
    "12": ("kick", "https://kick.com/"),
}

CATEGORIAS = ["música", "humor", "deportes", "moda", "gaming", "tecnología",
              "comida", "viajes", "fitness", "negocios", "arte", "otra"]

PRO_TEXTO = (f"PRO: hasta {LIMITE_PRO} seguimientos al día (gratis: {LIMITE_GRATIS}), "
             f"listas de {LISTA_PRO} personas y prioridad en las listas.")

# ======================= BANNER =======================
def banner(subtitulo=""):
    ancho = 38
    print("╔" + "═" * ancho + "╗")
    print("║" + NOMBRE.center(ancho) + "║")
    print("║" + SLOGAN.center(ancho) + "║")
    if subtitulo:
        print("║" + subtitulo.center(ancho) + "║")
    print("╚" + "═" * ancho + "╝")

def caja(texto):
    print("┌" + "─" * 38 + "┐")
    for linea in textwrap.wrap(texto, 36):
        print("│ " + linea.ljust(36) + " │")
    print("└" + "─" * 38 + "┘")

# ======================= SERVIDOR =======================
def hoy():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()

def nuevo(p, u):
    return {"plataforma": p, "usuario": u, "sigue_a": [], "seguido_por": [],
            "confirmados": [], "rechazados": [], "strikes": 0, "bloqueado": False,
            "categoria": "general", "dia": "", "hoy": 0, "expira": ""}

def cargar():
    db = {}
    if os.path.exists(DB_FILE):
        with open(DB_FILE) as f:
            db = json.load(f)
    db.setdefault("usuarios", {})
    db.setdefault("licencias", {})
    db.setdefault("anuncio", "")
    for x in db["usuarios"].values():  # compatibilidad con datos viejos
        for k, v in nuevo("", "").items():
            x.setdefault(k, [] if isinstance(v, list) else v)
    return db

def guardar(db):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f)
    os.replace(tmp, DB_FILE)

def es_pro(x):
    return bool(x["expira"]) and x["expira"] >= hoy()

def limite(x):
    return LIMITE_PRO if es_pro(x) else LIMITE_GRATIS

def usados_hoy(x):
    return x["hoy"] if x["dia"] == hoy() else 0

def conteo(us):
    c = Counter()
    for y in us.values():
        for n in y["confirmados"]:
            c[(y["plataforma"], n)] += 1
    return c

def puntos(x, cc):
    # +2 por cada persona a la que seguiste y te confirmó,
    # +1 por cada verificación que haces, -5 por cada reporte
    p = (cc[(x["plataforma"], x["usuario"])] * 2
         + len(x["confirmados"]) + len(x["rechazados"]) - x["strikes"] * 5)
    return max(0, p)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def responder(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def admin_ok(self):
        # Solo el panel, que lee admin.key del disco del servidor
        return bool(ADMIN_KEY) and secrets.compare_digest(
            self.headers.get("X-Admin-Key", ""), ADMIN_KEY)

    # ---------------------- POST ----------------------
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.responder({"error": "datos"}, 400)

        if self.path.startswith("/admin/"):
            if not self.admin_ok():
                return self.responder({"error": "no autorizado"}, 403)
            with LOCK:
                db = cargar()
                us = db["usuarios"]
                if self.path == "/admin/anuncio":
                    db["anuncio"] = str(d.get("texto", ""))[:300]
                elif self.path == "/admin/crear_licencia":
                    dias = max(1, min(int(d.get("dias", 30)), 3650))
                    clave = "PRO-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))
                    db["licencias"][clave] = {"dias": dias, "usada_por": None, "creada": hoy()}
                    guardar(db)
                    return self.responder({"ok": True, "licencia": clave, "dias": dias})
                elif self.path in ("/admin/bloquear", "/admin/limpiar"):
                    x = us.get(d.get("clave", ""))
                    if not x:
                        return self.responder({"error": "no existe"}, 404)
                    if self.path == "/admin/bloquear":
                        x["bloqueado"] = bool(d.get("bloquear"))
                    else:
                        x["strikes"] = 0
                else:
                    return self.responder({"error": "ruta"}, 404)
                guardar(db)
            return self.responder({"ok": True})

        p = str(d.get("plataforma", "")).strip().lower()
        u = str(d.get("usuario", "")).strip().lstrip("@").lower()
        if not PLAT_RE.match(p) or not USER_RE.match(u):
            return self.responder({"error": "usuario o plataforma inválidos"}, 400)
        k = f"{p}:{u}"
        with LOCK:
            db = cargar()
            us = db["usuarios"]
            if k in us and us[k]["bloqueado"]:
                return self.responder({"error": "bloqueado"}, 403)

            if self.path == "/registrar":
                x = us.setdefault(k, nuevo(p, u))
                cat = str(d.get("categoria", "")).strip().lower()
                if cat:
                    x["categoria"] = cat[:20]

            elif self.path == "/activar":
                x = us.get(k)
                lic = db["licencias"].get(str(d.get("licencia", "")).strip().upper())
                if not x:
                    return self.responder({"error": "no registrado"}, 404)
                if not lic or lic["usada_por"]:
                    return self.responder({"ok": False, "error": "Licencia inválida o ya usada"})
                base = max(hoy(), x["expira"]) if x["expira"] else hoy()
                fin = datetime.date.fromisoformat(base) + datetime.timedelta(days=lic["dias"])
                x["expira"] = fin.isoformat()
                lic["usada_por"] = k
                guardar(db)
                return self.responder({"ok": True, "expira": x["expira"]})

            elif self.path == "/hecho":  # "yo ya seguí a X"
                a = str(d.get("a", "")).lower()
                ka = f"{p}:{a}"
                if k in us and ka in us and a != u and a not in us[k]["sigue_a"]:
                    x = us[k]
                    if x["dia"] != hoy():
                        x["dia"], x["hoy"] = hoy(), 0
                    if x["hoy"] >= limite(x):
                        return self.responder({"ok": False, "error": "limite",
                                               "limite": limite(x)})
                    x["hoy"] += 1
                    x["sigue_a"].append(a)
                    if u not in us[ka]["seguido_por"]:
                        us[ka]["seguido_por"].append(u)

            elif self.path == "/confirmar":  # "X sí/no me siguió"
                de = str(d.get("de", "")).lower()
                kd = f"{p}:{de}"
                if k in us and kd in us:
                    me = us[k]
                    if (de in me["seguido_por"] and de not in me["confirmados"]
                            and de not in me["rechazados"]):
                        if d.get("ok"):
                            me["confirmados"].append(de)
                        else:
                            me["rechazados"].append(de)
                            us[kd]["strikes"] += 1
            else:
                return self.responder({"error": "ruta"}, 404)
            guardar(db)
        self.responder({"ok": True})

    # ---------------------- GET ----------------------
    def do_GET(self):
        ruta = parse.urlparse(self.path).path
        q = parse.parse_qs(parse.urlparse(self.path).query)

        if ruta == "/anuncio":
            return self.responder({"texto": cargar()["anuncio"]})

        if ruta.startswith("/admin/"):
            if not self.admin_ok():
                return self.responder({"error": "no autorizado"}, 403)
            db = cargar()
            us = db["usuarios"]
            if ruta == "/admin/resumen":
                por = {}
                for x in us.values():
                    por[x["plataforma"]] = por.get(x["plataforma"], 0) + 1
                lics = db["licencias"].values()
                return self.responder({
                    "usuarios": len(us), "por_plataforma": por,
                    "seguimientos": sum(len(x["sigue_a"]) for x in us.values()),
                    "confirmados": sum(len(x["confirmados"]) for x in us.values()),
                    "rechazados": sum(len(x["rechazados"]) for x in us.values()),
                    "bloqueados": sum(1 for x in us.values() if x["bloqueado"]),
                    "pro": sum(1 for x in us.values() if es_pro(x)),
                    "licencias": len(lics),
                    "licencias_libres": sum(1 for l in lics if not l["usada_por"])})
            if ruta == "/admin/top":
                t = sorted(us.items(), key=lambda kv: len(kv[1]["sigue_a"]), reverse=True)[:10]
                return self.responder({"lista": [[k, len(v["sigue_a"]),
                    len(v["confirmados"]), v["strikes"]] for k, v in t]})
            if ruta == "/admin/reportados":
                r = [[k, v["strikes"], v["bloqueado"]] for k, v in us.items()
                     if v["strikes"] > 0 or v["bloqueado"]]
                r.sort(key=lambda z: -z[1])
                return self.responder({"lista": r})
            if ruta == "/admin/licencias":
                return self.responder({"lista": [[c, l["dias"], l["usada_por"] or "libre"]
                                                 for c, l in db["licencias"].items()]})
            return self.responder({"error": "ruta"}, 404)

        p = q.get("plataforma", [""])[0]
        u = q.get("usuario", [""])[0].lower()
        db = cargar()
        us = db["usuarios"]
        me = us.get(f"{p}:{u}")
        if not me:
            return self.responder({"error": "no registrado"}, 404)
        if me["bloqueado"]:
            return self.responder({"error": "bloqueado"}, 403)
        cc = conteo(us)

        if ruta == "/lista":
            cat = q.get("categoria", [""])[0].lower()
            cupo = LISTA_PRO if es_pro(me) else LISTA_GRATIS
            c = [x for x in us.values()
                 if x["plataforma"] == p and x["usuario"] != u
                 and x["usuario"] not in me["sigue_a"]
                 and x["strikes"] < MAX_STRIKES and not x["bloqueado"]
                 and (not cat or x["categoria"] == cat)]
            # mitad por reputación (PRO con prioridad), mitad por los que menos seguidores tienen
            a = sorted(c, key=lambda x: -(puntos(x, cc) + (10 if es_pro(x) else 0)))
            b = sorted(c, key=lambda x: len(x["confirmados"]))
            mezcla, vistos = [], set()
            for i in range(len(c)):
                for lst in (a, b):
                    nom = lst[i]["usuario"]
                    if nom not in vistos:
                        vistos.add(nom)
                        mezcla.append(nom)
            return self.responder({"lista": mezcla[:cupo]})

        if ruta == "/porverificar":
            pend = [x for x in me["seguido_por"]
                    if x not in me["confirmados"] and x not in me["rechazados"]]
            return self.responder({"lista": pend})

        if ruta == "/devolver":
            pend = [x for x in me["confirmados"] if x not in me["sigue_a"]]
            return self.responder({"lista": pend})

        if ruta == "/ranking":
            rank = sorted([x for x in us.values()
                           if x["plataforma"] == p and not x["bloqueado"]],
                          key=lambda x: -puntos(x, cc))
            pos = next((i + 1 for i, x in enumerate(rank) if x["usuario"] == u), None)
            return self.responder({
                "top": [[x["usuario"], puntos(x, cc), es_pro(x)] for x in rank[:10]],
                "mi_pos": pos, "mis_puntos": puntos(me, cc), "total": len(rank)})

        if ruta == "/stats":
            confirmaron = sum(1 for x in us.values()
                              if x["plataforma"] == p and u in x["confirmados"])
            lim = limite(me)
            return self.responder({
                "plan": "PRO" if es_pro(me) else "GRATIS", "expira": me["expira"],
                "limite": lim, "restantes": max(0, lim - usados_hoy(me)),
                "puntos": puntos(me, cc), "categoria": me["categoria"],
                "seguiste": len(me["sigue_a"]), "confirmaron": confirmaron,
                "te_siguieron": len(me["confirmados"]),
                "por_verificar": len([x for x in me["seguido_por"]
                    if x not in me["confirmados"] and x not in me["rechazados"]]),
                "por_devolver": len([x for x in me["confirmados"] if x not in me["sigue_a"]]),
                "strikes": me["strikes"], "max_strikes": MAX_STRIKES})
        self.responder({"error": "ruta"}, 404)

def servidor():
    global ADMIN_KEY
    ADMIN_KEY = secrets.token_hex(16)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(ADMIN_KEY)
    puerto = int(os.environ.get("PORT", 8080))
    banner("Servidor")
    print(f"\nServidor activo en el puerto {puerto}")
    print("Panel de control: python seguidores.py panel (otra sesión, misma carpeta)")
    ThreadingHTTPServer(("0.0.0.0", puerto), H).serve_forever()

# ======================= CLIENTE =======================
def llamar(ruta, datos=None):
    try:
        if datos is None:
            r = request.urlopen(SERVIDOR + ruta, timeout=15)
        else:
            r = request.urlopen(request.Request(
                SERVIDOR + ruta, json.dumps(datos).encode(),
                {"Content-Type": "application/json"}), timeout=15)
        return json.load(r)
    except error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}

def ejecutar(args):
    try:
        return subprocess.run(args, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode
    except FileNotFoundError:
        return 127

def abrir(url):
    """Abre el perfil en la app o navegador del celular (sin usar shell)."""
    if not url.startswith("http"):
        return
    if ejecutar(["am", "start", "-a", "android.intent.action.VIEW", "-d", url]) != 0:
        ejecutar(["termux-open-url", url])

def notificar(texto):
    ejecutar(["termux-notification", "--title", NOMBRE, "--content", texto])

def elegir_plataforma():
    print("\nPlataformas:")
    for k, (n, _) in PLATAFORMAS.items():
        print(f"{k}) {n}")
    print("0) Otra")
    op = input("Plataforma: ").strip()
    if op == "0":
        nombre = re.sub(r"[^a-z0-9_-]", "", input("Nombre de la plataforma: ").strip().lower())[:20]
        base = input("Link base del perfil (ej. https://sitio.com/): ").strip()
        return (nombre or "otra"), base
    return PLATAFORMAS.get(op, PLATAFORMAS["1"])

def elegir_categoria():
    print("\nCategorías:")
    for i, c in enumerate(CATEGORIAS, 1):
        print(f"{i}) {c}")
    r = input("Elige una (Enter para dejarla igual): ").strip()
    if r.isdigit() and 1 <= int(r) <= len(CATEGORIAS):
        return CATEGORIAS[int(r) - 1]
    return ""

def cliente():
    banner()
    a = llamar("/anuncio")
    if a.get("texto"):
        print()
        caja(a["texto"])
    nombre, base = elegir_plataforma()
    user = input("Tu usuario (sin @): ").strip().lstrip("@").lower()
    cat = elegir_categoria()
    base_d = {"plataforma": nombre, "usuario": user}
    r = llamar("/registrar", {**base_d, "categoria": cat})
    if r.get("error"):
        print("Tu cuenta está bloqueada." if r["error"] == "bloqueado" else f"Error: {r['error']}")
        return
    cons = f"?plataforma={nombre}&usuario={user}"

    def recorrer(lista):
        for u in lista:
            print(f"\n→ {base}{u}")
            abrir(f"{base}{u}")
            resp = input("¿Ya lo seguiste? (s/n/salir): ").strip().lower()
            if resp == "s":
                r = llamar("/hecho", {**base_d, "a": u})
                if r.get("error") == "limite":
                    print(f"\nLlegaste a tu límite de hoy ({r['limite']}).")
                    print("Vuelve mañana o activa PRO (opción 7).")
                    return
                print("Anotado ✓")
            elif resp == "salir":
                return

    primera = True
    while True:
        s = llamar("/stats" + cons)
        if "error" in s:
            print("Error:", s["error"]); break
        if primera:
            primera = False
            avisos = []
            if s["por_devolver"]:
                avisos.append(f"{s['por_devolver']} persona(s) te siguieron y te falta devolver")
            if s["por_verificar"]:
                avisos.append(f"{s['por_verificar']} por verificar")
            if avisos:
                texto = "Recordatorio: " + " y ".join(avisos) + "."
                print("\n🔔 " + texto)
                notificar(texto)
        plan = f"PRO hasta {s['expira']}" if s["plan"] == "PRO" else "GRATIS"
        print(f"\n[{plan}] Hoy te quedan {s['restantes']}/{s['limite']} · "
              f"Puntos: {s['puntos']} · Categoría: {s['categoria']}")
        print("1) Ver gente para seguir")
        print(f"2) Verificar quién me siguió ({s['por_verificar']} pendientes)")
        print(f"3) Devolver seguimientos ({s['por_devolver']} pendientes)")
        print("4) Mis números")
        print("5) Ranking")
        print("6) Cambiar categoría")
        print("7) Activar licencia PRO")
        print("8) Salir")
        op = input("> ").strip()

        if op == "8":
            break

        elif op == "1":
            f = input("Enter = todas las categorías, 2 = solo mi categoría: ").strip()
            extra = f"&categoria={parse.quote(s['categoria'])}" if f == "2" else ""
            r = llamar("/lista" + cons + extra)
            if "error" in r:
                print("Error:", r["error"]); continue
            if not r["lista"]:
                print("No hay nadie por ahora. Vuelve más tarde."); continue
            recorrer(r["lista"])

        elif op == "2":
            r = llamar("/porverificar" + cons)
            if "error" in r:
                print("Error:", r["error"]); continue
            if not r["lista"]:
                print("No tienes seguidores por verificar."); continue
            print("\nTe abro tu perfil: revisa tu lista de seguidores.")
            abrir(f"{base}{user}")
            for u in r["lista"]:
                resp = input(f"¿@{u} te está siguiendo de verdad? (s/n/salir): ").strip().lower()
                if resp == "salir":
                    break
                if resp in ("s", "n"):
                    llamar("/confirmar", {**base_d, "de": u, "ok": resp == "s"})
                    print("Confirmado ✓" if resp == "s" else "Reportado ✗")

        elif op == "3":
            r = llamar("/devolver" + cons)
            if "error" in r:
                print("Error:", r["error"]); continue
            if not r["lista"]:
                print("No tienes a nadie pendiente de devolver."); continue
            recorrer(r["lista"])

        elif op == "4":
            print(f"\nPlan:                       {plan}")
            print(f"Seguiste a:                 {s['seguiste']}")
            print(f"De ellos, te confirmaron:   {s['confirmaron']}")
            print(f"Te siguieron (verificado):  {s['te_siguieron']}")
            print(f"Por verificar:              {s['por_verificar']}")
            print(f"Reportes en tu contra:      {s['strikes']}/{s['max_strikes']}")
            print(f"Puntos:                     {s['puntos']}")

        elif op == "5":
            r = llamar("/ranking" + cons)
            if "error" in r:
                print("Error:", r["error"]); continue
            print(f"\n=== RANKING {nombre.upper()} ===")
            for i, (nom, pts, pro) in enumerate(r["top"], 1):
                print(f"{i}. @{nom} — {pts} pts{' [PRO]' if pro else ''}")
            print(f"\nTu posición: {r['mi_pos']} de {r['total']} ({r['mis_puntos']} pts)")
            print("Puntos: +2 por cada persona a la que seguiste y te confirmó,")
            print("+1 por cada verificación que haces, -5 por cada reporte.")

        elif op == "6":
            nueva = elegir_categoria()
            if nueva:
                llamar("/registrar", {**base_d, "categoria": nueva})
                print("Categoría actualizada ✓")

        elif op == "7":
            print(f"\n{PRECIO}\n{PRO_TEXTO}\n{CONTACTO}\n")
            clave = input("Pega tu licencia (Enter para cancelar): ").strip()
            if clave:
                r = llamar("/activar", {**base_d, "licencia": clave})
                print(f"¡PRO activado hasta {r['expira']}! ✓" if r.get("ok")
                      else r.get("error", "Error"))

# ======================= PANEL DE CONTROL =======================
def panel():
    """Solo para ti. Ejecútalo en el mismo dispositivo y carpeta del servidor,
    en otra sesión de Termux:  python seguidores.py panel"""
    base = f"http://127.0.0.1:{os.environ.get('PORT', 8080)}"

    def cabeceras():
        try:
            with open(KEY_FILE) as f:
                return {"X-Admin-Key": f.read().strip(), "Content-Type": "application/json"}
        except FileNotFoundError:
            return {"Content-Type": "application/json"}

    def get(ruta):
        try:
            return json.load(request.urlopen(
                request.Request(base + ruta, headers=cabeceras()), timeout=10))
        except Exception as e:
            return {"error": str(e)}

    def post(ruta, datos):
        try:
            return json.load(request.urlopen(request.Request(
                base + ruta, json.dumps(datos).encode(), cabeceras()), timeout=10))
        except Exception as e:
            return {"error": str(e)}

    def pedir_clave():
        nombre = elegir_plataforma()[0]
        u = input("Usuario (sin @): ").strip().lstrip("@").lower()
        return f"{nombre}:{u}"

    banner("Panel de control")
    while True:
        print("\n1) Resumen general")
        print("2) Usuarios más activos")
        print("3) Usuarios con reportes o bloqueados")
        print("4) Bloquear usuario")
        print("5) Desbloquear usuario")
        print("6) Quitar reportes a un usuario")
        print("7) Crear licencia PRO")
        print("8) Ver licencias")
        print("9) Cambiar anuncio (banner para todos)")
        print("10) Salir")
        op = input("> ").strip()
        if op == "10":
            break
        if op == "1":
            r = get("/admin/resumen")
            if "error" in r:
                print("Error:", r["error"], "(¿servidor encendido y en esta carpeta?)"); continue
            print(f"\nUsuarios: {r['usuarios']}  (PRO: {r['pro']})")
            for p, n in r["por_plataforma"].items():
                print(f"  - {p}: {n}")
            print(f"Seguimientos hechos:      {r['seguimientos']}")
            print(f"Seguimientos confirmados: {r['confirmados']}")
            print(f"Seguimientos rechazados:  {r['rechazados']}")
            print(f"Usuarios bloqueados:      {r['bloqueados']}")
            print(f"Licencias: {r['licencias']} (libres: {r['licencias_libres']})")
        elif op == "2":
            r = get("/admin/top")
            if "error" in r:
                print("Error:", r["error"]); continue
            print("\nUsuario | siguió | confirmados | reportes")
            for k, a, c, st in r["lista"]:
                print(f"{k} | {a} | {c} | {st}")
        elif op == "3":
            r = get("/admin/reportados")
            if "error" in r:
                print("Error:", r["error"]); continue
            if not r["lista"]:
                print("Nadie con reportes ni bloqueado."); continue
            print("\nUsuario | reportes | bloqueado")
            for k, st, b in r["lista"]:
                print(f"{k} | {st} | {'SÍ' if b else 'no'}")
        elif op in ("4", "5", "6"):
            clave = pedir_clave()
            if op == "6":
                r = post("/admin/limpiar", {"clave": clave})
            else:
                r = post("/admin/bloquear", {"clave": clave, "bloquear": op == "4"})
            print("Hecho ✓" if r.get("ok") else f"No se pudo: {r.get('error')}")
        elif op == "7":
            dias = input("¿Cuántos días dura? (Enter = 30): ").strip()
            r = post("/admin/crear_licencia", {"dias": int(dias) if dias.isdigit() else 30})
            if r.get("ok"):
                print(f"\nLicencia creada ({r['dias']} días):\n\n   {r['licencia']}\n")
                print("Entrégasela a quien te pague. Solo se puede usar una vez.")
            else:
                print("Error:", r.get("error"))
        elif op == "8":
            r = get("/admin/licencias")
            if "error" in r:
                print("Error:", r["error"]); continue
            if not r["lista"]:
                print("Aún no has creado licencias."); continue
            for c, d, estado in r["lista"]:
                print(f"{c} | {d} días | {estado}")
        elif op == "9":
            texto = input("Texto del anuncio (Enter = quitarlo): ").strip()
            r = post("/admin/anuncio", {"texto": texto})
            print("Anuncio actualizado ✓" if r.get("ok") else f"Error: {r.get('error')}")

if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "servidor":
        servidor()
    elif modo == "panel":
        panel()
    else:
        cliente()
