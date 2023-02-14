import argparse
import asyncio
import logging
import os

from systemd_ctypes import EventLoopPolicy, PathWatch


class Listener:
    @staticmethod
    def do_identity_changed(fd, err):
        print('identity changed', fd, err and os.strerror(err))

    @staticmethod
    def do_inotify_event(mask, cookie, name):
        print('inotify event', mask, cookie, name)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', help='The path to watch')
    args = parser.parse_args()

    watch = PathWatch(args.path, Listener)
    await asyncio.sleep(1000)
    watch.close()

logging.basicConfig(level=logging.DEBUG)
asyncio.set_event_loop_policy(EventLoopPolicy())
asyncio.run(main())
