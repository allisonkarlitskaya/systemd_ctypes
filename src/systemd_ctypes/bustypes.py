# systemd_ctypes
#
# Copyright (C) 2023 Allison Karlitskaya <allison.karlitskaya@redhat.com>
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


# This file is responsible for creating functions to (de)serialize Python
# objects into and out of BusMessage objects.
#
# Each Type corresponds to a (possibly complex) D-Bus type.  It has a .reader
# and a .writer property.  The readers take a message and deserialize a single
# value from it, returning the value:
#
#   def reader(message: BusMessage) -> object:
#
# The writers take a message and a value, and append the value to the message.
#
#   def writer(message: BusMessage, value: object) -> None:
#
# The necessary information for the specific type of object to be handled is
# part of the function.  No additional information needs to be provided.

import binascii
import ctypes
import functools
import inspect
import json
import re
import typing

from enum import Enum
from typing import Callable, ClassVar, Dict, Iterable, List, Optional, Sequence, Tuple, Union

try:
    from typing import TypeGuard
except ImportError:
    TypeGuard = {'BusType.objectpath': bool, 'BusType.signature': bool}


try:
    from typing import Annotated
except ImportError:
    import collections

    class Annotation:
        def __init__(self, origin, metadata):
            self.__metadata__ = [metadata]

    class AnnotatedType(collections.defaultdict):
        def __missing__(self, key):
            return Annotation(*key)

    Annotated = AnnotatedType()


libsystemd = ctypes.CDLL('libsystemd.so.0')


_object_path_re = re.compile(r'/|(/[A-Za-z0-9_]+)+')


def is_object_path(candidate: str) -> TypeGuard['BusType.objectpath']:
    return _object_path_re.fullmatch(candidate) is not None


def is_signature(candidate: str) -> TypeGuard['BusType.signature']:
    offset = 0

    def maybe_pop(acceptable) -> Optional[str]:
        nonlocal offset
        char = candidate[offset]
        if char in acceptable:
            offset += 1
            return char
        else:
            return None

    def pop(acceptable) -> str:
        char = maybe_pop(acceptable)
        assert char is not None
        return char

    def find_next() -> None:
        first = pop('ybnqiuxtsogdva(')    # valid first characters
        if first == 'a':
            if maybe_pop('{'):              # dict
                pop('ybnqiuxtsogd')           # key
                find_next()                   # value
                pop('}')
            else:                           # array
                find_next()                   # item
        elif first == '(':                  # structure
            find_next()                       # at least one item
            while not maybe_pop(')'):
                find_next()                   # and possibly more

    try:
        while offset < len(candidate):
            find_next()
        return True
    except (AssertionError, IndexError):
        return False


def yield_base_helpers() -> Iterable[Tuple[str, object]]:
    for method in ['enter_container', 'exit_container', 'open_container', 'close_container',
                   'append_basic', 'read_basic', 'append_array', 'read_array']:
        yield method, getattr(libsystemd, f'sd_bus_message_{method}')

    for name in ['size_t', 'char_p']:
        instance = getattr(ctypes, f'c_{name}')()
        yield f'{name}', instance
        yield f'{name}_ref', ctypes.byref(instance)
        yield f'{name}_setter', instance.__class__.value.__set__

    for char in 'aervy':
        yield char, ctypes.c_char(ord(char))

    # https://docs.python.org/3/c-api/unicode.html#c.PyUnicode_FromString
    to_bytes = ctypes.pythonapi.PyBytes_FromStringAndSize
    to_bytes.restype = ctypes.py_object
    yield 'to_bytes', to_bytes


_base_helpers = dict(yield_base_helpers())


def call_with_kwargs(func, kwargs):
    parameters = set(inspect.signature(func).parameters)
    return func(**{key: value for key, value in kwargs.items() if key in parameters})


class _cached(type):
    """Ensures that there's no more than a single instance corresponding to a given type."""
    _cache: Dict[Tuple[type, tuple], object] = {}

    def __call__(cls, *args):
        instance = _cached._cache.get((cls, args))
        if instance is None:
            instance = super().__call__(*args)
            _cached._cache[cls, args] = instance
        return instance


