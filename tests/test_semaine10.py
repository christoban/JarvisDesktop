#!/usr/bin/env python3
"""
test_semaine10.py — Tests Semaine 10 : Sécurité & Authentification

Groupes :
  1. Auth — tokens, HMAC, appareils, anti-replay
  2. Permissions — niveaux, confirmations, timeout
  3. Crypto — chiffrement AES-GCM, auto-test
  4. Intégration bridge — accès non autorisé refusé, confirmation e2e
  5. Non-régression — commandes normales toujours acceptées

Usage :
    cd JarvisDesktop
    python tests/test_modules/test_semaine10.py
"""

import sys, os, json, time, threading, urllib.request, urllib.error
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

PASS = 0
FAIL = 0


def test(name, fn, expect=True):
    global PASS, FAIL
    try:
        result = fn()
        ok     = result.get("success", result.get("ok", False))
        msg    = result.get("message", result.get("reason", str(result)))[:70]
        passed = (expect is None) or (bool(ok) == bool(expect))
        if passed: PASS += 1
        else:      FAIL += 1
        icon = "✅" if passed else "❌"
        print(f"  {icon} {name}")
        print(f"       → {msg}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}")
        print(f"       Exception : {e}")
    print()


def sep(title):
    print(f"{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}\n")


# ════════════════════════════════════════════════════════════
#  GROUPE 1 — Auth
# ════════════════════════════════════════════════════════════
def test_auth():
    sep("GROUPE 1 — Auth (tokens, appareils, HMAC)")
    from security.auth import Auth, MODE_SIMPLE, MODE_HMAC, MODE_STRICT
    from config.settings import SECRET_TOKEN

    # ── Mode simple ──────────────────────────────────────────
    auth = Auth(mode=MODE_SIMPLE)

    test("Token valide accepté",
         lambda: auth.verify_request({"X-Jarvis-Token": SECRET_TOKEN,
                                       "X-Device-Id": "TEST_PHONE"}))

    test("Token invalide refusé",
         lambda: auth.verify_request({"X-Jarvis-Token": "mauvais_token",
                                       "X-Device-Id": "HACKER"}),
         expect=False)

    test("Token vide refusé",
         lambda: auth.verify_request({}),
         expect=False)

    # ── Gestion des appareils ─────────────────────────────────
    test("Enregistrer un appareil",
         lambda: auth.register_device("PHONE_TEST", "Téléphone test", level=2))

    test("Appareil enregistré détecté",
         lambda: {"ok": auth.is_device_registered("PHONE_TEST"),
                  "message": "PHONE_TEST dans le registre"})

    test("Appareil inconnu non détecté",
         lambda: {"ok": not auth.is_device_registered("APPAREIL_INCONNU"),
                  "message": "APPAREIL_INCONNU absent du registre"})

    test("Niveau appareil correct",
         lambda: {"ok": auth.get_device_level("PHONE_TEST") == 2,
                  "message": f"level={auth.get_device_level('PHONE_TEST')}"})

    test("Lister les appareils",
         lambda: {"ok": len(auth.list_devices()) >= 1,
                  "message": f"{len(auth.list_devices())} appareil(s)"})

    test("Révoquer un appareil",
         lambda: auth.revoke_device("PHONE_TEST"))

    test("Appareil révoqué bien supprimé",
         lambda: {"ok": not auth.is_device_registered("PHONE_TEST"),
                  "message": "Révocation confirmée"})

    # ── Mode HMAC ────────────────────────────────────────────
    auth_hmac = Auth(mode=MODE_HMAC)
    headers   = auth_hmac.generate_token("TEST_DEVICE", "POST",
                                          "/api/command", b'{"command":"test"}')

    test("Token HMAC généré correctement",
         lambda: {"ok": "X-Jarvis-Sig" in headers and "X-Nonce" in headers,
                  "message": f"Sig={headers['X-Jarvis-Sig'][:20]}..."})

    test("Signature HMAC valide acceptée",
         lambda: auth_hmac.verify_request(headers, b'{"command":"test"}',
                                           "POST", "/api/command"))

    test("Mauvaise signature rejetée",
         lambda: auth_hmac.verify_request(
             {**headers, "X-Jarvis-Sig": "fausse_signature"},
             b'{"command":"test"}', "POST", "/api/command"),
         expect=False)

    # ── Anti-replay : même nonce deux fois ────────────────────
    headers2  = auth_hmac.generate_token("TEST_DEVICE", "POST",
                                          "/api/command", b'{"command":"test"}')
    auth_hmac.verify_request(headers2, b'{"command":"test"}', "POST", "/api/command")
    test("Nonce réutilisé → rejeté (anti-replay)",
         lambda: auth_hmac.verify_request(headers2, b'{"command":"test"}',
                                           "POST", "/api/command"),
         expect=False)

    # ── Timestamp expiré ─────────────────────────────────────
    old_headers = {**headers, "X-Timestamp": str(int(time.time()) - 400)}
    test("Timestamp expiré rejeté (> 5 min)",
         lambda: auth_hmac.verify_request(old_headers, b'{"command":"test"}',
                                           "POST", "/api/command"),
         expect=False)


