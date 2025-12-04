# systemd_ctypes
#
# Copyright (C) 2025 Allison Karlitskaya <allison.karlitskaya@redhat.com>
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

import argparse
import asyncio
import shutil
import sys
import uuid

from systemd_ctypes import Bus, EventLoopPolicy, Handle


def property_changed(message):
    print("Property changed:", message.get_body())
    return 0


async def main():
    system = Bus.default_system()
    system.set_allow_interactive_authorization(True)

    parser = argparse.ArgumentParser(description="Run a command as root via systemd")
    parser.add_argument("command", nargs="+", help="Command and arguments to run")
    args = parser.parse_args()

    pathname = shutil.which(args.command[0])
    if not pathname:
        print(f"Failed to find executable for {args.command[0]}", file=sys.stderr)

    unit_name = f"run-r{uuid.uuid4().hex}.service"

    print(f"Started: {unit_name}", file=sys.stderr)

    result = system.call_method(
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "StartTransientUnit",
        "ssa(sv)a(sa(sv))",
        unit_name,
        "fail",
        [
            ("Description", {"t": "s", "v": f"run0: {' '.join(args.command)}"}),
            ("Type", {"t": "s", "v": "exec"}),
            ("User", {"t": "s", "v": "root"}),
            ("StandardInputFileDescriptor", {"t": "h", "v": Handle.borrow(0)}),
            ("StandardOutputFileDescriptor", {"t": "h", "v": Handle.borrow(1)}),
            ("StandardErrorFileDescriptor", {"t": "h", "v": Handle.borrow(2)}),
            ("ExecStart", {"t": "a(sasb)", "v": [(pathname, args.command, False)]}),
        ],
        [],
    )

    print("Result:", result)


asyncio.set_event_loop_policy(EventLoopPolicy())
asyncio.run(main())
