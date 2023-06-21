import binascii
import ctypes
import typing
from fractions import Fraction
from typing import List

import pytest
from systemd_ctypes import Bus, BusMessage, BusType, bustypes


@pytest.fixture
def bus():
    return Bus.default_user()


@pytest.fixture
def message(bus):
    return bus.message_new_method_call('x.y', '/y', 'z.a', 'a')


@pytest.mark.parametrize('annotation,typestring', [
    (BusType.boolean, 'b'),
    (BusType.byte, 'y'),
    (BusType.int16, 'n'),
    (BusType.uint16, 'q'),
    (BusType.int32, 'i'),
    (BusType.uint32, 'u'),
    (BusType.int64, 'x'),
    (BusType.uint64, 't'),
    (BusType.double, 'd'),
    (BusType.string, 's'),
    (BusType.objectpath, 'o'),
    (BusType.signature, 'g'),
    (BusType.variant, 'v'),
    (bool, 'b'),
    (bytes, 'ay'),
    (int, 'i'),
    (str, 's'),
    (typing.List[str], 'as'),
    # (list[str], 'as'),
    (typing.Dict[str, str], 'a{ss}'),
    # (dict[str, str], 'a{ss}'),
    (typing.Tuple[int, str, bool], '(isb)'),
    # (tuple[int, str, bool], '(isb)'),
])
def test_map_annotations(annotation, typestring):
    bustype = bustypes.from_annotation(annotation)
    assert bustype.typestring == typestring, annotation


@pytest.mark.parametrize('annotation', [
    list, typing.List, dict, typing.Dict, tuple, typing.Tuple, typing.Tuple[()],
    object, set,  # typing.Annotated[object, int], list[str, str],
])
def test_bad_annotation(annotation):
    with pytest.raises(TypeError):
        print(bustypes.from_annotation(annotation))


@pytest.mark.parametrize('signature', [
    *'bynqiuxtdsogv', 'as', 'a{sv}', 'a{sa{sv}}',
    '', 'ss', 'ass', 'asss', '(s)(s)', 'a{sv}sss', 'a{ss}s(ss)', '(((s)))', '((o)x(o))'
])
def test_valid_signature(signature):
    assert bustypes.is_signature(signature)
    types = bustypes.from_signature(signature)
    assert ''.join(type_.typestring for type_ in types) == signature


@pytest.mark.parametrize('signature', [
    *'acefhjklmprwz', 'a{vs}', '{ss}', '(ss', 'a{sss}', '()', '((s)', 'a[sv]', 'a<sv>'
])
def test_invalid_signature(signature):
    assert not bustypes.is_signature(signature)
    with pytest.raises(TypeError):
        bustypes.from_signature(signature)


@pytest.mark.parametrize('annotation', [*BusType, bool, str, int, bytes])
def test_reader_writer(message: BusMessage, annotation: type) -> None:
    pass


def test_simple(message: BusMessage) -> None:
    args = [True, 'foo', 2, 22222222222222222, ['a', 'b', 'c'], ('a', 1, True), {'a': 'b', 'c': 'd'},
            bustypes.Variant(5)]
    types = bustypes.from_signature('bsixas(sib)a{ss}v')

    for (type_, arg) in zip(types, args):
        type_.writer(message, arg)
    message.seal(0, 0)

    result = [type_.reader(message) for type_ in types]
    assert result == args


def test_bad_path(message: BusMessage) -> None:
    writer = bustypes.from_annotation(BusType.objectpath).writer
    writer(message, '/path')
    with pytest.raises(ValueError):
        writer(message, 'path')


def test_bad_signature(message: BusMessage) -> None:
    writer = bustypes.from_annotation(BusType.signature).writer
    writer(message, 'a{sv}')
    with pytest.raises(ValueError):
        writer(message, 'a{vs}')


def test_bad_base64(message: BusMessage) -> None:
    writer = bustypes.from_annotation(BusType.bytestring).writer
    writer(message, '')
    writer(message, 'aaaa')
    with pytest.raises(ValueError):
        writer(message, 'a')


def test_bad_mapping(message: BusMessage) -> None:
    writer = bustypes.from_annotation(typing.Dict[str, str]).writer
    writer(message, {})
    writer(message, {'a': 'b', 'c': 'd'})
    with pytest.raises(AttributeError):
        writer(message, {'a', 'b', 'c'})  # no '.items()'
    with pytest.raises(TypeError):
        writer(message, {1: 'a'})  # wrong key type
    with pytest.raises(TypeError):
        writer(message, {'a': 1})  # wrong value type

    class weird:
        def __init__(self, items):
            self._items = items

        def items(self):
            return self._items

    writer(message, weird([]))
    with pytest.raises(TypeError):
        writer(message, weird([1]))            # can't unpack '1' as key, value -- wrong type
    with pytest.raises(ValueError):
        writer(message, weird([()]))           # can't unpack () as key, value -- wrong value
    with pytest.raises(ValueError):
        writer(message, weird([(1, 2, 3)]))    # ditto


