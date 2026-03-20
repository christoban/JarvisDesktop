# PATCH jarvis_bridge.py + agent.py + intent_executor.py
# Corrections B3, B4, B6, B7, B16
# Appliquer manuellement — recherche/remplacement exact

==============================================================
## PATCH 1 — jarvis_bridge.py — B3 : ThreadingHTTPServer
==============================================================

### CHERCHER (ligne ~15) :
```python
from http.server import BaseHTTPRequestHandler, HTTPServer
```

### REMPLACER PAR :
```python
from http.server import BaseHTTPRequestHandler, HTTPServer
try:
    from http.server import ThreadingHTTPServer
except ImportError:
    ThreadingHTTPServer = HTTPServer  # Python < 3.7 fallback
```

---

### CHERCHER (vers la fin du fichier, dans le bloc if __name__ == "__main__") :
```python
    server = HTTPServer(("0.0.0.0", PORT), BridgeHandler)
```

### REMPLACER PAR :
```python
    server = ThreadingHTTPServer(("0.0.0.0", PORT), BridgeHandler)
```

---

==============================================================
## PATCH 2 — jarvis_bridge.py — B4 : nettoyage _results
==============================================================

### CHERCHER dans la fonction _execute(), le bloc suivant :
```python
        with _store_lock:
            _results[cmd_id] = {
                "result":      result,
                "executed_at": int(time.time()),
                "duration_ms": elapsed,
            }
```

### REMPLACER PAR :
```python
        with _store_lock:
            _results[cmd_id] = {
                "result":      result,
                "executed_at": int(time.time()),
                "duration_ms": elapsed,
            }
            # CORRECTION B4 : nettoyage automatique — garder max 200 résultats
            if len(_results) > 200:
                oldest_keys = sorted(_results.keys())[:len(_results) - 200]
                for _k in oldest_keys:
                    del _results[_k]
```

---

==============================================================
## PATCH 3 — core/agent.py — B6 : self._dr manquant
==============================================================

### Dans Agent.__init__(), CHERCHER :
```python
        self._voice    = JarvisVoice()
        self._memory   = JarvisMemory()
```

### REMPLACER PAR :
```python
        self._voice    = JarvisVoice()
        self._memory   = JarvisMemory()
        self._dr       = None   # DocReader — lazy init (correction B6)
```

---

==============================================================
## PATCH 4 — core/agent.py — B7 : import re dupliqué
==============================================================

### En haut du fichier agent.py, CHERCHER :
```python
import time
import re
from pathlib import Path
```

(vérifier que `import re` est bien présent en haut — si oui, ne rien changer)

### Puis dans la méthode _handle_followup(), CHERCHER :
```python
        r = reply.lower().strip()
        import re
```

### REMPLACER PAR :
```python
        r = reply.lower().strip()
        # import re est déjà en tête de fichier (correction B7)
```

### Puis dans _resolve_intent_clarification(), CHERCHER :
```python
        r = reply.lower().strip()
        if not choices:
            return None

        selected = None
        import re
```

### REMPLACER PAR :
```python
        r = reply.lower().strip()
        if not choices:
            return None

        selected = None
        # import re est déjà en tête de fichier (correction B7)
```

---

==============================================================
## PATCH 5 — core/intent_executor.py — B6 : self._dr manquant
==============================================================

### Dans IntentExecutor.__init__(), CHERCHER le bloc des attributs None :
```python
        self._sc = None   # SystemControl
        self._am = None   # AppManager
        self._fm = None   # FileManager
        self._bc = None   # BrowserControl
        self._au = None   # AudioManager
        self._nm = None   # NetworkManager
        self._history = None
        self._macros = None
        self._power = None
        self._window = None
        self._raw_command_agent = None
```

### REMPLACER PAR :
```python
        self._sc = None   # SystemControl
        self._am = None   # AppManager
        self._fm = None   # FileManager
        self._bc = None   # BrowserControl
        self._au = None   # AudioManager
        self._nm = None   # NetworkManager
        self._dr = None   # DocReader (correction B6)
        self._history = None
        self._macros = None
        self._power = None
        self._window = None
        self._raw_command_agent = None
```

### Et dans la propriété dr, CHERCHER :
```python
    @property
    def dr(self):
        """DocReader"""
        if not hasattr(self, '_dr') or self._dr is None:
            from modules.doc_reader import DocReader
            self._dr = DocReader()
        return self._dr
```

### REMPLACER PAR :
```python
    @property
    def dr(self):
        """DocReader"""
        if self._dr is None:
            from modules.doc_reader import DocReader
            self._dr = DocReader()
        return self._dr
```

---

==============================================================
## PATCH 6 — browser/browser_control.py — B16 : or True parasite
==============================================================

### Dans la méthode google_search(), CHERCHER :
```python
        if self._session.is_ready() or True:  # toujours essayer CDP
            ready = self._session.ensure_session(launch_if_missing=True)
```

### REMPLACER PAR :
```python
        # Toujours tenter CDP — ensure_session lance Chrome si nécessaire
        ready = self._session.ensure_session(launch_if_missing=True)
```

(Supprimer le `if` et diminuer l'indentation du bloc qui suit d'un niveau,
OU garder l'indentation et juste enlever le `if` en laissant le code à plat)

La version la plus simple — remplacer le bloc complet par :
```python
        # CDP : recherche + extraction des résultats
        ready = self._session.ensure_session(launch_if_missing=True)
        if ready["success"]:
            search_url = SITE_SEARCH_URLS.get(engine.lower(), SITE_SEARCH_URLS["google"])
            url = search_url.format(quote_plus(query))
            # ... (reste du code inchangé, juste sans le if externe)
```

==============================================================
## RÉCAPITULATIF DES FICHIERS TOUCHÉS
==============================================================

| Fichier | Patches | Lignes modifiées |
|---|---|---|
| jarvis_bridge.py | B3, B4 | ~5 lignes |
| core/agent.py | B6, B7 | ~4 lignes |
| core/intent_executor.py | B6 | ~2 lignes |
| browser/browser_control.py | B16 | ~2 lignes |

Temps estimé : 15 minutes
