from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import EduVulcanAPI
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    api: EduVulcanAPI = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EduVulcanCalendar(entry, api)])


class EduVulcanCalendar(CalendarEntity):
    def __init__(self, entry: ConfigEntry, api: EduVulcanAPI) -> None:
        self.api = api
        self._attr_name = "eduVULCAN Plan Lekcji"
        self._attr_unique_id = f"{entry.entry_id}_calendar"

    async def async_get_events(self, hass, start_date, end_date):
        items = await self.api.get_schedule()
        events = []
        tzinfo = dt_util.get_time_zone(hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
        start_bound = dt_util.as_local(start_date)
        end_bound = dt_util.as_local(end_date)

        for item in items:
            start = item.time_slot.start
            end = item.time_slot.end

            start_dt = datetime.combine(item.date_, start, tzinfo=tzinfo)
            end_dt = datetime.combine(item.date_, end, tzinfo=tzinfo)

            if end_dt < start_bound or start_dt > end_bound:
                continue

            subject = item.subject.name if item.subject else "?"
            teacher = (
                item.teacher_primary.display_name if item.teacher_primary else "?"
            )
            room = item.room.code if item.room else "?"

            events.append(
                CalendarEvent(
                    start=start_dt,
                    end=end_dt,
                    summary=subject,
                    description=f"{teacher}, sala {room}",
                    location=room,
                )
            )

        return events
