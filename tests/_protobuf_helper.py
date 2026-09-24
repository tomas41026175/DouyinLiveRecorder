"""Minimal protobuf *encoder* for building test fixtures -- the mirror image
of danmaku_capture.py's minimal *decoder*. Deliberately hand-rolled (no
protobuf dependency), same philosophy as the module under test."""


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def tag(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)


def bytes_field(field: int, value: bytes) -> bytes:
    return tag(field, 2) + varint(len(value)) + value


def string_field(field: int, value: str) -> bytes:
    return bytes_field(field, value.encode("utf-8"))
