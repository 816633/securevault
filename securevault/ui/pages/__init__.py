"""八个标签页。"""

from .status import StatusPage
from .records import RecordsPage
from .exclude import ExcludePage
from .schedule import SchedulePage
from .tools import ToolsPage
from .settings import SettingsPage
from .logs import LogsPage
from .about import AboutPage

__all__ = ["StatusPage", "RecordsPage", "ExcludePage", "SchedulePage",
           "ToolsPage", "SettingsPage", "LogsPage", "AboutPage"]
