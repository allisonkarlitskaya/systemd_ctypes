# systemd_ctypes
#
# Copyright (C) 2023 Martin Pitt <mpitt@redhat.com>
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

# once this runs, test with:
# busctl --user call com.example.Test / com.example.Test HelloWorld s "World"

import asyncio

from systemd_ctypes import bus, run_async


class com_example_Test(bus.Object):
    @bus.Interface.Method('s', 's')
    def hello_world(self, name):
        return f'Hello {name}!'


async def main():
    user_bus = bus.Bus.default_user()

    test_object = com_example_Test()
    test_slot = user_bus.add_object('/', test_object)

    user_bus.request_name('com.example.Test', 0)

    await asyncio.sleep(30)

    user_bus.release_name('com.example.Test')
    del test_slot


run_async(main())
