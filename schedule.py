from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def active_seconds(now=None):
    """Seconds left in today's 07:00–24:00 KST session; zero during quiet hours."""
    now = datetime.now(KST) if now is None else now.astimezone(KST)
    if now.hour < 7:
        return 0
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - now).total_seconds()
