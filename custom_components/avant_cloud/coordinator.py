from __future__ import annotations

import asyncio
import logging
import platform
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import psutil

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, DEFAULT_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AvantCloudCoordinator:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._url = entry.data["url"].rstrip("/")
        self._token = entry.data["token"]
        # Intervalo vem das options (editável) — data é apenas fallback
        self._intervalo = int(entry.options.get("intervalo", entry.data.get("intervalo", DEFAULT_INTERVAL)))
        self._unsub = None

    # ── Ciclo de vida ──────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        await self._async_push()
        self._unsub = async_track_time_interval(
            self.hass,
            self._async_push_cb,
            timedelta(minutes=self._intervalo),
        )
        _LOGGER.info("Avant Cloud: monitoramento iniciado (intervalo %d min)", self._intervalo)

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        _LOGGER.info("Avant Cloud: monitoramento encerrado")

    async def _async_push_cb(self, _now: Any) -> None:
        await self._async_push()

    async def _async_push(self) -> None:
        try:
            data = await self._async_collect()
            await self._async_send(data)
        except Exception as exc:
            _LOGGER.error("Avant Cloud: erro inesperado no ciclo de push: %s", exc)

    # ── Coleta de dados ────────────────────────────────────────────────────────

    async def _async_collect(self) -> dict:
        payload: dict[str, Any] = {}

        # ─ HA Core version
        from homeassistant.const import __version__ as ha_version
        payload["ha_version"] = ha_version

        # ─ Atualização do Core
        core_upd = self.hass.states.get("update.home_assistant_core_update")
        if core_upd:
            payload["ha_version_disponivel"] = core_upd.attributes.get("latest_version")
            payload["atualizacao_disponivel"] = core_upd.state == "on"

        # ─ Métricas do sistema (bloqueante → executor)
        metrics = await self.hass.async_add_executor_job(self._collect_system_metrics)
        payload.update(metrics)

        # ─ IP local
        payload["ip_local"] = await self._async_get_local_ip()

        # ─ Boot time como ISO para o servidor calcular o uptime
        boot_ts = await self.hass.async_add_executor_job(psutil.boot_time)
        payload["uptime"] = datetime.fromtimestamp(boot_ts, tz=timezone.utc).isoformat()

        # ─ Último backup
        payload["ultimo_backup"] = self._get_state_safe("sensor.backup_last_successful_automatic_backup")

        # ─ Intervalo configurado (usado pelo servidor para calcular online/offline)
        payload["intervalo_minutos"] = self._intervalo

        # ─ Sensores customizados
        payload["sensores"] = await self._async_collect_sensores()

        return payload

    def _collect_system_metrics(self) -> dict:
        result: dict[str, Any] = {}

        # CPU — interval=0.5s para não bloquear muito
        result["cpu_percent"] = round(psutil.cpu_percent(interval=0.5), 1)

        # Memória RAM
        mem = psutil.virtual_memory()
        result["memoria_percent"] = round(mem.percent, 1)

        # Disco raiz
        try:
            disk = psutil.disk_usage("/")
            result["disco_usado_gib"] = round(disk.used / (1024 ** 3), 2)
            result["disco_livre_gib"] = round(disk.free / (1024 ** 3), 2)
        except Exception:
            pass

        # Temperatura da CPU
        try:
            temps = psutil.sensors_temperatures()
            cpu_temp = None
            for key in ("coretemp", "cpu_thermal", "cpu-thermal", "k10temp", "acpitz", "soc_thermal", "Tdie"):
                if key in temps and temps[key]:
                    cpu_temp = round(temps[key][0].current, 1)
                    break
            if cpu_temp is not None:
                result["cpu_temperatura"] = cpu_temp
        except (AttributeError, Exception):
            pass

        return result

    async def _async_get_local_ip(self) -> str:
        try:
            from homeassistant.components.network import async_get_adapters
            adapters = await async_get_adapters(self.hass)
            for adapter in adapters:
                if not adapter.get("enabled"):
                    continue
                name = adapter.get("name", "")
                if name.startswith("lo") or name.startswith("docker") or name.startswith("br-"):
                    continue
                ipv4_list = adapter.get("ipv4", [])
                if ipv4_list:
                    return ipv4_list[0].get("address", "unknown")
        except Exception:
            pass
        # Fallback via socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "unknown"

    async def _async_collect_sensores(self) -> dict:
        hass = self.hass
        sensores: dict[str, Any] = {}

        # ─ Sistema operacional do host
        os_version = await hass.async_add_executor_job(self._get_os_version)
        sensores["so_versao"] = {"valor": os_version, "unidade": ""}

        # ─ HAOS version
        haos = hass.states.get("update.home_assistant_operating_system_update")
        if haos:
            sensores["haos_version"]           = {"valor": haos.attributes.get("installed_version", "N/A"), "unidade": ""}
            sensores["haos_version_disponivel"] = {"valor": haos.attributes.get("latest_version", "N/A"),   "unidade": ""}
            sensores["haos_atualizacao"]        = {"valor": str(haos.state == "on").lower(), "unidade": ""}

        # ─ Supervisor version
        sup = hass.states.get("update.home_assistant_supervisor_update")
        if sup:
            sensores["supervisor_version"]   = {"valor": sup.attributes.get("installed_version", "N/A"), "unidade": ""}
            sensores["supervisor_atualizacao"] = {"valor": str(sup.state == "on").lower(), "unidade": ""}

        # ─ Swap (bloqueante → executor)
        swap_pct = await hass.async_add_executor_job(self._get_swap_percent)
        if swap_pct is not None:
            sensores["swap_percent"] = {"valor": str(swap_pct), "unidade": "%"}

        # ─ Estatísticas do HA
        all_states = hass.states.async_all()
        sensores["total_entidades"]  = {"valor": str(len(all_states)),                                              "unidade": ""}
        sensores["total_automacoes"] = {"valor": str(sum(1 for s in all_states if s.domain == "automation")),      "unidade": ""}
        sensores["total_scripts"]    = {"valor": str(sum(1 for s in all_states if s.domain == "script")),          "unidade": ""}
        sensores["total_integracao"] = {"valor": str(sum(1 for s in all_states if s.domain == "update")),          "unidade": ""}

        # ─ Próximo backup
        proximo = self._get_state_safe("sensor.backup_next_scheduled_automatic_backup")
        if proximo:
            sensores["proximo_backup"] = {"valor": proximo, "unidade": ""}

        # ─ Network I/O total acumulado
        try:
            net = await hass.async_add_executor_job(psutil.net_io_counters)
            sensores["rede_total_entrada_gb"] = {"valor": str(round(net.bytes_recv / (1024 ** 3), 2)), "unidade": "GB"}
            sensores["rede_total_saida_gb"]   = {"valor": str(round(net.bytes_sent / (1024 ** 3), 2)), "unidade": "GB"}
        except Exception:
            pass

        return sensores

    def _get_swap_percent(self) -> float | None:
        try:
            return round(psutil.swap_memory().percent, 1)
        except Exception:
            return None

    def _get_os_version(self) -> str:
        try:
            info = platform.freedesktop_os_release()
            return info.get("PRETTY_NAME") or info.get("NAME", platform.system())
        except (AttributeError, OSError):
            pass
        return f"{platform.system()} {platform.release()}".strip()

    def _get_state_safe(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        if state and state.state not in ("unknown", "unavailable", "none", ""):
            return state.state
        return None

    # ── Envio para a API ───────────────────────────────────────────────────────

    async def _async_send(self, data: dict) -> None:
        verify_ssl = self._url.startswith("https://")
        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
        url = f"{self._url}/api/ingest"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with session.post(url, json=data, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    _LOGGER.debug("Avant Cloud: push enviado com sucesso")
                elif resp.status == 401:
                    _LOGGER.error("Avant Cloud: token inválido — verifique as configurações da integração")
                else:
                    body = await resp.text()
                    _LOGGER.warning("Avant Cloud: resposta inesperada %s — %s", resp.status, body[:200])

        except aiohttp.ClientConnectorError:
            _LOGGER.warning(
                "Avant Cloud: servidor inacessível (%s) — próxima tentativa em %d min",
                self._url, self._intervalo,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Avant Cloud: timeout ao conectar — próxima tentativa em %d min", self._intervalo)
        except Exception as exc:
            _LOGGER.error("Avant Cloud: erro ao enviar dados: %s", exc)
