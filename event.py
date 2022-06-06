# Copyright (C) 2022  Allison Karlitskaya
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
import selectors
import signal

from libsystemd import sd, sd_event_p


class Event(sd_event_p):
    @staticmethod
    def default():
        event = Event()
        sd.event_default(event)
        return event

    def get_fd(self):
        return sd.event_get_fd(self)

    def prepare(self):
        sd.event_prepare(self)

    def check(self):
        return sd.event_wait(self, 0)

    def dispatch(self):
        sd.event_dispatch(self)

    def run(self, usec=0):
        print(' ', sd.event_run(self, usec))

    def loop(self):
        # KeyboardInterrupt doesn't get delivered into C code...
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        sd.event_loop(self)

    def get_loop(self):
        return asyncio.SelectorEventLoop(Selector(self))

# This is all a bit more awkward than it should have to be: systemd's event
# loop chaining model is designed for glib's prepare/check/dispatch paradigm;
# failing to call prepare() can lead to deadlocks, for example.
#
# Hack a selector subclass which calls prepare() before sleeping and this for us.
class Selector(selectors.DefaultSelector):
    def __init__(self, event=None):
        super().__init__()
        self.sd_event = event or Event.default()
        self.key = self.register(event.get_fd(), selectors.EVENT_READ)

    def select(self, timeout=None):
        self.sd_event.prepare()
        ready = super().select(timeout)
        if self.sd_event.check():
            self.sd_event.dispatch()
        # NB: this could return zero events with infinite timeout, but nobody seems to mind.
        return [(key, events) for (key, events) in ready if key != self.key]
