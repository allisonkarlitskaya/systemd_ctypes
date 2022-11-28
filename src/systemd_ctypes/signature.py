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

def consume_multiple(signature, offset, endchar):
    children = []
    while signature[offset] != endchar:
        offset, child = consume_one(signature, offset)
        children.append(child)
    return offset + 1, children


def consume_one(signature, start):
    first = signature[start]

    if first == 'a':
        end, childinfo = consume_one(signature, start + 1)
        typeinfo = ('a', signature[start + 1:end], childinfo)
    elif first == '(':
        end, childinfo = consume_multiple(signature, start + 1, ')')
        typeinfo = ('r', signature[start + 1: end - 1], childinfo)
    elif first == '{':
        end, childinfo = consume_multiple(signature, start + 1, '}')
        typeinfo = ('e', signature[start + 1: end - 1], childinfo)
    else:
        end = start + 1
        typeinfo = (first, first, None)

    return end, typeinfo


def parse_typestring(typestring):
    end, typeinfo = consume_one(typestring, 0)
    assert end == len(typestring)
    return typeinfo


def parse_signature(signature):
    typeinfos = []
    offset = 0
    while offset < len(signature):
        offset, child = consume_one(signature, offset)
        typeinfos.append(child)
    return typeinfos