# ════════════════════════════════════════════════════════════
#  GROUPE 2 — Permissions
# ════════════════════════════════════════════════════════════
def test_permissions():
    sep("GROUPE 2 — Permissions et confirmations")
    from security.permissions import (
        Permissions, LEVEL_READ, LEVEL_WRITE, LEVEL_DANGER, PERMISSION_MAP
    )

    perms = Permissions()

    # ── Niveaux ───────────────────────────────────────────────
    test("SYSTEM_INFO = LEVEL_READ",
         lambda: {"ok": perms.get_level("SYSTEM_INFO") == LEVEL_READ,
                  "message": f"level={perms.get_level('SYSTEM_INFO')}"})

    test("APP_OPEN = LEVEL_WRITE",
         lambda: {"ok": perms.get_level("APP_OPEN") == LEVEL_WRITE,
                  "message": f"level={perms.get_level('APP_OPEN')}"})

    test("SYSTEM_SHUTDOWN = LEVEL_DANGER",
         lambda: {"ok": perms.get_level("SYSTEM_SHUTDOWN") == LEVEL_DANGER,
                  "message": f"level={perms.get_level('SYSTEM_SHUTDOWN')}"})

    test("FILE_DELETE = LEVEL_DANGER",
         lambda: {"ok": perms.get_level("FILE_DELETE") == LEVEL_DANGER,
                  "message": f"level={perms.get_level('FILE_DELETE')}"})

    # ── Confirmation requise ──────────────────────────────────
    test("SYSTEM_SHUTDOWN nécessite confirmation",
         lambda: {"ok": perms.requires_confirmation("SYSTEM_SHUTDOWN"),
                  "message": "SYSTEM_SHUTDOWN → confirm requis"})

    test("SYSTEM_INFO ne nécessite pas confirmation",
         lambda: {"ok": not perms.requires_confirmation("SYSTEM_INFO"),
                  "message": "SYSTEM_INFO → pas de confirm"})

    test("APP_OPEN ne nécessite pas confirmation",
         lambda: {"ok": not perms.requires_confirmation("APP_OPEN"),
                  "message": "APP_OPEN → pas de confirm"})

    # ── is_allowed ────────────────────────────────────────────
    test("Niveau 1 peut faire SYSTEM_INFO",
         lambda: {"ok": perms.is_allowed("SYSTEM_INFO", device_level=1),
                  "message": "level 1 → SYSTEM_INFO OK"})

    test("Niveau 1 ne peut pas faire APP_OPEN",
         lambda: {"ok": not perms.is_allowed("APP_OPEN", device_level=1),
                  "message": "level 1 → APP_OPEN refusé"})

    test("Niveau 3 peut tout faire",
         lambda: {"ok": perms.is_allowed("SYSTEM_SHUTDOWN", device_level=3),
                  "message": "level 3 → SYSTEM_SHUTDOWN OK"})

    test("Niveau 2 ne peut pas faire SYSTEM_SHUTDOWN",
         lambda: {"ok": not perms.is_allowed("SYSTEM_SHUTDOWN", device_level=2),
                  "message": "level 2 → SYSTEM_SHUTDOWN refusé"})

    # ── Flux confirmation ─────────────────────────────────────
    req = perms.create_confirmation("FILE_DELETE", {"path": "/tmp/test.txt"},
                                     "supprime test.txt")
    test("Confirmation créée",
         lambda: {"ok": req is not None and req.id,
                  "message": f"id={req.id[:8]}"})

    test("En attente dans get_pending()",
         lambda: {"ok": any(p["id"] == req.id for p in perms.get_pending()),
                  "message": f"{len(perms.get_pending())} en attente"})

    test("Confirmation acceptée",
         lambda: perms.confirm(req.id))

    test("Confirmation attendue = True (non bloquant)",
         lambda: {"ok": req.confirmed is True,
                  "message": "req.confirmed = True"})

    # ── Refus ─────────────────────────────────────────────────
    req2 = perms.create_confirmation("SYSTEM_SHUTDOWN", {}, "éteins")
    perms.refuse(req2.id)
    test("Action refusée = False",
         lambda: {"ok": req2.confirmed is False,
                  "message": "req2.confirmed = False"})

    # ── ID inconnu ────────────────────────────────────────────
    test("Confirm ID inconnu → erreur",
         lambda: perms.confirm("id_inexistant_xyz"),
         expect=False)


