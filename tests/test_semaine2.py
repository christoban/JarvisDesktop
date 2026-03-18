
"""
test_system_control.py — Tests complets du module SystemControl
Vendredi Semaine 2 : validation de toutes les fonctions

COMMENT LANCER :
    cd jarvis_windows
    python -m pytest tests/test_modules/test_system_control.py -v

ATTENTION : les fonctions d'alimentation (shutdown, restart, sleep, lock)
sont testées en mode "simulation" uniquement pour ne pas redémarrer la machine.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from modules.system_control import SystemControl

print(sys.path)

# ══════════════════════════════════════════════════════════════════════════════
#  INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

def get_sc():
    return SystemControl()

def assert_response(result: dict, expected_success: bool = None):
    """Vérifie le format standard de toute réponse."""
    assert isinstance(result, dict), f"La réponse doit être un dict, reçu : {type(result)}"
    assert "success" in result,  "Clé 'success' manquante"
    assert "message" in result,  "Clé 'message' manquante"
    assert "data"    in result,  "Clé 'data' manquante"
    assert isinstance(result["message"], str), "Le message doit être une str"
    if expected_success is not None:
        assert result["success"] == expected_success, (
            f"Attendu success={expected_success}, reçu : {result}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS FORMAT & INSTANTIATION
# ══════════════════════════════════════════════════════════════════════════════

def test_instantiation():
    """SystemControl s'instancie sans erreur."""
    sc = get_sc()
    assert sc is not None

def test_ok_helper():
    """_ok retourne le bon format."""
    result = SystemControl._ok("Test OK", {"k": "v"})
    assert result["success"] == True
    assert result["message"] == "Test OK"
    assert result["data"] == {"k": "v"}

def test_err_helper():
    """_err retourne le bon format."""
    result = SystemControl._err("Erreur test")
    assert result["success"] == False
    assert "Erreur test" in result["message"]
    assert result["data"] is None

def test_format_uptime_seconds():
    """_format_uptime : moins d'une minute."""
    assert SystemControl._format_uptime(30) == "< 1m"

def test_format_uptime_minutes():
    """_format_uptime : minutes seulement."""
    assert SystemControl._format_uptime(90) == "1m"

def test_format_uptime_hours():
    """_format_uptime : heures + minutes."""
    result = SystemControl._format_uptime(3690)  # 1h 1m 30s
    assert "1h" in result
    assert "1m" in result

def test_format_uptime_days():
    """_format_uptime : jours + heures + minutes."""
    result = SystemControl._format_uptime(90060)  # 1j 1h 1m
    assert "1j" in result
    assert "1h" in result


# ══════════════════════════════════════════════════════════════════════════════
#  MARDI — PROCESSUS & CPU/RAM
# (Sûrs à tester, ne modifient pas le système)
# ══════════════════════════════════════════════════════════════════════════════

def test_list_processes_returns_correct_format():
    """list_processes retourne le bon format."""
    sc = get_sc()
    result = sc.list_processes()
    assert_response(result, expected_success=True)
    data = result["data"]
    assert "processes"  in data
    assert "total"      in data
    assert "display"    in data
    assert isinstance(data["processes"], list)

def test_list_processes_not_empty():
    """list_processes retourne au moins 1 processus."""
    sc = get_sc()
    result = sc.list_processes()
    assert len(result["data"]["processes"]) > 0

def test_list_processes_top_limit():
    """list_processes respecte la limite top."""
    sc = get_sc()
    for top in [5, 10, 20]:
        result = sc.list_processes(top=top)
        assert len(result["data"]["processes"]) <= top

def test_list_processes_process_structure():
    """Chaque processus a les bons champs."""
    sc = get_sc()
    result = sc.list_processes(top=5)
    for proc in result["data"]["processes"]:
        assert "pid"         in proc
        assert "name"        in proc
        assert "cpu_percent" in proc
        assert "ram_mb"      in proc
        assert "status"      in proc
        assert isinstance(proc["pid"], int)
        assert isinstance(proc["name"], str)
        assert isinstance(proc["cpu_percent"], float)
        assert isinstance(proc["ram_mb"], float)

def test_list_processes_sort_by_cpu():
    """Tri par CPU : le premier processus a >= CPU que le second."""
    sc = get_sc()
    result = sc.list_processes(top=10, sort_by="cpu")
    procs = result["data"]["processes"]
    if len(procs) >= 2:
        assert procs[0]["cpu_percent"] >= procs[1]["cpu_percent"]

def test_list_processes_sort_by_ram():
    """Tri par RAM : le premier processus a >= RAM que le second."""
    sc = get_sc()
    result = sc.list_processes(top=10, sort_by="ram")
    procs = result["data"]["processes"]
    if len(procs) >= 2:
        assert procs[0]["ram_mb"] >= procs[1]["ram_mb"]

def test_kill_process_invalid_pid():
    """kill_process avec un PID inexistant retourne success=False."""
    sc = get_sc()
    result = sc.kill_process(9999999)
    assert_response(result, expected_success=False)