class Type(metaclass=_cached):
    __slots__ = 'typestring', 'bytes_typestring', 'writer', 'reader'
    typestring: str
    bytes_typestring: bytes

    def __init__(self, typestring: str, **kwargs):
        self.typestring = typestring
        self.bytes_typestring = typestring.encode('ascii')

        kwargs = dict(_base_helpers, **kwargs)
        self.writer = call_with_kwargs(self.get_writer, kwargs)
        self.reader = call_with_kwargs(self.get_reader, kwargs)

    def __repr__(self):
        return f"{self.__class__.__name__}('{self.typestring}')"

    def get_writer(self, **kwargs: object) -> Callable[[object, object], None]:
        def not_implemented_writer(message, value):
            raise NotImplementedError
        return not_implemented_writer

    def get_reader(self, **kwargs: object) -> Callable[[object], object]:
        def not_implemented_reader(message):
            raise NotImplementedError
        return not_implemented_reader


class BasicType(Type):
    __slots__ = ()

    def __init__(self, typestring: str, ctype, get_wrapper=None, **kwargs):
        variable = ctype()  # NB: not thread-safe
        super().__init__(typestring, ctype=ctype, type_constant=ctypes.c_char(ord(typestring)),
                         getter=get_wrapper or ctype.value.__get__, setter=ctype.value.__set__,
                         variable=variable, reference=ctypes.byref(variable), **kwargs)

    def get_reader(self, read_basic, type_constant, variable, reference, getter):
        def basic_reader(message):
            if read_basic(message, type_constant, reference) <= 0:
                raise StopIteration
            return getter(variable)
        return basic_reader


class FixedType(BasicType):
    __slots__ = ()

    def get_writer(self, append_basic, type_constant, variable, reference, setter, getter):
        def fixed_writer(message, value):
            setter(variable, value)
            if getter(variable) != value:
                raise TypeError(f"Cannot represent value {value} with type '{self.typestring}'")
            append_basic(message, type_constant, reference)
        return fixed_writer


class StringLikeType(BasicType):
    __slots__ = ()

    def __init__(self, typestring: str, guard: Optional[Callable[[str], bool]] = None):
        # https://docs.python.org/3/c-api/unicode.html#c.PyUnicode_FromString
        to_unicode = ctypes.pythonapi.PyUnicode_FromString
        to_unicode.restype = ctypes.py_object

        if guard is not None:
            def convert(candidate):
                if not isinstance(candidate, str):
                    raise TypeError(f"'{self.typestring}' encodes 'str', not '{candidate.__class__.__name__}'")
                if not guard(candidate):
                    raise ValueError(f"Invalid value provided for type '{self.typestring}'")
                return str.encode(candidate)
        else:
            convert = str.encode

        super().__init__(typestring, ctypes.c_char_p, to_unicode, convert=convert)

    def get_writer(self, append_basic, type_constant, convert):
        def string_writer(message, value):
            append_basic(message, type_constant, convert(value))
        return string_writer


class BytestringType(Type):
    __slots__ = ()

    def get_writer(self, append_array, y, size_t_setter, size_t):
        def bytes_writer(message, value):
            if not isinstance(value, bytes):
                if isinstance(value, str):
                    try:
                        value = binascii.a2b_base64(value.encode('ascii'))  # or decode base64
                    except binascii.Error as exc:
                        raise ValueError("'ay' cannot encode invalid base64 string") from exc
                elif isinstance(value, (memoryview, bytearray)):
                    value = bytes(value)
                else:
                    raise TypeError("'ay' can only encode bytes-like or base64 string objects, "
                                    f"not '{value.__class__.__name__}'.")
            size_t_setter(size_t, len(value))
            append_array(message, y, value, size_t)
        return bytes_writer

    def get_reader(self, read_array, y, to_bytes, char_p, char_p_ref, size_t, size_t_ref):
        def bytes_reader(message):
            if read_array(message, y, char_p_ref, size_t_ref) <= 0:
                raise StopIteration
            return to_bytes(char_p, size_t)
        return bytes_reader


class ContainerType(Type):
    _typestring_template: ClassVar[str]
    __slots__ = 'item_types'
    item_types: Sequence[Type]

    def __init__(self, *item_types: Type, **kwargs):
        assert len(item_types) > 0
        item_typestrings = ''.join(item.typestring for item in item_types)
        self.item_types = item_types
        super().__init__(self._typestring_template.replace('_', item_typestrings),
                         type_contents=ctypes.c_char_p(item_typestrings.encode('ascii')),
                         **kwargs)


