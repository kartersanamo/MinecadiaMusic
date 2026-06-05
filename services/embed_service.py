import os
from typing import Optional


class EmbedService:
    @staticmethod
    def get_logo_url(logo_path: Optional[str]) -> Optional[str]:
        if not logo_path:
            return None
        if logo_path.startswith(("http://", "https://")):
            return logo_path
        if os.path.isfile(logo_path):
            return f"attachment://{os.path.basename(logo_path)}"
        return None