def test_kill_process_invalid_name():
    """kill_process avec un nom inexistant retourne success=False."""
    sc = get_sc()
    result = sc.kill_process("processus_qui_nexiste_pas_jarvis_xyz")
    assert_response(result, expected_success=False)
    assert "not" in result["message"].lower() or "aucun" in result["message"].lower()


# ══════════════════════════════════════════════════════════════════════════════
#  MARDI — INFOS SYSTÈME
# ══════════════════════════════════════════════════════════════════════════════

def test_system_info_format():
    """system_info retourne le bon format avec toutes les clés."""
    sc = get_sc()
    result = sc.system_info()
    assert_response(result, expected_success=True)
    data = result["data"]
    assert "cpu"     in data
    assert "ram"     in data
    assert "swap"    in data
    assert "system"  in data
    assert "display" in data

def test_system_info_cpu_values():
    """CPU : valeurs cohérentes."""
    sc = get_sc()
    data = sc.system_info()["data"]["cpu"]
    assert 0 <= data["percent"] <= 100, f"CPU% hors limites : {data['percent']}"
    assert data["cores_physical"] >= 1
    assert data["cores_logical"]  >= data["cores_physical"]
    assert data["freq_mhz"]       >= 0

def test_system_info_ram_values():
    """RAM : valeurs cohérentes."""
    sc = get_sc()
    data = sc.system_info()["data"]["ram"]
    assert data["total_gb"]  > 0
    assert data["used_gb"]   >= 0
    assert data["free_gb"]   >= 0
    assert 0 <= data["percent"] <= 100
    # used + free ≈ total (la différence peut être du cache OS)
    assert data["used_gb"] + data["free_gb"] <= data["total_gb"] + 1

def test_system_info_system_fields():
    """Informations OS : champs présents et non vides."""
    sc = get_sc()
    sys_data = sc.system_info()["data"]["system"]
    assert sys_data["os"]       != ""
    assert sys_data["hostname"] != ""
    assert sys_data["uptime"]   != ""
    assert sys_data["boot_time"] != ""

def test_system_info_display_is_string():
    """Le display formaté est une chaîne non vide."""
    sc = get_sc()
    display = sc.system_info()["data"]["display"]
    assert isinstance(display, str)
    assert len(display) > 50


# ══════════════════════════════════════════════════════════════════════════════
#  MERCREDI — DISQUE
# ══════════════════════════════════════════════════════════════════════════════

def test_disk_info_format():
    """disk_info retourne le bon format."""
    sc = get_sc()
    result = sc.disk_info()
    assert_response(result, expected_success=True)
    assert "partitions" in result["data"]
    assert "display"    in result["data"]

def test_disk_info_at_least_one_partition():
    """Au moins une partition est détectée."""
    sc = get_sc()
    result = sc.disk_info()
    partitions = result["data"]["partitions"]
    assert len(partitions) >= 1

def test_disk_info_partition_structure():
    """Chaque partition a les bons champs et valeurs cohérentes."""
    sc = get_sc()
    result = sc.disk_info()
    for part in result["data"]["partitions"]:
        assert "device"    in part
        assert "total_gb"  in part
        assert "used_gb"   in part
        assert "free_gb"   in part
        assert "percent"   in part
        # Les champs numériques sont bien des floats/ints
        assert isinstance(part["total_gb"],  float)
        assert isinstance(part["used_gb"],   float)
        assert isinstance(part["free_gb"],   float)
        assert isinstance(part["percent"],   float)
        # Valeurs non négatives
        assert part["total_gb"]  >= 0
        assert part["used_gb"]   >= 0
        assert part["free_gb"]   >= 0
        assert 0 <= part["percent"] <= 100

