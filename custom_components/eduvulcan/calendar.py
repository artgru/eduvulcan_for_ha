from datetime import datetime
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .api import EduVulcanAPI


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    api = EduVulcanAPI(
        hass,
        entry.data["login"],
        entry.data["password"],
    )
    async_add_entities([EduVulcanCalendar(api)])


class EduVulcanCalendar(CalendarEntity):
    def __init__(self, api: EduVulcanAPI):
        self.api = api
        self._attr_name = "eduVULCAN Plan Lekcji"
        self._events = []

    async def async_get_events(self, hass, start_date, end_date):
        items = await self.api.get_schedule()
        events = []

        for item in items:
            start = item.time_slot.start
            end = item.time_slot.end

            start_dt = datetime.combine(item.date_, start)
            end_dt = datetime.combine(item.date_, end)

            subject = item.subject.name if item.subject else "?"
            teacher = item.teacher_primary.display_name if item.teacher_primary else "?"
            room = item.room.code if item.room else "?"

            events.append(
                CalendarEvent(
                    start=start_dt,
                    end=end_dt,
                    summary=subject,
                    description=f"{teacher}, sala {room}",
                )
            )

        return events
