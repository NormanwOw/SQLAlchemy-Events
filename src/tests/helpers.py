import os
from pathlib import Path
from typing import Any


def process_marker_handler(rows: list[Any]) -> None:
    marker = os.environ.get('SA_EVENTS_TEST_MARKER')

    if marker:
        Path(marker).write_text(','.join(str(row) for row in rows))