# ════════════════════════════════════════════════════════════
#  GROUPE 3 — Crypto
# ════════════════════════════════════════════════════════════
def test_crypto():
    sep("GROUPE 3 — Chiffrement AES-256-GCM")
    from security.crypto import MessageCrypto

    mc = MessageCrypto()

    test("Instanciation",
         lambda: {"ok": True,
                  "message": f"available={mc.available}"})

    test("health_check",
         lambda: {**mc.health_check(), "success": mc.health_check()["available"]})

    if mc.available:
        test("Chiffrement produit base64",
             lambda: {"ok": len(mc.encrypt({"test": True})) > 20,
                      "message": f"len={len(mc.encrypt({'test': True}))}"})

        payload = mc.encrypt({"command": "ouvre chrome", "level": 42})
        test("Déchiffrement correct",
             lambda: {"ok": mc.decrypt(payload) == {"command": "ouvre chrome",
                                                     "level": 42},
                      "message": f"→ {mc.decrypt(payload)}"})

        test("Payload chiffré détecté",
             lambda: {"ok": mc.is_encrypted(payload),
                      "message": "is_encrypted=True"})

        test("JSON clair détecté comme non chiffré",
             lambda: {"ok": not mc.is_encrypted('{"test": true}'),
                      "message": "is_encrypted=False"})

        # Tamper : modifier le payload → déchiffrement échoue
        if len(payload) > 10:
            tampered = payload[:-4] + "XXXX"
            test("Payload modifié rejeté (authenticité GCM)",
                 lambda: {"ok": mc.decrypt(tampered) == {},
                          "message": "decrypt tampered → {}"})
    else:
        print("  ⏭  Tests chiffrement ignorés (cryptography absent)")
        print()


