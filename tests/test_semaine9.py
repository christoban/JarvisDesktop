"""
test_semaine9.py — Tests Semaine 9

Groupes:
1) NotificationSender
2) NetworkManager
3) ScreenManager
4) Integration parser + executor
5) Non-regression
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============================================================================
# Helpers
# ============================================================================


def _offline_parser():
    from core.command_parser import CommandParser

    p = CommandParser()
    p.ai_available = False
    p.client = None
    return p


class _DummyHTTPResponse:
    def __init__(self, payload: bytes = b"{}"):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


# ============================================================================
# GROUPE 1 — NotificationSender (9 tests)
# ============================================================================


def test_g1_notification_sender_init():
    from communication.notification_sender import NotificationSender

    sender = NotificationSender()
    assert sender is not None
    assert sender.backend in {"bridge", "azure_hub"}


def test_g1_notify_task_done(monkeypatch):
    from communication.notification_sender import NotificationSender

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _DummyHTTPResponse())
    sender = NotificationSender()
    result = sender.notify_task_done("Volume 70%", "Volume 70% — commande executee")
    assert result["success"] is True


def test_g1_notify_error(monkeypatch):
    from communication.notification_sender import NotificationSender

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _DummyHTTPResponse())
    sender = NotificationSender()
    result = sender.notify_error("Erreur test", context="g1")
    assert result["success"] is True


def test_g1_notify_battery_low(monkeypatch):
    from communication.notification_sender import NotificationSender

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _DummyHTTPResponse())
    sender = NotificationSender()
    result = sender.notify_battery_low(15)
    assert result["success"] is True


def test_g1_notify_screenshot(monkeypatch):
    from communication.notification_sender import NotificationSender

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _DummyHTTPResponse())
    sender = NotificationSender()
    result = sender.notify_screenshot(path="data/screenshots/x.png", b64="abcd")
    assert result["success"] is True


def test_g1_send_generic(monkeypatch):
    from communication.notification_sender import NotificationSender

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _DummyHTTPResponse())
    sender = NotificationSender()
    result = sender.send("Titre", "Corps", {"x": 1}, type="info")
    assert result["success"] is True


def test_g1_send_empty_title_error():
    from communication.notification_sender import NotificationSender

    sender = NotificationSender()
    result = sender.send("", "Corps")
    assert result["success"] is False


def test_g1_health_check(monkeypatch):
    from communication.notification_sender import NotificationSender

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _DummyHTTPResponse())
    sender = NotificationSender()
    health = sender.health_check()
    assert "available" in health
    assert "backend" in health


def test_g1_queue_on_bridge_offline(monkeypatch):
    from communication.notification_sender import NotificationSender

    def _raise_urlerror(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", _raise_urlerror)
    sender = NotificationSender()
    sender.backend = "bridge"
    result = sender.send("Titre", "Bridge offline", type="info")

    assert result["success"] is True
    assert sender._queued_count() >= 1


# ============================================================================
# GROUPE 2 — NetworkManager (8 tests)
# ============================================================================


def test_g2_network_manager_init():
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    assert nm.SYSTEM in {"windows", "linux", "darwin"}


def test_g2_list_wifi_networks_windows_parse(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    nm.SYSTEM = "windows"

    sample = """
SSID 1 : Maison
    Signal             : 67%
SSID 2 : Bureau
    Signal             : 40%
""".strip()

    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, sample))
    result = nm.list_wifi_networks()
    assert result["success"] is True
    nets = result["data"]["networks"]
    assert nets[0]["ssid"] == "Maison"


def test_g2_get_current_wifi_windows_parse(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    nm.SYSTEM = "windows"

    sample = """
    State                  : connected
    SSID                   : Maison
    Signal                 : 72%
""".strip()

    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, sample))
    result = nm.get_current_wifi()
    assert result["success"] is True
    assert result["data"]["connected"] is True
    assert result["data"]["ssid"] == "Maison"


def test_g2_get_network_info_windows_ip(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    nm.SYSTEM = "windows"

    ipconfig_sample = """
Windows IP Configuration
   IPv4 Address. . . . . . . . . . . : 192.168.1.50
   Default Gateway . . . . . . . . . : 192.168.1.1
   DNS Servers . . . . . . . . . . . : 1.1.1.1
