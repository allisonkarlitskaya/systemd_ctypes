# systemd_ctypes
#
# Copyright (C) 2022 Allison Karlitskaya <allison.karlitskaya@redhat.com>
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

import errno
import logging
import os
import stat
from typing import Any, List, Optional

from .event import Event
from .inotify import Event as IN

logger = logging.getLogger(__name__)


# inotify hard facts:
#
# DELETE_SELF doesn't get called until all references to an inode are gone
#   - including open fds
#   - including on directories
#
# ATTRIB gets called when unlinking files (because the link count changes) but
# not on directories.  When unlinking an open directory, no events at all
# happen on the directory.  ATTRIB also collects child events, which means we
# get a lot of unwanted noise.
#
# There's nothing like UNLINK_SELF, unfortunately.
#
# Even if it was possible to take this approach, it might not work: after
# you've opened the fd, it might get deleted before you can establish the watch
# on it.
#
# Additionally, systemd makes it impossible to register those events on
# symlinks (because it removes IN_DONT_FOLLOW in order to watch via
# /proc/self/fd).
#
# For all of these reasons, unfortunately, the best way seems to be to watch
# for CREATE|DELETE|MOVE events on each intermediate directory.
#
# Unfortunately there is no way to filter to only the name we're interested in,
# so we're gonna get a lot of unnecessary wakeups.
#
# Also: due to the above-mentioned race about watching after opening the fd,
# let's just always watch for both create and delete events *before* trying to
# open the fd.  We could try to reduce the mask after the fact, but meh...
#
# We use a WatchInvalidator utility class to fill the role of "Tell me when an
# event happened on this (directory) fd which impacted the name file".  We
# build a series of these when setting up a watch in order to find out if any
# part of the path leading to the monitored file changed.


class Handle(int):
    """An integer subclass that makes it easier to work with file descriptors"""

    def __new__(cls, fd: int = -1) -> 'Handle':
        return super(Handle, cls).__new__(cls, fd)

    # separate __init__() to set _needs_close mostly to keep pylint quiet
    def __init__(self, fd: int = -1):
        super().__init__()
        self._needs_close = fd != -1

    def __bool__(self) -> bool:
        return self != -1

    def close(self) -> None:
        if self._needs_close:
            self._needs_close = False
            os.close(self)

    def __eq__(self, value: object) -> bool:
        if int.__eq__(self, value):  # also handles both == -1
            return True

        if not isinstance(value, int):  # other object is not an int
            return False

        if not self or not value:  # when only one == -1
            return False

        return os.path.sameopenfile(self, value)

    def __del__(self) -> None:
        if self._needs_close:
            self.close()

    def __enter__(self) -> 'Handle':
        return self

    def __exit__(self, _type: type, _value: object, _traceback: object) -> None:
        self.close()

    @classmethod
    def open(cls, *args: Any, **kwargs: Any) -> 'Handle':
        return cls(os.open(*args, **kwargs))

    def steal(self) -> 'Handle':
        self._needs_close = False
        return self.__class__(int(self))


class WatchInvalidator:
    _name: bytes
    _source: Optional[Event.Source]
    _watch: Optional['PathWatch']

    def event(self, mask: IN, _cookie: int, name: Optional[bytes]) -> None:
        logger.debug('invalidator event %s %s', mask, name)
        if self._watch is not None:
            # If this node itself disappeared, that's definitely an
            # invalidation.  Otherwise, the name needs to match.
            if IN.IGNORED in mask or self._name == name:
                logger.debug('Invalidating!')
                self._watch.invalidate()

    def __init__(self, watch: 'PathWatch', event: Event, dirfd: int, name: str):
        self._watch = watch
        self._name = name.encode('utf-8')

        # establishing invalidation watches is best-effort and can fail for a
        # number of reasons, including search (+x) but not read (+r) permission
        # on a particular path component, or exceeding limits on watches
        try:
            mask = IN.CREATE | IN.DELETE | IN.MOVE | IN.DELETE_SELF | IN.IGNORED
            self._source = event.add_inotify_fd(dirfd, mask, self.event)
        except OSError:
            self._source = None

    def close(self) -> None:
        # This is a little bit tricky: systemd doesn't have a specific close
        # API outside of unref, so let's make it as explicit as possible.
        self._watch = None
        self._source = None


