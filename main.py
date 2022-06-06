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

from systemd_ctypes import Bus, Event, introspection

async def main():
    system = Bus.default_system()
    system.attach_event(None, 0)

    message = system.message_new_method_call('org.freedesktop.hostname1',
                                             '/org/freedesktop/hostname1',
                                             'org.freedesktop.DBus.Introspectable',
                                             'Introspect')
    reply = system.call(message, -1)
    xml, = reply.get_body()
    print(introspection.parse_xml(xml))


    message = system.message_new_method_call('org.freedesktop.hostname1',
                                             '/org/freedesktop/hostname1',
                                             'org.freedesktop.DBus.Properties',
                                             'GetAll')
    message.append('s', 'org.freedesktop.hostname1')
    result = await system.call_async(message, 1000000)
    print(result.get_body())

loop = Event.create_event_loop()
loop.run_until_complete(main())
