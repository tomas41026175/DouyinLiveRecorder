"""Regression tests for src/danmaku_capture.py's pure protobuf decode logic
(_iter_fields, _decode_chat_messages, _parse_close_frame, parse_push_frame).
No network/websocket/thread code is exercised here -- only the hand-rolled
protobuf reader, using hand-built byte fixtures via _protobuf_helper (the
same "no external protobuf lib" philosophy as the module under test).
Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, struct, gzip, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
from _protobuf_helper import varint, tag, bytes_field, string_field
danmaku_capture = load("danmaku_capture")


def build_user(nickname):
    return string_field(3, nickname)  # User.nickName = field 3


def build_chat_message(content, nickname=None):
    """ChatMessage: field 2 = User submsg, field 3 = content string."""
    out = b""
    if nickname is not None:
        out += bytes_field(2, build_user(nickname))
    out += string_field(3, content)
    return out


def build_message(method, payload_bytes):
    """Message: field 1 = method string, field 2 = payload bytes."""
    return string_field(1, method) + bytes_field(2, payload_bytes)


def build_response(messages, internal_ext=None):
    """Response: field 1 = repeated Message, field 5 = internalExt string."""
    out = b""
    for m in messages:
        out += bytes_field(1, m)
    if internal_ext is not None:
        out += string_field(5, internal_ext)
    return out


class IterFieldsTests(unittest.TestCase):
    def test_length_delimited_roundtrip(self):
        buf = string_field(3, "hello")
        fields = list(danmaku_capture._iter_fields(buf))
        self.assertEqual(len(fields), 1)
        field, wire, val = fields[0]
        self.assertEqual((field, wire), (3, 2))
        self.assertEqual(val, b"hello")

    def test_varint_field(self):
        buf = tag(1, 0) + varint(42)
        fields = list(danmaku_capture._iter_fields(buf))
        self.assertEqual(fields, [(1, 0, 42)])

    def test_multiple_fields_in_order(self):
        buf = string_field(1, "a") + string_field(2, "b")
        fields = list(danmaku_capture._iter_fields(buf))
        self.assertEqual([(f, w) for f, w, _ in fields], [(1, 2), (2, 2)])

    def test_empty_buffer_yields_nothing(self):
        self.assertEqual(list(danmaku_capture._iter_fields(b"")), [])


class AsTextTests(unittest.TestCase):
    def test_valid_utf8_returned(self):
        self.assertEqual(danmaku_capture._as_text("你好".encode("utf-8")), "你好")

    def test_invalid_utf8_returns_none(self):
        self.assertIsNone(danmaku_capture._as_text(b"\xff\xfe\x00"))

    def test_empty_or_whitespace_returns_none(self):
        self.assertIsNone(danmaku_capture._as_text(b""))
        self.assertIsNone(danmaku_capture._as_text(b"   "))


class DecodeChatMessagesTests(unittest.TestCase):
    def test_extracts_content_and_user(self):
        chat = build_chat_message("hello there", nickname="小美")
        msg = build_message("WebcastChatMessage", chat)
        response = build_response([msg])
        out = danmaku_capture._decode_chat_messages(response)
        self.assertEqual(out, [{"user": "小美", "content": "hello there"}])

    def test_extracts_content_without_user(self):
        chat = build_chat_message("no nickname here")
        msg = build_message("WebcastChatMessage", chat)
        response = build_response([msg])
        out = danmaku_capture._decode_chat_messages(response)
        self.assertEqual(out, [{"user": "", "content": "no nickname here"}])

    def test_ignores_non_chat_message_types(self):
        # e.g. WebcastGiftMessage, WebcastLikeMessage -- must not be decoded
        # as chat just because they share the same envelope shape.
        payload = build_chat_message("this looks like chat but isn't")
        msg = build_message("WebcastGiftMessage", payload)
        response = build_response([msg])
        out = danmaku_capture._decode_chat_messages(response)
        self.assertEqual(out, [])

    def test_multiple_chat_messages_in_one_response(self):
        m1 = build_message("WebcastChatMessage", build_chat_message("first"))
        m2 = build_message("WebcastChatMessage", build_chat_message("second"))
        response = build_response([m1, m2])
        out = danmaku_capture._decode_chat_messages(response)
        self.assertEqual([o["content"] for o in out], ["first", "second"])

    def test_regression_internalExt_never_read_as_content(self):
        """The historical bug: Response.internalExt (field 5, a diagnostic
        string like "internal_src:pushserver|...") used to get misread as
        chat content via a wrong-field-number fallback. With no real chat
        messages present, decoding a Response that only has internalExt set
        must yield an empty list, never a fake "message" made of that
        diagnostic string."""
        response = build_response([], internal_ext="internal_src:pushserver|first_req_ms:123|seq:1")
        out = danmaku_capture._decode_chat_messages(response)
        self.assertEqual(out, [])

    def test_junk_looking_content_filtered_even_if_in_content_field(self):
        """Defense-in-depth: even if a real ChatMessage.content field somehow
        contained diagnostic-looking text, the junk regex must reject it
        rather than store it as a "chat message"."""
        chat = build_chat_message("internal_src:pushserver|wss_msg_type:x")
        msg = build_message("WebcastChatMessage", chat)
        response = build_response([msg])
        out = danmaku_capture._decode_chat_messages(response)
        self.assertEqual(out, [])

    def test_empty_response_returns_empty_list(self):
        self.assertEqual(danmaku_capture._decode_chat_messages(b""), [])

    def test_malformed_bytes_do_not_raise(self):
        # truncated/garbage bytes must degrade to "no messages found", not crash
        out = danmaku_capture._decode_chat_messages(b"\xff\xff\xff")
        self.assertEqual(out, [])


class ParsePushFrameTests(unittest.TestCase):
    def test_extracts_chat_from_field8_payload(self):
        chat = build_chat_message("hi", nickname="阿明")
        msg = build_message("WebcastChatMessage", chat)
        response = build_response([msg])
        frame = bytes_field(8, response)
        out = danmaku_capture.parse_push_frame(frame)
        self.assertEqual(out, [{"user": "阿明", "content": "hi"}])

    def test_gzip_compressed_payload_decompressed_first(self):
        chat = build_chat_message("gzipped hi")
        msg = build_message("WebcastChatMessage", chat)
        response = build_response([msg])
        compressed = gzip.compress(response)
        frame = bytes_field(8, compressed)
        out = danmaku_capture.parse_push_frame(frame)
        self.assertEqual(out, [{"user": "", "content": "gzipped hi"}])

    def test_no_field8_falls_back_to_whole_frame(self):
        chat = build_chat_message("fallback")
        msg = build_message("WebcastChatMessage", chat)
        response = build_response([msg])  # not wrapped in field 8 at all
        out = danmaku_capture.parse_push_frame(response)
        self.assertEqual(out, [{"user": "", "content": "fallback"}])

    def test_malformed_frame_returns_empty_not_raises(self):
        self.assertEqual(danmaku_capture.parse_push_frame(b"\x00\xff garbage"), [])


class ParseCloseFrameTests(unittest.TestCase):
    def test_code_and_reason(self):
        payload = struct.pack(">H", 1000) + "bye".encode("utf-8")
        code, reason = danmaku_capture._parse_close_frame(payload)
        self.assertEqual(code, 1000)
        self.assertEqual(reason, "bye")

    def test_short_payload_returns_none_code(self):
        code, reason = danmaku_capture._parse_close_frame(b"\x01")
        self.assertIsNone(code)
        self.assertEqual(reason, "")

    def test_empty_payload(self):
        code, reason = danmaku_capture._parse_close_frame(b"")
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