class PathStack(List[str]):
    def add_path(self, pathname: str) -> None:
        # TO DO: consider doing something reasonable with trailing slashes
        # this is a stack, popped from the end: push components in reverse
        self.extend(item for item in reversed(pathname.split('/')) if item)
        if pathname.startswith('/'):
            self.append('/')

    def __init__(self, path: str):
        super().__init__()
        self.add_path(path)


class Listener:
    def do_inotify_event(self, mask: IN, cookie: int, name: Optional[bytes]) -> None:
        raise NotImplementedError

    def do_identity_changed(self, fd: Optional[Handle], errno: Optional[int]) -> None:
        raise NotImplementedError


class PathWatch:
    _event: Event
    _listener: Listener
    _path: str
    _invalidators: List[WatchInvalidator]
    _errno: Optional[int]
    _source: Optional[Event.Source]
    _tag: Optional[None]
    _fd: Handle

    def __init__(self, path: str, listener: Listener, event: Optional[Event] = None):
        self._event = event or Event.default()
        self._path = path
        self._listener = listener

        self._invalidators = []
        self._errno = None
        self._source = None
        self._tag = None
        self._fd = Handle()

        self.invalidate()

    def got_event(self, mask: IN, cookie: int, name: Optional[bytes]) -> None:
        logger.debug('target event %s: %s %s %s', self._path, mask, cookie, name)
        self._listener.do_inotify_event(mask, cookie, name)

    def invalidate(self) -> None:
        for invalidator in self._invalidators:
            invalidator.close()
        self._invalidators = []

        try:
            fd = self.walk()
        except OSError as error:
            logger.debug('walk ended in error %d', error.errno)

            if self._source or self._fd or self._errno != error.errno:
                logger.debug('Ending existing watches.')
                self._source = None
                self._fd.close()
                self._fd = Handle()
                self._errno = error.errno

                logger.debug('Notifying of new error state %d', self._errno)
                self._listener.do_identity_changed(None, self._errno)

            return

        with fd:
            logger.debug('walk successful.  Got fd %d', fd)
            if fd == self._fd:
                logger.debug('fd seems to refer to same file.  Doing nothing.')
                return

            logger.debug('This file is new for us.  Removing old watch.')
            self._source = None
            self._fd.close()
            self._fd = fd.steal()

            try:
                logger.debug('Establishing a new watch.')
                self._source = self._event.add_inotify_fd(self._fd, IN.CHANGED, self.got_event)
                logger.debug('Watching successfully.  Notifying of new identity.')
                self._listener.do_identity_changed(self._fd, None)
            except OSError as error:
                logger.debug('Watching failed (%d).  Notifying of new identity.', error.errno)
                self._listener.do_identity_changed(self._fd, error.errno)

    def walk(self) -> Handle:
        remaining_symlink_lookups = 40
        remaining_components = PathStack(self._path)
        dirfd = Handle()

        try:
            logger.debug('Starting path walk')

            while remaining_components:
                logger.debug('r=%s dfd=%s', remaining_components, dirfd)

                name = remaining_components.pop()

                if dirfd and name != '/':
                    self._invalidators.append(WatchInvalidator(self, self._event, dirfd, name))

                with Handle.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd) as fd:
                    mode = os.fstat(fd).st_mode

                    if stat.S_ISLNK(mode):
                        if remaining_symlink_lookups == 0:
                            raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))
                        remaining_symlink_lookups -= 1
                        linkpath = os.readlink('', dir_fd=fd)
                        logger.debug('%s is a symlink.  adding %s to components', name, linkpath)
                        remaining_components.add_path(linkpath)

                    else:
                        dirfd.close()
                        dirfd = fd.steal()

            return dirfd.steal()

        finally:
            dirfd.close()

    def close(self) -> None:
        for invalidator in self._invalidators:
            invalidator.close()
        self._invalidators = []
        self._source = None
        self._fd.close()