""".strip()

    def fake_run(cmd, timeout=15):
        if cmd[:2] == ["ipconfig", "/all"]:
            return True, ipconfig_sample
        if cmd and cmd[0] == "ping":
            return True, "Reply from 8.8.8.8: bytes=32 time=12ms TTL=117"
        return False, "unknown"

    monkeypatch.setattr(nm, "_run", fake_run)
    result = nm.get_network_info()
    assert result["success"] is True
    assert result["data"]["local_ip"] == "192.168.1.50"


def test_g2_enable_wifi_windows_or_skip(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    if "linux" in nm.SYSTEM:
        pytest.skip("Test enable/disable wifi explicitement ignore sur Linux")

    nm.SYSTEM = "windows"
    payload = '{"found":true,"access":"Allowed","name":"Wi-Fi","state":"On"}'
    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, payload))
    result = nm.enable_wifi()
    assert result["success"] is True
    assert result["data"]["state"] == "On"


def test_g2_disable_wifi_windows_or_skip(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    if "linux" in nm.SYSTEM:
        pytest.skip("Test enable/disable wifi explicitement ignore sur Linux")

    nm.SYSTEM = "windows"
    payload = '{"found":true,"access":"Allowed","name":"Wi-Fi","state":"Off"}'
    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, payload))
    result = nm.disable_wifi()
    assert result["success"] is True
    assert result["data"]["state"] == "Off"


def test_g2_disable_wifi_windows_radio_state_mismatch(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    nm.SYSTEM = "windows"

    payload = '{"found":true,"access":"Allowed","name":"Wi-Fi","state":"On"}'
    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, payload))

    result = nm.disable_wifi()
    assert result["success"] is False
    assert "etat final" in result["message"]


def test_g2_connect_wifi_without_ssid_error():
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    result = nm.connect_wifi("", "")
    assert result["success"] is False


def test_g2_list_bluetooth_devices_windows_parse(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    nm.SYSTEM = "windows"

    payload = '[{"FriendlyName":"AirPods","Status":"OK","InstanceId":"abc"}]'
    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, payload))

    result = nm.list_bluetooth_devices()
    assert result["success"] is True
    assert result["data"]["devices"][0]["name"] == "AirPods"


def test_g2_enable_bluetooth_windows_radio_success(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    nm.SYSTEM = "windows"

    payload = '{"found":true,"access":"Allowed","name":"Bluetooth","state":"On"}'
    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, payload))

    result = nm.enable_bluetooth()
    assert result["success"] is True
    assert result["data"]["state"] == "On"


def test_g2_disable_bluetooth_windows_radio_state_mismatch(monkeypatch):
    from modules.network_manager import NetworkManager

    nm = NetworkManager()
    nm.SYSTEM = "windows"

    payload = '{"found":true,"access":"Allowed","name":"Bluetooth","state":"On"}'
    monkeypatch.setattr(nm, "_run", lambda *a, **k: (True, payload))

    result = nm.disable_bluetooth()
    assert result["success"] is False
    assert "etat final" in result["message"]


# ============================================================================
# GROUPE 3 — ScreenManager (6 tests)
# ============================================================================


def test_g3_screen_manager_init():
    from modules.screen_manager import ScreenManager

    sm = ScreenManager()
    assert sm.backend in {"mss", "pil", "scrot", "stub"}


def test_g3_capture_screen(monkeypatch, tmp_path):
    from modules.screen_manager import ScreenManager

    sm = ScreenManager()
    sm.backend = "mss"
    sm.screenshots_dir = tmp_path

    def fake_capture(path, monitor=1):
        path.write_bytes(b"fake-image")
        return True, ""

    monkeypatch.setattr(sm, "_capture_mss", fake_capture)
    monkeypatch.setattr(sm, "_compress_image", lambda *a, **k: None)

    result = sm.capture_screen(send_to_phone=False, monitor=1)
    assert result["success"] is True
    assert Path(result["data"]["path"]).exists()
    assert result["data"]["base64"]


def test_g3_get_screen_info(monkeypatch):
    from modules.screen_manager import ScreenManager

    sm = ScreenManager()

    class FakeMSSCtx:
        def __enter__(self):
            class _Ctx:
                monitors = [
                    {"left": 0, "top": 0, "width": 3000, "height": 1200},
                    {"left": 0, "top": 0, "width": 1920, "height": 1080},
                    {"left": 1920, "top": 0, "width": 1080, "height": 1920},
                ]
            return _Ctx()

        def __exit__(self, exc_type, exc, tb):
            return False

    import types
    import modules.screen_manager as sm_mod
    monkeypatch.setattr(sm_mod, "_MSS_AVAILABLE", True)

    fake_mss_module = types.SimpleNamespace(mss=lambda: FakeMSSCtx())
    monkeypatch.setattr(sm_mod, "mss", fake_mss_module)

    result = sm.get_screen_info()
    assert result["success"] is True
    assert len(result["data"]["screens"]) == 2


def test_g3_set_brightness(monkeypatch):
    from modules.screen_manager import ScreenManager

    sm = ScreenManager()
    if "windows" in sm.SYSTEM:
        monkeypatch.setattr(sm, "_run", lambda *a, **k: (True, "ok"))
        result = sm.set_brightness(70)
        assert result["success"] is True
    elif "linux" in sm.SYSTEM:
        calls = {"count": 0}

        def fake_run(cmd, timeout=15):
            calls["count"] += 1
            if cmd[:2] == ["xrandr", "--query"]:
                return True, "HDMI-1 connected 1920x1080"
            return True, "ok"

        monkeypatch.setattr(sm, "_run", fake_run)
        result = sm.set_brightness(70)
        assert result["success"] is True
        assert calls["count"] >= 2
    else:
        result = sm.set_brightness(70)
        assert result["success"] is False


def test_g3_health_check():
    from modules.screen_manager import ScreenManager

    sm = ScreenManager()
    health = sm.health_check()
    assert health["success"] is True
    assert "capture_available" in health["data"]


def test_g3_screenshots_dir_created(tmp_path):
    from modules.screen_manager import ScreenManager

    sm = ScreenManager()
    sm.screenshots_dir = tmp_path / "screenshots"
    sm.screenshots_dir.mkdir(parents=True, exist_ok=True)
    assert sm.screenshots_dir.exists()


def test_g3_send_screenshot_to_phone_share_mode(monkeypatch, tmp_path):
    from modules.screen_manager import ScreenManager

    sm = ScreenManager()
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"fake-image")

    monkeypatch.setattr(sm, "_encode_b64", lambda *_a, **_k: "abcd")

    class FakeSender:
        def notify_screenshot(self, path="", b64=""):
            return {"success": True, "message": "queued", "data": {"path": path}}

    monkeypatch.setitem(sys.modules, "communication.notification_sender", types.SimpleNamespace(NotificationSender=FakeSender))

    result = sm.send_screenshot_to_phone(str(image_path), share_mode="share")
    assert result["success"] is True
    assert "Partage d'ecran direct non implemente" in result["message"]
    assert result["data"]["destination"] == "Jarvis Mobile > Historique"


# ============================================================================
# GROUPE 4 — Integration parser + executor
# 11 tests parsing + 7 tests execution
# ============================================================================


@pytest.mark.parametrize(
    "command,expected",
    [
        ("liste les reseaux wifi", "WIFI_LIST"),
        ("connecte au wifi Maison", "WIFI_CONNECT"),
        ("deconnecte du wifi", "WIFI_DISCONNECT"),
        ("active le wifi", "WIFI_ENABLE"),
        ("desactive le wifi", "WIFI_DISABLE"),
        ("active le bluetooth", "BLUETOOTH_ENABLE"),
        ("desactive le bluetooth", "BLUETOOTH_DISABLE"),
        ("liste les appareils bluetooth", "BLUETOOTH_LIST"),
        ("infos reseau", "NETWORK_INFO"),
        ("envoie la capture au telephone", "SCREENSHOT_TO_PHONE"),
        ("luminosite 70", "SCREEN_BRIGHTNESS"),
        ("mets la luminosite a 70%", "SCREEN_BRIGHTNESS"),
        ("partager l'ecran", "SCREENSHOT_TO_PHONE"),
        ("resolution de l'ecran", "SCREEN_INFO"),
        ("deverrouille l'ecran", "SYSTEM_UNLOCK"),
    ],
)
def test_g4_parse_new_intents(command, expected):
    parser = _offline_parser()
    result = parser.parse(command)
    assert result["intent"] == expected


def test_g4_postprocess_brightness_over_audio():
    parser = _offline_parser()
    result = parser._postprocess_result(
        "mets la luminosite a 70%",
        {"intent": "AUDIO_PLAY", "params": {"query": "la luminosite a 70%"}, "confidence": 0.4, "raw": "mets la luminosite a 70%"},
    )
    assert result["intent"] == "SCREEN_BRIGHTNESS"
    assert result["params"]["level"] == 70


def test_g4_executor_execute_wifi_list(monkeypatch):
    from core.intent_executor import IntentExecutor

    ex = IntentExecutor()
    monkeypatch.setattr(ex.nm, "list_wifi_networks", lambda: {"success": True, "message": "ok", "data": {"networks": []}})
    result = ex.execute("WIFI_LIST", {})
    assert result["success"] is True


def test_g4_executor_execute_wifi_connect(monkeypatch):
    from core.intent_executor import IntentExecutor

    ex = IntentExecutor()
    monkeypatch.setattr(ex.nm, "connect_wifi", lambda ssid, password="": {"success": True, "message": ssid, "data": None})
    result = ex.execute("WIFI_CONNECT", {"ssid": "Maison", "password": "1234"})
    assert result["success"] is True


def test_g4_executor_execute_wifi_disconnect(monkeypatch):
    from core.intent_executor import IntentExecutor

    ex = IntentExecutor()
    monkeypatch.setattr(ex.nm, "disconnect_wifi", lambda: {"success": True, "message": "ok", "data": None})
    result = ex.execute("WIFI_DISCONNECT", {})
    assert result["success"] is True


def test_g4_executor_execute_bluetooth_list(monkeypatch):
    from core.intent_executor import IntentExecutor

    ex = IntentExecutor()
    monkeypatch.setattr(ex.nm, "list_bluetooth_devices", lambda: {"success": True, "message": "ok", "data": {"devices": []}})
    result = ex.execute("BLUETOOTH_LIST", {})
    assert result["success"] is True


def test_g4_executor_execute_network_info(monkeypatch):
    from core.intent_executor import IntentExecutor

    ex = IntentExecutor()
    monkeypatch.setattr(ex.nm, "get_network_info", lambda: {"success": True, "message": "ok", "data": {"local_ip": "127.0.0.1"}})
    result = ex.execute("NETWORK_INFO", {})
    assert result["success"] is True


def test_g4_executor_execute_screen_capture(monkeypatch):
    from core.intent_executor import IntentExecutor
    # Patch class import used inside handler
    import modules.screen_manager as sm_mod

    class FakeScreenManager:
        def capture_screen(self, send_to_phone=False, monitor=1):
            return {"success": True, "message": "ok", "data": {"monitor": monitor}}

    monkeypatch.setattr(sm_mod, "ScreenManager", FakeScreenManager)

    ex = IntentExecutor()
    result = ex.execute("SCREEN_CAPTURE", {"monitor": 1})
    assert result["success"] is True


def test_g4_executor_execute_screen_info(monkeypatch):
    from core.intent_executor import IntentExecutor

    import modules.screen_manager as sm_mod

    class FakeScreenManager:
        def get_screen_info(self):
            return {"success": True, "message": "ok", "data": {"screens": [{"id": 1}]}}

    monkeypatch.setattr(sm_mod, "ScreenManager", FakeScreenManager)

    ex = IntentExecutor()
    result = ex.execute("SCREEN_INFO", {})
    assert result["success"] is True


def test_g4_executor_execute_system_unlock():
    from core.intent_executor import IntentExecutor

    ex = IntentExecutor()
    result = ex.execute("SYSTEM_UNLOCK", {})
    assert result["success"] is False
    assert "non supporte" in result["message"]


# ============================================================================
# GROUPE 5 — Non-regression (4 tests)
# ============================================================================


@pytest.mark.parametrize(
    "command,expected",
    [
        ("eteins l'ordinateur", "SYSTEM_SHUTDOWN"),
        ("monte le volume", "AUDIO_VOLUME_UP"),
        ("ouvre chrome", "APP_OPEN"),
        ("infos systeme", "SYSTEM_INFO"),
    ],
)
def test_g5_non_regression_parsing(command, expected):
    parser = _offline_parser()
    result = parser.parse(command)
    assert result["intent"] == expected