def test_disk_info_sizes_consistent():
    """used + free ≈ total pour chaque partition."""
    sc = get_sc()
    for part in sc.disk_info()["data"]["partitions"]:
        total    = part["total_gb"]
        used     = part["used_gb"]
        free     = part["free_gb"]
        # Tolérance de 1 GB pour les arrondis
        assert abs((used + free) - total) < 1.5, (
            f"Incohérence {part['device']}: {used} + {free} != {total}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  MERCREDI — TEMPÉRATURE & RÉSEAU
# ══════════════════════════════════════════════════════════════════════════════

def test_temperature_info_returns_response():
    """temperature_info retourne toujours une réponse valide (même si indispo)."""
    sc = get_sc()
    result = sc.temperature_info()
    assert_response(result)
    # Peut être True ou False selon le système, mais doit toujours répondre

def test_network_info_format():
    """network_info retourne le bon format."""
    sc = get_sc()
    result = sc.network_info()
    assert_response(result, expected_success=True)
    assert "interfaces" in result["data"]
    assert "display"    in result["data"]

def test_network_info_has_interfaces():
    """Au moins une interface réseau détectée."""
    sc = get_sc()
    result = sc.network_info()
    assert len(result["data"]["interfaces"]) >= 1


# ══════════════════════════════════════════════════════════════════════════════
#  MERCREDI — RAPPORT COMPLET
# ══════════════════════════════════════════════════════════════════════════════

def test_full_system_report_format():
    """full_system_report retourne toutes les sections."""
    sc = get_sc()
    result = sc.full_system_report()
    assert_response(result, expected_success=True)
    data = result["data"]
    assert "system"      in data
    assert "disk"        in data
    assert "temperature" in data
    assert "network"     in data
    assert "display"     in data

def test_full_system_report_display_not_empty():
    """Le rapport complet a un display non vide."""
    sc = get_sc()
    result = sc.full_system_report()
    assert len(result["data"]["display"]) > 100


# ══════════════════════════════════════════════════════════════════════════════
#  JEUDI — INTÉGRATION AGENT
# ══════════════════════════════════════════════════════════════════════════════

def test_agent_dispatch_cpu():
    """Agent reconnaît 'cpu' et retourne les infos système."""
    from core.agent import Agent
    agent = Agent()
    result = agent.handle_command("cpu")
    assert_response(result, expected_success=True)
    assert "cpu" in result["data"]

def test_agent_dispatch_ram():
    """Agent reconnaît 'ram'."""
    from core.agent import Agent
    result = Agent().handle_command("ram")
    assert_response(result, expected_success=True)

def test_agent_dispatch_disque():
    """Agent reconnaît 'disque'."""
    from core.agent import Agent
    result = Agent().handle_command("disque")
    assert_response(result, expected_success=True)

def test_agent_dispatch_processus():
    """Agent reconnaît 'processus'."""
    from core.agent import Agent
    result = Agent().handle_command("processus")
    assert_response(result, expected_success=True)
    assert "processes" in result["data"]

def test_agent_dispatch_etat_systeme():
    """Agent reconnaît 'quel est l'état du système'."""
    from core.agent import Agent
    result = Agent().handle_command("quel est l'état du système")
    assert_response(result, expected_success=True)

def test_agent_dispatch_rapport_complet():
    """Agent reconnaît 'rapport complet'."""
    from core.agent import Agent
    result = Agent().handle_command("rapport complet")
    assert_response(result, expected_success=True)

def test_agent_extract_delay_seconds():
    """_extract_delay extrait les secondes correctement."""
    from core.agent import Agent
    assert Agent._extract_delay("éteins dans 30 secondes") == 30
    assert Agent._extract_delay("éteins dans 5 sec")      == 5

def test_agent_extract_delay_minutes():
    """_extract_delay convertit les minutes en secondes."""
    from core.agent import Agent
    assert Agent._extract_delay("éteins dans 2 minutes") == 120

def test_agent_extract_delay_default():
    """_extract_delay retourne le défaut si rien trouvé."""
    from core.agent import Agent
    assert Agent._extract_delay("éteins", default=10) == 10

def test_agent_extract_target():
    """_extract_target extrait la cible après un mot-clé."""
    from core.agent import Agent
    assert Agent._extract_target("tue chrome", ["tue"]) == "chrome"
    assert Agent._extract_target("kill notepad.exe", ["kill"]) == "notepad.exe"

def test_agent_kill_nonexistent():
    """Agent : 'tue processus_inexistant' retourne success=False proprement."""
    from core.agent import Agent
    result = Agent().handle_command("tue processus_inexistant_jarvis")
    assert_response(result, expected_success=False)

def test_agent_help():
    """Agent : 'aide' retourne le menu d'aide."""
    from core.agent import Agent
    result = Agent().handle_command("aide")
    assert result["success"] == True
    assert "display" in result["data"]
    assert "SYSTÈME" in result["data"]["display"]


# ══════════════════════════════════════════════════════════════════════════════
#  VENDREDI — SIMULATION ALIMENTATION (sans exécuter réellement)
# Ces tests vérifient que les fonctions SONT DÉFINIES et retournent le bon type.
# Ils ne déclenchent PAS réellement l'extinction/redémarrage.
# ══════════════════════════════════════════════════════════════════════════════

def test_shutdown_method_exists():
    """shutdown() est défini et retourne le bon format."""
    sc = get_sc()
    assert callable(sc.shutdown)

def test_restart_method_exists():
    """restart() est défini."""
    sc = get_sc()
    assert callable(sc.restart)

def test_sleep_method_exists():
    """sleep() est défini."""
    sc = get_sc()
    assert callable(sc.sleep)

def test_lock_screen_method_exists():
    """lock_screen() est défini."""
    sc = get_sc()
    assert callable(sc.lock_screen)

def test_cancel_shutdown_returns_response():
    """cancel_shutdown() retourne une réponse valide (même si rien à annuler)."""
    sc = get_sc()
    result = sc.cancel_shutdown()
    assert_response(result)  # Peut être True ou False, mais format correct