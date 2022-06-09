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

from systemd_ctypes import Bus, Event, introspection


def property_changed(message):
    print('Property changed:', message.get_body())
    return 0


async def main():
    system = Bus.default_system()
    system.attach_event(None, 0)

    xml, = system.call_method('org.freedesktop.hostname1',
                              '/org/freedesktop/hostname1',
                              'org.freedesktop.DBus.Introspectable',
                              'Introspect')
    print(introspection.parse_xml(xml))


    items, = await system.call_method_async('org.freedesktop.hostname1',
                                            '/org/freedesktop/hostname1',
                                            'org.freedesktop.DBus.Properties',
                                            'GetAll',
                                            's', 'org.freedesktop.hostname1')
    print(items)

    return system.add_match("interface='org.freedesktop.DBus.Properties'", property_changed)

loop = Event.create_event_loop()
SLOT = loop.run_until_complete(main())
loop.run_forever()
