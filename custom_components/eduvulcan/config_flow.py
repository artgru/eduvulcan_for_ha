from homeassistant import config_entries
import voluptuous as vol

from .const import DOMAIN, CONF_LOGIN, CONF_PASSWORD

class EduvulcanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="eduVULCAN", data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_LOGIN): str,
            vol.Required(CONF_PASSWORD): str,
        })

        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_reauth(self, user_input=None):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry, data=user_input
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        schema = vol.Schema({
            vol.Required(CONF_LOGIN): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema)
