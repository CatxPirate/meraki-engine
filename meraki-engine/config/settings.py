from dataclasses import dataclass, field

@dataclass
class Settings:
    retry_limit: int = 3
    verify_timeout: int = 5000
    visual_diff_threshold: float = 0.05
    scroll_delay: float = 0.3
    click_delay: float = 0.1
    human_confirm_timeout: int = 300
