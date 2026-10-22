"""Optional newline-delimited progress events for the desktop CLI clients."""
import json


PROGRESS_PREFIX = '@@PROGRESS '


def update_progress(callback, stage, message, current=0, total=0):
    if callback is not None:
        callback({'stage': stage, 'message': message, 'current': current, 'total': total})


def print_progress(event):
    print(PROGRESS_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)
