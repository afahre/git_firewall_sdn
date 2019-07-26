#! usr/bin/env python
import logging
import time
from time import gmtime, mktime
import datetime
from datetime import datetime

class Incoming_packet_time():


    def time(self):

        wib = 7*60*60

        now_utc = datetime.utcnow()
        base_utc = datetime(1970, 1, 11)

        time_delta = now_utc - base_utc
        time_delta = time_delta.total_seconds()
        time_delta = time_delta + wib

        now_time_wib = datetime.fromtimestamp(mktime(gmtime(time_delta)))

        print(now_time_wib)
        return now_time_wib