class ArrayType(ContainerType):
    _typestring_template = 'a_'
    __slots__ = ()

    def __init__(self, item_type):
        super().__init__(item_type,
                         item_writer=item_type.writer,
                         item_reader=item_type.reader,
                         list_append=list.append)

    def get_reader(self, enter_container, exit_container, list_append, item_reader):
        def array_reader(message):
            if enter_container(message, 0, None) <= 0:
                raise StopIteration
            result = []
            try:
                while True:
                    list_append(result, item_reader(message))
            except StopIteration:
                return result
            finally:
                exit_container(message)
        return array_reader

    def get_writer(self, a, type_contents, open_container, close_container, item_writer):
        def array_writer(message, value):
            open_container(message, a, type_contents)
            for item in value:
                item_writer(message, item)
            close_container(message)
        return array_writer


class StructType(ContainerType):
    _typestring_template = '(_)'
    __slots__ = ()

    def get_reader(self, enter_container, exit_container):
        item_readers = tuple(item_type.reader for item_type in self.item_types)

        def array_reader(message):
            if enter_container(message, 0, None) <= 0:
                raise StopIteration
            result = tuple(item_reader(message) for item_reader in item_readers)
            exit_container(message)
            return result
        return array_reader

    def get_writer(self, r, type_contents, open_container, close_container):
        item_writers = tuple(item_type.writer for item_type in self.item_types)

        def struct_writer(message, value):
            if len(value) != len(item_writers):
                raise TypeError(f"Wrong numbers of items ({len(value)}) for structure type '{self.typestring}'")
            open_container(message, r, type_contents)
            for item_writer, item in zip(item_writers, value):
                item_writer(message, item)
            close_container(message)
        return struct_writer


class DictionaryType(ContainerType):
    _typestring_template = 'a{_}'
    __slots__ = ()

    def __init__(self, key_type, value_type, **kwargs):
        assert isinstance(key_type, BasicType)
        item_type = '{' + key_type.typestring + value_type.typestring + '}'
        super().__init__(key_type, value_type,
                         key_reader=key_type.reader, key_writer=key_type.writer,
                         value_reader=value_type.reader, value_writer=value_type.writer,
                         item_type=ctypes.c_char_p(item_type.encode('ascii')))

    def get_reader(self, enter_container, exit_container, key_reader, value_reader):
        def dict_reader(message):
            if enter_container(message, 0, None) <= 0:    # array
                raise StopIteration
            result = {}
            while enter_container(message, 0, None) > 0:  # entry
                key = key_reader(message)
                value = value_reader(message)
                result[key] = value
                exit_container(message)
            exit_container(message)
            return result
        return dict_reader

    def get_writer(self, a, item_type, e, type_contents, open_container, close_container, key_writer, value_writer):
        def dict_writer(message, value):
            open_container(message, a, item_type)                    # array
            for key, value in value.items():
                open_container(message, e, type_contents)              # entry
                key_writer(message, key)                                 # key
                value_writer(message, value)                             # value
                close_container(message)                               # end entry
            close_container(message)                                 # end array
        return dict_writer


class VariantType(Type):
    __slots__ = ()

    def get_reader(self, enter_container, exit_container):
        def variant_reader(message):
            if enter_container(message, 0, None) <= 0:
                raise StopIteration
            typestring = message.get_signature(False)
            type_, = from_signature(typestring)
            value = type_.reader(message)
            exit_container(message)
            return Variant(value, type_)
        return variant_reader

    def get_writer(self, open_container, close_container, v):
        def variant_writer(message, value):
            if isinstance(value, Variant):
                type_ = value.type
                contents = value.value
            else:
                try:
                    type_, = from_signature(value['t'])
                    contents = value['v']
                except KeyError as exc:
                    raise TypeError("'v' can encode Variant objects, or mappings with 't' and 'v' keys") from exc

            open_container(message, v, type_.bytes_typestring)
            type_.writer(message, contents)
            close_container(message)
        return variant_writer


class Variant:
    __slots__ = 'type', 'value'

    def __init__(self, value, hint=None):
        if isinstance(hint, Type):
            self.type = hint
        elif isinstance(hint, str):
            self.type, = from_signature(hint)
        else:
            self.type = from_annotation(hint or value.__class__)
        self.value = value

    def __repr__(self):
        return f"systemd_ctypes.Variant({self.value}, '{self.type.typestring}')"

    def __eq__(self, other):
        if isinstance(other, Variant):
            return self.type == other.type and self.value == other.value
        else:
            return (self.type,) == from_signature(other['t']) and self.value == other['v']

    def __hash__(self):
        return hash(self.type) ^ hash(self.value)


