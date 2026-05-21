from enum import Enum, auto

class FallbackOrder(Enum):
    DOM = 0
    SCROLL = auto()
    HOVER = auto()
    COORDINATE = auto()
    VISION = auto()
    HUMAN = auto()

class VerifyStrategy(Enum):
    DOM_CHANGE = "dom_change"
    URL_CHANGE = "url_change"
    LOADER_GONE = "loader_gone"
    VISUAL_DIFF = "visual_diff"
    NETWORK = "network"
