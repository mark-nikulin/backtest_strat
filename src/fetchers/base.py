import abc
import logging
import asyncio
from pathlib import Path
from datetime import date, timedelta
from typing import List, Optional

class BaseExchangeFetcher(abc.ABC):
    def __init__()