class BusType(Enum):
    boolean = Annotated[bool, FixedType('b', ctypes.c_int, ctypes.c_int.__bool__)]
    byte = Annotated[int, FixedType('y', ctypes.c_uint8)]
    int16 = Annotated[int, FixedType('n', ctypes.c_int16)]
    uint16 = Annotated[int, FixedType('q', ctypes.c_uint16)]
    int32 = Annotated[int, FixedType('i', ctypes.c_int32)]
    uint32 = Annotated[int, FixedType('u', ctypes.c_uint32)]
    int64 = Annotated[int, FixedType('x', ctypes.c_int64)]
    uint64 = Annotated[int, FixedType('t', ctypes.c_uint64)]
    double = Annotated[float, FixedType('d', ctypes.c_double)]
    string = Annotated[str, StringLikeType('s')]
    objectpath = Annotated[str, StringLikeType('o', is_object_path)]
    signature = Annotated[str, StringLikeType('g', is_signature)]
    bytestring = Annotated[bytes, BytestringType('ay')]
    variant = Annotated[dict, VariantType('v')]


_base_equivalence_map: Dict[type, BusType] = {
    bool: BusType.boolean,
    bytes: BusType.bytestring,
    int: BusType.int32,
    str: BusType.string,
    Variant: BusType.variant,
}

_factory_map: Dict[object, Callable[..., Type]] = {
    dict: DictionaryType, typing.Dict: DictionaryType,
    list: ArrayType, typing.List: ArrayType,
    tuple: StructType, typing.Tuple: StructType,
}


@functools.lru_cache()
def from_annotation(annotation: Union[str, type, BusType]) -> Type:
    # Simple Python types
    if isinstance(annotation, str):
        types = from_signature(annotation)
        if len(types) != 1:
            raise TypeError(f"Signature '{annotation}' invalid as a type string "
                            f"because it describes {len(types)} types, not one.")
        return types[0]

    if isinstance(annotation, type):
        annotation = _base_equivalence_map.get(annotation, annotation)

    # Our own BusType types
    if isinstance(annotation, BusType):
        return annotation.value.__metadata__[0]

    # Container types
    try:
        factory = _factory_map[annotation.__origin__]
        args = [from_annotation(arg) for arg in annotation.__args__]
        return factory(*args)
    except (AssertionError, AttributeError, KeyError, TypeError):
        raise TypeError(f"Cannot interpret {annotation} as a dbus type")


_base_typestring_map: Dict[str, Type] = {
    bustype.typestring: bustype for bustype in (from_annotation(entry) for entry in BusType)
}


@functools.lru_cache()
def from_signature(signature: str) -> Tuple[Type, ...]:
    stack = list(reversed(signature))

    def get_one():
        first = stack.pop()
        if first == 'a':
            if stack[-1] == 'y':
                first += stack.pop()
            elif stack[-1] == '{':
                stack.pop()
                return DictionaryType(*get_several('}'))
            else:
                return ArrayType(get_one())
        elif first == '(':
            return StructType(*get_several(')'))

        return _base_typestring_map[first]

    def get_several(end):
        yield get_one()
        while stack[-1] != end:
            yield get_one()
        stack.pop()

    def get_all():
        while stack:
            yield get_one()

    try:
        return tuple(get_all())
    except (AssertionError, IndexError, KeyError) as exc:
        raise TypeError(f"Invalid type signature '{signature}'") from exc


class MessageType:
    item_types: Sequence[Type]
    typestrings: List[str]
    signature: str

    def __init__(self, item_types):
        self.item_types = [from_annotation(item_type) for item_type in item_types]
        self.typestrings = [item_type.typestring for item_type in self.item_types]
        self.signature = ''.join(self.typestrings)

    def write(self, message, *items):
        assert len(items) == len(self.item_types)
        for item_type, item in zip(self.item_types, items):
            item_type.writer(message, item)

    def read(self, message):
        if not message.has_signature(self.signature):
            return None
        return tuple(item_type.reader(message) for item_type in self.item_types)

    def __len__(self):
        return len(self.item_types)


class JSONEncoder(json.JSONEncoder):
    def default(self, obj: object) -> object:
        if isinstance(obj, Variant):
            return {"t": obj.type.typestring, "v": obj.value}
        elif isinstance(obj, bytes):
            return binascii.b2a_base64(obj, newline=False).decode('ascii')
        return super().default(obj)
