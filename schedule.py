from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def active_seconds(now=None):
    """Seconds until 01:00 KST; fully stopped between 01:00 and 07:00."""
    now = datetime.now(KST) if now is None else now.astimezone(KST)
    if 1 <= now.hour < 7:
        return 0
    end = now.replace(hour=1, minute=0, second=0, microsecond=0)
    if now.hour >= 7:
        end += timedelta(days=1)
    return (end - now).total_seconds()


def nightly_backup_due(now=None):
    now = datetime.now(KST) if now is None else now.astimezone(KST)
    return now.hour == 0 and now.minute == 59
