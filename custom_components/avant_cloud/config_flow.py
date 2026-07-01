from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, DEFAULT_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _schema_setup() -> vol.Schema:
    return vol.Schema({
        vol.Required("url"):   str,
        vol.Required("token"): str,
    })


def _schema_options(url: str, token: str, intervalo: int) -> vol.Schema:
    return vol.Schema({
        vol.Required("url",       default=url):       str,
        vol.Required("token",     default=token):     str,
        vol.Required("intervalo", default=intervalo): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=60)
        ),
    })


async def _validate(hass, url: str, token: str) -> str | None:
    """Testa a conexão. Retorna chave de erro ou None se OK."""
    verify_ssl = url.startswith("https://")
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    try:
        async with session.post(
            f"{url}/api/ingest",
            json={"ha_version": "avant_cloud_setup_test"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 401:
                return "invalid_token"
            if resp.status not in (200, 201):
                _LOGGER.warning("Avant Cloud: resposta inesperada %s", resp.status)
                return "cannot_connect"
            return None
    except aiohttp.ClientConnectorError:
        return "cannot_connect"
    except asyncio.TimeoutError:
        return "timeout"
    except Exception as exc:
        _LOGGER.error("Avant Cloud: erro desconhecido: %s", exc)
        return "unknown"


# ── Setup inicial ──────────────────────────────────────────────────────────────

class AvantCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            url   = user_input["url"].rstrip("/")
            token = user_input["token"].strip()

            error = await _validate(self.hass, url, token)
            if error:
                errors["base"] = error
            else:
                title = urlparse(url).netloc or url
                return self.async_create_entry(
                    title=title,
                    data={"url": url, "token": token},
                    options={"intervalo": DEFAULT_INTERVAL},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema_setup(),
            errors=errors,
        )

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AvantCloudOptionsFlow(config_entry)


# ── Options Flow (editar URL, token e intervalo após instalação) ───────────────

class AvantCloudOptionsFlow(config_entries.OptionsFlow):

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        current_url      = self._config_entry.data.get("url", "")
        current_token    = self._config_entry.data.get("token", "")
        current_intervalo = self._config_entry.options.get("intervalo", DEFAULT_INTERVAL)

        if user_input is not None:
            url      = user_input["url"].rstrip("/")
            token    = user_input["token"].strip()
            intervalo = user_input["intervalo"]

            # Só valida conexão se URL ou token mudaram
            if url != current_url or token != current_token:
                error = await _validate(self.hass, url, token)
                if error:
                    errors["base"] = error

            if not errors:
                # Atualiza data (url + token) e options (intervalo)
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={"url": url, "token": token},
                )
                return self.async_create_entry(title="", data={"intervalo": intervalo})

        return self.async_show_form(
            step_id="init",
            data_schema=_schema_options(current_url, current_token, current_intervalo),
            errors=errors,
        )
