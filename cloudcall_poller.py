"""CloudCall recording poller — runs on a schedule via supervisord.

Polls CloudCall Call Logs API every N minutes for new recorded calls,
fetches their recording URLs, and inserts them into the local database.

Only runs between configured business hours (default 6AM-6PM Central Time).
Sleeps between polls.
"""

import logging
import sys
from datetime import datetime
from time import sleep, time

from zoneinfo import ZoneInfo

from cloudcall_config import (
    CLOUDCALL_ENABLED,
    CLOUDCALL_POLL_INTERVAL_MINUTES,
    CLOUDCALL_POLL_START_HOUR_CT,
    CLOUDCALL_POLL_END_HOUR_CT,
    CLOUDCALL_CATCHUP_INTERVAL_MINUTES,
)
from cloudcall_db import init_cloudcall_tables
from cloudcall_service import (
    poll_recent_recordings,
    record_poll_success,
    record_poll_failure,
    catchup_lookback_minutes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("calltranscription.poller")

CT = ZoneInfo("America/Chicago")


def is_within_business_hours() -> bool:
    """Check if current time is within polling hours (Central Time)."""
    now_ct = datetime.now(CT)
    return CLOUDCALL_POLL_START_HOUR_CT <= now_ct.hour < CLOUDCALL_POLL_END_HOUR_CT


def main():
    if not CLOUDCALL_ENABLED:
        logger.error("CLOUDCALL_REFRESH_TOKEN not set — poller cannot run. Exiting.")
        sys.exit(1)

    # Ensure tables exist
    init_cloudcall_tables()

    interval_seconds = CLOUDCALL_POLL_INTERVAL_MINUTES * 60
    catchup_interval_seconds = CLOUDCALL_CATCHUP_INTERVAL_MINUTES * 60
    # 0.0 => a catch-up runs on the first in-hours cycle (heals deploy/restart
    # gaps), then every catchup_interval thereafter.
    last_catchup = 0.0
    logger.info(
        "CloudCall poller started: every %d min, %d:00-%d:00 CT; "
        "catch-up every %d min",
        CLOUDCALL_POLL_INTERVAL_MINUTES,
        CLOUDCALL_POLL_START_HOUR_CT,
        CLOUDCALL_POLL_END_HOUR_CT,
        CLOUDCALL_CATCHUP_INTERVAL_MINUTES,
    )

    while True:
        if is_within_business_hours():
            now = time()
            do_catchup = (last_catchup == 0.0) or (now - last_catchup >= catchup_interval_seconds)
            try:
                if do_catchup:
                    lookback = catchup_lookback_minutes()
                    inserted = poll_recent_recordings(lookback_minutes=lookback)
                    last_catchup = now
                    logger.info(
                        "Catch-up poll (%d min lookback): %d new recordings", lookback, inserted
                    )
                else:
                    inserted = poll_recent_recordings()
                    if inserted > 0:
                        logger.info("Poll cycle: %d new recordings", inserted)
                record_poll_success(inserted)
            except Exception as e:
                record_poll_failure(str(e))
                logger.error("Poll cycle failed: %s", e)
        else:
            now_ct = datetime.now(CT)
            logger.debug("Outside business hours (%d:00 CT), sleeping", now_ct.hour)

        sleep(interval_seconds)


if __name__ == "__main__":
    main()