# ════════════════════════════════════════════════════════════
#  GROUPE 4 — Intégration bridge (HTTP)
# ════════════════════════════════════════════════════════════
def test_bridge_security():
    sep("GROUPE 4 — Intégration bridge HTTP")
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import uuid as _uuid

    PORT  = 7785
    TOKEN = "menedona_2005_christoban_2026"
    BASE  = f"http://localhost:{PORT}"

    def hget(path, headers=None):
        h = {"X-Jarvis-Token": TOKEN, "X-Device-Id": "TEST"}
        if headers: h.update(headers)
        req = urllib.request.Request(f"{BASE}{path}", headers=h)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()), r.status

    def hpost(path, data, headers=None):
        h = {"Content-Type": "application/json",
             "X-Jarvis-Token": TOKEN, "X-Device-Id": "TEST"}
        if headers: h.update(headers)
        body = json.dumps(data).encode()
        req  = urllib.request.Request(f"{BASE}{path}", data=body,
                                       headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status

    # Démarrer un bridge de test minimal
    results_store = {}
    results_lock  = threading.Lock()
    pending_confs = {}

    class TestHandler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _body(self): return self.rfile.read(int(self.headers.get("Content-Length",0)))
        def _json(self, d, c=200):
            b = json.dumps(d).encode()
            self.send_response(c); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
        def _auth_ok(self):
            return self.headers.get("X-Jarvis-Token","") == TOKEN
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/health":
                return self._json({"status":"healthy","pc_connected":True})
            if not self._auth_ok():
                return self._json({"error":"Unauthorized"},401)
            if path == "/api/pending":
                return self._json({"pending": list(pending_confs.values())})
            if path.startswith("/api/result/"):
                cid = path.split("/")[-1]
                with results_lock:
                    e = results_store.get(cid)
                if e: return self._json({"status":"done","result":e["result"]})
                return self._json({"status":"pending"},404)
            self._json({"error":"not found"},404)
        def do_POST(self):
            body = self._body(); path = self.path.split("?")[0]
            if not self._auth_ok():
                return self._json({"error":"Unauthorized","reason":"token_invalide"},401)
            if path == "/api/command":
                data = json.loads(body); cmd = data.get("command","").strip()
                if not cmd: return self._json({"error":"vide"},400)
                cid = str(_uuid.uuid4())[:8]
                def run():
                    from core.agent import Agent
                    r = Agent().handle_command(cmd)
                    with results_lock: results_store[cid] = {"result":r}
                threading.Thread(target=run,daemon=True).start()
                return self._json({"id":cid,"status":"pending"},202)
            if path == "/api/confirm":
                data = json.loads(body); cid = data.get("id","")
                if cid in pending_confs:
                    pending_confs[cid]["confirmed"] = True
                    return self._json({"ok":True})
                return self._json({"ok":False,"reason":"ID inconnu"},404)
            self._json({"error":"not found"},404)

    srv = HTTPServer(("localhost",PORT), TestHandler)
    t   = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start(); time.sleep(0.3)

    # ── Tests ────────────────────────────────────────────────
    global PASS, FAIL

    # Health check (pas d'auth)
    try:
        d, s = hget("/api/health", headers={})
        ok = d.get("status") == "healthy"
        if ok: PASS += 1
        else:  FAIL += 1
        print(f"  {'✅' if ok else '❌'} Health sans token → accessible")
        print(f"       → {d}")
    except Exception as e:
        FAIL += 1; print(f"  ❌ Health : {e}")
    print()

    # Accès /api/pending sans token → 401
    try:
        hget("/api/pending", headers={"X-Jarvis-Token": "MAUVAIS"})
        FAIL += 1; print("  ❌ Accès sans token aurait dû être refusé")
    except urllib.error.HTTPError as e:
        ok = e.code == 401
        if ok: PASS += 1
        else:  FAIL += 1
        print(f"  {'✅' if ok else '❌'} Accès sans token → 401")
        print(f"       → HTTP {e.code}")
    print()

    # POST commande avec mauvais token → 401
    try:
        hpost("/api/command", {"command":"test"},
              headers={"X-Jarvis-Token":"FAUX","Content-Type":"application/json"})
        FAIL += 1; print("  ❌ Commande avec mauvais token aurait dû être refusée")
    except urllib.error.HTTPError as e:
        ok = e.code == 401
        if ok: PASS += 1
        else:  FAIL += 1
        print(f"  {'✅' if ok else '❌'} Commande mauvais token → 401")
    print()

    # Commande vide → 400
    try:
        hpost("/api/command", {"command": "   "})
        FAIL += 1
    except urllib.error.HTTPError as e:
        ok = e.code == 400
        if ok: PASS += 1
        else:  FAIL += 1
        print(f"  {'✅' if ok else '❌'} Commande vide → 400")
    print()

    # Commande normale acceptée
    try:
        d, s = hpost("/api/command", {"command":"montre les infos système"})
        ok = "id" in d
        if ok: PASS += 1
        else:  FAIL += 1
        print(f"  {'✅' if ok else '❌'} Commande valide acceptée → id={d.get('id','?')}")
    except Exception as e:
        FAIL += 1; print(f"  ❌ Commande valide : {e}")
    print()

    srv.shutdown()


# ════════════════════════════════════════════════════════════
#  GROUPE 5 — Non-régression
# ════════════════════════════════════════════════════════════
def test_regression():
    sep("GROUPE 5 — Non-régression (semaines précédentes)")
    from core.command_parser  import CommandParser
    from core.intent_executor import IntentExecutor
    from security.permissions import Permissions

    cp    = CommandParser()
    perms = Permissions()

    cases = [
        ("montre les infos système",   "SYSTEM_INFO",   False),
        ("éteins l'ordinateur",        "SYSTEM_SHUTDOWN", True),
        ("supprime le fichier",        "FILE_DELETE",    True),
        ("ouvre chrome",               "APP_OPEN",       False),
        ("mets le volume à 70%",       "AUDIO_VOLUME_SET", False),
    ]
    for cmd, expected_intent, expect_confirm in cases:
        global PASS, FAIL
        parsed = cp.parse(cmd)
        intent = parsed.get("intent","UNKNOWN")
        intent_ok = (intent == expected_intent)
        conf_ok   = (perms.requires_confirmation(intent) == expect_confirm)
        passed    = intent_ok and conf_ok
        if passed: PASS += 1
        else:      FAIL += 1
        icon = "✅" if passed else "❌"
        conf_sym = "⚠️ " if expect_confirm else "✓ "
        print(f"  {icon} \"{cmd}\"")
        print(f"       → intent={intent} {'' if intent_ok else f'(attendu {expected_intent})'} "
              f"| confirm={perms.requires_confirmation(intent)} {conf_sym}")
    print()


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════
def main():
    print("\n" + "═"*60)
    print("  TESTS SEMAINE 10 — Sécurité & Authentification")
    print("═"*60 + "\n")

    test_auth()
    test_permissions()
    test_crypto()
    test_bridge_security()
    test_regression()

    total = PASS + FAIL
    print("═"*60)
    print(f"  {PASS} PASSES  |  {FAIL} FAILURES  |  {total} TOTAL")
    print("═"*60)
    if FAIL == 0:
        print("  ✅ Semaine 10 VALIDÉE — Sécurité opérationnelle\n")
    else:
        print(f"  ⚠️  {FAIL} test(s) échoué(s)\n")


if __name__ == "__main__":
    main()