def test_bytestring(message: BusMessage) -> None:
    bustype = bustypes.from_annotation(bytes)
    bustype.writer(message, b'1234')
    message.seal(0, 0)
    assert bustype.reader(message) == b'1234'


one_half = Fraction(1, 2)
one_third = Fraction(1, 3)


@pytest.mark.parametrize('typestring,value', [
    ('y', 0), ('y', 255),
    ('n', -2**15), ('n', 2**15 - 1), ('q', 0), ('q', 2**16 - 1),
    ('i', -2**31), ('i', 2**31 - 1), ('u', 0), ('u', 2**32 - 1),
    ('x', -2**63), ('x', 2**63 - 1), ('t', 0), ('t', 2**64 - 1),
    ('d', one_half), ('d', -2**53), ('d', 2**53),
])
def test_number_limits(message: BusMessage, typestring: str, value: object) -> None:
    message.append_arg(typestring, value)
    message.seal(0, 0)
    x, = message.get_body()
    assert x == value


@pytest.mark.parametrize('typestring,value', [
    ('y', 256), ('y', -1), ('y', one_half),
    ('n', -2**15 - 1), ('n', 2**15), ('n', one_half), ('q', -1), ('q', 2**16), ('q', one_half),
    ('i', -2**31 - 1), ('i', 2**31), ('i', one_half), ('u', -1), ('u', 2**32), ('u', one_half),
    ('x', -2**63 - 1), ('x', 2**63), ('x', one_half), ('t', -1), ('t', 2**64), ('t', one_half),
    ('d', one_third), ('d', -2**53 - 1), ('d', 2**53 + 1),
    ('ay', 0),
])
def test_thats_too_much_man(message: BusMessage, typestring: str, value: object) -> None:
    with pytest.raises(TypeError):
        message.append_arg(typestring, value)


@pytest.mark.parametrize('typestring', [
    *'bynqiuxtdsog',
    'ay', 'ai', 'as',
    '(ii)', '(ss)', '(bb)', '(yy)',
    'a{ss}', 'a(ss)', 'v',
])
def test_crossover_episode(typestring: str) -> None:
    # Throw everything at everything and make sure it either works, or cleanly raises one of:
    #  - TypeError
    #  - ValueError
    #  - AttributeError
    cast: list[object] = [
        '', 0, -1, 1, '/', '/q', '/q/', 'a{sv}', "true", "false",
        {'a': 'b', 'c': 'd'},
        {'x', 'y', 'z'}, (1, 2), (True, False),
        {"t": 'i', "v": 5}, {"t": 'i', "v": "x"},
        {"t", "z", "v", None}, {"t": "i"}, {"v": 5},
        # from here on, utter non-sense
        ctypes.c_int(), ctypes.c_void_p(), ctypes.c_char_p(),
        type, int, str, isinstance, len, str.encode
    ]
    for bits in [7, 8, 15, 16, 31, 32, 63, 64]:
        cast.extend([-2**bits, -2**bits - 1, -2**bits + 1, 2**bits, 2**bits - 1, 2**bits + 1])

    def equiv(via_bus, orig):
        if via_bus == orig:
            return True

        if typestring == 'ay':
            try:
                if binascii.a2b_base64(str.encode(orig)) == via_bus:
                    return True
            except TypeError:
                pass

        elif typestring.startswith('a'):
            if via_bus == list(orig):
                return True

        elif typestring.startswith('('):
            if via_bus == tuple(orig):
                return True

        return False

    worked = 0
    for member in cast:
        m = Bus.default_user().message_new_method_call(None, '/x', 'a.b', 'c')
        try:
            m.append(typestring, member)
        except AttributeError as exc:
            assert 'a{' in typestring
            assert 'items' in str(exc)
        except ValueError as exc:
            assert typestring in ['o', 'g', 'ay']
            assert f"'{typestring}'" in str(exc)
        except TypeError:
            pass  # can happen in lots of places
        else:
            m.seal(0, 0)
            result, = m.get_body()
            assert equiv(result, member)
            worked += 1

    assert worked > 0


def test_singleton_types() -> None:
    # make sure each type has exactly one instance
    str_list = bustypes.from_annotation(List[str])
    tuple_str_list, = bustypes.from_signature('(asas)')
    assert isinstance(tuple_str_list, bustypes.ContainerType)
    assert all(str_list is item for item in tuple_str_list.item_types)
