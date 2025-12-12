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
import json
import logging
import os.path
import shutil
import sys
import uuid
from collections.abc import Mapping, Sequence

from systemd_ctypes import Bus, BusError, BusMessage, EventLoopPolicy, Variant

logger = logging.getLogger(__name__)


async def wait_exited(bus: Bus, unit_path: str) -> dict[str, object]:
    # We need to track the properties of the unit to find out when it exits
    props: dict[str, object] = {}
    exited = asyncio.Event()

    def update_props(changed: dict[str, Variant]) -> None:
        props.update((k, v.value) for k, v in changed.items())
        logger.debug(
            "  update props: %s / %s / %s / %s",
            props.get("Job"),
            props.get("ActiveState"),
            props.get("Result"),
            props.get("ExecMainStatus"),
        )
        # Initially we're in "inactive" and the Job is set so we need to wait for "inactive" with no Job
        if props.get("ActiveState") in ("inactive", "failed") and props.get("Job") == (0, "/"):
            logger.debug("    → exited!")
            exited.set()

    def properties_changed(message: BusMessage) -> bool:
        logger.debug("Properties changed")
        if props is not None:
            _interface, changed, _invalidated = message.get_body()
            assert isinstance(changed, dict)
            update_props(changed)
        else:
            logger.debug("  ignoring properties change before initial value set")

        return True

    logger.debug("Watching properties on {unit_path}")
    slot = bus.add_match(
        ",".join(
            f"{k}='{v}'"
            for k, v in {
                "type": "signal",
                "sender": "org.freedesktop.systemd1",
                "path": unit_path,
                "interface": "org.freedesktop.DBus.Properties",
                "member": "PropertiesChanged",
            }.items()
        ),
        properties_changed,
    )

    logger.debug("Gathering Unit props on {unit_path}")
    (unit_props,) = await bus.call_method_async(
        "org.freedesktop.systemd1",
        unit_path,
        "org.freedesktop.DBus.Properties",
        "GetAll",
        "s",
        "org.freedesktop.systemd1.Unit",
    )

    logger.debug("Gathering Service props on {unit_path}")
    (service_props,) = await bus.call_method_async(
        "org.freedesktop.systemd1",
        unit_path,
        "org.freedesktop.DBus.Properties",
        "GetAll",
        "s",
        "org.freedesktop.systemd1.Service",
    )

    assert isinstance(service_props, dict)
    assert isinstance(unit_props, dict)
    logger.debug("Setting initial properties")
    update_props({**unit_props, **service_props})

    # Wait for the combination we're looking for
    await exited.wait()

    logger.debug("Removing properties subscription {unit_path}")
    slot.cancel()  # unsubscribe

    return props  # and return the full properties bag


async def run0(bus: Bus, cmd: str, args: Sequence[str]) -> Mapping[str, object]:
    # NB: carefully chosen to avoid the need for escapes
    unit_name = f"run0r{uuid.uuid4().hex}.service"
    unit_path = f"/org/freedesktop/systemd1/unit/{unit_name.replace('.', '_2e')}"

    (start_job,) = await bus.call_method_async(
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "StartTransientUnit",
        "ssa(sv)a(sa(sv))",
        unit_name,
        "fail",  # mode
        [
            ("Description", {"t": "s", "v": f"run0: {cmd} {' '.join(args)}"}),
            ("Type", {"t": "s", "v": "exec"}),
            ("User", {"t": "s", "v": "root"}),
            ("StandardInputFileDescriptor", {"t": "h", "v": sys.stdin}),
            ("StandardOutputFileDescriptor", {"t": "h", "v": sys.stdout}),
            ("StandardErrorFileDescriptor", {"t": "h", "v": sys.stderr}),
            ("ExecStart", {"t": "a(sasb)", "v": [(cmd, (cmd, *args), False)]}),
        ],
        [],
    )

    assert isinstance(start_job, str)

    return await wait_exited(bus, unit_path)


def main() -> str | int | None:
    asyncio.set_event_loop_policy(EventLoopPolicy())

    parser = argparse.ArgumentParser(description="Run a command as root via systemd")
    parser.add_argument("--debug", action="store_true", help="Enable debugging")
    parser.add_argument("cmd", help="Command to run")
    parser.add_argument("args", nargs="*", help="Arguments to the command")
    args = parser.parse_args()

    if args.debug:
        print("huhu")
        logging.basicConfig(format="%(name)s-%(levelname)s: %(message)s")
        logging.getLogger().setLevel(level=logging.DEBUG)

    # We should pass an absolute pathname to systemd, so we need to do the $PATH lookup
    pathname = args.cmd if os.path.isabs(args.cmd) else shutil.which(args.cmd)
    if not pathname:
        print(f"Failed to find executable for {args.cmd}", file=sys.stderr)

    system = Bus.default_system()
    system.set_allow_interactive_authorization(True)
    try:
        properties = asyncio.run(run0(system, pathname, args.args), debug=args.debug)
    except BusError as exc:
        print(f"Error: [{exc.name}] {exc.message}")
        return "255"

    if logger.isEnabledFor(logging.DEBUG):
        dump = json.dumps(properties, indent=4, default=repr, sort_keys=True)
        logger.debug("Properties: %s", dump)

    match properties.get("ActiveState"):
        case "failed":
            logger.error("failed to start!")
            return 255

        case "inactive":
            match properties.get("Result"):
                case "success":
                    print("Success!")
                    return None

                case "exit-code":
                    exit_code = properties.get("ExecMainStatus")
                    print(f"Exit with status {exit_code}")
                    return int(exit_code or 255)  # type: ignore[arg-type]

                case other:
                    return f"Exit with status '{other}'"

        case other:
            return f"Strange state {other}"


if __name__ == "__main__":
    sys.exit(main())
