"""
network_manager.py — Contrôle réseau Wifi et Bluetooth
Utilise les commandes Windows netsh et PowerShell.

CORRECTION [Bug 3] — list_wifi_networks() retournait vide même quand le PC
est connecté à un réseau Wi-Fi.

Cause : `netsh wlan show networks mode=bssid` liste les réseaux détectés
par scan actif. Si le scan Wi-Fi est désactivé (mode économie d'énergie,
ou paramètre Windows), la sortie est vide — même si le PC est connecté.

Correction : après le scan, on appelle `get_current_wifi()` (qui utilise
`netsh wlan show interfaces`) pour récupérer le réseau actuellement connecté.
Si ce réseau n'est pas dans la liste scannée, on l'ajoute avec le signal
réel. Le réseau connecté est toujours affiché en premier avec le flag
`connected: True`.
"""

import json
import os
import platform
import re
import subprocess
import tempfile
import textwrap
import ctypes
from pathlib import Path

from config.logger import get_logger
logger = get_logger(__name__)


class NetworkManager:

    def __init__(self):
        self.SYSTEM = platform.system().lower()
        logger.info(f"NetworkManager initialisé ({self.SYSTEM})")

    # ── WiFi ──────────────────────────────────────────────────────────────────

    def list_wifi_networks(self) -> dict:
        """
        Liste les réseaux Wi-Fi disponibles.

        [Bug 3] Combine deux sources :
        1. `netsh wlan show networks` → réseaux dans l'air (peut être vide
           si le scan est désactivé en mode économie d'énergie)
        2. `netsh wlan show interfaces` → réseau actuellement connecté
           (toujours disponible même sans scan actif)

        Le réseau connecté est marqué `connected: True` et placé en tête.
        """
        if "windows" in self.SYSTEM:
            networks = []

            # ── Source 1 : scan des réseaux disponibles ─────────────────────
            ok, out = self._run(["netsh", "wlan", "show", "networks", "mode=bssid"])
            if ok:
                current = None
                for line in out.splitlines():
                    clean = line.strip()
                    if clean.startswith("SSID ") and " : " in clean:
                        name = clean.split(" : ", 1)[1].strip()
                        if name:
                            current = {"ssid": name, "signal": 0, "connected": False}
                            networks.append(current)
                    elif clean.startswith("Signal") and " : " in clean and current is not None:
                        raw = clean.split(" : ", 1)[1].strip()
                        m = re.search(r"(\d+)", raw)
                        current["signal"] = int(m.group(1)) if m else 0

            # Dédupliquer par SSID (garder le signal max)
            unique: dict[str, dict] = {}
            for n in networks:
                ssid = n.get("ssid", "")
                if not ssid:
                    continue
                if ssid not in unique or n.get("signal", 0) > unique[ssid].get("signal", 0):
                    unique[ssid] = dict(n)

            # ── Source 2 : réseau actuellement connecté ──────────────────────
            # [Bug 3] Cette commande fonctionne même si le scan est désactivé.
            current_wifi = self.get_current_wifi()
            current_ssid = ""
            current_signal = 0

            if current_wifi.get("success"):
                data = current_wifi.get("data") or {}
                current_ssid = data.get("ssid", "").strip()
                current_signal = data.get("signal", 0)
                is_connected = data.get("connected", False)

                if current_ssid and is_connected:
                    if current_ssid in unique:
                        # Mettre à jour le flag connected et le signal réel
                        unique[current_ssid]["connected"] = True
                        if current_signal > 0:
                            unique[current_ssid]["signal"] = current_signal
                    else:
                        # [Bug 3] Le réseau connecté n'est pas dans le scan
                        # → l'ajouter explicitement
                        unique[current_ssid] = {
                            "ssid": current_ssid,
                            "signal": current_signal,
                            "connected": True,
                        }
                        logger.info(f"[Bug3-fix] Réseau connecté ajouté hors scan: {current_ssid}")

            # ── Tri : connecté en premier, puis par signal décroissant ────────
            ordered = sorted(
                unique.values(),
                key=lambda x: (not x.get("connected", False), -x.get("signal", 0))
            )

            if not ordered:
                return self._err(
                    "Aucun réseau Wi-Fi détecté. "
                    "Vérifie que le Wi-Fi est activé sur ce PC."
                )

            # Construire l'affichage
            lines = [f"Réseaux Wi-Fi ({len(ordered)}) :"]
            for n in ordered:
                icon = "📶" if n.get("connected") else "  "
                conn = " [connecté]" if n.get("connected") else ""
                lines.append(f"  {icon} {n['ssid']}  —  {n['signal']}%{conn}")

            return self._ok(
                f"{len(ordered)} réseau(x) Wi-Fi détecté(s).",
                {
                    "networks": ordered,
                    "connected_ssid": current_ssid,
                    "display": "\n".join(lines),
                }
            )

        # ── Linux ─────────────────────────────────────────────────────────────
        ok, out = self._run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi", "list"])
        if not ok:
            return self._err(f"Impossible de lister les réseaux Wi-Fi: {out}")

        networks = []
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            active = parts[0].strip() == "yes"
            ssid   = parts[1].strip()
            signal = int(parts[2].strip()) if parts[2].strip().isdigit() else 0
            if ssid:
                networks.append({"ssid": ssid, "signal": signal, "connected": active})

        ordered = sorted(networks, key=lambda x: (not x.get("connected"), -x.get("signal", 0)))
        connected_ssid = next((n["ssid"] for n in ordered if n.get("connected")), "")
        return self._ok(
            f"{len(ordered)} réseau(x) Wi-Fi détecté(s).",
            {"networks": ordered, "connected_ssid": connected_ssid}
        )

    def get_current_wifi(self) -> dict:
        """
        Retourne le réseau Wi-Fi actuellement connecté.
        Utilise `netsh wlan show interfaces` sur Windows — fonctionne
        même si le scan Wi-Fi est désactivé.
        """
        if "windows" in self.SYSTEM:
            ok, out = self._run(["netsh", "wlan", "show", "interfaces"])
            if not ok:
                return self._err(f"Impossible de lire l'état Wi-Fi: {out}")

            state = self._extract_key(out, "State") or self._extract_key(out, "Etat")
            ssid  = self._extract_key(out, "SSID") or ""
            # Éviter de lire BSSID comme SSID
            if ssid.lower().startswith("bssid"):
                ssid = ""
            signal_raw = self._extract_key(out, "Signal") or "0"
            m = re.search(r"(\d+)", signal_raw)
            signal = int(m.group(1)) if m else 0
            connected = bool(state and any(k in state.lower() for k in ["connected", "connecte", "associé"]))

            return self._ok("État Wi-Fi récupéré.", {
                "ssid":      ssid,
                "signal":    signal,
                "connected": connected,
            })

        ok, out = self._run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi", "list"])
        if not ok:
            return self._err(f"Impossible de lire l'état Wi-Fi: {out}")

        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            active, ssid, signal = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if active == "yes":
                return self._ok("État Wi-Fi récupéré.", {
                    "ssid":      ssid,
                    "signal":    int(signal) if signal.isdigit() else 0,
                    "connected": True,
                })

        return self._ok("Aucun Wi-Fi actif.", {"ssid": "", "signal": 0, "connected": False})

    def connect_wifi(self, ssid: str, password: str = "") -> dict:
        ssid = (ssid or "").strip()
        if not ssid:
            return self._err("SSID manquant.")

        if "windows" in self.SYSTEM:
            if password:
                try:
                    xml = self._build_windows_wifi_profile_xml(ssid, password)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xml", mode="w", encoding="utf-8") as tmp:
                        tmp.write(xml)
                        profile_path = tmp.name
                    ok, add_out = self._run(["netsh", "wlan", "add", "profile", f"filename={profile_path}", "user=current"])
                    Path(profile_path).unlink(missing_ok=True)
                    if not ok:
                        return self._err(f"Ajout profil Wi-Fi échoué: {add_out}")
                except Exception as e:
                    return self._err(f"Erreur création profil Wi-Fi: {e}")

            ok, out = self._run(["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"])
            if not ok:
                return self._err(f"Connexion Wi-Fi échouée: {out}")
            return self._ok(f"Connexion au réseau '{ssid}' demandée.", {"ssid": ssid})

        cmd = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            cmd.extend(["password", password])
        ok, out = self._run(cmd)
        if not ok:
            return self._err(f"Connexion Wi-Fi échouée: {out}")
        return self._ok(f"Connexion au réseau '{ssid}' réussie.", {"ssid": ssid})

    def disconnect_wifi(self) -> dict:
        if "windows" in self.SYSTEM:
            ok, out = self._run(["netsh", "wlan", "disconnect"])
        else:
            iface = self._linux_wifi_iface()
            if not iface:
                return self._err("Interface Wi-Fi introuvable.")
            ok, out = self._run(["nmcli", "device", "disconnect", iface])

        if not ok:
            return self._err(f"Déconnexion Wi-Fi échouée: {out}")
        return self._ok("Wi-Fi déconnecté.")

    def enable_wifi(self) -> dict:
        if "windows" in self.SYSTEM:
            return self._set_windows_wifi_radio(True)
        ok, out = self._run(["nmcli", "radio", "wifi", "on"])
        if not ok:
            return self._err(f"Activation Wi-Fi échouée: {out}")
        return self._ok("Wi-Fi activé.")

    def disable_wifi(self) -> dict:
        if "windows" in self.SYSTEM:
            return self._set_windows_wifi_radio(False)
        ok, out = self._run(["nmcli", "radio", "wifi", "off"])
        if not ok:
            return self._err(f"Désactivation Wi-Fi échouée: {out}")
        return self._ok("Wi-Fi désactivé.")

    # ── Bluetooth ─────────────────────────────────────────────────────────────

    def enable_bluetooth(self) -> dict:
        if "windows" in self.SYSTEM:
            return self._set_windows_bluetooth_radio(True)
        ok, out = self._run(["rfkill", "unblock", "bluetooth"])
        if not ok:
            return self._err(f"Activation Bluetooth échouée: {out}")
        return self._ok("Bluetooth activé.")

    def disable_bluetooth(self) -> dict:
        if "windows" in self.SYSTEM:
            return self._set_windows_bluetooth_radio(False)
        ok, out = self._run(["rfkill", "block", "bluetooth"])
        if not ok:
            return self._err(f"Désactivation Bluetooth échouée: {out}")
        return self._ok("Bluetooth désactivé.")

    def list_bluetooth_devices(self) -> dict:
        if "windows" in self.SYSTEM:
            ps_cmd = (
                "Get-PnpDevice -Class Bluetooth | "
                "Select-Object FriendlyName,Status,InstanceId | "
                "ConvertTo-Json -Compress"
            )
            ok, out = self._run(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=20)
            if not ok:
                return self._err(f"Listing Bluetooth échoué: {out}")
            try:
                parsed = json.loads(out) if out.strip() else []
                if isinstance(parsed, dict):
                    parsed = [parsed]
                devices = [
                    {
                        "name":   d.get("FriendlyName") or "Unknown",
                        "status": d.get("Status") or "Unknown",
                        "id":     d.get("InstanceId") or "",
                    }
                    for d in parsed
                ]
                return self._ok(f"{len(devices)} appareil(s) Bluetooth détecté(s).", {"devices": devices})
            except Exception:
                return self._err(f"Réponse PowerShell non exploitable: {out[:300]}")

        ok, out = self._run(["bluetoothctl", "paired-devices"])
        if not ok:
            return self._err(f"Listing Bluetooth échoué: {out}")
        devices = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("Device "):
                continue
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                devices.append({"id": parts[1], "name": parts[2], "status": "paired"})
        return self._ok(f"{len(devices)} appareil(s) Bluetooth détecté(s).", {"devices": devices})

    def get_network_info(self) -> dict:
        if "windows" in self.SYSTEM:
            ok, ipconfig_out = self._run(["ipconfig", "/all"], timeout=25)
            if not ok:
                return self._err(f"Lecture ipconfig échouée: {ipconfig_out}")

            ipv4    = self._extract_first(r"IPv4[^:]*:\s*([0-9.]+)", ipconfig_out)
            gateway = self._extract_first(r"Default Gateway[^:]*:\s*([0-9.]+)", ipconfig_out)
            dns     = re.findall(r"DNS Servers[^:]*:\s*([0-9.]+)", ipconfig_out)
            if not dns:
                dns = re.findall(r"\n\s+([0-9.]+)\s*\n", ipconfig_out)

            ping_ok, ping_out = self._run(["ping", "8.8.8.8", "-n", "1", "-w", "1500"], timeout=5)
            internet = ping_ok and "TTL=" in ping_out.upper()

            # Ajouter l'info du wifi connecté
            wifi_info = self.get_current_wifi()
            wifi_ssid = ""
            if wifi_info.get("success") and (wifi_info.get("data") or {}).get("connected"):
                wifi_ssid = (wifi_info.get("data") or {}).get("ssid", "")

            return self._ok("Informations réseau récupérées.", {
                "system":    self.SYSTEM,
                "local_ip":  ipv4,
                "gateway":   gateway,
                "dns":       dns[:3],
                "internet":  internet,
                "wifi_ssid": wifi_ssid,
                "display": (
                    f"IP locale : {ipv4 or 'N/A'}\n"
                    f"Passerelle : {gateway or 'N/A'}\n"
                    f"DNS : {', '.join(dns[:3]) if dns else 'N/A'}\n"
                    f"Internet : {'✅ Oui' if internet else '❌ Non'}\n"
                    + (f"Wi-Fi : {wifi_ssid}\n" if wifi_ssid else "")
                ),
            })

        ok, ip_out = self._run(["sh", "-c", "ip -4 addr show | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}' | head -n1"])
        local_ip = ip_out.strip() if ok else ""
        ok, route_out = self._run(["sh", "-c", "ip route | grep default | awk '{print $3}' | head -n1"])
        gateway = route_out.strip() if ok else ""
        ok, dns_out = self._run(["sh", "-c", "grep '^nameserver' /etc/resolv.conf | awk '{print $2}'"])
        dns = [x.strip() for x in dns_out.splitlines() if x.strip()] if ok else []
        ping_ok, _ = self._run(["ping", "-c", "1", "-W", "2", "8.8.8.8"], timeout=5)

        return self._ok("Informations réseau récupérées.", {
            "system":   self.SYSTEM,
            "local_ip": local_ip,
            "gateway":  gateway,
            "dns":      dns[:3],
            "internet": ping_ok,
        })

    # ── Helpers internes ──────────────────────────────────────────────────────

    def _run(self, cmd, timeout: int = 15) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
                shell=False,
            )
            output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            return proc.returncode == 0, output.strip()
        except Exception as e:
            return False, str(e)

    def _linux_wifi_iface(self) -> str:
        ok, out = self._run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"])
        if not ok:
            return ""
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1].strip() == "wifi":
                return parts[0].strip()
        return ""

    def _windows_bluetooth_adapters(self) -> list[dict]:
        ps_cmd = (
            "Get-PnpDevice -Class Bluetooth | "
            "Select-Object FriendlyName,Status,InstanceId | "
            "ConvertTo-Json -Compress"
        )
        ok, out = self._run(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=12)
        if not ok:
            return []
        try:
            parsed = json.loads(out) if out.strip() else []
            if isinstance(parsed, dict):
                parsed = [parsed]
        except Exception:
            return []

        adapter_keywords = ["adapter", "radio", "wireless", "intel", "realtek", "qualcomm", "broadcom", "mediatek"]
        candidates = []
        for d in parsed:
            name        = str(d.get("FriendlyName") or "").lower()
            instance_id = str(d.get("InstanceId") or "")
            if not instance_id:
                continue
            if any(k in name for k in adapter_keywords):
                candidates.append({
                    "name":   d.get("FriendlyName") or "Unknown",
                    "id":     instance_id,
                    "status": d.get("Status") or "Unknown",
                })

        if candidates:
            return candidates
        first = parsed[0] if parsed else None
        if first and first.get("InstanceId"):
            return [{"name": first.get("FriendlyName") or "Unknown", "id": first.get("InstanceId"), "status": first.get("Status") or "Unknown"}]
        return []

    def _set_windows_bluetooth_radio(self, enabled: bool) -> dict:
        state_name      = "On" if enabled else "Off"
        verb            = "Activation" if enabled else "Désactivation"
        success_message = "Bluetooth activé." if enabled else "Bluetooth désactivé."

        ok, out = self._run(
            ["powershell", "-NoProfile", "-Command", self._windows_bluetooth_radio_script(state_name)],
            timeout=20,
        )
        if not ok:
            return self._err(f"{verb} Bluetooth échouée: {out}")
        try:
            payload = json.loads(out) if out.strip() else {}
        except Exception:
            return self._err(f"{verb} Bluetooth échouée: réponse PowerShell invalide: {out[:300]}")

        if not payload.get("found"):
            return self._err("Aucun radio Bluetooth Windows détecté.")
        if payload.get("access") not in {"Allowed", "Unspecified"}:
            return self._err(f"{verb} Bluetooth refusée par Windows: accès={payload.get('access')}")
        final_state = str(payload.get("state") or "").strip()
        if final_state != state_name:
            return self._err(f"{verb} Bluetooth non appliquée: état final='{final_state or 'inconnu'}'.")
        return self._ok(success_message, {"radio": payload.get("name") or "Bluetooth", "state": final_state})

    def _set_windows_wifi_radio(self, enabled: bool) -> dict:
        state_name      = "On" if enabled else "Off"
        verb            = "Activation" if enabled else "Désactivation"
        success_message = "Wi-Fi activé." if enabled else "Wi-Fi désactivé."

        ok, out = self._run(
            ["powershell", "-NoProfile", "-Command", self._windows_wifi_radio_script(state_name)],
            timeout=20,
        )
        if not ok:
            return self._err(f"{verb} Wi-Fi échouée: {out}")
        try:
            payload = json.loads(out) if out.strip() else {}
        except Exception:
            return self._err(f"{verb} Wi-Fi échouée: réponse PowerShell invalide: {out[:300]}")

        if not payload.get("found"):
            return self._err("Aucun radio Wi-Fi Windows détecté.")
        if payload.get("access") not in {"Allowed", "Unspecified"}:
            return self._err(f"{verb} Wi-Fi refusée par Windows: accès={payload.get('access')}")
        final_state = str(payload.get("state") or "").strip()
        if final_state != state_name:
            return self._err(f"{verb} Wi-Fi non appliquée: état final='{final_state or 'inconnu'}'.")
        return self._ok(success_message, {"radio": payload.get("name") or "Wi-Fi", "state": final_state})

    @staticmethod
    def _windows_bluetooth_radio_script(state_name: str) -> str:
        return textwrap.dedent(f"""
            Add-Type -AssemblyName System.Runtime.WindowsRuntime
            [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] > $null
            $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
                $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
            }})[0]
            $accessOp = [Windows.Devices.Radios.Radio]::RequestAccessAsync()
            $accessTask = $asTask.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null, @($accessOp))
            $access = $accessTask.Result
            $radiosOp = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
            $radiosTask = $asTask.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($radiosOp))
            $radio = ($radiosTask.Result | Where-Object {{ $_.Kind -eq 'Bluetooth' }} | Select-Object -First 1)
            if ($null -eq $radio) {{
                [pscustomobject]@{{ found = $false; access = [string]$access; name = ''; state = '' }} | ConvertTo-Json -Compress
                exit 0
            }}
            $targetState = [Windows.Devices.Radios.RadioState]::{state_name}
            $setOp = $radio.SetStateAsync($targetState)
            $setTask = $asTask.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null, @($setOp))
            $setAccess = $setTask.Result
            [pscustomobject]@{{
                found = $true; access = [string]$setAccess
                name = [string]$radio.Name; state = [string]$radio.State
            }} | ConvertTo-Json -Compress
        """).strip()

    @staticmethod
    def _windows_wifi_radio_script(state_name: str) -> str:
        return textwrap.dedent(f"""
            Add-Type -AssemblyName System.Runtime.WindowsRuntime
            [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] > $null
            $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
                $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
            }})[0]
            $accessOp = [Windows.Devices.Radios.Radio]::RequestAccessAsync()
            $accessTask = $asTask.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null, @($accessOp))
            $access = $accessTask.Result
            $radiosOp = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
            $radiosTask = $asTask.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($radiosOp))
            $radio = ($radiosTask.Result | Where-Object {{ $_.Kind -eq 'WiFi' }} | Select-Object -First 1)
            if ($null -eq $radio) {{
                [pscustomobject]@{{ found = $false; access = [string]$access; name = ''; state = '' }} | ConvertTo-Json -Compress
                exit 0
            }}
            $targetState = [Windows.Devices.Radios.RadioState]::{state_name}
            $setOp = $radio.SetStateAsync($targetState)
            $setTask = $asTask.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null, @($setOp))
            $setAccess = $setTask.Result
            [pscustomobject]@{{
                found = $true; access = [string]$setAccess
                name = [string]$radio.Name; state = [string]$radio.State
            }} | ConvertTo-Json -Compress
        """).strip()

    @staticmethod
    def _is_windows_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def _extract_key(text: str, key: str) -> str:
        pattern = rf"^\s*{re.escape(key)}\s*:\s*(.+)$"
        for line in text.splitlines():
            m = re.match(pattern, line)
            if m:
                return m.group(1).strip()
        return ""

    @staticmethod
    def _extract_first(pattern: str, text: str) -> str:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _build_windows_wifi_profile_xml(ssid: str, password: str) -> str:
        escaped_ssid     = ssid.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        escaped_password = password.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return textwrap.dedent(f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{escaped_ssid}</name>
  <SSIDConfig>
    <SSID>
      <name>{escaped_ssid}</name>
    </SSID>
  </SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>AES</encryption>
        <useOneX>false</useOneX>
      </authEncryption>
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{escaped_password}</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>""").strip()

    @staticmethod
    def _ok(message: str, data=None) -> dict:
        return {"success": True,  "message": message, "data": data}

    @staticmethod
    def _err(message: str, data=None) -> dict:
        return {"success": False, "message": message, "data": data}