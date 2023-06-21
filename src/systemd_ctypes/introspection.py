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
        "in": [tag.attrib['type'] for tag in method.findall("arg") if tag.get('direction', 'in') == 'in'],
        "out": [tag.attrib['type'] for tag in method.findall("arg[@direction='out']")]
    }


def parse_property(prop):
    return {
        "flags": 'w' if prop.attrib.get('access') == 'write' else 'r',
        "type": prop.attrib['type']
    }


def parse_signal(signal):
    return {"in": [tag.attrib['type'] for tag in signal.findall("arg")]}


def parse_interface(interface):
    return {
        "methods": {tag.attrib['name']: parse_method(tag) for tag in interface.findall('method')},
        "properties": {tag.attrib['name']: parse_property(tag) for tag in interface.findall('property')},
        "signals": {tag.attrib['name']: parse_signal(tag) for tag in interface.findall('signal')}
    }


def parse_xml(xml):
    et = ET.fromstring(xml)
    return {tag.attrib['name']: parse_interface(tag) for tag in et.findall('interface')}


# Pretend like this is a little bit functional
def element(tag, children=(), **kwargs):
    tag = ET.Element(tag, kwargs)
    tag.extend(children)
    return tag


def method_to_xml(name, method_info):
    return element('method', name=name,
                   children=[
                       element('arg', type=arg_type, direction=direction)
                       for direction in ['in', 'out']
                       for arg_type in method_info[direction]
                   ])


def property_to_xml(name, property_info):
    return element('property', name=name,
                   access='write' if property_info['flags'] == 'w' else 'read',
                   type=property_info['type'])


def signal_to_xml(name, signal_info):
    return element('signal', name=name,
                   children=[
                       element('arg', type=arg_type) for arg_type in signal_info['in']
                   ])


def interface_to_xml(name, interface_info):
    return element('interface', name=name,
                   children=[
                       *(method_to_xml(name, info) for name, info in interface_info['methods'].items()),
                       *(property_to_xml(name, info) for name, info in interface_info['properties'].items()),
                       *(signal_to_xml(name, info) for name, info in interface_info['signals'].items()),
                   ])


def to_xml(interfaces):
    node = element('node', children=(interface_to_xml(name, members) for name, members in interfaces.items()))
    return ET.tostring(node, encoding='unicode')
