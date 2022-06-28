# `systemd_ctypes`

A small pure-[`ctypes`](https://docs.python.org/3/library/ctypes.html) wrapper around [`libsystemd`](https://www.freedesktop.org/software/systemd/man/).

This depends on at least `libsystemd 239`, which was released on 2018-06-22.

This project aims to build a small wrapper around `libsystemd` based on `ctypes`, using semi-automated binding techniques.  The highlevel goals are:
 - easy to embed in other projects (eg: in [`zipapp`](https://docs.python.org/3/library/zipapp.html) packages)
 - small code size with little binding-related boilerplate: in many cases, one line per bound function
 - reasonable performance, but not at the cost of readability
 - seemless integration of systemd's event loop with [`asyncio`](https://docs.python.org/3/library/asyncio.html), including [`async`/`await`](https://docs.python.org/3/library/asyncio-task.html).

The initial focus is on the [`sd_event`](https://www.freedesktop.org/software/systemd/man/sd-event.html) and [`sd_bus`](https://www.freedesktop.org/software/systemd/man/sd-bus.html) APIs.

There's a higher-level [`PathWatch`](systemd_ctypes/pathwatch.py) API to make it easier to use `inotify`.

This project originated as a weekend hack to support the efforts to write a portable Python version of [`cockpit-bridge`](https://cockpit-project.org/guide/latest/cockpit-bridge.1.html).

Run tests with [`tox`](https://tox.wiki/).
