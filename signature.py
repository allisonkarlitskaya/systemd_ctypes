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
