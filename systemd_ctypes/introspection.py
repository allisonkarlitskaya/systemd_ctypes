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

import xml.etree.ElementTree as ET

def parse_method(method):
    return {
        "in": [tag.attrib['type'] for tag in method.findall("arg[@direction!='out']")],
        "out": [tag.attrib['type'] for tag in method.findall("arg[@direction='out']")]
    }


def parse_property(prop):
    return {
        "flags": 'w' if prop.attrib.get('access') == 'write' else 'r',
        "type": prop.attrib['type']
    }


def parse_signal(signal):
    return {"in": [tag.attrib['type'] for tag in signal]}


def parse_interface(interface):
    return {
        "methods": {tag.attrib['name']: parse_method(tag) for tag in interface.findall('method')},
        "properties": {tag.attrib['name']: parse_property(tag) for tag in interface.findall('property')},
        "signals": {tag.attrib['name']: parse_signal(tag) for tag in interface.findall('signal')}
    }


def parse_xml(xml, interface_names=None):
    et = ET.fromstring(xml)

    if interface_names is not None:
        predicate = lambda tag: tag.attrib['name'] in interface_names
    else:
        predicate = lambda tag: True

    return {tag.attrib['name']: parse_interface(tag) for tag in et.findall('interface') if predicate(tag)}
