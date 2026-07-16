from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DlnaDevice:
    uuid: str
    friendly_name: str
    manufacturer: str
    model_name: str
    location: str
    host: str
    av_transport_url: str
    rendering_control_url: str = ""
    connection_manager_url: str = ""
    online: bool = True

    @property
    def display_model(self) -> str:
        parts = [self.manufacturer, self.model_name]
        return " ".join(part for part in parts if part).strip()
