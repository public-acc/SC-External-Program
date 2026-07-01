"""scnd - StarCraft native save-code codec for EUD maps.

scnd is intentionally separate from sc_uiux_bridge.  It keeps the same
creator-facing "bind variables first, save/load later" shape, but the payload is
made for chat codes:

1. compile-time schema order, no key names in the code
2. dense bit packing
3. EUD-friendly keyed stream transform and 64-bit tag
4. Base64url text output
5. state-machine ticks so large saves do not run in one frame

This is an in-map authenticated codec.  A maker key embedded in a published map
cannot be treated like a server-side secret, but this blocks casual editing and
keeps the code compact without relying on an external launcher.

Compression roadmap note:
SCND v1 stores header[5] as a flag dword.  Newer code interprets it as
codec_id in the low 8 bits plus codec params in the high 24 bits.  RAW=0 and
ZERO_RLE=1 remain byte-for-byte compatible with existing v1 save codes.

Stage 2 adds a profile/trial skeleton and a best-buffer reservation.  It still
only emits RAW or ZERO_RLE, but later codecs can plug into the same trial path
without changing the save-code header.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from math import ceil, log2
import importlib
import importlib.machinery
import os
import sys
from typing import Any

import eudplib as ep

# Some EUD Editor/euddraft plugin loaders import plugin files under an internal
# module name even when the plugin section is written as [scnd].  Expose this
# module under the stable name too so optional bridges can find SCND reliably.
if __name__ != "scnd":
    sys.modules["scnd"] = sys.modules[__name__]


def _is_msqc_plugin_module(module):
    name = getattr(module, "__name__", "")
    return name.rsplit(".", 1)[-1].casefold() == "msqc"


def _install_msqc_settings_loader_hook():
    """Inject SCND MSQC settings before the original MSQC.py parses them."""
    loader_cls = importlib.machinery.SourceFileLoader
    current_exec = getattr(loader_cls, "exec_module", None)
    if getattr(current_exec, "_scnd_msqc_settings_hook", False):
        return

    original_exec = current_exec

    def exec_module_with_scnd_msqc_settings(loader, module):
        if _is_msqc_plugin_module(module) and not getattr(
            module, "_scnd_msqc_settings_installed", False
        ):
            module_settings = getattr(module, "settings", None)
            if isinstance(module_settings, dict):
                added = install_msqc_settings(module_settings)
                module._scnd_msqc_settings_installed = True
                if added:
                    print("[SCND] MSQC settings injected before MSQC load: %d" % added)
        return original_exec(loader, module)

    exec_module_with_scnd_msqc_settings._scnd_msqc_settings_hook = True
    exec_module_with_scnd_msqc_settings._scnd_msqc_original_exec = original_exec
    loader_cls.exec_module = exec_module_with_scnd_msqc_settings


_install_msqc_settings_loader_hook()


PLAYERS = 8

SCND_VERSION = 1
SCND_MAGIC = 0x53434E44  # "SCND"
SCND_NEW_USER_MAGIC = 0x53434E55  # "SCNU"
SCND_NEW_USER_MARKER = 0x4E455731  # "NEW1"
SCND_NEW_USER_VERSION = 1
SCND_NEW_USER_DWORDS = 8
SCND_NEW_USER_DOMAIN = "SCND_NEW_USER_V1"
SCND_NEW_USER_PACKET_NONE = 0
SCND_NEW_USER_PACKET_OK = 1
SCND_NEW_USER_PACKET_BAD = 2
SCND_MAX_DWORDS = 2048
SCND_MAX_CODE_CHARS = 12000
SCND_PRINT_CHARS = 72
SCND_HANGUL_PRINT_CHARS = 52
# Stable Hangul save-data mode.  SCNDgram/SCAcorn can recognize the full
# completed Hangul syllable block, so the visible page codec uses all 11172
# syllables.  Runtime conversion is grouped as 5 packet bytes -> 3 Hangul
# syllables, avoiding a full arbitrary-precision base conversion while still
# beating the old 13-bit/base8192 stream on large saves.
SCND_HANGUL_BASE = 11172
SCND_HANGUL_GROUP_BYTES = 5
SCND_HANGUL_GROUP_CHARS = 3
SCND_HANGUL_BASE_CP = 0xAC00
SCND_MAX_HANGUL_CHARS = ((SCND_MAX_DWORDS * 4 + SCND_HANGUL_GROUP_BYTES - 1) // SCND_HANGUL_GROUP_BYTES) * SCND_HANGUL_GROUP_CHARS + 1
SCND_UNICODE_BASE = 32768
SCND_UNICODE_BASE_BITS = 15
SCND_UNICODE_HANGUL_COUNT = 11172
SCND_UNICODE_CJK_COUNT = 20992
SCND_UNICODE_EXTA_COUNT = SCND_UNICODE_BASE - SCND_UNICODE_HANGUL_COUNT - SCND_UNICODE_CJK_COUNT
SCND_UNICODE_HANGUL_CP = 0xAC00
SCND_UNICODE_CJK_CP = 0x4E00
SCND_UNICODE_EXTA_CP = 0x3400
SCND_SAVE_DATA_HEADER = ""
SCND_SAVE_DATA_COLOR = "\x04"
SCND_SAVE_DATA_CHARS_PER_LINE = 52
SCND_SAVE_DATA_VISIBLE_LINES = 11
SCND_SAVE_DATA_HEADER_LINES = 1 if SCND_SAVE_DATA_HEADER else 0
SCND_SAVE_DATA_CODE_LINES = SCND_SAVE_DATA_VISIBLE_LINES - SCND_SAVE_DATA_HEADER_LINES
SCND_SAVE_DATA_VIEW_TICKS = 4320
SCND_SAVE_DATA_BUILD_LINES_PER_TICK = 4
SCND_SAVE_DATA_MAX_VISIBLE_CHARS = SCND_SAVE_DATA_CHARS_PER_LINE * SCND_SAVE_DATA_CODE_LINES
SCND_SCREEN_BRIGHTNESS_ADDR = 0x657A9C
SCND_CODE_VIEW_DARK_BRIGHTNESS = 1
SCND_CODE_VIEW_NORMAL_BRIGHTNESS = 31
SCND_STATUS_PRINT_INTERVAL = 24
SCND_IDLE_GATE_ENABLED = True
SCND_DEBUG_PROFILING = False
SCND_MANIFEST_MPQ_PATH = "scnd_manifest.json"
SCND_DEFAULT_CHAT_RESULT = 0x58D900
SCND_DEFAULT_CHAT_PTR = 0x58F500
SCND_DEFAULT_CHAT_LEN = 0x58F504
SCND_DEFAULT_CHAT_PATTERN = 0x58F508
SCND_CHAT_CODE_APPEND = 7001
SCND_CHAT_CLEAR = 7002
SCND_CHAT_LOAD_DONE = 7003
SCND_CHAT_SAVE = 7004
SCND_CHAT_LOAD = 7005
SCND_PLAYER_NAME_ADDR = 0x57EEEB
SCND_PLAYER_NAME_STRIDE = 36
SCND_PLAYER_NAME_MAX_BYTES = 24
# More work per tick makes runtime finish faster.  The tick loop uses
# EUDLoopRange so this no longer multiplies the generated trigger body.
SCND_WORK_PER_TICK = 8
SCND_CRYPTO_ROUNDS = 8
SCND_HEADER_DWORDS = 6
SCND_TAG_DWORDS = 2
SCND_DATA_OFFSET = SCND_HEADER_DWORDS
SCND_COMPAT_EXACT = 0
SCND_COMPAT_BITS = 1
SCND_COMPAT_KEY_BITS = 2
SCND_COMPAT_MAX = SCND_COMPAT_KEY_BITS
SCND_CODEC_RAW = 0
SCND_CODEC_ZERO_RLE = 1
SCND_CODEC_VARINT = 2
SCND_CODEC_DELTA_VARINT = 3
SCND_CODEC_BITPLANE_RLE = 4
SCND_CODEC_DICT = 5
SCND_CODEC_FILL = 6
SCND_MAX_SUPPORTED_CODEC_ID = SCND_CODEC_FILL
SCND_FLAG_CODEC_MASK = 0xFF
SCND_FLAG_PARAM_MASK = 0xFFFFFF
SCND_FLAG_RAW = SCND_CODEC_RAW
SCND_FLAG_ZERO_RLE = SCND_CODEC_ZERO_RLE
SCND_FLAG_VARINT = SCND_CODEC_VARINT
SCND_TRIAL_RAW = 1 << SCND_CODEC_RAW
SCND_TRIAL_ZERO_RLE = 1 << SCND_CODEC_ZERO_RLE
SCND_TRIAL_VARINT = 1 << SCND_CODEC_VARINT
SCND_TRIAL_FILL = 1 << SCND_CODEC_FILL
# VARINT byte-length thresholds. Values strictly less than these need 1, 2, 3,
# or 4 bytes respectively; everything 2^28 or larger needs 5 bytes.
SCND_VARINT_THRESHOLD_1 = 0x80         # 2^7
SCND_VARINT_THRESHOLD_2 = 0x4000       # 2^14
SCND_VARINT_THRESHOLD_3 = 0x200000     # 2^21
SCND_VARINT_THRESHOLD_4 = 0x10000000   # 2^28
SCND_VARINT_PARAMS_MAX = 0xFFFFFF      # codec params24 must fit byte-length

MESSAGE_NONE = 0
MESSAGE_SAVE_DONE = 1
MESSAGE_LOAD_DONE = 2
MESSAGE_SAVE_FAILED = 3
MESSAGE_LOAD_FAILED = 4
MESSAGE_INPUT_APPENDED = 5
MESSAGE_INPUT_CLEARED = 6

RESULT_NONE = 0
RESULT_OK = 1
RESULT_BUSY = 2
RESULT_BAD_SCHEMA = 3
RESULT_BAD_CODE = 4
RESULT_BAD_TAG = 5
RESULT_TOO_LARGE = 6
RESULT_BAD_BINDING = 7
RESULT_GLOBAL_REQUIRED = 8
RESULT_GLOBAL_MISMATCH = 9
RESULT_UNEXPECTED_GLOBAL = 10

_PHASE_IDLE = 0
_PHASE_CLEAR_WORK = 1
_PHASE_PACK = 2
_PHASE_PROFILE = 3
_PHASE_TRIAL_LOOP = 4
_PHASE_BEST_COMMIT = 5
_PHASE_COMPRESS = _PHASE_TRIAL_LOOP
_PHASE_COPY_COMPRESSED = 6
_PHASE_WRITE_HEADER = 7
_PHASE_CRYPT = 8
_PHASE_ENCODE_CLEAR = 9
_PHASE_ENCODE = 10
_PHASE_SAVE_DONE = 11
_PHASE_INPUT_SYNC_COPY = 13
_PHASE_INPUT_SYNC_LOAD = 14
_PHASE_HANGUL_DECODE_CLEAR = 18
_PHASE_HANGUL_DECODE = 19
_PHASE_DECODE_CLEAR = 20
_PHASE_DECODE = 21
_PHASE_VALIDATE = 22
_PHASE_VALIDATE_MAC = 23
_PHASE_LOAD_CRYPT = 24
_PHASE_FILL_EXPAND = 25
_PHASE_DECOMPRESS = 26
_PHASE_COPY_DECOMPRESSED = 27
_PHASE_SYNC_START = 28
_PHASE_SYNC_WAIT = 29
_PHASE_SYNC_COPY = 35
_PHASE_UNPACK = 30
_PHASE_LOAD_DONE = 31
_PHASE_COMPAT_DEFAULTS = 32
_PHASE_COMPAT_CONVERT = 33
_PHASE_COMPAT_COPY = 34
_PHASE_FAILED = 90

_MSQC_CHANNEL_COUNT = 20
_MSQC_MAP_WIDTH = 1900
_MSQC_MIN_WIDTH = 25
_MSQC_MAX_PARITY = 52
_MSQC_INIT_VALUE = 13
_MSQC_SCND_INPUT_START = 40
_MSQC_SCND_INPUT_FIRST = 41
_MSQC_SCND_INPUT_SECOND = 42
_MSQC_SCND_INPUT_SEQ = 43
_MSQC_SCND_INPUT_COMMIT = 44
_MSQC_SCND_INPUT_CANCEL = 45
_MSQC_SCND_INPUT_CODE_CLOSE = 46
_MSQC_SCND_INPUT_MAGIC = 0x5343
_MSQC_SCND_INPUT_CHANNEL_COUNT = 3
_MSQC_SCND_INPUT_STALE_LIMIT = 3
SCND_INPUT_SYNC_MAGIC = 0x53494E50  # "SINP"
SCND_INPUT_SYNC_HEADER_DWORDS = 6
SCND_INPUT_SYNC_MAX_CHARS = (SCND_MAX_DWORDS - SCND_INPUT_SYNC_HEADER_DWORDS) * 2

_SCND_INPUT_MSQC_TRANSFER = ep.EUDVariable()
_SCND_INPUT_MSQC_SENDS = [
    ep.EUDVariable() for _ in range(_MSQC_SCND_INPUT_CHANNEL_COUNT)
]
_SCND_INPUT_MSQC_RECEIVES = [
    ep.PVariable() for _ in range(_MSQC_SCND_INPUT_CHANNEL_COUNT)
]
SCNDInputMSQCIsTransfer = _SCND_INPUT_MSQC_TRANSFER
for _idx, _send in enumerate(_SCND_INPUT_MSQC_SENDS, 1):
    globals()["SCNDInputMSQCSend%d" % _idx] = _send
for _idx, _receive in enumerate(_SCND_INPUT_MSQC_RECEIVES, 1):
    globals()["SCNDInputMSQCReceive%d" % _idx] = _receive
_SCND_INPUT_MSQC_REGISTERED = False


@dataclass(frozen=True)
class _FlatEntry:
    key: str
    values: Any
    kind: str
    bits: int
    bit_offset: int
    min_value: int
    default: int
    item_index: int
    count: int
    slots: int


@dataclass(frozen=True)
class _PackGroup:
    key: str
    values: Any
    kind: str
    bits: int
    bit_offset: int
    min_value: int
    start_index: int
    group_count: int
    item_index_start: int
    array_count: int
    slots: int


@dataclass(frozen=True)
class _ObfuscatedKeyRecord:
    key_index: int
    c0: int
    c1: int
    c2: int
    c3: int
    r0: int
    r1: int
    r2: int
    r3: int
    a0: int
    a1: int
    a2: int
    a3: int
    w0: int
    w1: int


@dataclass(frozen=True)
class _ObfuscatedContext:
    records: tuple[_ObfuscatedKeyRecord, ...]


_FLAT: list[_FlatEntry] = []
_PACK_GROUPS: list[_PackGroup] = []
_BINDING_KEYS: set[str] = set()
_TOTAL_BITS = 0
_PLAYER_TOTAL_BITS = 0
_GLOBAL_TOTAL_BITS = 0
_PACKED_DWORDS = 0
_PLAYER_PACKED_DWORDS = 0
_GLOBAL_PACKED_DWORDS = 0
_PLAYER_FIELD_COUNT = 0
_PLAYER_COMPAT_BITS_MAX_DWORDS = 0
_PLAYER_COMPAT_KEY_BITS_MAX_DWORDS = 0
_PLAYER_KEY_HASH_TABLE = None
_PLAYER_DEFAULT_DWORDS_TABLE = None
_PLAYER_EXACT_PREFIX_TABLES = None
_SYNC_PLAIN_DWORDS = 0
_SCHEMA_HASH = 1
_PLAYER_SCHEMA_HASH = 1
_GLOBAL_SCHEMA_HASH = 1
_CONTEXT_CACHE: dict[tuple[str, int, str, int], tuple[int, int, int, int]] = {}
_CONTEXT_OBF_CACHE: dict[tuple[str, int, str, int, int], _ObfuscatedContext] = {}
_SYNC_ENABLED = False
_SYNC_LOADER = None
_SYNC_REGISTERED = False
_SYNC_RUNTIME_INITIALIZED = False
_HAS_GLOBAL_BINDINGS = False
_GLOBAL_VALUES_LOCK = ep.EUDArray([0])
_GLOBAL_PENDING_DATA = None
_GLOBAL_PENDING_DWORDS = 0
_GLOBAL_PENDING_OWNER = ep.EUDArray([0])
_SCOPE_PLAYER = 0
_SCOPE_GLOBAL = 1

_APP_CONFIGURED = False
_APP_MAP_ID = ""
_APP_SAVE_ID = 0
_APP_MAKER_KEY = ""
_APP_MAP_DISPLAY_NAME = ""
_APP_MAP_IMAGE = ""
_APP_COMPAT_MODE = SCND_COMPAT_EXACT
_APP_CHAT_RESULT = SCND_DEFAULT_CHAT_RESULT
_APP_CHAT_PTR = SCND_DEFAULT_CHAT_PTR
_APP_CHAT_LEN = SCND_DEFAULT_CHAT_LEN
_APP_CHAT_PATTERN = SCND_DEFAULT_CHAT_PATTERN
_APP_CHAT_CODE_APPEND = SCND_CHAT_CODE_APPEND
_APP_CHAT_CLEAR = SCND_CHAT_CLEAR
_APP_CHAT_LOAD_DONE = SCND_CHAT_LOAD_DONE
_APP_CHAT_SAVE = SCND_CHAT_SAVE
_APP_CHAT_LOAD = SCND_CHAT_LOAD
_APP_CODE_VIEW_TICKS = SCND_SAVE_DATA_VIEW_TICKS
_APP_CODE_VIEW_CHARS = SCND_SAVE_DATA_CHARS_PER_LINE
_APP_CODE_VIEW_BUILD_LINES = SCND_SAVE_DATA_BUILD_LINES_PER_TICK
_APP_CODE_VIEW_LINES = SCND_SAVE_DATA_VISIBLE_LINES
_APP_CODE_VIEW_LINE_BYTES = SCND_SAVE_DATA_CHARS_PER_LINE * 3 + 8
_APP_ENABLE_HANGUL_KEYS = True
_APP_DISPLAY_LINE = 0
_APP_CODE_VIEW = None
_APP_CODE_VIEW_DESCRIPTOR = None
_APP_DARKEN_CODE_VIEW = True
_APP_NEW_USER_CODE_WORDS: tuple[int, ...] | None = None
_APP_CODE_VIEW_DARK_BRIGHTNESS = SCND_CODE_VIEW_DARK_BRIGHTNESS
_APP_CODE_VIEW_NORMAL_BRIGHTNESS = SCND_CODE_VIEW_NORMAL_BRIGHTNESS

_WORK = ep.Db(PLAYERS * SCND_MAX_DWORDS * 4)
_TEMP = ep.Db(PLAYERS * SCND_MAX_DWORDS * 4)
_CODE = ep.Db(PLAYERS * SCND_MAX_CODE_CHARS)
_INPUT = ep.Db(PLAYERS * SCND_MAX_CODE_CHARS)
_HANGUL_INPUT = ep.Db(PLAYERS * SCND_MAX_HANGUL_CHARS * 4)
_HANGUL_CHAR = ep.Db(b"\x01\x01\x01\x00")

_B64_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_B64_ENCODE = ep.Db(_B64_ALPHABET)
_b64_decode_bytes = bytearray([0xFF] * 256)
for _b64_index, _b64_char_code in enumerate(_B64_ALPHABET):
    _b64_decode_bytes[_b64_char_code] = _b64_index
_B64_DECODE = ep.Db(bytes(_b64_decode_bytes))

_STATE = ep.EUDArray([0] * PLAYERS)
_MODE = ep.EUDArray([0] * PLAYERS)
_RESULT = ep.EUDArray([0] * PLAYERS)
_MESSAGE = ep.EUDArray([0] * PLAYERS)
_INDEX = ep.EUDArray([0] * PLAYERS)
_SUBINDEX = ep.EUDArray([0] * PLAYERS)
_CODE_LEN = ep.EUDArray([0] * PLAYERS)
_INPUT_LEN = ep.EUDArray([0] * PLAYERS)
_HANGUL_INPUT_LEN = ep.EUDArray([0] * PLAYERS)
_HANGUL_INPUT_TARGET_BYTES = ep.EUDArray([0] * PLAYERS)
_HANGUL_INPUT_TARGET_CHARS = ep.EUDArray([0] * PLAYERS)
_BYTE_LEN = ep.EUDArray([0] * PLAYERS)
_PLAIN_DWORDS = ep.EUDArray([0] * PLAYERS)
_SAVED_FIELD_COUNT = ep.EUDArray([0] * PLAYERS)
_SAVED_VALUE_CURSOR = ep.EUDArray([0] * PLAYERS)
_PAYLOAD_DWORDS = ep.EUDArray([0] * PLAYERS)
_ACTIVE_SCOPE = ep.EUDArray([_SCOPE_PLAYER] * PLAYERS)
_ACTIVE_TOTAL_BITS = ep.EUDArray([0] * PLAYERS)
_ACTIVE_PACKED_DWORDS = ep.EUDArray([0] * PLAYERS)
_ACTIVE_SCHEMA_HASH = ep.EUDArray([1] * PLAYERS)
_FLAGS = ep.EUDArray([0] * PLAYERS)
_PRINT_INDEX = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_HEADER_DONE = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_DATA_CHARS = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_BYTE_INDEX = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_TOTAL_BYTES = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_TOTAL_CHARS = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_ACC = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_ACC_BITS = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_DIGIT0 = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_DIGIT1 = ep.EUDArray([0] * PLAYERS)
_HANGUL_PRINT_DIGIT2 = ep.EUDArray([0] * PLAYERS)
_NONCE = ep.EUDArray([0] * PLAYERS)
_KEY0 = ep.EUDArray([0] * PLAYERS)
_KEY1 = ep.EUDArray([0] * PLAYERS)
_KEY2 = ep.EUDArray([0] * PLAYERS)
_KEY3 = ep.EUDArray([0] * PLAYERS)
_MAC0 = ep.EUDArray([0] * PLAYERS)
_MAC1 = ep.EUDArray([0] * PLAYERS)
_ACC = ep.EUDArray([0] * PLAYERS)
_ACC_BITS = ep.EUDArray([0] * PLAYERS)
_DECODE_OUT = ep.EUDArray([0] * PLAYERS)
_LAST_CODEC = ep.EUDArray([SCND_CODEC_RAW] * PLAYERS)
_LAST_RAW_DWORDS = ep.EUDArray([0] * PLAYERS)
_LAST_PACKED_DWORDS = ep.EUDArray([0] * PLAYERS)
_LAST_RATIO_PCT = ep.EUDArray([0] * PLAYERS)
_BEST_LEN = ep.EUDArray([0] * PLAYERS)
_BEST_CODEC = ep.EUDArray([SCND_CODEC_RAW] * PLAYERS)
_BEST_PARAMS = ep.EUDArray([0] * PLAYERS)
_PROFILE_ZERO_RUNS = ep.EUDArray([0] * PLAYERS)
_PROFILE_NONZERO_DWORDS = ep.EUDArray([0] * PLAYERS)
_PROFILE_PREV_IS_ZERO = ep.EUDArray([0] * PLAYERS)
_PROFILE_FIRST_DWORD = ep.EUDArray([0] * PLAYERS)
_PROFILE_ALL_SAME = ep.EUDArray([1] * PLAYERS)
# Running total of varint-encoded byte counts per dword. Updated in
# _profile_one_dword so finish_profile can decide VARINT without a trial pass.
_PROFILE_VARINT_BYTES = ep.EUDArray([0] * PLAYERS)
_APP_CODE_BUILD_ACTIVE = ep.EUDArray([0] * PLAYERS)
_APP_CODE_VIEW_LINE_INDEX = ep.EUDArray([0] * PLAYERS)
_APP_CODE_VIEW_LINE_LEN = ep.EUDArray([0] * PLAYERS)
_APP_CODE_VIEW_TIMER = ep.EUDArray([0] * PLAYERS)
_APP_CODE_VIEW_LOCAL_CLOSED = ep.EUDArray([0] * PLAYERS)
_APP_HANGUL_INPUT_ACTIVE = ep.EUDArray([0] * PLAYERS)
_APP_LOAD_ENABLED = ep.EUDArray([0] * PLAYERS)
_APP_STATUS_TIMER = ep.EUDArray([0] * PLAYERS)
_APP_PACKET_SEQ_READY = ep.EUDArray([0] * PLAYERS)
_APP_PACKET_LAST_SEQ = ep.EUDArray([0] * PLAYERS)
_APP_PACKET_EXPECTED_SEQ = ep.EUDArray([0] * PLAYERS)
_APP_PACKET_ARM_DELAY = ep.EUDArray([0] * PLAYERS)
_APP_HANGUL_LOAD_TAIL = ep.EUDArray([0] * PLAYERS)
_APP_LOAD_INTENT = ep.EUDArray([0] * PLAYERS)
_APP_IS_NEW_USER = ep.EUDArray([0] * PLAYERS)
_APP_PACKET_PROBE_COUNT = ep.EUDArray([0] * PLAYERS)
_APP_MSQC_STALE_TICKS = ep.EUDArray([0] * PLAYERS)
_APP_LOAD_QUEUED = ep.EUDArray([0] * PLAYERS)
_APP_LOAD_OWNER = ep.EUDArray([0])
_APP_INPUT_PROBE_ONLY = False
if SCND_DEBUG_PROFILING:
    _SCND_DEBUG_IDLE_HITS = ep.EUDArray([0] * PLAYERS)
    _SCND_DEBUG_WAKE_HITS = ep.EUDArray([0] * PLAYERS)
    _SCND_DEBUG_FULL_HITS = ep.EUDArray([0] * PLAYERS)
else:
    _SCND_DEBUG_IDLE_HITS = None
    _SCND_DEBUG_WAKE_HITS = None
    _SCND_DEBUG_FULL_HITS = None
_APP_KEY_COUNT = 41
_APP_KEY_PREV = ep.EUDArray([0] * (PLAYERS * _APP_KEY_COUNT))
_APP_KEY_CUR = ep.EUDArray([0] * (PLAYERS * _APP_KEY_COUNT))
_APP_KEY_READER = ep.EUDByteReader()
_PLAYER_NAME_READER = ep.EUDByteReader()

def _set_active_schema_mismatch_result(player):
    if _HAS_GLOBAL_BINDINGS:
        if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_SCOPE_GLOBAL)):
            _RESULT[player] = RESULT_GLOBAL_MISMATCH
        if ep.EUDElse()():
            _RESULT[player] = RESULT_BAD_SCHEMA
        ep.EUDEndIf()
    else:
        _RESULT[player] = RESULT_BAD_SCHEMA


def _set_active_global_mismatch_result(player, fallback_result):
    if _HAS_GLOBAL_BINDINGS:
        if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_SCOPE_GLOBAL)):
            _RESULT[player] = RESULT_GLOBAL_MISMATCH
        if ep.EUDElse()():
            _RESULT[player] = fallback_result
        ep.EUDEndIf()
    else:
        _RESULT[player] = fallback_result


def _fail_load_now(player, result):
    _STATE[player] = _PHASE_FAILED
    _MODE[player] = 2
    _RESULT[player] = result
    _MESSAGE[player] = MESSAGE_LOAD_FAILED
    _APP_IS_NEW_USER[player] = 0


_APP_KEY_STATE_ADDR = 0x596A18
_APP_PACKET_FIRST_VKS = (
    0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47,
    0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E,
)
_APP_PACKET_SECOND_VKS = (
    0x4F, 0x50, 0x51, 0x52, 0x53, 0x54,
    0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x30, 0x31,
)
_APP_PACKET_SEQ_VKS = (0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39)
_APP_KEY_GATE_SLOT = 36
_APP_KEY_STROBE_SLOT = 37
_APP_KEY_COMMIT_SLOT = 38
_APP_KEY_CANCEL_SLOT = 39
_APP_KEY_ESCAPE_SLOT = 40
_APP_VK_SCND_GATE = 0x7C
_APP_VK_SCND_STROBE = 0x20
_APP_VK_COMMIT = 0x0D
_APP_VK_CANCEL = 0x08
_APP_VK_ESCAPE = 0x1B


def _stable_hash(text: str) -> int:
    value = 2166136261
    for byte in str(text).encode("utf-8"):
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value or 1


def _u32(value: int) -> int:
    return int(value) & 0xFFFFFFFF


def _rotl32_const(value: int, bits: int) -> int:
    bits = int(bits) & 31
    value = _u32(value)
    if bits == 0:
        return value
    return _u32((value << bits) | (value >> (32 - bits)))


def _derive_obfuscation_words(domain: str, count: int) -> list[int]:
    words: list[int] = []
    counter = 0
    seed = str(domain).encode("utf-8")
    while len(words) < int(count):
        digest = hashlib.sha256(seed + b"|" + str(counter).encode("ascii")).digest()
        for offset in range(0, len(digest), 4):
            words.append(int.from_bytes(digest[offset:offset + 4], "little") & 0xFFFFFFFF)
            if len(words) >= int(count):
                break
        counter += 1
    return words


@ep.EUDFunc
def _player_name_hash(player):
    value = ep.EUDVariable()
    active = ep.EUDVariable()
    ch = ep.EUDVariable()
    value << 2166136261
    active << 1
    _PLAYER_NAME_READER.seekoffset(SCND_PLAYER_NAME_ADDR + ep.f_mul(player, SCND_PLAYER_NAME_STRIDE))
    for _ in range(SCND_PLAYER_NAME_MAX_BYTES):
        ch << _PLAYER_NAME_READER.readbyte()
        if ep.EUDIf()(active.Exactly(1)):
            if ep.EUDIf()(ch.Exactly(0)):
                active << 0
            if ep.EUDElse()():
                value << ep.f_mul(ep.f_bitxor(value, ch), 16777619)
            ep.EUDEndIf()
        ep.EUDEndIf()
    if ep.EUDIf()(value.Exactly(0)):
        value << 1
    ep.EUDEndIf()
    return value


# region: codec VARINT  ------------------------------------------------------
#
# Format (per dword v, unsigned 32-bit):
#   while v >= 0x80:
#       emit (v & 0x7F) | 0x80     # continuation bit set
#       v >>= 7
#   emit v & 0x7F                  # terminator (continuation bit clear)
#
# Worst case: v = 0xFFFFFFFF -> 5 bytes ([0xFF, 0xFF, 0xFF, 0xFF, 0x0F]).
# Decoder reads bytes until the high bit is clear; shifts each by 7*i.
#
# Output bytes are packed little-endian into payload dwords. Trailing slot in
# the last dword is zero-padded (decoder never reads past byte_count). The
# byte count fits in params24 because SCND_MAX_DWORDS * 5 < 2^24.


# endregion: codec VARINT  ---------------------------------------------------


def _scope_for_kind(kind: str) -> int:
    return _SCOPE_GLOBAL if str(kind).startswith("global_") else _SCOPE_PLAYER


def _player_entries() -> list[_FlatEntry]:
    return [entry for entry in _FLAT if _scope_for_kind(entry.kind) == _SCOPE_PLAYER]


def _entry_key_hash(entry: _FlatEntry) -> int:
    return _stable_hash("field|" + entry.key)


def _player_pack_groups() -> list[_PackGroup]:
    return [group for group in _PACK_GROUPS if _scope_for_kind(group.kind) == _SCOPE_PLAYER]


def _player_ordinal_at_flat_index(flat_index: int) -> int:
    return sum(1 for entry in _FLAT[:int(flat_index)] if _scope_for_kind(entry.kind) == _SCOPE_PLAYER)


def _player_key_hash_table():
    global _PLAYER_KEY_HASH_TABLE
    if _PLAYER_KEY_HASH_TABLE is None:
        _PLAYER_KEY_HASH_TABLE = ep.EUDArray([_entry_key_hash(entry) for entry in _player_entries()])
    return _PLAYER_KEY_HASH_TABLE


def _compat_bits_metadata_bytes(field_count: int) -> int:
    return 4 + int(field_count)


def _compat_key_bits_metadata_bytes(field_count: int) -> int:
    return 4 + int(field_count) * 4 + int(field_count)


def _compat_max_payload_dwords(mode: int) -> int:
    field_count = len(_player_entries())
    if mode == SCND_COMPAT_BITS:
        byte_count = _compat_bits_metadata_bytes(field_count) + field_count * 5
        return (byte_count + 3) // 4
    if mode == SCND_COMPAT_KEY_BITS:
        byte_count = _compat_key_bits_metadata_bytes(field_count) + field_count * 5
        return (byte_count + 3) // 4
    return _PLAYER_PACKED_DWORDS


def _player_save_plain_dwords() -> int:
    if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
        return _PLAYER_COMPAT_BITS_MAX_DWORDS
    if _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
        return _PLAYER_COMPAT_KEY_BITS_MAX_DWORDS
    return _PLAYER_PACKED_DWORDS


def _build_player_default_dwords() -> list[int]:
    dwords = [0] * _PLAYER_PACKED_DWORDS
    for entry in _player_entries():
        packed = int(entry.default) - int(entry.min_value)
        if entry.bits < 32:
            packed &= (1 << entry.bits) - 1
        word_index = entry.bit_offset // 32
        bit_pos = entry.bit_offset % 32
        if entry.bits == 32:
            dwords[word_index] = packed & 0xFFFFFFFF
        elif bit_pos + entry.bits <= 32:
            dwords[word_index] |= (packed << bit_pos) & 0xFFFFFFFF
        else:
            low_bits = 32 - bit_pos
            high_bits = entry.bits - low_bits
            low_mask = (1 << low_bits) - 1
            dwords[word_index] |= ((packed & low_mask) << bit_pos) & 0xFFFFFFFF
            dwords[word_index + 1] |= (packed >> low_bits) & ((1 << high_bits) - 1)
    return [value & 0xFFFFFFFF for value in dwords]


def _player_default_dwords_table():
    global _PLAYER_DEFAULT_DWORDS_TABLE
    if _PLAYER_DEFAULT_DWORDS_TABLE is None:
        _PLAYER_DEFAULT_DWORDS_TABLE = ep.EUDArray(_build_player_default_dwords())
    return _PLAYER_DEFAULT_DWORDS_TABLE


def _schema_hash_for_entries(entries: list[_FlatEntry], total_bits: int) -> int:
    value = 0x811C9DC5
    for entry in entries:
        text = "%s|%s|%d|%d|%d|%d|%d" % (
            entry.key,
            entry.kind,
            entry.bits,
            entry.min_value,
            entry.default,
            entry.item_index,
            entry.count,
        )
        value ^= _stable_hash(text)
        value = (value * 16777619) & 0xFFFFFFFF
    value ^= int(total_bits)
    value = (value * 16777619) & 0xFFFFFFFF
    return value or 1


def _player_exact_prefix_tables():
    global _PLAYER_EXACT_PREFIX_TABLES
    if _PLAYER_EXACT_PREFIX_TABLES is not None:
        return _PLAYER_EXACT_PREFIX_TABLES

    records: list[tuple[int, int, int, int, int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()
    entries = _player_entries()
    current = (int(_PLAYER_SCHEMA_HASH), int(_PLAYER_TOTAL_BITS), int(_PLAYER_PACKED_DWORDS))
    for end in range(1, len(entries) + 1):
        prefix = entries[:end]
        total_bits = int(prefix[-1].bit_offset) + int(prefix[-1].bits)
        packed_dwords = (total_bits + 31) // 32
        schema_hash = _schema_hash_for_entries(prefix, total_bits)
        header = (schema_hash, total_bits, packed_dwords)
        if header == current or header in seen:
            continue
        key0, key1, key2, key3 = _context_keys(_APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY, schema_hash)
        records.append((schema_hash, total_bits, packed_dwords, key0, key1, key2, key3))
        seen.add(header)

    if records:
        _PLAYER_EXACT_PREFIX_TABLES = (
            len(records),
            ep.EUDArray([record[0] for record in records]),
            ep.EUDArray([record[1] for record in records]),
            ep.EUDArray([record[2] for record in records]),
            ep.EUDArray([record[3] for record in records]),
            ep.EUDArray([record[4] for record in records]),
            ep.EUDArray([record[5] for record in records]),
            ep.EUDArray([record[6] for record in records]),
        )
    else:
        empty = ep.EUDArray([0])
        _PLAYER_EXACT_PREFIX_TABLES = (0, empty, empty, empty, empty, empty, empty, empty)
    return _PLAYER_EXACT_PREFIX_TABLES


def _schema_hash(scope: int | None = None) -> int:
    value = 0x811C9DC5
    for entry in _FLAT:
        if scope is not None and _scope_for_kind(entry.kind) != scope:
            continue
        text = "%s|%s|%d|%d|%d|%d|%d" % (
            entry.key,
            entry.kind,
            entry.bits,
            entry.min_value,
            entry.default,
            entry.item_index,
            entry.count,
        )
        value ^= _stable_hash(text)
        value = (value * 16777619) & 0xFFFFFFFF
    if scope == _SCOPE_PLAYER:
        value ^= _PLAYER_TOTAL_BITS
    elif scope == _SCOPE_GLOBAL:
        value ^= _GLOBAL_TOTAL_BITS
    else:
        value ^= _TOTAL_BITS
    value = (value * 16777619) & 0xFFFFFFFF
    return value or 1


def _estimate_hangul8192_chars_for_payload_dwords(payload_dwords) -> int:
    payload_dwords = max(0, int(payload_dwords))
    total_bytes = (SCND_HEADER_DWORDS + payload_dwords + SCND_TAG_DWORDS) * 4
    data_chars = ((total_bytes + SCND_HANGUL_GROUP_BYTES - 1) // SCND_HANGUL_GROUP_BYTES) * SCND_HANGUL_GROUP_CHARS
    return 1 + data_chars


def _check_save_data_capacity() -> None:
    visible_chars = _estimate_hangul8192_chars_for_payload_dwords(_player_save_plain_dwords())
    if visible_chars > SCND_SAVE_DATA_MAX_VISIBLE_CHARS:
        raise RuntimeError(
            "SCND save-data is too large for one screen: %d Hangul chars, capacity %d "
            "(%d chars x %d code rows). Reduce bind sizes, split data, or lower compat_mode."
            % (
                visible_chars,
                SCND_SAVE_DATA_MAX_VISIBLE_CHARS,
                SCND_SAVE_DATA_CHARS_PER_LINE,
                SCND_SAVE_DATA_CODE_LINES,
            )
        )


def _rebuild_schema() -> None:
    global _PACKED_DWORDS, _PLAYER_PACKED_DWORDS, _GLOBAL_PACKED_DWORDS
    global _PLAYER_FIELD_COUNT, _PLAYER_COMPAT_BITS_MAX_DWORDS, _PLAYER_COMPAT_KEY_BITS_MAX_DWORDS
    global _PLAYER_KEY_HASH_TABLE
    global _PLAYER_DEFAULT_DWORDS_TABLE
    global _PLAYER_EXACT_PREFIX_TABLES
    global _SYNC_PLAIN_DWORDS
    global _SCHEMA_HASH, _PLAYER_SCHEMA_HASH, _GLOBAL_SCHEMA_HASH
    global _APP_NEW_USER_CODE_WORDS
    _PLAYER_KEY_HASH_TABLE = None
    _PLAYER_DEFAULT_DWORDS_TABLE = None
    _PLAYER_EXACT_PREFIX_TABLES = None
    _APP_NEW_USER_CODE_WORDS = None
    _PLAYER_PACKED_DWORDS = (_PLAYER_TOTAL_BITS + 31) // 32
    _GLOBAL_PACKED_DWORDS = (_GLOBAL_TOTAL_BITS + 31) // 32
    _PLAYER_FIELD_COUNT = len(_player_entries())
    _PLAYER_COMPAT_BITS_MAX_DWORDS = _compat_max_payload_dwords(SCND_COMPAT_BITS)
    _PLAYER_COMPAT_KEY_BITS_MAX_DWORDS = _compat_max_payload_dwords(SCND_COMPAT_KEY_BITS)
    _SYNC_PLAIN_DWORDS = max(_PLAYER_PACKED_DWORDS, _GLOBAL_PACKED_DWORDS)
    _PACKED_DWORDS = max(_player_save_plain_dwords(), _GLOBAL_PACKED_DWORDS)
    if SCND_HEADER_DWORDS + _PACKED_DWORDS + SCND_TAG_DWORDS > SCND_MAX_DWORDS:
        raise RuntimeError(
            "SCND payload is too large: %d data dwords, capacity %d"
            % (_PACKED_DWORDS, SCND_MAX_DWORDS - SCND_HEADER_DWORDS - SCND_TAG_DWORDS)
        )
    _check_save_data_capacity()
    _SCHEMA_HASH = _schema_hash(None)
    _PLAYER_SCHEMA_HASH = _schema_hash(_SCOPE_PLAYER)
    _GLOBAL_SCHEMA_HASH = _schema_hash(_SCOPE_GLOBAL)


def clear_bindings() -> None:
    """Clear all save-code bindings.  Call before registering a new schema."""
    global _TOTAL_BITS, _PLAYER_TOTAL_BITS, _GLOBAL_TOTAL_BITS, _HAS_GLOBAL_BINDINGS
    del _FLAT[:]
    del _PACK_GROUPS[:]
    _BINDING_KEYS.clear()
    _TOTAL_BITS = 0
    _PLAYER_TOTAL_BITS = 0
    _GLOBAL_TOTAL_BITS = 0
    _HAS_GLOBAL_BINDINGS = False
    _rebuild_schema()


def _normalize_byte_count(byte_count=None):
    if byte_count is None:
        return None
    byte_count = int(byte_count)
    if byte_count < 1 or byte_count > 4:
        raise RuntimeError("SCND byte_count must be 1..4, got %d" % byte_count)
    return byte_count


def _normalize_bits(bits=None, min_value=0, max_value=None, byte_count=None) -> int:
    byte_count = _normalize_byte_count(byte_count)
    if byte_count is not None:
        byte_bits = byte_count * 8
        if bits is not None and int(bits) != byte_bits:
            raise RuntimeError(
                "SCND bits and byte_count disagree: bits=%d, byte_count=%d"
                % (int(bits), byte_count)
            )
        bits = byte_bits
    if bits is None:
        if max_value is None:
            raise RuntimeError("SCND bits or max_value must be provided.")
        span = int(max_value) - int(min_value) + 1
        if span <= 1:
            return 1
        bits = int(ceil(log2(span)))
    bits = int(bits)
    if bits < 1 or bits > 32:
        raise RuntimeError("SCND bits must be 1..32, got %d" % bits)
    return bits


def _is_width_sequence(value) -> bool:
    return isinstance(value, (list, tuple))


def _sequence_value(value, index: int):
    if _is_width_sequence(value):
        return value[index]
    return value


def _validate_sequence_length(name: str, value, count: int) -> None:
    if _is_width_sequence(value) and len(value) != count:
        raise RuntimeError("SCND %s length must match count %d, got %d" % (name, count, len(value)))


def _normalize_item_widths(count: int, bits=None, min_value=0, max_value=None, byte_count=None):
    _validate_sequence_length("bits", bits, count)
    _validate_sequence_length("min_value", min_value, count)
    _validate_sequence_length("max_value", max_value, count)
    _validate_sequence_length("byte_count", byte_count, count)

    widths = []
    for index in range(count):
        item_bits = _sequence_value(bits, index)
        item_min = _sequence_value(min_value, index)
        item_max = _sequence_value(max_value, index)
        item_bytes = _sequence_value(byte_count, index)
        widths.append(_normalize_bits(item_bits, item_min, item_max, item_bytes))
    return widths


def _expand_array_default(default, count: int, slots: int):
    _validate_sequence_length("default", default, count)
    if _is_width_sequence(default):
        values = []
        for _slot in range(slots):
            values.extend(int(value) for value in default)
        return values
    return [int(default)] * (slots * count)


def _add_flat_entry(
    key: str,
    values,
    kind: str,
    bits: int,
    min_value: int,
    default: int,
    item_index: int,
    count: int,
    slots: int,
) -> None:
    global _TOTAL_BITS, _PLAYER_TOTAL_BITS, _GLOBAL_TOTAL_BITS
    kind_scope = _scope_for_kind(kind)
    if kind_scope == _SCOPE_GLOBAL:
        if bits == 32 and (_GLOBAL_TOTAL_BITS % 32) != 0:
            _GLOBAL_TOTAL_BITS += 32 - (_GLOBAL_TOTAL_BITS % 32)
        bit_offset = _GLOBAL_TOTAL_BITS
        _GLOBAL_TOTAL_BITS += bits
    else:
        if bits == 32 and (_PLAYER_TOTAL_BITS % 32) != 0:
            _PLAYER_TOTAL_BITS += 32 - (_PLAYER_TOTAL_BITS % 32)
        bit_offset = _PLAYER_TOTAL_BITS
        _PLAYER_TOTAL_BITS += bits
    _TOTAL_BITS = _PLAYER_TOTAL_BITS + _GLOBAL_TOTAL_BITS
    _FLAT.append(
        _FlatEntry(
            key=key,
            values=values,
            kind=kind,
            bits=bits,
            bit_offset=bit_offset,
            min_value=int(min_value),
            default=int(default),
            item_index=int(item_index),
            count=int(count),
            slots=int(slots),
        )
    )
    return bit_offset


def _add_pack_group(
    key: str,
    values,
    kind: str,
    bits: int,
    bit_offset: int,
    min_value: int,
    start_index: int,
    group_count: int,
    item_index_start: int,
    array_count: int,
    slots: int,
) -> None:
    _PACK_GROUPS.append(
        _PackGroup(
            key=key,
            values=values,
            kind=kind,
            bits=int(bits),
            bit_offset=int(bit_offset),
            min_value=int(min_value),
            start_index=int(start_index),
            group_count=int(group_count),
            item_index_start=int(item_index_start),
            array_count=int(array_count),
            slots=int(slots),
        )
    )


def _check_key(key: str) -> str:
    key = str(key).strip()
    if not key:
        raise RuntimeError("SCND binding key must not be empty.")
    if key in _BINDING_KEYS:
        raise RuntimeError("SCND binding key is duplicated: %s" % key)
    _BINDING_KEYS.add(key)
    return key


def bind_player_uint(
    key: str,
    values,
    bits=None,
    min_value: int = 0,
    max_value=None,
    default: int = 0,
    byte_count=None,
) -> int:
    """Bind one per-player unsigned integer array/PVariable."""
    key = _check_key(key)
    bits = _normalize_bits(bits, min_value, max_value, byte_count)
    start_index = len(_FLAT)
    bit_offset = _add_flat_entry(key, values, "number", bits, min_value, default, 0, 1, PLAYERS)
    _add_pack_group(key, values, "number", bits, bit_offset, min_value, start_index, 1, 0, 1, PLAYERS)
    _rebuild_schema()
    return len(_FLAT) - 1


def bind_player_uint_bytes(
    key: str,
    values,
    byte_count: int = 1,
    min_value: int = 0,
    default: int = 0,
) -> int:
    """Bind one per-player unsigned integer with a fixed 1..4 byte width."""
    return bind_player_uint(
        key,
        values,
        byte_count=byte_count,
        min_value=min_value,
        default=default,
    )


def bind_player_bool(key: str, values, default: int = 0) -> int:
    return bind_player_uint(key, values, bits=1, min_value=0, max_value=1, default=default)


def bind_player_array(
    key: str,
    values,
    count: int,
    bits=None,
    min_value: int = 0,
    max_value=None,
    default: int = 0,
    slots: int = PLAYERS,
    byte_count=None,
) -> int:
    """Bind a per-player flat array: values[player * count + i].

    bits/byte_count/min_value/max_value/default may be one value for every
    item, or a list/tuple with one value per item.
    """
    key = _check_key(key)
    count = int(count)
    slots = int(slots)
    if count < 1:
        raise RuntimeError("SCND array count must be at least 1.")
    if slots < 1 or slots > PLAYERS:
        raise RuntimeError("SCND array slots must be 1..%d." % PLAYERS)
    widths = _normalize_item_widths(count, bits, min_value, max_value, byte_count)
    _validate_sequence_length("default", default, count)
    start_index = len(_FLAT)
    start_bit_offset = 0
    min_values = [int(_sequence_value(min_value, i)) for i in range(count)]
    for i in range(count):
        bit_offset = _add_flat_entry(
            "%s[%d]" % (key, i),
            values,
            "array",
            widths[i],
            int(_sequence_value(min_value, i)),
            int(_sequence_value(default, i)),
            i,
            count,
            slots,
        )
        if i == 0:
            start_bit_offset = bit_offset
    if all(width == widths[0] for width in widths) and all(item_min == min_values[0] for item_min in min_values):
        _add_pack_group(key, values, "array", widths[0], start_bit_offset, min_values[0], start_index, count, 0, count, slots)
    else:
        for i in range(count):
            entry = _FLAT[start_index + i]
            _add_pack_group(entry.key, values, "array", entry.bits, entry.bit_offset, entry.min_value, start_index + i, 1, i, count, slots)
    _rebuild_schema()
    return start_index


def bind_player_array_bytes(
    key: str,
    values,
    count: int,
    byte_count: int = 1,
    min_value: int = 0,
    default: int = 0,
    slots: int = PLAYERS,
) -> int:
    """Bind a per-player flat array with fixed byte widths.

    byte_count may be a single 1..4 value, or a list/tuple with one byte width
    per item.
    """
    return bind_player_array(
        key,
        values,
        count,
        byte_count=byte_count,
        min_value=min_value,
        default=default,
        slots=slots,
    )


def bind_player_array_bits(
    key: str,
    values,
    count: int,
    *bits,
    min_value: int = 0,
    default: int = 0,
    slots: int = PLAYERS,
) -> int:
    """EPS-friendly per-player array binding with per-item bit widths.

    Use from EPS as:
        scnd.bind_player_array_bits("mixed", VALUES, 4, 6, 17, 4, 1);

    A single bit width is accepted as a shorthand for every item.
    """
    if len(bits) == 0:
        raise RuntimeError("SCND bind_player_array_bits requires at least one bit width.")
    item_bits = int(bits[0]) if len(bits) == 1 else [int(bit) for bit in bits]
    return bind_player_array(
        key,
        values,
        count,
        item_bits,
        min_value=min_value,
        default=default,
        slots=slots,
    )


def bind_player_bool_array(
    key: str,
    values,
    count: int,
    default: int = 0,
    slots: int = PLAYERS,
) -> int:
    return bind_player_array(
        key,
        values,
        count,
        bits=1,
        min_value=0,
        max_value=1,
        default=default,
        slots=slots,
    )


def bind_global_uint(
    key: str,
    values,
    bits=None,
    min_value: int = 0,
    max_value=None,
    default: int = 0,
    byte_count=None,
) -> int:
    """Bind one map-global unsigned integer.

    The value is not indexed by player.  During load the first successful global
    assignment locks later loads from overwriting global bindings.
    """
    global _HAS_GLOBAL_BINDINGS
    key = _check_key(key)
    bits = _normalize_bits(bits, min_value, max_value, byte_count)
    start_index = len(_FLAT)
    bit_offset = _add_flat_entry(key, values, "global_number", bits, min_value, default, 0, 1, 1)
    _add_pack_group(key, values, "global_number", bits, bit_offset, min_value, start_index, 1, 0, 1, 1)
    _HAS_GLOBAL_BINDINGS = True
    _rebuild_schema()
    return len(_FLAT) - 1


def bind_global_array(
    key_or_values,
    values_or_bits=None,
    bits=None,
    min_value: int = 0,
    max_value=None,
    default: int = 0,
    byte_count=None,
    count: int | None = None,
) -> int:
    """Bind a map-global flat array: values[index].

    Supported forms:
      bind_global_array("key", values, [6, 17, 4])
      bind_global_array(values, [6, 17, 4])
      bind_global_array(values, 8, count=10)
    """
    global _HAS_GLOBAL_BINDINGS
    if isinstance(key_or_values, str):
        key = key_or_values
        values = values_or_bits
        item_bits = bits
    else:
        key = "global"
        values = key_or_values
        item_bits = values_or_bits if bits is None else bits

    if values is None:
        raise RuntimeError("SCND bind_global_array requires a values array.")

    if count is None:
        if _is_width_sequence(item_bits):
            count = len(item_bits)
        elif _is_width_sequence(byte_count):
            count = len(byte_count)
        elif hasattr(item_bits, "length"):
            raise RuntimeError(
                "SCND bind_global_array bit widths must be a Python list/tuple at compile time. "
                "Use scnd.bind_global_array_bits(values, 6, 17, 4, ...) from EPS."
            )
        else:
            count = 1

    key = _check_key(key)
    count = int(count)
    if count < 1:
        raise RuntimeError("SCND global array count must be at least 1.")
    widths = _normalize_item_widths(count, item_bits, min_value, max_value, byte_count)
    _validate_sequence_length("default", default, count)
    start_index = len(_FLAT)
    start_bit_offset = 0
    min_values = [int(_sequence_value(min_value, i)) for i in range(count)]
    for i in range(count):
        bit_offset = _add_flat_entry(
            "%s[%d]" % (key, i),
            values,
            "global_array",
            widths[i],
            int(_sequence_value(min_value, i)),
            int(_sequence_value(default, i)),
            i,
            count,
            1,
        )
        if i == 0:
            start_bit_offset = bit_offset
    if all(width == widths[0] for width in widths) and all(item_min == min_values[0] for item_min in min_values):
        _add_pack_group(key, values, "global_array", widths[0], start_bit_offset, min_values[0], start_index, count, 0, count, 1)
    else:
        for i in range(count):
            entry = _FLAT[start_index + i]
            _add_pack_group(entry.key, values, "global_array", entry.bits, entry.bit_offset, entry.min_value, start_index + i, 1, i, count, 1)
    _HAS_GLOBAL_BINDINGS = True
    _rebuild_schema()
    return start_index


def bind_global_array_bits(values, *bits, key: str = "global", min_value: int = 0, default: int = 0) -> int:
    return bind_global_array(key, values, list(bits), min_value=min_value, default=default)


def reset_global_lock() -> None:
    _GLOBAL_VALUES_LOCK[0] = 0
    _GLOBAL_PENDING_OWNER[0] = 0


bind_uint = bind_player_uint
bind_uint_bytes = bind_player_uint_bytes
bind_bool = bind_player_bool
bind_array = bind_player_array
bind_array_bytes = bind_player_array_bytes
bind_bool_array = bind_player_bool_array
bind_player_value = bind_player_uint
bind_player_value_bytes = bind_player_uint_bytes
bind_player_single = bind_player_uint
bind_player_single_bytes = bind_player_uint_bytes
bind_player_bool_value = bind_player_bool
bind_global_value = bind_global_uint


def bind_player_schema(schema, slots: int = PLAYERS, clear: bool = True):
    """Create per-player EUDArray objects and bind them in one compact call.

    Each item may be either a dict or a tuple:

        {"key": "level", "count": 50, "max_value": 255}
        ("flags", 16, {"bits": 1})
        ("items", 100, {"byte_count": 1})
        ("mixed", 3, {"byte_count": [1, 2, 4]})

    The return value is a dict of key -> EUDArray.  Values are stored as
    arrays[key][player * count + index].
    """
    if clear:
        clear_bindings()

    arrays = {}
    for raw in schema:
        if isinstance(raw, dict):
            item = dict(raw)
        else:
            key = raw[0]
            count = raw[1]
            options = raw[2] if len(raw) >= 3 else {}
            item = {"key": key, "count": count}
            item.update(options)

        key = str(item["key"])
        count = int(item["count"])
        default = item.get("default", 0)
        item_slots = int(item.get("slots", slots))
        values = item.get("values")
        if values is None:
            values = ep.EUDArray(_expand_array_default(default, count, item_slots))

        if item.get("bool", False):
            bind_player_bool_array(key, values, count, default=default, slots=item_slots)
        elif "byte_count" in item:
            bind_player_array_bytes(
                key,
                values,
                count,
                byte_count=item["byte_count"],
                min_value=item.get("min_value", 0),
                default=default,
                slots=item_slots,
            )
        else:
            bind_player_array(
                key,
                values,
                count,
                bits=item.get("bits"),
                min_value=item.get("min_value", 0),
                max_value=item.get("max_value"),
                default=default,
                slots=item_slots,
            )

        arrays[key] = values

    return arrays


bind_schema = bind_player_schema


def binding_count() -> int:
    return len(_FLAT)


def total_bits() -> int:
    return _TOTAL_BITS


def packed_dwords() -> int:
    return _PACKED_DWORDS


def schema_hash() -> int:
    return _SCHEMA_HASH


def schema_bit_budget() -> dict[str, Any]:
    """Return Python-side size information for the registered schema.

    This helper is intentionally compile-time only.  It does not create any EUD
    triggers and is meant for maker scripts, examples, and build logs.
    """
    by_bits: dict[int, int] = {}
    by_kind: dict[str, int] = {}
    for entry in _FLAT:
        by_bits[entry.bits] = by_bits.get(entry.bits, 0) + 1
        by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1

    packed_bits = _PACKED_DWORDS * 32
    player_packed_bits = _player_save_plain_dwords() * 32
    global_packed_bits = _GLOBAL_PACKED_DWORDS * 32
    payload_bits = max(_PLAYER_TOTAL_BITS, _GLOBAL_TOTAL_BITS)
    packed_bytes = max((_PLAYER_TOTAL_BITS + 7) // 8, (_GLOBAL_TOTAL_BITS + 7) // 8)
    storage_bytes = (SCND_HEADER_DWORDS + _PACKED_DWORDS + SCND_TAG_DWORDS) * 4
    player_save_dwords = _player_save_plain_dwords()
    return {
        "bindings": len(_FLAT),
        "schema_hash": _SCHEMA_HASH,
        "player_schema_hash": _PLAYER_SCHEMA_HASH,
        "global_schema_hash": _GLOBAL_SCHEMA_HASH,
        "total_bits": _TOTAL_BITS,
        "player_total_bits": _PLAYER_TOTAL_BITS,
        "global_total_bits": _GLOBAL_TOTAL_BITS,
        "compat_mode": int(_APP_COMPAT_MODE),
        "packed_bits": packed_bits,
        "payload_bits": payload_bits,
        "padding_bits": max(0, packed_bits - payload_bits),
        "player_padding_bits": max(0, player_packed_bits - _PLAYER_TOTAL_BITS),
        "global_padding_bits": max(0, global_packed_bits - _GLOBAL_TOTAL_BITS),
        "packed_dwords": _PACKED_DWORDS,
        "player_packed_dwords": _PLAYER_PACKED_DWORDS,
        "global_packed_dwords": _GLOBAL_PACKED_DWORDS,
        "player_save_plain_dwords": player_save_dwords,
        "player_field_count": _PLAYER_FIELD_COUNT,
        "player_compat_bits_max_dwords": _PLAYER_COMPAT_BITS_MAX_DWORDS,
        "player_compat_key_bits_max_dwords": _PLAYER_COMPAT_KEY_BITS_MAX_DWORDS,
        "packed_bytes": packed_bytes,
        "storage_bytes": storage_bytes,
        "base64_chars": estimate_code_chars(),
        "base64_lines_72": estimate_code_lines(72),
        "hangul11172_chars": estimate_hangul_chars(),
        "hangul8192_chars": estimate_hangul8192_chars(),
        "unicode32768_chars": estimate_unicode32768_chars(),
        "by_bits": dict(sorted(by_bits.items())),
        "by_kind": dict(sorted(by_kind.items())),
    }


def _field_schema_for_scope(scope: int) -> list[dict[str, Any]]:
    schema: list[dict[str, Any]] = []
    for entry in _FLAT:
        if _scope_for_kind(entry.kind) != scope:
            continue
        schema.append(
            {
                "key": entry.key,
                "kind": entry.kind,
                "bits": int(entry.bits),
                "bitOffset": int(entry.bit_offset),
                "minValue": int(entry.min_value),
                "default": int(entry.default),
                "itemIndex": int(entry.item_index),
                "count": int(entry.count),
            }
        )
    return schema


def player_field_schema() -> list[dict[str, Any]]:
    """Return the exact player-save schema needed by SCND_SAVE_APPS_SCRIPT.gs."""
    return _field_schema_for_scope(_SCOPE_PLAYER)


def global_field_schema() -> list[dict[str, Any]]:
    """Return the exact global-load schema used by the current map build."""
    return _field_schema_for_scope(_SCOPE_GLOBAL)


def _save_app_script_setup_text(props: dict[str, str]) -> str:
    template_lines = [
        "const PRIVATE_SHEET_ID = %s;" % json.dumps(props["PRIVATE_SPREADSHEET_ID"], ensure_ascii=False),
        "",
        "const SCND_SERVER_PROPERTY_TEMPLATE = {",
    ]
    keys = [
        "PRIVATE_SPREADSHEET_ID",
        "MAP_ID",
        "MAP_ID_KEY",
        "MAP_KEY",
        "SAVE_SHEET_NAME",
        "REQUIRE_MAP_ID_KEY",
        "MAX_SAVE_CODE_CHARS",
        "RATE_LIMIT_SECONDS",
        "FAST_APPEND_ONLY",
        "RETURN_TIMING",
        "COMPAT_MODE",
        "PLAYER_TOTAL_BITS",
        "PLAYER_PACKED_DWORDS",
        "PLAYER_SCHEMA_HASH",
        "SCND_FIELD_SCHEMA_JSON",
    ]
    for index, key in enumerate(keys):
        suffix = "," if index + 1 < len(keys) else ""
        if key == "PRIVATE_SPREADSHEET_ID":
            template_lines.append("  %s: PRIVATE_SHEET_ID%s" % (key, suffix))
        elif key == "SCND_FIELD_SCHEMA_JSON":
            template_lines.append("  %s: %s%s" % (key, props[key], suffix))
        else:
            template_lines.append("  %s: %s%s" % (key, json.dumps(props[key], ensure_ascii=False), suffix))
    template_lines.append("};")
    return "\n".join(template_lines) + "\n"


def write_save_app_script_setup(
    map_id: str,
    maker_key: str,
    *,
    map_id_key: str = "",
    path: str = "SCND_SERVER_PROPERTY_TEMPLATE.gs",
    private_spreadsheet_id: str = "",
    save_sheet_name: str = "SaveLog",
) -> str:
    """Write the setup file used by SCND_SAVE_APPS_SCRIPT.gs."""
    schema = player_field_schema()
    map_id_text = str(map_id or "")
    key_text = str(maker_key or "")
    map_id_key_text = str(map_id_key or _encrypt_map_id(map_id_text, key_text))
    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    props = {
        "PRIVATE_SPREADSHEET_ID": str(private_spreadsheet_id or ""),
        "MAP_ID": map_id_text,
        "MAP_ID_KEY": map_id_key_text,
        "MAP_KEY": key_text,
        "SAVE_SHEET_NAME": str(save_sheet_name or "SaveLog"),
        "REQUIRE_MAP_ID_KEY": "1",
        "MAX_SAVE_CODE_CHARS": "20000",
        "RATE_LIMIT_SECONDS": "5",
        "FAST_APPEND_ONLY": "1",
        "RETURN_TIMING": "1",
        "COMPAT_MODE": str(int(_APP_COMPAT_MODE)),
        "PLAYER_TOTAL_BITS": str(int(_PLAYER_TOTAL_BITS)),
        "PLAYER_PACKED_DWORDS": str(int(_PLAYER_PACKED_DWORDS)),
        "PLAYER_SCHEMA_HASH": "%08x" % int(_PLAYER_SCHEMA_HASH),
        "SCND_FIELD_SCHEMA_JSON": schema_json,
    }
    abs_path = os.path.abspath(str(path))
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fp:
        fp.write(_save_app_script_setup_text(props))
    return abs_path


def estimate_hangul11172_chars_for_bits(bit_count, overhead_bits=0) -> int:
    """Estimate visible Hangul syllables for a base-11172 text layer."""
    bit_count = max(0, int(bit_count))
    overhead_bits = max(0, int(overhead_bits))
    return int(ceil((bit_count + overhead_bits) / log2(11172)))


def estimate_hangul8192_chars_for_bits(bit_count, overhead_bits=0) -> int:
    """Estimate visible Hangul syllables for the full-Hangul grouped layer."""
    bit_count = max(0, int(bit_count))
    overhead_bits = max(0, int(overhead_bits))
    byte_count = (bit_count + overhead_bits + 7) // 8
    return ((byte_count + SCND_HANGUL_GROUP_BYTES - 1) // SCND_HANGUL_GROUP_BYTES) * SCND_HANGUL_GROUP_CHARS


def estimate_chat_hangul_capacity(prefix_bytes=1, chat_byte_limit=79, char_bytes=3):
    """Return one-line Hangul capacity after a command prefix.

    Returns (hangul_chars, base11172_bits, stable_hangul_bits).  The measured StarCraft
    chat path usually behaves like UTF-8 for completed Hangul syllables, so the
    default is 3 bytes per visible syllable.  The default text budget is 79
    bytes; the 80th byte is the terminating null in the usual chat buffer.
    """
    available = max(0, int(chat_byte_limit) - int(prefix_bytes))
    chars = available // max(1, int(char_bytes))
    return chars, int(chars * log2(11172)), (chars // SCND_HANGUL_GROUP_CHARS) * SCND_HANGUL_GROUP_BYTES * 8


def estimate_chat_unicode32768_capacity(prefix_bytes=1, chat_byte_limit=79, char_bytes=3):
    available = max(0, int(chat_byte_limit) - int(prefix_bytes))
    chars = available // max(1, int(char_bytes))
    return chars, chars * SCND_UNICODE_BASE_BITS


def estimate_unicode32768_chars_for_payload(payload_dwords) -> int:
    payload_dwords = max(0, int(payload_dwords))
    total_bytes = (SCND_HEADER_DWORDS + payload_dwords + SCND_TAG_DWORDS) * 4
    return (total_bytes * 8 + SCND_UNICODE_BASE_BITS - 1) // SCND_UNICODE_BASE_BITS


def estimate_unicode32768_chars() -> int:
    return estimate_unicode32768_chars_for_payload(_PACKED_DWORDS)


def estimate_fill_unicode32768_chars() -> int:
    return estimate_unicode32768_chars_for_payload(1)


def estimate_code_chars() -> int:
    return estimate_code_chars_for_payload(_PACKED_DWORDS)


def estimate_code_chars_for_payload(payload_dwords) -> int:
    payload_dwords = max(0, int(payload_dwords))
    total_bytes = (SCND_HEADER_DWORDS + payload_dwords + SCND_TAG_DWORDS) * 4
    return (total_bytes * 4 + 2) // 3


def estimate_code_lines(chars_per_line=SCND_PRINT_CHARS) -> int:
    chars_per_line = max(1, int(chars_per_line))
    return (estimate_code_chars() + chars_per_line - 1) // chars_per_line


def estimate_fill_code_chars() -> int:
    return estimate_code_chars_for_payload(1)


def estimate_fill_code_lines(chars_per_line=SCND_PRINT_CHARS) -> int:
    chars_per_line = max(1, int(chars_per_line))
    return (estimate_fill_code_chars() + chars_per_line - 1) // chars_per_line


def estimate_hangul8192_chars_for_payload(payload_dwords) -> int:
    return _estimate_hangul8192_chars_for_payload_dwords(payload_dwords)


def estimate_hangul8192_chars() -> int:
    return estimate_hangul8192_chars_for_payload(_PACKED_DWORDS)


def estimate_hangul8192_lines(chars_per_line=SCND_HANGUL_PRINT_CHARS) -> int:
    chars_per_line = max(1, int(chars_per_line))
    return (estimate_hangul8192_chars() + chars_per_line - 1) // chars_per_line


def estimate_hangul8192_pages(chars_per_line=SCND_SAVE_DATA_CHARS_PER_LINE, code_lines_per_page=SCND_SAVE_DATA_CODE_LINES) -> int:
    """Estimate screen pages for the save-data block.

    The default matches the practical StarCraft input/display limit used by
    SCNDgram: 56 Hangul syllables per code line, with the title on row 0 and
    ten data/padding rows below it. The save-data renderer omits the final
    trailing newline so the title is not pushed out.
    """
    code_lines_per_page = max(1, int(code_lines_per_page))
    lines = estimate_hangul8192_lines(chars_per_line)
    return (lines + code_lines_per_page - 1) // code_lines_per_page


def estimate_fill_hangul8192_chars() -> int:
    return estimate_hangul8192_chars_for_payload(1)


def estimate_fill_hangul8192_lines(chars_per_line=SCND_HANGUL_PRINT_CHARS) -> int:
    chars_per_line = max(1, int(chars_per_line))
    return (estimate_fill_hangul8192_chars() + chars_per_line - 1) // chars_per_line


def estimate_hangul_chars() -> int:
    """Estimate visible chars for crytohangul-style base-11172 output.

    SCND runtime still uses Base64url because chat input is byte-limited and the
    Base64 path is linear-time.  This helper exists only to compare visible
    length against a Hangul codec like crytohangul.txt.
    """
    total_bytes = (SCND_HEADER_DWORDS + _PACKED_DWORDS + SCND_TAG_DWORDS) * 4
    return int(ceil((total_bytes * 8) / log2(11172)))


def estimate_hangul_lines(hangul_per_line=20) -> int:
    hangul_per_line = max(1, int(hangul_per_line))
    return (estimate_hangul_chars() + hangul_per_line - 1) // hangul_per_line


def estimate_hangul_utf8_bytes() -> int:
    """Estimate UTF-8 bytes used by visible Hangul output."""
    return estimate_hangul_chars() * 3


def estimate_hangul_chat_lines(chat_byte_limit=78, prefix_bytes=3) -> int:
    """Estimate paste lines if Hangul syllables are used in byte-limited chat."""
    char_bytes = 3
    payload_bytes = max(char_bytes, int(chat_byte_limit) - int(prefix_bytes))
    hangul_per_line = max(1, payload_bytes // char_bytes)
    return estimate_hangul_lines(hangul_per_line)


def _context_keys(map_id: str, save_id: int, maker_key: str, schema_hash: int | None = None) -> tuple[int, int, int, int]:
    active_schema_hash = _SCHEMA_HASH if schema_hash is None else int(schema_hash)
    cache_key = (str(map_id), int(save_id), str(maker_key), active_schema_hash)
    cached = _CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    base = "%s|%d|%s|%08x|%d" % (
        cache_key[0],
        cache_key[1],
        cache_key[2],
        cache_key[3],
        SCND_VERSION,
    )
    keys = (
        _stable_hash("k0|" + base),
        _stable_hash("k1|" + base),
        _stable_hash("k2|" + base),
        _stable_hash("k3|" + base),
    )
    _CONTEXT_CACHE[cache_key] = keys
    return keys


def _obfuscate_context_keys(
    map_id: str,
    save_id: int,
    maker_key: str,
    schema_hash: int,
    scope: int,
) -> _ObfuscatedContext:
    cache_key = (str(map_id), int(save_id), str(maker_key), int(schema_hash), int(scope))
    cached = _CONTEXT_OBF_CACHE.get(cache_key)
    if cached is not None:
        return cached

    keys = _context_keys(map_id, save_id, maker_key, schema_hash)
    domain = "ctx-obf|%s|%d|%s|%08x|%d|%d" % (
        cache_key[0],
        cache_key[1],
        cache_key[2],
        cache_key[3] & 0xFFFFFFFF,
        cache_key[4],
        SCND_VERSION,
    )
    words = _derive_obfuscation_words(domain, 4 * 13 + 8)
    order_salt = words[-8:]
    order = sorted(range(4), key=lambda index: order_salt[index])
    records: list[_ObfuscatedKeyRecord] = []

    for key_index in order:
        base = key_index * 13
        key = _u32(keys[key_index])
        m0 = words[base + 0]
        m1 = words[base + 1]
        m2 = words[base + 2]
        a0 = words[base + 3]
        a1 = words[base + 4]
        a2 = words[base + 5]
        a3 = words[base + 6]
        r0 = words[base + 7] % 31 + 1
        r1 = words[base + 8] % 31 + 1
        r2 = words[base + 9] % 31 + 1
        r3 = words[base + 10] % 31 + 1

        # The final k0..k3 values are never emitted as direct constants.  Each
        # key is split into chained masks, additions, and rotations; the records
        # themselves are emitted in a deterministic shuffled order.
        c0 = _rotl32_const(_u32((key ^ m0) + a0), r0)
        c1 = _rotl32_const(_u32((m0 ^ m1) + a1), r1)
        c2 = _rotl32_const(_u32((m1 ^ m2) + a2), r2)
        c3 = _rotl32_const(_u32(m2 ^ a3), r3)

        # Extra per-record whitening keeps two identical intermediate chunks
        # from showing up as identical trigger constants.
        w0 = words[base + 11]
        w1 = words[base + 12]
        records.append(
            _ObfuscatedKeyRecord(
                key_index=key_index,
                c0=_u32(c0 ^ w0),
                c1=_u32(c1 ^ _rotl32_const(w0, 9)),
                c2=_u32(c2 ^ w1),
                c3=_u32(c3 ^ _rotl32_const(w1, 13)),
                r0=r0,
                r1=r1,
                r2=r2,
                r3=r3,
                a0=_u32(a0 ^ w0),
                a1=_u32(a1 ^ _rotl32_const(w0, 9)),
                a2=_u32(a2 ^ w1),
                a3=_u32(a3 ^ _rotl32_const(w1, 13)),
                w0=w0,
                w1=w1,
            )
        )

    context = _ObfuscatedContext(tuple(records))
    _CONTEXT_OBF_CACHE[cache_key] = context
    return context


def _context_schema_hash(scope: int) -> int:
    if scope == _SCOPE_GLOBAL:
        return _GLOBAL_SCHEMA_HASH
    if _APP_COMPAT_MODE == SCND_COMPAT_EXACT:
        return _PLAYER_SCHEMA_HASH
    return 0


def _make_flag(codec_id: int, params: int = 0) -> int:
    return ((int(params) & SCND_FLAG_PARAM_MASK) << 8) | (int(codec_id) & SCND_FLAG_CODEC_MASK)


def _make_flag_expr(codec_id, params=0):
    """Build a flag from Python constants or runtime EUD values."""
    if isinstance(codec_id, int) and isinstance(params, int):
        return _make_flag(codec_id, params)

    if isinstance(params, int):
        param_part = (int(params) & SCND_FLAG_PARAM_MASK) << 8
    else:
        param_part = ep.f_bitlshift(ep.f_bitand(params, SCND_FLAG_PARAM_MASK), 8)

    if isinstance(codec_id, int):
        codec_part = int(codec_id) & SCND_FLAG_CODEC_MASK
    else:
        codec_part = ep.f_bitand(codec_id, SCND_FLAG_CODEC_MASK)

    return ep.f_bitor(param_part, codec_part)


def _set_flag(player, codec_id, params=0) -> None:
    _FLAGS[player] = _make_flag_expr(codec_id, params)


@ep.EUDFunc
def _flag_codec(flag):
    return ep.f_bitand(flag, SCND_FLAG_CODEC_MASK)


@ep.EUDFunc
def _flag_params(flag):
    return ep.f_bitrshift(flag, 8)


_get_flag_codec = _flag_codec
_get_flag_params = _flag_params


def _work_epd(player):
    return ep.EPD(_WORK) + ep.f_mul(player, SCND_MAX_DWORDS)


def _temp_epd(player):
    return ep.EPD(_TEMP) + ep.f_mul(player, SCND_MAX_DWORDS)


def _ensure_global_pending_storage():
    global _GLOBAL_PENDING_DATA, _GLOBAL_PENDING_DWORDS
    required_dwords = max(1, int(_GLOBAL_PACKED_DWORDS))
    if _GLOBAL_PENDING_DATA is None:
        _GLOBAL_PENDING_DATA = ep.Db(required_dwords * 4)
        _GLOBAL_PENDING_DWORDS = required_dwords
    elif _GLOBAL_PENDING_DWORDS < required_dwords:
        raise RuntimeError("SCND global bindings changed after runtime compilation.")
    return _GLOBAL_PENDING_DATA


def _global_pending_epd():
    return ep.EPD(_ensure_global_pending_storage())


def _work_addr(player, byte_index=0):
    return _WORK + ep.f_mul(player, SCND_MAX_DWORDS * 4) + byte_index


def _temp_addr(player, byte_index=0):
    return _TEMP + ep.f_mul(player, SCND_MAX_DWORDS * 4) + byte_index


def _code_addr(player, byte_index=0):
    return _CODE + ep.f_mul(player, SCND_MAX_CODE_CHARS) + byte_index


def _input_addr(player, byte_index=0):
    return _INPUT + ep.f_mul(player, SCND_MAX_CODE_CHARS) + byte_index


def _app_code_view_line_addr(player, line_index=0, byte_index=0):
    line_slot = ep.f_mul(player, _APP_CODE_VIEW_LINES) + line_index
    return _APP_CODE_VIEW + ep.f_mul(line_slot, _APP_CODE_VIEW_LINE_BYTES) + byte_index


def _ptr_from_epd(epd):
    return epd * 4 + 0x58A364


def _scnd_code_view_descriptor_magic_dwords():
    magic = b"SCNDCV1!"
    return [
        int.from_bytes(magic[:4], "little"),
        int.from_bytes(magic[4:8], "little"),
    ]


# Descriptor dword index of the self-pointer slot (version 2).
_SCND_CODE_VIEW_SELF_PTR_DWORD = 10


def _touch_code_view_descriptor(block):
    def _touch(block=block):
        # Stamp the descriptor's own 1.16.1 address into the self-pointer
        # slot so external readers can rebase every raw pointer exactly:
        # real = real_descriptor_address - self_ptr + raw_ptr.
        ep.RawTrigger(
            actions=[
                ep.SetMemoryEPD(block, ep.Add, 0),
                ep.SetMemoryEPD(
                    block._epd + _SCND_CODE_VIEW_SELF_PTR_DWORD,
                    ep.SetTo,
                    _ptr_from_epd(block._epd),
                ),
            ]
        )

    ep.EUDOnStart(_touch)


def _ensure_code_view_descriptor():
    global _APP_CODE_VIEW_DESCRIPTOR
    if _APP_CODE_VIEW is None:
        return None

    if _APP_CODE_VIEW_DESCRIPTOR is None:
        _APP_CODE_VIEW_DESCRIPTOR = ep.EUDArray(
            _scnd_code_view_descriptor_magic_dwords()
            + [
                2,
                _APP_CODE_VIEW,
                _APP_CODE_VIEW_LINE_BYTES,
                _APP_CODE_VIEW_LINES,
                PLAYERS,
                _APP_DISPLAY_LINE,
                _ptr_from_epd(_APP_CODE_VIEW_TIMER._epd),
                _ptr_from_epd(_APP_CODE_VIEW_LOCAL_CLOSED._epd),
                0,  # self pointer, stamped at map start
            ]
        )
        _touch_code_view_descriptor(_APP_CODE_VIEW_DESCRIPTOR)

    return _APP_CODE_VIEW_DESCRIPTOR


def _hinput_epd(player):
    return ep.EPD(_HANGUL_INPUT) + ep.f_mul(player, SCND_MAX_HANGUL_CHARS)


def _app_shared_eprintln(player, *parts):
    # Keep the DisplayText/eprintln action in the shared trigger flow.
    # If a message must differ per client, prepare the pointed buffer locally,
    # but do not guard the print action itself with a non-shared condition.
    ep.f_eprintln(*parts)


def _msqc_pos_to_word(pos):
    y, x = ep.f_div(pos, 0x10000)
    x -= _MSQC_MIN_WIDTH
    y -= _MSQC_MIN_WIDTH
    return x + ep.f_mul(y, _MSQC_MAP_WIDTH)


def _msqc_word_to_pos(value, parity):
    raw = ep.EUDVariable()
    y = ep.EUDVariable()
    x = ep.EUDVariable()
    raw << value + ep.f_mul(parity, 0x10000)
    y, x = ep.f_div(raw, _MSQC_MAP_WIDTH)
    x += _MSQC_MIN_WIDTH
    y += _MSQC_MIN_WIDTH
    return x + ep.f_mul(y, 0x10000)


def _msqc_word_value(word):
    parity, value = ep.f_div(word, 0x10000)
    return value


def _msqc_word_parity(word):
    parity, value = ep.f_div(word, 0x10000)
    return parity


def _register_launcher_input_msqc():
    global _SCND_INPUT_MSQC_REGISTERED
    if _SCND_INPUT_MSQC_REGISTERED:
        return
    ep.EUDRegisterObjectToNamespace(
        "SCNDInputMSQCIsTransfer", SCNDInputMSQCIsTransfer
    )
    for index in range(1, _MSQC_SCND_INPUT_CHANNEL_COUNT + 1):
        ep.EUDRegisterObjectToNamespace(
            "SCNDInputMSQCSend%d" % index,
            globals()["SCNDInputMSQCSend%d" % index],
        )
        ep.EUDRegisterObjectToNamespace(
            "SCNDInputMSQCReceive%d" % index,
            globals()["SCNDInputMSQCReceive%d" % index],
        )
    _SCND_INPUT_MSQC_REGISTERED = True


def _install_launcher_input_msqc_settings(settings):
    _register_launcher_input_msqc()
    added = 0
    for index in range(1, _MSQC_SCND_INPUT_CHANNEL_COUNT + 1):
        key = "SCNDInputMSQCIsTransfer.AtLeast(1); xy, SCNDInputMSQCSend%d" % index
        value = "SCNDInputMSQCReceive%d" % index
        if key not in settings:
            settings[key] = value
            added += 1
    return added


class _ExternalMsqcLoader:
    def __init__(self, loader):
        self.loader = loader
        self.module_name = getattr(loader, "__name__", None)
        if not self.module_name:
            for name, module in sys.modules.items():
                if module is loader:
                    self.module_name = name
                    break
        self.started = ep.EUDArray([0] * PLAYERS)
        self.finished = ep.EUDArray([0] * PLAYERS)
        self.namespace_registered = False

    def _get(self, *names):
        for name in names:
            value = getattr(self.loader, name, None)
            if value is not None:
                return value
        raise RuntimeError("SCND msqcloader is missing %s" % "/".join(names))

    def _register_namespace(self):
        if self.namespace_registered:
            return
        transfer = self._get("MSQCIsTransfer")
        ep.EUDRegisterObjectToNamespace("SCNDMSQCIsTransfer", transfer)
        for index in range(1, _MSQC_CHANNEL_COUNT + 1):
            send = self._get("MSQCSend%d" % index)
            receive = self._get("MSQCReceive%d" % index)
            ep.EUDRegisterObjectToNamespace("SCNDMSQCSend%d" % index, send)
            ep.EUDRegisterObjectToNamespace("SCNDMSQCReceive%d" % index, receive)
        self.namespace_registered = True

    def init_runtime(self):
        init = getattr(self.loader, "Init", None)
        if init is not None:
            init()
        self._register_namespace()

    def install_msqc_settings(self, settings):
        self._register_namespace()
        transfer_expr = "SCNDMSQCIsTransfer"
        added = 0
        for index in range(1, _MSQC_CHANNEL_COUNT + 1):
            send_expr = "SCNDMSQCSend%d" % index
            recv_expr = "SCNDMSQCReceive%d" % index
            key = "%s.AtLeast(1); xy, %s" % (transfer_expr, send_expr)
            value = recv_expr
            if key not in settings:
                settings[key] = value
                added += 1
        return added

    def register_msqc_sync(self, register_sync):
        self._register_namespace()
        added = 0
        for index in range(1, _MSQC_CHANNEL_COUNT + 1):
            register_sync(
                "SCNDMSQCIsTransfer.AtLeast(1)",
                "SCNDMSQCSend%d" % index,
                "SCNDMSQCReceive%d" % index,
            )
            added += 1
        return added

    def _maybe_start_remote(self, player, receive_epd):
        loadstart = getattr(self.loader, "loadstart", None)
        receive1 = getattr(self.loader, "MSQCReceive1", None)
        if loadstart is None or receive1 is None:
            return

        raw = ep.EUDVariable()
        decoded = ep.EUDVariable()
        parity = ep.EUDVariable()
        value = ep.EUDVariable()
        raw << receive1[player]

        if ep.EUDIfNot()(raw.Exactly(-1)):
            if ep.EUDIfNot()(raw.Exactly(0)):
                decoded << _msqc_pos_to_word(raw)
                parity << decoded // 0x10000
                value << decoded - ep.f_mul(parity, 0x10000)
                if ep.EUDIf()([parity.Exactly(_MSQC_MAX_PARITY), value.Exactly(_MSQC_INIT_VALUE)]):
                    if ep.EUDIf()(_STATE[player].Exactly(_PHASE_IDLE)):
                        _STATE[player] = _PHASE_SYNC_WAIT
                        _RESULT[player] = RESULT_NONE
                        _MESSAGE[player] = MESSAGE_NONE
                        _INDEX[player] = 0
                        _SUBINDEX[player] = 0
                        _APP_LOAD_INTENT[player] = 0
                    ep.EUDEndIf()

                    if ep.EUDIf()([self.started[player].Exactly(0), loadstart[player].Exactly(0)]):
                        loaddatalen = getattr(self.loader, "loaddatalen", None)
                        lastrindex = getattr(self.loader, "lastrindex", None)
                        waittimer = getattr(self.loader, "waittimer", None)
                        receiveindex = getattr(self.loader, "receiveindex", None)
                        receivedata = getattr(self.loader, "receivedata", None)
                        if loaddatalen is not None:
                            loaddatalen[player] = 0
                        if lastrindex is not None:
                            lastrindex[player] = 0
                        if waittimer is not None:
                            waittimer[player] = 10
                        if receiveindex is not None:
                            receiveindex[player] = 0
                        if receivedata is not None:
                            receivedata[player] = receive_epd
                        loadstart[player] = 1
                        self.started[player] = 1
                        self.finished[player] = 0
                    ep.EUDEndIf()
                ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()

    def start(self, player, send_epd, receive_epd):
        start = self._get("Start", "start")
        origcp = ep.f_getcurpl()
        ep.f_setcurpl(player)
        start(player, send_epd, receive_epd)
        ep.f_setcurpl(origcp)
        self.started[player] = 1
        self.finished[player] = 0

    def start_local(self, dword_len):
        start_local = self._get("StartLocal", "start_local")
        start_local(dword_len)

    def loop(self, player, receive_epd):
        self._maybe_start_remote(player, receive_epd)
        loop = self._get("Loop", "loop")
        origcp = ep.f_getcurpl()
        ep.f_setcurpl(player)
        loop(player)
        ep.f_setcurpl(origcp)

        loadstart = getattr(self.loader, "loadstart", None)
        if loadstart is not None:
            if ep.EUDIf()(self.started[player].AtLeast(1)):
                if ep.EUDIf()(loadstart[player].Exactly(0)):
                    self.started[player] = 0
                    self.finished[player] = 1
                ep.EUDEndIf()
            ep.EUDEndIf()

    def is_finished(self, player):
        return self.finished[player]

    def clear_finished(self, player):
        self.finished[player] = 0


def _is_msqc_loader_module(module):
    return (
        module is not None
        and hasattr(module, "Start")
        and hasattr(module, "StartLocal")
        and hasattr(module, "Loop")
    )


def _find_msqc_loader_module():
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if name.endswith("msqcloader") and _is_msqc_loader_module(module):
            return module

    for module_name in (
        "TriggerEditor.TriggerEditor.__epspy__.msqcloader",
        "TriggerEditor.__epspy__.msqcloader",
        "TriggerEditor.msqcloader",
        "msqcloader",
    ):
        try:
            module = sys.modules.get(module_name)
            if module is None:
                module = importlib.import_module(module_name)
        except Exception:
            continue
        if _is_msqc_loader_module(module):
            return module
    return None


def use_msqcloader(loader=None):
    """Enable MSQC sharing for SCND loads before bound arrays are unpacked."""
    global _SYNC_ENABLED, _SYNC_LOADER
    if _SYNC_LOADER is not None:
        if loader is None:
            return _SYNC_LOADER
        loader_name = getattr(loader, "__name__", None)
        if _SYNC_LOADER.loader is loader or (
            loader_name is not None and loader_name == _SYNC_LOADER.module_name
        ):
            return _SYNC_LOADER
        if _is_msqc_loader_module(loader):
            return _SYNC_LOADER
        raise RuntimeError("SCND msqcloader is already initialized.")
    if loader is None:
        loader = _find_msqc_loader_module()
    if not _is_msqc_loader_module(loader):
        raise RuntimeError("SCND needs a compatible msqcloader module.")
    _SYNC_LOADER = _ExternalMsqcLoader(loader)
    _SYNC_ENABLED = True
    return _SYNC_LOADER


def auto_use_msqcloader(required: bool = False):
    """Find and enable the project's msqcloader module when it is available."""
    if _SYNC_LOADER is not None:
        return _SYNC_LOADER
    loader = _find_msqc_loader_module()
    if loader is None:
        if required:
            raise RuntimeError("SCND could not find a compatible msqcloader module.")
        return None
    return use_msqcloader(loader)


def init_msqcloader(required: bool = False):
    global _SYNC_RUNTIME_INITIALIZED
    if _SYNC_LOADER is None:
        auto_use_msqcloader(required=required)
    if _SYNC_LOADER is None:
        return None
    if not _SYNC_RUNTIME_INITIALIZED:
        _SYNC_LOADER.init_runtime()
        _SYNC_RUNTIME_INITIALIZED = True
    return _SYNC_LOADER


def install_msqc_settings(settings):
    if _SYNC_LOADER is None:
        auto_use_msqcloader(required=False)
    launcher_added = _install_launcher_input_msqc_settings(settings)
    payload_added = 0
    if _SYNC_LOADER is not None:
        payload_added = _SYNC_LOADER.install_msqc_settings(settings)
    added = launcher_added + payload_added
    print(
        "[SCND] MSQC bridge settings: "
        "launcher=%d, payload=%d, total=%d, msqcloader=%s"
        % (
            launcher_added,
            payload_added,
            added,
            "yes" if _SYNC_LOADER is not None else "no",
        )
    )
    return added


def register_msqc_sync(register_sync):
    global _SYNC_REGISTERED
    _register_launcher_input_msqc()
    if _SYNC_LOADER is None or _SYNC_REGISTERED:
        print(
            "[SCND] MSQC register_sync: "
            "payload=0, msqcloader=%s, already_registered=%s"
            % (
                "yes" if _SYNC_LOADER is not None else "no",
                "yes" if _SYNC_REGISTERED else "no",
            )
        )
        return 0
    added = _SYNC_LOADER.register_msqc_sync(register_sync)
    _SYNC_REGISTERED = True
    print(
        "[SCND] MSQC register_sync: "
        "payload=%d, msqcloader=yes, already_registered=no"
        % added
    )
    return added


def _set_load_payload_ready(player):
    if _APP_COMPAT_MODE != SCND_COMPAT_EXACT:
        if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_SCOPE_PLAYER)):
            _STATE[player] = _PHASE_COMPAT_DEFAULTS
            _INDEX[player] = 0
            _SUBINDEX[player] = 0
            _SAVED_FIELD_COUNT[player] = ep.f_dwread_epd(_work_epd(player) + SCND_DATA_OFFSET)
            if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
                _SAVED_VALUE_CURSOR[player] = _compat_mode1_value_start(_SAVED_FIELD_COUNT[player])
            elif _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
                _SAVED_VALUE_CURSOR[player] = _compat_mode2_value_start(_SAVED_FIELD_COUNT[player])
        if ep.EUDElse()():
            if _SYNC_ENABLED:
                _STATE[player] = _PHASE_SYNC_START
            else:
                _STATE[player] = _PHASE_UNPACK
            _INDEX[player] = 0
        ep.EUDEndIf()
        return
    if _SYNC_ENABLED:
        _STATE[player] = _PHASE_SYNC_START
    else:
        _STATE[player] = _PHASE_UNPACK
    _INDEX[player] = 0


@ep.EUDFunc
def _write_sync_plain_header(player):
    work = _work_epd(player)
    ep.f_dwwrite_epd(work + 0, SCND_MAGIC)
    ep.f_dwwrite_epd(work + 1, _ACTIVE_SCHEMA_HASH[player])
    ep.f_dwwrite_epd(work + 2, _ACTIVE_TOTAL_BITS[player])
    ep.f_dwwrite_epd(work + 3, 0)
    ep.f_dwwrite_epd(work + 4, _ACTIVE_PACKED_DWORDS[player])
    ep.f_dwwrite_epd(work + 5, SCND_FLAG_RAW)


@ep.EUDFunc
def _validate_sync_plain_header(player):
    ok = ep.EUDVariable()
    magic = ep.EUDVariable()
    schema = ep.EUDVariable()
    total_bits = ep.EUDVariable()
    packed_dwords = ep.EUDVariable()
    flags = ep.EUDVariable()
    work = _work_epd(player)
    ok << 0

    magic << ep.f_dwread_epd(work + 0)
    schema << ep.f_dwread_epd(work + 1)
    total_bits << ep.f_dwread_epd(work + 2)
    packed_dwords << ep.f_dwread_epd(work + 4)
    flags << ep.f_dwread_epd(work + 5)

    if ep.EUDIf()([
        magic.Exactly(SCND_MAGIC),
        schema.Exactly(_context_schema_hash(_SCOPE_PLAYER)),
        total_bits.Exactly(_PLAYER_TOTAL_BITS),
        packed_dwords.Exactly(_PLAYER_PACKED_DWORDS),
        flags.Exactly(SCND_FLAG_RAW),
    ]):
        _set_active_schema_to_canonical_player(player)
        _PLAIN_DWORDS[player] = _PLAYER_PACKED_DWORDS
        _PAYLOAD_DWORDS[player] = _PLAYER_PACKED_DWORDS
        _FLAGS[player] = SCND_FLAG_RAW
        ok << 1
    ep.EUDEndIf()

    if _HAS_GLOBAL_BINDINGS:
        if ep.EUDIf()([
            ok.Exactly(0),
            magic.Exactly(SCND_MAGIC),
            schema.Exactly(_context_schema_hash(_SCOPE_GLOBAL)),
            total_bits.Exactly(_GLOBAL_TOTAL_BITS),
            packed_dwords.Exactly(_GLOBAL_PACKED_DWORDS),
            flags.Exactly(SCND_FLAG_RAW),
        ]):
            _set_active_schema(player, _SCOPE_GLOBAL)
            _PLAIN_DWORDS[player] = _GLOBAL_PACKED_DWORDS
            _PAYLOAD_DWORDS[player] = _GLOBAL_PACKED_DWORDS
            _FLAGS[player] = SCND_FLAG_RAW
            ok << 1
        ep.EUDEndIf()

    return ok


@ep.EUDFunc
def _validate_input_sync_header(player):
    ok = ep.EUDVariable()
    word = ep.EUDVariable()
    count = ep.EUDVariable()
    expected = ep.EUDVariable()
    work = _work_epd(player)
    ok << 1

    word << ep.f_dwread_epd(work + 0)
    if ep.EUDIfNot()(word.Exactly(SCND_INPUT_SYNC_MAGIC)):
        ok << 0
    ep.EUDEndIf()

    count << ep.f_dwread_epd(work + 1)
    if ep.EUDIf()(count.Exactly(0)):
        ok << 0
    ep.EUDEndIf()
    if ep.EUDIf()(count.AtLeast(SCND_INPUT_SYNC_MAX_CHARS + 1)):
        ok << 0
    ep.EUDEndIf()

    expected << ep.f_dwread_epd(work + 5)
    if ep.EUDIf()(expected.Exactly(0)):
        ok << 0
    ep.EUDEndIf()
    if ep.EUDIf()(count < expected):
        ok << 0
    ep.EUDEndIf()
    if ep.EUDIf()(count > expected):
        ok << 0
    ep.EUDEndIf()
    return ok


@ep.EUDFunc
def _input_sync_read_char(player, index):
    pair_index = ep.EUDVariable()
    half = ep.EUDVariable()
    word = ep.EUDVariable()
    value = ep.EUDVariable()
    pair_index, half = ep.f_div(index, 2)
    word << ep.f_dwread_epd(_work_epd(player) + SCND_INPUT_SYNC_HEADER_DWORDS + pair_index)
    value << ep.f_bitand(word, 0xFFFF)
    if ep.EUDIf()(half.Exactly(1)):
        value << ep.f_bitrshift(word, 16)
    ep.EUDEndIf()
    return value


@ep.EUDFunc
def _input_sync_copy_step(player):
    value = ep.EUDVariable()
    value << _input_sync_read_char(player, _INDEX[player])
    ep.f_dwwrite_epd(_hinput_epd(player) + _INDEX[player], value)
    _INDEX[player] += 1


def _tick_sync_player(player):
    if not _SYNC_ENABLED or _SYNC_LOADER is None:
        return

    send_epd = _work_epd(player)
    recv_epd = _temp_epd(player)
    if ep.EUDIf()(_STATE[player].Exactly(_PHASE_SYNC_START)):
        _write_sync_plain_header(player)
        _SYNC_LOADER.start(player, send_epd, recv_epd)
        if ep.EUDIf()(ep.IsUserCP()):
            _SYNC_LOADER.start_local(SCND_HEADER_DWORDS + _SYNC_PLAIN_DWORDS)
        ep.EUDEndIf()
        _STATE[player] = _PHASE_SYNC_WAIT
    ep.EUDEndIf()

    _SYNC_LOADER.loop(player, recv_epd)

    if ep.EUDIf()(_STATE[player].Exactly(_PHASE_SYNC_WAIT)):
        if ep.EUDIf()(_SYNC_LOADER.is_finished(player).AtLeast(1)):
            _STATE[player] = _PHASE_SYNC_COPY
            _INDEX[player] = 0
            _SYNC_LOADER.clear_finished(player)
        ep.EUDEndIf()
    ep.EUDEndIf()


@ep.EUDFunc
def _hangul8192_len_from_bytes(byte_len):
    groups, remainder = ep.f_div(byte_len + SCND_HANGUL_GROUP_BYTES - 1, SCND_HANGUL_GROUP_BYTES)
    return ep.f_mul(groups, SCND_HANGUL_GROUP_CHARS)


@ep.EUDFunc
def _unicode32768_len_from_bytes(byte_len):
    quotient, remainder = ep.f_div(ep.f_mul(byte_len, 8) + SCND_UNICODE_BASE_BITS - 1, SCND_UNICODE_BASE_BITS)
    return quotient


@ep.EUDFunc
def _write_hangul8192_char(value):
    cp = ep.EUDVariable()
    cp << SCND_HANGUL_BASE_CP + value
    ep.f_bwrite(_HANGUL_CHAR + 0, 0xE0 + ep.f_bitrshift(cp, 12))
    ep.f_bwrite(_HANGUL_CHAR + 1, 0x80 + ep.f_bitand(ep.f_bitrshift(cp, 6), 0x3F))
    ep.f_bwrite(_HANGUL_CHAR + 2, 0x80 + ep.f_bitand(cp, 0x3F))
    ep.f_bwrite(_HANGUL_CHAR + 3, 0)


@ep.EUDFunc
def _divmod_hangul_base_bytes5(b0, b1, b2, b3, b4):
    rem = ep.EUDVariable()
    cur = ep.EUDVariable()
    q0 = ep.EUDVariable()
    q1 = ep.EUDVariable()
    q2 = ep.EUDVariable()
    q3 = ep.EUDVariable()
    q4 = ep.EUDVariable()

    rem << 0
    cur << b4
    q4, rem = ep.f_div(cur, SCND_HANGUL_BASE)
    cur << ep.f_mul(rem, 256) + b3
    q3, rem = ep.f_div(cur, SCND_HANGUL_BASE)
    cur << ep.f_mul(rem, 256) + b2
    q2, rem = ep.f_div(cur, SCND_HANGUL_BASE)
    cur << ep.f_mul(rem, 256) + b1
    q1, rem = ep.f_div(cur, SCND_HANGUL_BASE)
    cur << ep.f_mul(rem, 256) + b0
    q0, rem = ep.f_div(cur, SCND_HANGUL_BASE)
    return q0, q1, q2, q3, q4, rem


@ep.EUDFunc
def _write_unicode32768_char(value):
    cp = ep.EUDVariable()
    cp << SCND_UNICODE_HANGUL_CP + value
    if ep.EUDIf()(value >= SCND_UNICODE_HANGUL_COUNT):
        cp << SCND_UNICODE_CJK_CP + value - SCND_UNICODE_HANGUL_COUNT
    ep.EUDEndIf()
    if ep.EUDIf()(value >= SCND_UNICODE_HANGUL_COUNT + SCND_UNICODE_CJK_COUNT):
        cp << SCND_UNICODE_EXTA_CP + value - SCND_UNICODE_HANGUL_COUNT - SCND_UNICODE_CJK_COUNT
    ep.EUDEndIf()
    ep.f_bwrite(_HANGUL_CHAR + 0, 0xE0 + ep.f_bitrshift(cp, 12))
    ep.f_bwrite(_HANGUL_CHAR + 1, 0x80 + ep.f_bitand(ep.f_bitrshift(cp, 6), 0x3F))
    ep.f_bwrite(_HANGUL_CHAR + 2, 0x80 + ep.f_bitand(cp, 0x3F))
    ep.f_bwrite(_HANGUL_CHAR + 3, 0)


@ep.EUDFunc
def _read_hangul8192_header_dwords(player):
    return ep.f_dwread_epd(_hinput_epd(player))


@ep.EUDFunc
def _rotl32(value, bits):
    return ep.f_bitor(ep.f_bitlshift(value, bits), ep.f_bitrshift(value, 32 - bits))


@ep.EUDFunc
def _stream_word(index, nonce, key0, key1, key2, key3):
    x = ep.EUDVariable()
    y = ep.EUDVariable()
    mixed = ep.EUDVariable()
    x << nonce + key0 + ep.f_mul(index + 1, 0x9E3779B9)
    y << key1 + ep.f_mul(index + 3, 0xBB67AE85)
    for round_index in range(SCND_CRYPTO_ROUNDS):
        mixed << ep.f_bitxor(
            ep.f_bitxor(ep.f_bitlshift(y, 4) + key2, y + 0x9E3779B9 * (round_index + 1)),
            ep.f_bitrshift(y, 5) + key3,
        )
        x += mixed
        mixed << ep.f_bitxor(
            ep.f_bitxor(ep.f_bitlshift(x, 4) + key0, x + 0x7F4A7C15 * (round_index + 1)),
            ep.f_bitrshift(x, 5) + key1,
        )
        y += mixed
    return ep.f_bitxor(x, _rotl32(y, 11))


@ep.EUDFunc
def _mac_mix0(mac0, mac1, word, key0, key1):
    mixed = ep.EUDVariable()
    mixed << mac0 + word + key0 + 0xA5A5A5A5
    mixed << ep.f_bitxor(_rotl32(mixed, 5), mac1)
    mixed += ep.f_bitxor(ep.f_bitrshift(word, 7), key1)
    return mixed


@ep.EUDFunc
def _mac_mix1(mac0, mac1, word, key2, key3):
    mixed = ep.EUDVariable()
    mixed << mac1 + ep.f_bitxor(word, key2) + 0x3C6EF372
    mixed << ep.f_bitxor(_rotl32(mixed, 7), mac0)
    mixed += ep.f_bitxor(ep.f_bitlshift(word, 3), key3)
    return mixed


@ep.EUDFunc
def _b64_char(value):
    return ep.f_bread(_B64_ENCODE + value)


@ep.EUDFunc
def _b64_value(ch):
    value = ep.EUDVariable()
    value << ep.f_bread(_B64_DECODE + ch)
    if ep.EUDIf()(value.Exactly(0xFF)):
        value << 0xFFFFFFFF
    ep.EUDEndIf()
    return value


def _read_group_value(group: _PackGroup, player, group_index):
    value = ep.EUDVariable()
    if group.kind == "number":
        value << group.values[player]
    elif group.kind == "global_number":
        value << group.values[0]
    elif group.kind == "array":
        index = ep.f_mul(player, group.array_count) + group.item_index_start + group_index
        value << 0
        if group.slots >= PLAYERS:
            value << group.values[index]
        else:
            if ep.EUDIf()(player <= group.slots - 1):
                value << group.values[index]
            ep.EUDEndIf()
    elif group.kind == "global_array":
        index = group.item_index_start + group_index
        value << group.values[index]
    else:
        raise RuntimeError("Unknown SCND group kind: %s" % group.kind)
    if group.min_value:
        value -= group.min_value
    if group.bits < 32:
        value << ep.f_bitand(value, (1 << group.bits) - 1)
    return value


def _write_group_value(group: _PackGroup, player, group_index, packed_value):
    value = ep.EUDVariable()
    value << packed_value
    if group.min_value:
        value += group.min_value
    if group.kind == "number":
        group.values[player] = value
    elif group.kind == "global_number":
        if ep.EUDIf()(_GLOBAL_VALUES_LOCK[0].Exactly(0)):
            group.values[0] = value
        if ep.EUDElse()():
            current = ep.EUDVariable()
            current << group.values[0]
            if ep.EUDIfNot()(current.Exactly(value)):
                _fail_load_now(player, RESULT_GLOBAL_MISMATCH)
            ep.EUDEndIf()
        ep.EUDEndIf()
    elif group.kind == "array":
        index = ep.f_mul(player, group.array_count) + group.item_index_start + group_index
        if group.slots >= PLAYERS:
            group.values[index] = value
        else:
            if ep.EUDIf()(player <= group.slots - 1):
                group.values[index] = value
            ep.EUDEndIf()
    elif group.kind == "global_array":
        index = group.item_index_start + group_index
        if ep.EUDIf()(_GLOBAL_VALUES_LOCK[0].Exactly(0)):
            group.values[index] = value
        if ep.EUDElse()():
            current = ep.EUDVariable()
            current << group.values[index]
            if ep.EUDIfNot()(current.Exactly(value)):
                _fail_load_now(player, RESULT_GLOBAL_MISMATCH)
            ep.EUDEndIf()
        ep.EUDEndIf()
    else:
        raise RuntimeError("Unknown SCND group kind: %s" % group.kind)


def _pack_group_item(group: _PackGroup, player, group_index):
    bit_offset = ep.EUDVariable()
    word_index = ep.EUDVariable()
    bit_pos = ep.EUDVariable()
    work = _work_epd(player)
    value = _read_group_value(group, player, group_index)
    bit_offset << group.bit_offset + ep.f_mul(group_index, group.bits)
    word_index, bit_pos = ep.f_div(bit_offset, 32)

    if group.bits == 32:
        ep.f_dwwrite_epd(work + SCND_DATA_OFFSET + word_index, value)
        return

    current = ep.f_dwread_epd(work + SCND_DATA_OFFSET + word_index)
    ep.f_dwwrite_epd(work + SCND_DATA_OFFSET + word_index, ep.f_bitor(current, ep.f_bitlshift(value, bit_pos)))
    if ep.EUDIf()(bit_pos + group.bits > 32):
        high = ep.f_bitrshift(value, 32 - bit_pos)
        current = ep.f_dwread_epd(work + SCND_DATA_OFFSET + word_index + 1)
        ep.f_dwwrite_epd(work + SCND_DATA_OFFSET + word_index + 1, ep.f_bitor(current, high))
    ep.EUDEndIf()


def _unpack_group_item(group: _PackGroup, player, group_index):
    bit_offset = ep.EUDVariable()
    word_index = ep.EUDVariable()
    bit_pos = ep.EUDVariable()
    work = _work_epd(player)
    value = ep.EUDVariable()
    bit_offset << group.bit_offset + ep.f_mul(group_index, group.bits)
    word_index, bit_pos = ep.f_div(bit_offset, 32)

    if group.bits == 32:
        value << ep.f_dwread_epd(work + SCND_DATA_OFFSET + word_index)
        _write_group_value(group, player, group_index, value)
        return

    raw0 = ep.f_dwread_epd(work + SCND_DATA_OFFSET + word_index)
    value << ep.f_bitrshift(raw0, bit_pos)
    if ep.EUDIf()(bit_pos + group.bits > 32):
        raw1 = ep.f_dwread_epd(work + SCND_DATA_OFFSET + word_index + 1)
        value << ep.f_bitor(value, ep.f_bitlshift(raw1, 32 - bit_pos))
    ep.EUDEndIf()
    value << ep.f_bitand(value, (1 << group.bits) - 1)
    _write_group_value(group, player, group_index, value)


def _copy_global_plain_to_pending_one(player):
    value = ep.f_dwread_epd(_work_epd(player) + SCND_DATA_OFFSET + _INDEX[player])
    ep.f_dwwrite_epd(_global_pending_epd() + _INDEX[player], value)
    _INDEX[player] += 1


@ep.EUDFunc
def _global_pending_available_for(player):
    available = ep.EUDVariable()
    owner = ep.EUDVariable()
    available << 0
    owner << _GLOBAL_PENDING_OWNER[0]
    if ep.EUDIf()(owner.Exactly(0)):
        available << 1
    if ep.EUDElse()():
        owner -= player
        if ep.EUDIf()(owner.Exactly(1)):
            available << 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    return available


@ep.EUDFunc
def _global_pending_owned_by(player):
    owned = ep.EUDVariable()
    owner = ep.EUDVariable()
    owned << 0
    owner << _GLOBAL_PENDING_OWNER[0]
    owner -= player
    if ep.EUDIf()(owner.Exactly(1)):
        owned << 1
    ep.EUDEndIf()
    return owned


@ep.EUDFunc
def _reserve_global_pending_for(player):
    reserved = ep.EUDVariable()
    owner_value = ep.EUDVariable()
    reserved << 0
    if ep.EUDIf()(_global_pending_available_for(player).AtLeast(1)):
        owner_value << player
        owner_value += 1
        _GLOBAL_PENDING_OWNER[0] = owner_value
        reserved << 1
    ep.EUDEndIf()
    return reserved


@ep.EUDFunc
def _release_global_pending_if_owned(player):
    if ep.EUDIf()(_global_pending_owned_by(player).AtLeast(1)):
        _GLOBAL_PENDING_OWNER[0] = 0
    ep.EUDEndIf()


@ep.EUDFunc
def _load_owned_by(player):
    owned = ep.EUDVariable()
    owner = ep.EUDVariable()
    owned << 0
    owner << _APP_LOAD_OWNER[0]
    owner -= player
    if ep.EUDIf()(owner.Exactly(1)):
        owned << 1
    ep.EUDEndIf()
    return owned


@ep.EUDFunc
def _load_available_for(player):
    available = ep.EUDVariable()
    owner = ep.EUDVariable()
    available << 0
    owner << _APP_LOAD_OWNER[0]
    if ep.EUDIf()(owner.Exactly(0)):
        available << 1
    if ep.EUDElse()():
        owner -= player
        if ep.EUDIf()(owner.Exactly(1)):
            available << 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    return available


@ep.EUDFunc
def _reserve_load_for(player):
    reserved = ep.EUDVariable()
    owner_value = ep.EUDVariable()
    reserved << 0
    if ep.EUDIf()(_load_available_for(player).AtLeast(1)):
        owner_value << player
        owner_value += 1
        _APP_LOAD_OWNER[0] = owner_value
        reserved << 1
    ep.EUDEndIf()
    return reserved


@ep.EUDFunc
def _release_load_if_owned(player):
    if ep.EUDIf()(_load_owned_by(player).AtLeast(1)):
        _APP_LOAD_OWNER[0] = 0
    ep.EUDEndIf()


def _commit_pending_global_group_item(group: _PackGroup, player, group_index):
    bit_offset = ep.EUDVariable()
    word_index = ep.EUDVariable()
    bit_pos = ep.EUDVariable()
    value = ep.EUDVariable()
    bit_offset << group.bit_offset + ep.f_mul(group_index, group.bits)
    word_index, bit_pos = ep.f_div(bit_offset, 32)

    if group.bits == 32:
        value << ep.f_dwread_epd(_global_pending_epd() + word_index)
        _write_group_value(group, player, group_index, value)
        return

    raw0 = ep.f_dwread_epd(_global_pending_epd() + word_index)
    value << ep.f_bitrshift(raw0, bit_pos)
    if ep.EUDIf()(bit_pos + group.bits > 32):
        raw1 = ep.f_dwread_epd(_global_pending_epd() + word_index + 1)
        value << ep.f_bitor(value, ep.f_bitlshift(raw1, 32 - bit_pos))
    ep.EUDEndIf()
    value << ep.f_bitand(value, (1 << group.bits) - 1)
    _write_group_value(group, player, group_index, value)


@ep.EUDFunc
def _commit_pending_global_values(player):
    if ep.EUDIf()(_global_pending_owned_by(player).AtLeast(1)):
        for group in _PACK_GROUPS:
            if _scope_for_kind(group.kind) == _SCOPE_GLOBAL:
                for group_index in range(group.group_count):
                    if ep.EUDIf()(_STATE[player].Exactly(_PHASE_UNPACK)):
                        _commit_pending_global_group_item(group, player, group_index)
                    ep.EUDEndIf()
        if ep.EUDIf()(_STATE[player].Exactly(_PHASE_UNPACK)):
            _GLOBAL_VALUES_LOCK[0] = 1
        ep.EUDEndIf()
    ep.EUDEndIf()


def _write_bits_to_temp(entry: _FlatEntry, player, packed_value):
    word_index = entry.bit_offset // 32
    bit_pos = entry.bit_offset % 32
    temp = _temp_epd(player)
    value = ep.EUDVariable()
    value << packed_value
    if entry.bits < 32:
        value << ep.f_bitand(value, (1 << entry.bits) - 1)

    if entry.bits == 32:
        ep.f_dwwrite_epd(temp + word_index, value)
        return

    if bit_pos + entry.bits <= 32:
        field_mask = ((1 << entry.bits) - 1) << bit_pos
        keep_mask = 0xFFFFFFFF ^ field_mask
        current = ep.EUDVariable()
        current << ep.f_dwread_epd(temp + word_index)
        current << ep.f_bitand(current, keep_mask)
        ep.f_dwwrite_epd(temp + word_index, ep.f_bitor(current, ep.f_bitlshift(value, bit_pos)))
    else:
        low_bits = 32 - bit_pos
        high_bits = entry.bits - low_bits
        low_mask = ((1 << low_bits) - 1) << bit_pos
        high_mask = (1 << high_bits) - 1

        low_value = ep.f_bitlshift(ep.f_bitand(value, (1 << low_bits) - 1), bit_pos)
        current0 = ep.EUDVariable()
        current0 << ep.f_dwread_epd(temp + word_index)
        current0 << ep.f_bitand(current0, 0xFFFFFFFF ^ low_mask)
        ep.f_dwwrite_epd(temp + word_index, ep.f_bitor(current0, low_value))

        high_value = ep.f_bitrshift(value, low_bits)
        current1 = ep.EUDVariable()
        current1 << ep.f_dwread_epd(temp + word_index + 1)
        current1 << ep.f_bitand(current1, 0xFFFFFFFF ^ high_mask)
        ep.f_dwwrite_epd(temp + word_index + 1, ep.f_bitor(current1, ep.f_bitand(high_value, high_mask)))


def _write_varint_value_to_work(player, value):
    cursor = _SUBINDEX[player]
    word = ep.EUDVariable()
    word << value
    if ep.EUDWhile()(word >= SCND_VARINT_THRESHOLD_1):
        ep.f_bwrite(_work_addr(player, SCND_DATA_OFFSET * 4) + cursor, ep.f_bitor(ep.f_bitand(word, 0x7F), 0x80))
        cursor += 1
        word << ep.f_bitrshift(word, 7)
    ep.EUDEndWhile()
    ep.f_bwrite(_work_addr(player, SCND_DATA_OFFSET * 4) + cursor, ep.f_bitand(word, 0x7F))
    cursor += 1
    _SUBINDEX[player] = cursor


def _read_varint_value_from_work(player):
    cursor = _SAVED_VALUE_CURSOR[player]
    value = ep.EUDVariable()
    shift = ep.EUDVariable()
    read_count = ep.EUDVariable()
    keep_reading = ep.EUDVariable()
    value << 0
    shift << 0
    read_count << 0
    keep_reading << 1
    if ep.EUDWhile()([keep_reading.AtLeast(1), read_count < 5]):
        byte = ep.f_bread(_work_addr(player, SCND_DATA_OFFSET * 4) + cursor)
        value += ep.f_bitlshift(ep.f_bitand(byte, 0x7F), shift)
        cursor += 1
        read_count += 1
        if ep.EUDIf()(byte < 0x80):
            keep_reading << 0
        if ep.EUDElse()():
            shift += 7
        ep.EUDEndIf()
    ep.EUDEndWhile()
    if ep.EUDIf()(keep_reading.AtLeast(1)):
        _STATE[player] = _PHASE_FAILED
        _RESULT[player] = RESULT_BAD_CODE
        _MESSAGE[player] = MESSAGE_LOAD_FAILED
    ep.EUDEndIf()
    _SAVED_VALUE_CURSOR[player] = cursor
    return value


def _compat_mode1_value_start(count):
    return 4 + count


def _compat_mode2_value_start(count):
    return 4 + ep.f_mul(count, 5)


def _fail_bad_compat_payload(player):
    _STATE[player] = _PHASE_FAILED
    _RESULT[player] = RESULT_BAD_CODE
    _MESSAGE[player] = MESSAGE_LOAD_FAILED


def _compat_init_save_payload(player):
    if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
        ep.f_dwwrite_epd(_work_epd(player) + SCND_DATA_OFFSET, _PLAYER_FIELD_COUNT)
        _SUBINDEX[player] = _compat_bits_metadata_bytes(_PLAYER_FIELD_COUNT)
    elif _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
        ep.f_dwwrite_epd(_work_epd(player) + SCND_DATA_OFFSET, _PLAYER_FIELD_COUNT)
        _SUBINDEX[player] = _compat_key_bits_metadata_bytes(_PLAYER_FIELD_COUNT)


@ep.EUDFunc
def _pack_compat_step(player):
    idx = _INDEX[player]
    if ep.EUDIf()(idx.Exactly(0)):
        _compat_init_save_payload(player)
    ep.EUDEndIf()

    key_hashes = None
    if _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
        key_hashes = _player_key_hash_table()

    for group in _player_pack_groups():
        ordinal_start = _player_ordinal_at_flat_index(group.start_index)
        if ep.EUDIf()([idx >= group.start_index, idx < group.start_index + group.group_count]):
            group_index = idx - group.start_index
            ordinal = ordinal_start + group_index
            value = _read_group_value(group, player, group_index)
            if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
                ep.f_bwrite(_work_addr(player, SCND_DATA_OFFSET * 4 + 4) + ordinal, group.bits)
            elif _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
                ep.f_dwwrite_epd(_work_epd(player) + SCND_DATA_OFFSET + 1 + ordinal, key_hashes[ordinal])
                ep.f_bwrite(_work_addr(player, SCND_DATA_OFFSET * 4 + 4 + _PLAYER_FIELD_COUNT * 4) + ordinal, group.bits)
            _write_varint_value_to_work(player, value)
        ep.EUDEndIf()
    _INDEX[player] = idx + 1


@ep.EUDFunc
def _compat_copy_default_dword_to_temp(player):
    default_dwords = _player_default_dwords_table()
    ep.f_dwwrite_epd(_temp_epd(player) + _INDEX[player], default_dwords[_INDEX[player]])
    _INDEX[player] += 1


@ep.EUDFunc
def _compat_convert_bits_step(player):
    idx = _INDEX[player]
    saved_width = ep.EUDVariable()
    value = ep.EUDVariable()
    saved_width << ep.f_bread(_work_addr(player, SCND_DATA_OFFSET * 4 + 4) + idx)
    value << _read_varint_value_from_work(player)
    player_entries = _player_entries()
    for ordinal, entry in enumerate(player_entries):
        if ep.EUDIf()(idx.Exactly(ordinal)):
            if ep.EUDIf()([saved_width.AtMost(0), _STATE[player].Exactly(_PHASE_COMPAT_CONVERT)]):
                _STATE[player] = _PHASE_FAILED
                _RESULT[player] = RESULT_BAD_BINDING
                _MESSAGE[player] = MESSAGE_LOAD_FAILED
            ep.EUDEndIf()
            if ep.EUDIf()([_STATE[player].Exactly(_PHASE_COMPAT_CONVERT), saved_width > entry.bits]):
                _STATE[player] = _PHASE_FAILED
                _RESULT[player] = RESULT_BAD_BINDING
                _MESSAGE[player] = MESSAGE_LOAD_FAILED
            if ep.EUDElse()():
                if ep.EUDIf()(_STATE[player].Exactly(_PHASE_COMPAT_CONVERT)):
                    _write_bits_to_temp(entry, player, value)
                ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()
    _INDEX[player] = idx + 1


@ep.EUDFunc
def _compat_convert_key_bits_step(player):
    idx = _INDEX[player]
    key_hash = ep.EUDVariable()
    saved_width = ep.EUDVariable()
    value = ep.EUDVariable()
    matched = ep.EUDVariable()
    key_hash << ep.f_dwread_epd(_work_epd(player) + SCND_DATA_OFFSET + 1 + idx)
    saved_width << ep.f_bread(_work_addr(player, SCND_DATA_OFFSET * 4 + 4 + ep.f_mul(_SAVED_FIELD_COUNT[player], 4)) + idx)
    value << _read_varint_value_from_work(player)
    matched << 0
    for entry in _player_entries():
        if ep.EUDIf()(key_hash.Exactly(_entry_key_hash(entry))):
            matched << 1
            if ep.EUDIf()([saved_width.AtMost(0), _STATE[player].Exactly(_PHASE_COMPAT_CONVERT)]):
                _STATE[player] = _PHASE_FAILED
                _RESULT[player] = RESULT_BAD_BINDING
                _MESSAGE[player] = MESSAGE_LOAD_FAILED
            ep.EUDEndIf()
            if ep.EUDIf()([_STATE[player].Exactly(_PHASE_COMPAT_CONVERT), saved_width > entry.bits]):
                _STATE[player] = _PHASE_FAILED
                _RESULT[player] = RESULT_BAD_BINDING
                _MESSAGE[player] = MESSAGE_LOAD_FAILED
            if ep.EUDElse()():
                if ep.EUDIf()(_STATE[player].Exactly(_PHASE_COMPAT_CONVERT)):
                    _write_bits_to_temp(entry, player, value)
                ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()
    _INDEX[player] = idx + 1


@ep.EUDFunc
def _compat_copy_temp_to_work_one(player):
    value = ep.f_dwread_epd(_temp_epd(player) + _INDEX[player])
    ep.f_dwwrite_epd(_work_epd(player) + SCND_DATA_OFFSET + _INDEX[player], value)
    _INDEX[player] += 1


@ep.EUDFunc
def _pack_step(player):
    idx = _INDEX[player]
    matched = ep.EUDVariable()
    matched << 0
    for group in _PACK_GROUPS:
        if ep.EUDIf()([idx >= group.start_index, idx < group.start_index + group.group_count]):
            matched << 1
            if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_scope_for_kind(group.kind))):
                _pack_group_item(group, player, idx - group.start_index)
            ep.EUDEndIf()
            _INDEX[player] = idx + 1
        ep.EUDEndIf()
    if ep.EUDIf()(matched.Exactly(0)):
        _INDEX[player] = idx + 1
    ep.EUDEndIf()


@ep.EUDFunc
def _unpack_step(player):
    idx = _INDEX[player]
    matched = ep.EUDVariable()
    matched << 0
    for group in _PACK_GROUPS:
        if ep.EUDIf()([idx >= group.start_index, idx < group.start_index + group.group_count]):
            matched << 1
            if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_scope_for_kind(group.kind))):
                _unpack_group_item(group, player, idx - group.start_index)
            ep.EUDEndIf()
            _INDEX[player] = idx + 1
        ep.EUDEndIf()
    if ep.EUDIf()(matched.Exactly(0)):
        _INDEX[player] = idx + 1
    ep.EUDEndIf()


@ep.EUDFunc
def _write_header(player):
    work = _work_epd(player)
    ep.f_dwwrite_epd(work + 0, SCND_MAGIC)
    ep.f_dwwrite_epd(work + 1, _ACTIVE_SCHEMA_HASH[player])
    ep.f_dwwrite_epd(work + 2, _ACTIVE_TOTAL_BITS[player])
    ep.f_dwwrite_epd(work + 3, _NONCE[player])
    ep.f_dwwrite_epd(work + 4, _PLAIN_DWORDS[player])
    ep.f_dwwrite_epd(work + 5, _FLAGS[player])


@ep.EUDFunc
def _init_mac(player):
    _MAC0[player] = ep.f_bitxor(_KEY0[player], 0x243F6A88)
    _MAC1[player] = ep.f_bitxor(_KEY1[player], 0x85A308D3)
    for i in range(SCND_HEADER_DWORDS):
        word = ep.f_dwread_epd(_work_epd(player) + i)
        _MAC0[player] = _mac_mix0(_MAC0[player], _MAC1[player], word, _KEY0[player], _KEY1[player])
        _MAC1[player] = _mac_mix1(_MAC0[player], _MAC1[player], word, _KEY2[player], _KEY3[player])


@ep.EUDFunc
def _record_compress_stats(player):
    ratio = ep.EUDVariable()
    remainder = ep.EUDVariable()
    ratio << 0
    _LAST_CODEC[player] = _flag_codec(_FLAGS[player])
    _LAST_RAW_DWORDS[player] = _PLAIN_DWORDS[player]
    _LAST_PACKED_DWORDS[player] = _PAYLOAD_DWORDS[player]
    if ep.EUDIf()(_PLAIN_DWORDS[player].AtLeast(1)):
        ratio, remainder = ep.f_div(ep.f_mul(_PAYLOAD_DWORDS[player], 100), _PLAIN_DWORDS[player])
        if ep.EUDIf()([_PAYLOAD_DWORDS[player].AtLeast(1), ratio.Exactly(0)]):
            ratio << 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    _LAST_RATIO_PCT[player] = ratio


@ep.EUDFunc
def _reset_profile(player):
    _BEST_LEN[player] = _PLAIN_DWORDS[player]
    _BEST_CODEC[player] = SCND_CODEC_RAW
    _BEST_PARAMS[player] = 0
    _PROFILE_ZERO_RUNS[player] = 0
    _PROFILE_NONZERO_DWORDS[player] = 0
    _PROFILE_PREV_IS_ZERO[player] = 0
    _PROFILE_FIRST_DWORD[player] = 0
    _PROFILE_ALL_SAME[player] = 1
    _PROFILE_VARINT_BYTES[player] = 0


@ep.EUDFunc
def _profile_one_dword(player):
    work = _work_epd(player)
    data_index = _INDEX[player]
    word = ep.EUDVariable()
    word << ep.f_dwread_epd(work + SCND_DATA_OFFSET + data_index)

    if ep.EUDIf()(word.Exactly(0)):
        if ep.EUDIf()(_PROFILE_PREV_IS_ZERO[player].Exactly(0)):
            _PROFILE_ZERO_RUNS[player] += 1
        ep.EUDEndIf()
        _PROFILE_PREV_IS_ZERO[player] = 1
    if ep.EUDElse()():
        _PROFILE_NONZERO_DWORDS[player] += 1
        _PROFILE_PREV_IS_ZERO[player] = 0
    ep.EUDEndIf()

    # Accumulate the varint byte length for this dword: 1 byte for [0,0x80),
    # 2 for [0x80,0x4000), 3 for [0x4000,0x200000),
    # 4 for [0x200000,0x10000000), else 5.
    if ep.EUDIf()(word < SCND_VARINT_THRESHOLD_1):
        _PROFILE_VARINT_BYTES[player] += 1
    if ep.EUDElseIf()(word < SCND_VARINT_THRESHOLD_2):
        _PROFILE_VARINT_BYTES[player] += 2
    if ep.EUDElseIf()(word < SCND_VARINT_THRESHOLD_3):
        _PROFILE_VARINT_BYTES[player] += 3
    if ep.EUDElseIf()(word < SCND_VARINT_THRESHOLD_4):
        _PROFILE_VARINT_BYTES[player] += 4
    if ep.EUDElse()():
        _PROFILE_VARINT_BYTES[player] += 5
    ep.EUDEndIf()

    if ep.EUDIf()(data_index.Exactly(0)):
        _PROFILE_FIRST_DWORD[player] = word
    if ep.EUDElse()():
        if ep.EUDIf()(word != _PROFILE_FIRST_DWORD[player]):
            _PROFILE_ALL_SAME[player] = 0
        ep.EUDEndIf()
    ep.EUDEndIf()

    _INDEX[player] = data_index + 1


@ep.EUDFunc
def _finish_profile(player):
    rle_len = ep.EUDVariable()
    rle_len << _PROFILE_NONZERO_DWORDS[player] + ep.f_mul(_PROFILE_ZERO_RUNS[player], 2)
    # Convert varint byte count to payload dword count: ceil(bytes / 4).
    # The +3 then divide-by-4 keeps the gate honest about padding overhead.
    varint_dwords = ep.EUDVariable()
    varint_dwords << ep.f_div(_PROFILE_VARINT_BYTES[player] + 3, 4)[0]
    _BEST_LEN[player] = _PLAIN_DWORDS[player]
    _BEST_CODEC[player] = SCND_CODEC_RAW
    _BEST_PARAMS[player] = 0
    _PAYLOAD_DWORDS[player] = _PLAIN_DWORDS[player]

    if ep.EUDIf()(_PROFILE_ALL_SAME[player].Exactly(1)):
        _set_flag(player, SCND_CODEC_FILL)
        _PAYLOAD_DWORDS[player] = 1
        _record_compress_stats(player)
        _STATE[player] = _PHASE_WRITE_HEADER
        _INDEX[player] = 0
    # VARINT wins when its dword count beats both ZERO_RLE and RAW. We compare
    # against rle_len (which is set even when rle_len >= packed) and packed.
    if ep.EUDElseIf()([varint_dwords < _PLAIN_DWORDS[player], varint_dwords < rle_len]):
        _BEST_CODEC[player] = SCND_CODEC_VARINT
        _BEST_LEN[player] = varint_dwords
        _BEST_PARAMS[player] = _PROFILE_VARINT_BYTES[player]
        _set_flag(player, SCND_CODEC_VARINT, _PROFILE_VARINT_BYTES[player])
        _STATE[player] = _PHASE_TRIAL_LOOP
        _INDEX[player] = 0
        _SUBINDEX[player] = 0
        _ACC[player] = 0
    if ep.EUDElseIf()(rle_len < _PLAIN_DWORDS[player]):
        _set_flag(player, SCND_CODEC_ZERO_RLE)
        _STATE[player] = _PHASE_TRIAL_LOOP
        _INDEX[player] = 0
        _SUBINDEX[player] = 0
        _ACC[player] = 0
    if ep.EUDElse()():
        _set_flag(player, SCND_CODEC_RAW)
        _record_compress_stats(player)
        _STATE[player] = _PHASE_WRITE_HEADER
        _INDEX[player] = 0
    ep.EUDEndIf()


@ep.EUDFunc
def _finish_trial(player):
    _finish_compress(player)
    _BEST_CODEC[player] = _flag_codec(_FLAGS[player])
    _BEST_PARAMS[player] = _flag_params(_FLAGS[player])
    _BEST_LEN[player] = _PAYLOAD_DWORDS[player]
    _record_compress_stats(player)


@ep.EUDFunc
def _crypt_one_dword(player, data_index):
    work = _work_epd(player)
    word = ep.f_dwread_epd(work + SCND_DATA_OFFSET + data_index)
    stream = _stream_word(data_index, _NONCE[player], _KEY0[player], _KEY1[player], _KEY2[player], _KEY3[player])
    word << ep.f_bitxor(word, stream)
    ep.f_dwwrite_epd(work + SCND_DATA_OFFSET + data_index, word)
    _MAC0[player] = _mac_mix0(_MAC0[player], _MAC1[player], word, _KEY0[player], _KEY1[player])
    _MAC1[player] = _mac_mix1(_MAC0[player], _MAC1[player], word, _KEY2[player], _KEY3[player])


@ep.EUDFunc
def _decrypt_one_dword(player, data_index):
    work = _work_epd(player)
    word = ep.f_dwread_epd(work + SCND_DATA_OFFSET + data_index)
    stream = _stream_word(data_index, _NONCE[player], _KEY0[player], _KEY1[player], _KEY2[player], _KEY3[player])
    word << ep.f_bitxor(word, stream)
    ep.f_dwwrite_epd(work + SCND_DATA_OFFSET + data_index, word)


@ep.EUDFunc
def _compress_one_dword(player):
    work = _work_epd(player)
    temp = _temp_epd(player)
    source_index = _INDEX[player]
    out_index = _SUBINDEX[player]
    run_count = _ACC[player]
    word = ep.EUDVariable()
    abort = ep.EUDVariable()
    abort << 0

    if ep.EUDIf()(source_index < _PLAIN_DWORDS[player]):
        word << ep.f_dwread_epd(work + SCND_DATA_OFFSET + source_index)
        if ep.EUDIf()(word.Exactly(0)):
            run_count += 1
        if ep.EUDElse()():
            if ep.EUDIf()(run_count.AtLeast(1)):
                if ep.EUDIf()(out_index + 2 >= _PLAIN_DWORDS[player]):
                    _set_flag(player, SCND_CODEC_RAW)
                    _PAYLOAD_DWORDS[player] = _PLAIN_DWORDS[player]
                    source_index << _PLAIN_DWORDS[player]
                    run_count << 0
                    abort << 1
                ep.EUDEndIf()
                if ep.EUDIf()(abort.Exactly(0)):
                    ep.f_dwwrite_epd(temp + out_index, 0)
                    ep.f_dwwrite_epd(temp + out_index + 1, run_count)
                    out_index += 2
                    run_count << 0
                ep.EUDEndIf()
            ep.EUDEndIf()
            if ep.EUDIf()([abort.Exactly(0), out_index + 1 >= _PLAIN_DWORDS[player]]):
                _set_flag(player, SCND_CODEC_RAW)
                _PAYLOAD_DWORDS[player] = _PLAIN_DWORDS[player]
                source_index << _PLAIN_DWORDS[player]
                run_count << 0
                abort << 1
            ep.EUDEndIf()
            if ep.EUDIf()(abort.Exactly(0)):
                ep.f_dwwrite_epd(temp + out_index, word)
                out_index += 1
            ep.EUDEndIf()
        ep.EUDEndIf()
        if ep.EUDIf()(abort.Exactly(0)):
            source_index += 1
        ep.EUDEndIf()
    ep.EUDEndIf()

    _INDEX[player] = source_index
    _SUBINDEX[player] = out_index
    _ACC[player] = run_count


@ep.EUDFunc
def _finish_compress(player):
    temp = _temp_epd(player)
    out_index = _SUBINDEX[player]
    run_count = _ACC[player]
    abort = ep.EUDVariable()
    abort << 0
    if ep.EUDIf()(run_count.AtLeast(1)):
        if ep.EUDIf()(out_index + 2 >= _PLAIN_DWORDS[player]):
            _set_flag(player, SCND_CODEC_RAW)
            _PAYLOAD_DWORDS[player] = _PLAIN_DWORDS[player]
            _ACC[player] = 0
            abort << 1
        ep.EUDEndIf()
        if ep.EUDIf()(abort.Exactly(0)):
            ep.f_dwwrite_epd(temp + out_index, 0)
            ep.f_dwwrite_epd(temp + out_index + 1, run_count)
            out_index += 2
        ep.EUDEndIf()
    ep.EUDEndIf()
    _ACC[player] = 0
    if ep.EUDIf()([abort.Exactly(0), out_index < _PLAIN_DWORDS[player]]):
        _set_flag(player, SCND_CODEC_ZERO_RLE)
        _PAYLOAD_DWORDS[player] = out_index
    if ep.EUDElse()():
        _set_flag(player, SCND_CODEC_RAW)
        _PAYLOAD_DWORDS[player] = _PLAIN_DWORDS[player]
    ep.EUDEndIf()


@ep.EUDFunc
def _copy_temp_to_payload_one(player, index):
    value = ep.f_dwread_epd(_temp_epd(player) + index)
    ep.f_dwwrite_epd(_work_epd(player) + SCND_DATA_OFFSET + index, value)


@ep.EUDFunc
def _copy_sync_receive_to_work_one(player, index):
    value = ep.f_dwread_epd(_temp_epd(player) + index)
    ep.f_dwwrite_epd(_work_epd(player) + index, value)


@ep.EUDFunc
def _expand_fill_one_dword(player, index):
    work = _work_epd(player)
    value = ep.f_dwread_epd(work + SCND_DATA_OFFSET)
    ep.f_dwwrite_epd(work + SCND_DATA_OFFSET + index, value)


# region: codec VARINT ??EUD encoder/decoder  -------------------------------
# Encoder consumes one source dword per call and appends its 1..5 varint bytes
# to _temp via byte-addressed writes. _INDEX tracks source dword position;
# _SUBINDEX tracks the output byte cursor (saved as params24 at finish).
#
# Decoder consumes one varint-encoded value per call from _work and writes the
# decoded dword to _temp. _INDEX tracks the input byte cursor (relative to the
# data section base); _SUBINDEX tracks the output dword position.

@ep.EUDFunc
def _compress_varint_one_dword(player):
    work = _work_epd(player)
    source_index = _INDEX[player]
    out_index = _SUBINDEX[player]
    word = ep.EUDVariable()
    word << ep.f_dwread_epd(work + SCND_DATA_OFFSET + source_index)
    if ep.EUDWhile()(word >= SCND_VARINT_THRESHOLD_1):
        ep.f_bwrite(_temp_addr(player, out_index),
                    ep.f_bitor(ep.f_bitand(word, 0x7F), 0x80))
        out_index += 1
        word << ep.f_bitrshift(word, 7)
    ep.EUDEndWhile()
    ep.f_bwrite(_temp_addr(player, out_index), ep.f_bitand(word, 0x7F))
    out_index += 1
    source_index += 1
    _INDEX[player] = source_index
    _SUBINDEX[player] = out_index


@ep.EUDFunc
def _finish_varint(player):
    """Wraps up a varint encode pass: payload dwords = ceil(bytes/4)."""
    byte_count = _SUBINDEX[player]
    # Zero-pad the trailing dword slot so MAC and base64 see deterministic bytes.
    pad_index = ep.EUDVariable()
    pad_index << byte_count
    if ep.EUDWhile()(ep.f_bitand(pad_index, 3) != 0):
        ep.f_bwrite(_temp_addr(player, pad_index), 0)
        pad_index += 1
    ep.EUDEndWhile()
    payload_dwords, _rem = ep.f_div(byte_count + 3, 4)
    _PAYLOAD_DWORDS[player] = payload_dwords
    _set_flag(player, SCND_CODEC_VARINT, byte_count)


@ep.EUDFunc
def _decompress_varint_one_dword(player):
    """Read one varint-encoded dword from _work and write it to _temp."""
    in_byte_index = _INDEX[player]
    out_dword_index = _SUBINDEX[player]
    accumulator = ep.EUDVariable()
    shift = ep.EUDVariable()
    cont = ep.EUDVariable()
    accumulator << 0
    shift << 0
    cont << 1
    base = _work_addr(player, SCND_DATA_OFFSET * 4)
    if ep.EUDWhile()(cont.AtLeast(1)):
        byte = ep.f_bread(base + in_byte_index)
        accumulator += ep.f_bitlshift(ep.f_bitand(byte, 0x7F), shift)
        in_byte_index += 1
        if ep.EUDIf()(byte < 0x80):
            cont << 0
        if ep.EUDElse()():
            shift += 7
        ep.EUDEndIf()
    ep.EUDEndWhile()
    ep.f_dwwrite_epd(_temp_epd(player) + out_dword_index, accumulator)
    out_dword_index += 1
    _INDEX[player] = in_byte_index
    _SUBINDEX[player] = out_dword_index

# endregion: codec VARINT ??EUD encoder/decoder  ----------------------------


@ep.EUDFunc
def _decompress_one_step(player):
    work = _work_epd(player)
    temp = _temp_epd(player)
    payload_index = _INDEX[player]
    out_index = _SUBINDEX[player]
    run_left = _ACC[player]
    token = ep.EUDVariable()
    count = ep.EUDVariable()

    if ep.EUDIf()(run_left.AtLeast(1)):
        ep.f_dwwrite_epd(temp + out_index, 0)
        out_index += 1
        run_left -= 1
    if ep.EUDElse()():
        if ep.EUDIf()(payload_index < _PAYLOAD_DWORDS[player]):
            token << ep.f_dwread_epd(work + SCND_DATA_OFFSET + payload_index)
            payload_index += 1
            if ep.EUDIf()(token.Exactly(0)):
                if ep.EUDIf()(payload_index < _PAYLOAD_DWORDS[player]):
                    count << ep.f_dwread_epd(work + SCND_DATA_OFFSET + payload_index)
                    payload_index += 1
                    if ep.EUDIf()(count.AtLeast(1)):
                        ep.f_dwwrite_epd(temp + out_index, 0)
                        out_index += 1
                        run_left << count - 1
                    if ep.EUDElse()():
                        _STATE[player] = _PHASE_FAILED
                        _RESULT[player] = RESULT_BAD_CODE
                        _MESSAGE[player] = MESSAGE_LOAD_FAILED
                    ep.EUDEndIf()
                if ep.EUDElse()():
                    _STATE[player] = _PHASE_FAILED
                    _RESULT[player] = RESULT_BAD_CODE
                    _MESSAGE[player] = MESSAGE_LOAD_FAILED
                ep.EUDEndIf()
            if ep.EUDElse()():
                ep.f_dwwrite_epd(temp + out_index, token)
                out_index += 1
            ep.EUDEndIf()
        if ep.EUDElse()():
            _STATE[player] = _PHASE_FAILED
            _RESULT[player] = RESULT_BAD_CODE
            _MESSAGE[player] = MESSAGE_LOAD_FAILED
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(out_index > _PLAIN_DWORDS[player]):
        _STATE[player] = _PHASE_FAILED
        _RESULT[player] = RESULT_BAD_CODE
        _MESSAGE[player] = MESSAGE_LOAD_FAILED
    ep.EUDEndIf()

    _INDEX[player] = payload_index
    _SUBINDEX[player] = out_index
    _ACC[player] = run_left


@ep.EUDFunc
def _encode_step(player):
    total_bytes = (SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS) * 4
    byte_index = _INDEX[player]
    out_index = _SUBINDEX[player]
    b0 = ep.EUDVariable()
    b1 = ep.EUDVariable()
    b2 = ep.EUDVariable()
    c0 = ep.EUDVariable()
    c1 = ep.EUDVariable()
    c2 = ep.EUDVariable()
    c3 = ep.EUDVariable()

    if ep.EUDIf()(byte_index < total_bytes):
        b0 << ep.f_bread(_work_addr(player, byte_index))
        b1 << 0
        b2 << 0
        if ep.EUDIf()(byte_index + 1 < total_bytes):
            b1 << ep.f_bread(_work_addr(player, byte_index + 1))
        ep.EUDEndIf()
        if ep.EUDIf()(byte_index + 2 < total_bytes):
            b2 << ep.f_bread(_work_addr(player, byte_index + 2))
        ep.EUDEndIf()

        c0 << ep.f_bitrshift(b0, 2)
        c1 << ep.f_bitor(ep.f_bitlshift(ep.f_bitand(b0, 3), 4), ep.f_bitrshift(b1, 4))
        c2 << ep.f_bitor(ep.f_bitlshift(ep.f_bitand(b1, 15), 2), ep.f_bitrshift(b2, 6))
        c3 << ep.f_bitand(b2, 63)

        ep.f_bwrite(_code_addr(player, out_index), _b64_char(c0))
        ep.f_bwrite(_code_addr(player, out_index + 1), _b64_char(c1))
        if ep.EUDIf()(byte_index + 1 < total_bytes):
            ep.f_bwrite(_code_addr(player, out_index + 2), _b64_char(c2))
            out_index += 1
        ep.EUDEndIf()
        if ep.EUDIf()(byte_index + 2 < total_bytes):
            ep.f_bwrite(_code_addr(player, out_index + 2), _b64_char(c3))
            out_index += 1
        ep.EUDEndIf()

        _INDEX[player] = byte_index + 3
        _SUBINDEX[player] = out_index + 2
    ep.EUDEndIf()


@ep.EUDFunc
def _decode_step(player):
    in_len = _INPUT_LEN[player]
    in_index = _INDEX[player]
    out_index = _DECODE_OUT[player]
    acc = _ACC[player]
    acc_bits = _ACC_BITS[player]
    ch = ep.EUDVariable()
    value = ep.EUDVariable()
    out_byte = ep.EUDVariable()

    if ep.EUDIf()(in_index < in_len):
        ch << ep.f_bread(_input_addr(player, in_index))
        value << _b64_value(ch)
        if ep.EUDIf()(value.Exactly(0xFFFFFFFF)):
            _STATE[player] = _PHASE_FAILED
            _RESULT[player] = RESULT_BAD_CODE
            _MESSAGE[player] = MESSAGE_LOAD_FAILED
        if ep.EUDElse()():
            acc << ep.f_bitor(ep.f_bitlshift(acc, 6), value)
            acc_bits += 6
            if ep.EUDIf()(acc_bits >= 8):
                acc_bits -= 8
                out_byte << ep.f_bitand(ep.f_bitrshift(acc, acc_bits), 0xFF)
                if ep.EUDIf()(out_index < SCND_MAX_DWORDS * 4):
                    ep.f_bwrite(_work_addr(player, out_index), out_byte)
                    out_index += 1
                if ep.EUDElse()():
                    _STATE[player] = _PHASE_FAILED
                    _RESULT[player] = RESULT_TOO_LARGE
                    _MESSAGE[player] = MESSAGE_LOAD_FAILED
                ep.EUDEndIf()
            ep.EUDEndIf()
            _INDEX[player] = in_index + 1
            _DECODE_OUT[player] = out_index
            _ACC[player] = ep.f_bitand(acc, 0x00FFFFFF)
            _ACC_BITS[player] = acc_bits
        ep.EUDEndIf()
    ep.EUDEndIf()


@ep.EUDFunc
def _hangul8192_decode_step(player):
    in_index = _INDEX[player]
    out_index = _DECODE_OUT[player]
    target_bytes = _HANGUL_INPUT_TARGET_BYTES[player]
    d0 = ep.EUDVariable()
    d1 = ep.EUDVariable()
    d2 = ep.EUDVariable()
    n0 = ep.EUDVariable()
    n1 = ep.EUDVariable()
    n2 = ep.EUDVariable()
    n3 = ep.EUDVariable()
    n4 = ep.EUDVariable()
    cur = ep.EUDVariable()
    carry = ep.EUDVariable()
    bad = ep.EUDVariable()

    d0 << ep.f_dwread_epd(_hinput_epd(player) + in_index)
    d1 << ep.f_dwread_epd(_hinput_epd(player) + in_index + 1)
    d2 << ep.f_dwread_epd(_hinput_epd(player) + in_index + 2)
    bad << 0
    if ep.EUDIf()(d0 >= SCND_HANGUL_BASE):
        bad << 1
    ep.EUDEndIf()
    if ep.EUDIf()(d1 >= SCND_HANGUL_BASE):
        bad << 1
    ep.EUDEndIf()
    if ep.EUDIf()(d2 >= SCND_HANGUL_BASE):
        bad << 1
    ep.EUDEndIf()
    if ep.EUDIf()(bad.AtLeast(1)):
        _STATE[player] = _PHASE_FAILED
        _RESULT[player] = RESULT_BAD_CODE
        _MESSAGE[player] = MESSAGE_LOAD_FAILED
    if ep.EUDElse()():
        n0 << ep.f_bitand(d2, 0xFF)
        n1 << ep.f_bitrshift(d2, 8)
        n2 << 0
        n3 << 0
        n4 << 0

        cur << ep.f_mul(n0, SCND_HANGUL_BASE) + d1
        n0 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n1, SCND_HANGUL_BASE) + carry
        n1 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n2, SCND_HANGUL_BASE) + carry
        n2 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n3, SCND_HANGUL_BASE) + carry
        n3 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n4, SCND_HANGUL_BASE) + carry
        n4 << ep.f_bitand(cur, 0xFF)

        cur << ep.f_mul(n0, SCND_HANGUL_BASE) + d0
        n0 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n1, SCND_HANGUL_BASE) + carry
        n1 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n2, SCND_HANGUL_BASE) + carry
        n2 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n3, SCND_HANGUL_BASE) + carry
        n3 << ep.f_bitand(cur, 0xFF)
        carry << ep.f_bitrshift(cur, 8)
        cur << ep.f_mul(n4, SCND_HANGUL_BASE) + carry
        n4 << ep.f_bitand(cur, 0xFF)

        for byte_value in (n0, n1, n2, n3, n4):
            if ep.EUDIf()(out_index < target_bytes):
                if ep.EUDIf()(out_index < SCND_MAX_DWORDS * 4):
                    ep.f_bwrite(_work_addr(player, out_index), byte_value)
                    out_index += 1
                if ep.EUDElse()():
                    _STATE[player] = _PHASE_FAILED
                    _RESULT[player] = RESULT_TOO_LARGE
                    _MESSAGE[player] = MESSAGE_LOAD_FAILED
                ep.EUDEndIf()
            ep.EUDEndIf()
        _INDEX[player] = in_index + SCND_HANGUL_GROUP_CHARS
        _DECODE_OUT[player] = out_index
    ep.EUDEndIf()


def _accept_exact_player_prefix_header(player, schema_hash, total_bits, packed_dwords):
    accepted = ep.EUDVariable()
    idx = ep.EUDVariable()
    name_hash = ep.EUDVariable()
    accepted << 0
    idx << 0
    if not _APP_CONFIGURED or not _APP_MAP_ID or not _APP_MAKER_KEY:
        return accepted

    (
        prefix_count,
        schema_hashes,
        total_bit_values,
        packed_dword_values,
        key0_values,
        key1_values,
        key2_values,
        key3_values,
    ) = _player_exact_prefix_tables()
    if prefix_count <= 0:
        return accepted

    name_hash << _player_name_hash(player)
    for _ in ep.EUDLoopRange(prefix_count):
        if ep.EUDIf()([
            accepted.Exactly(0),
            schema_hash.Exactly(schema_hashes[idx]),
            total_bits.Exactly(total_bit_values[idx]),
            packed_dwords.Exactly(packed_dword_values[idx]),
        ]):
            accepted << 1
            _KEY0[player] = ep.f_bitxor(key0_values[idx], name_hash)
            _KEY1[player] = ep.f_bitxor(key1_values[idx], _rotl32(name_hash, 7))
            _KEY2[player] = ep.f_bitxor(key2_values[idx], ep.f_mul(name_hash, 0x9E3779B1))
            _KEY3[player] = ep.f_bitxor(key3_values[idx], _rotl32(name_hash, 17))
        ep.EUDEndIf()
        idx += 1

    return accepted


@ep.EUDFunc
def _validate_loaded_header(player):
    work = _work_epd(player)
    decoded_dwords = ep.EUDVariable()
    payload_dwords = ep.EUDVariable()
    codec_id = ep.EUDVariable()
    params = ep.EUDVariable()
    saved_schema_hash = ep.EUDVariable()
    saved_total_bits = ep.EUDVariable()
    saved_packed_dwords = ep.EUDVariable()
    quotient = ep.EUDVariable()
    remainder = ep.EUDVariable()
    ok = ep.EUDVariable()
    ok << 1
    quotient, remainder = ep.f_div(_DECODE_OUT[player], 4)
    decoded_dwords << quotient
    if ep.EUDIfNot()(remainder.Exactly(0)):
        ok << 0
        _RESULT[player] = RESULT_BAD_CODE
    ep.EUDEndIf()
    if ep.EUDIf()(ok.AtLeast(1)):
        if ep.EUDIfNot()(decoded_dwords.AtLeast(SCND_HEADER_DWORDS + SCND_TAG_DWORDS)):
            ok << 0
            _RESULT[player] = RESULT_BAD_CODE
        ep.EUDEndIf()
    ep.EUDEndIf()
    if ep.EUDIf()(ok.AtLeast(1)):
        if ep.EUDIfNot()(ep.f_dwread_epd(work + 0).Exactly(SCND_MAGIC)):
            ok << 0
            _RESULT[player] = RESULT_BAD_CODE
        ep.EUDEndIf()
    ep.EUDEndIf()
    saved_schema_hash << ep.f_dwread_epd(work + 1)
    saved_total_bits << ep.f_dwread_epd(work + 2)
    saved_packed_dwords << ep.f_dwread_epd(work + 4)
    if _APP_COMPAT_MODE == SCND_COMPAT_EXACT:
        if ep.EUDIf()(ok.AtLeast(1)):
            if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_SCOPE_PLAYER)):
                if ep.EUDIf()([
                    saved_schema_hash.Exactly(_ACTIVE_SCHEMA_HASH[player]),
                    saved_total_bits.Exactly(_ACTIVE_TOTAL_BITS[player]),
                    saved_packed_dwords.Exactly(_ACTIVE_PACKED_DWORDS[player]),
                ]):
                    pass
                if ep.EUDElseIf()(_accept_exact_player_prefix_header(
                    player,
                    saved_schema_hash,
                    saved_total_bits,
                    saved_packed_dwords,
                ).AtLeast(1)):
                    pass
                if ep.EUDElse()():
                    ok << 0
                    _set_active_schema_mismatch_result(player)
                ep.EUDEndIf()
            if ep.EUDElse()():
                if ep.EUDIf()([
                    saved_schema_hash.Exactly(_ACTIVE_SCHEMA_HASH[player]),
                    saved_total_bits.Exactly(_ACTIVE_TOTAL_BITS[player]),
                    saved_packed_dwords.Exactly(_ACTIVE_PACKED_DWORDS[player]),
                ]):
                    pass
                if ep.EUDElse()():
                    ok << 0
                    _set_active_schema_mismatch_result(player)
                ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()
    else:
        if ep.EUDIf()(ok.AtLeast(1)):
            if ep.EUDIfNot()(saved_schema_hash.Exactly(_ACTIVE_SCHEMA_HASH[player])):
                ok << 0
                _set_active_schema_mismatch_result(player)
            ep.EUDEndIf()
        ep.EUDEndIf()
        if ep.EUDIf()(ok.AtLeast(1)):
            if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_SCOPE_GLOBAL)):
                if ep.EUDIfNot()(saved_total_bits.Exactly(_ACTIVE_TOTAL_BITS[player])):
                    ok << 0
                    _set_active_schema_mismatch_result(player)
                ep.EUDEndIf()
            if ep.EUDElse()():
                if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
                    if ep.EUDIf()(saved_total_bits > _ACTIVE_TOTAL_BITS[player]):
                        ok << 0
                        _set_active_schema_mismatch_result(player)
                    ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()
        if ep.EUDIf()(ok.AtLeast(1)):
            if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_SCOPE_GLOBAL)):
                if ep.EUDIfNot()(saved_packed_dwords.Exactly(_ACTIVE_PACKED_DWORDS[player])):
                    ok << 0
                    _set_active_schema_mismatch_result(player)
                ep.EUDEndIf()
            if ep.EUDElse()():
                if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
                    if ep.EUDIf()(saved_packed_dwords > _ACTIVE_PACKED_DWORDS[player]):
                        ok << 0
                        _set_active_schema_mismatch_result(player)
                    ep.EUDEndIf()
                else:
                    if ep.EUDIf()(saved_packed_dwords > SCND_MAX_DWORDS - SCND_HEADER_DWORDS - SCND_TAG_DWORDS):
                        ok << 0
                        _set_active_schema_mismatch_result(player)
                    ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()
    if ep.EUDIf()(ok.AtLeast(1)):
        _FLAGS[player] = ep.f_dwread_epd(work + 5)
        codec_id << _flag_codec(_FLAGS[player])
        params << _flag_params(_FLAGS[player])
        payload_dwords << decoded_dwords - SCND_HEADER_DWORDS - SCND_TAG_DWORDS
        _PAYLOAD_DWORDS[player] = payload_dwords
        _PLAIN_DWORDS[player] = saved_packed_dwords
        if ep.EUDIf()(codec_id.Exactly(SCND_CODEC_RAW)):
            if ep.EUDIfNot()(payload_dwords.Exactly(saved_packed_dwords)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
            if ep.EUDIfNot()(params.Exactly(0)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
        if ep.EUDElseIf()(codec_id.Exactly(SCND_CODEC_ZERO_RLE)):
            if ep.EUDIf()(payload_dwords.AtLeast(saved_packed_dwords)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
            if ep.EUDIfNot()(params.Exactly(0)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
        if ep.EUDElseIf()(codec_id.Exactly(SCND_CODEC_VARINT)):
            # params24 carries the byte count. payload_dwords must equal
            # ceil(params/4); we verify by checking 4*payload >= params and
            # 4*(payload-1) < params (i.e., last dword is partly used).
            expected_payload = ep.EUDVariable()
            expected_payload << ep.f_div(params + 3, 4)[0]
            if ep.EUDIfNot()(payload_dwords.Exactly(expected_payload)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
            # Reject obviously bad params (0 or > 5 * PACKED).
            if ep.EUDIf()(params.Exactly(0)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
            if ep.EUDIf()(params > saved_packed_dwords * 5):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
        if ep.EUDElseIf()(codec_id.Exactly(SCND_CODEC_FILL)):
            if ep.EUDIfNot()(payload_dwords.Exactly(1)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
            if ep.EUDIfNot()(params.Exactly(0)):
                ok << 0
                _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
        if ep.EUDElse()():
            ok << 0
            _set_active_global_mismatch_result(player, RESULT_BAD_CODE)
        ep.EUDEndIf()
    ep.EUDEndIf()
    if ep.EUDIf()(ok.AtLeast(1)):
        _NONCE[player] = ep.f_dwread_epd(work + 3)
        _init_mac(player)
    ep.EUDEndIf()
    return ok


@ep.EUDFunc
def _new_user_packet_status(player):
    work = _work_epd(player)
    words = _active_new_user_code_words()
    decoded_dwords = ep.EUDVariable()
    quotient = ep.EUDVariable()
    remainder = ep.EUDVariable()
    status = ep.EUDVariable()

    status << SCND_NEW_USER_PACKET_NONE
    if not words or len(words) != SCND_NEW_USER_DWORDS:
        return status

    quotient, remainder = ep.f_div(_DECODE_OUT[player], 4)
    decoded_dwords << quotient

    if ep.EUDIf()(ep.f_dwread_epd(work + 0).Exactly(SCND_NEW_USER_MAGIC)):
        status << SCND_NEW_USER_PACKET_BAD
        expected = [
            remainder.Exactly(0),
            decoded_dwords.Exactly(SCND_NEW_USER_DWORDS),
        ]
        for index in range(1, SCND_NEW_USER_DWORDS):
            expected.append(ep.f_dwread_epd(work + index).Exactly(int(words[index]) & 0xFFFFFFFF))
        if ep.EUDIf()(expected):
            status << SCND_NEW_USER_PACKET_OK
        ep.EUDEndIf()
    ep.EUDEndIf()
    return status


@ep.EUDFunc
def _finish_new_user_load(player):
    _APP_IS_NEW_USER[player] = 1
    _STATE[player] = _PHASE_LOAD_DONE
    _RESULT[player] = RESULT_OK
    _MESSAGE[player] = MESSAGE_LOAD_DONE
    _app_close_load_gate(player)


@ep.EUDFunc
def _mac_loaded_one_dword(player, data_index):
    word = ep.f_dwread_epd(_work_epd(player) + SCND_DATA_OFFSET + data_index)
    _MAC0[player] = _mac_mix0(_MAC0[player], _MAC1[player], word, _KEY0[player], _KEY1[player])
    _MAC1[player] = _mac_mix1(_MAC0[player], _MAC1[player], word, _KEY2[player], _KEY3[player])


@ep.EUDFunc
def _validate_loaded_tag(player):
    work = _work_epd(player)
    ok = ep.EUDVariable()
    ok << 1
    if ep.EUDIfNot()(ep.f_dwread_epd(work + SCND_DATA_OFFSET + _PAYLOAD_DWORDS[player]).Exactly(_MAC0[player])):
        ok << 0
        _set_active_global_mismatch_result(player, RESULT_BAD_TAG)
    ep.EUDEndIf()
    if ep.EUDIfNot()(ep.f_dwread_epd(work + SCND_DATA_OFFSET + _PAYLOAD_DWORDS[player] + 1).Exactly(_MAC1[player])):
        ok << 0
        _set_active_global_mismatch_result(player, RESULT_BAD_TAG)
    ep.EUDEndIf()
    return ok


@ep.EUDFunc
def _start_player(player, is_save):
    ret = ep.EUDVariable()
    ret << 0
    if ep.EUDIf()(_STATE[player].Exactly(_PHASE_IDLE)):
        _STATE[player] = _PHASE_CLEAR_WORK
        _RESULT[player] = RESULT_NONE
        _MESSAGE[player] = MESSAGE_NONE
        _INDEX[player] = 0
        _SUBINDEX[player] = 0
        _CODE_LEN[player] = 0
        _PRINT_INDEX[player] = 0
        _MODE[player] = 0
        _APP_IS_NEW_USER[player] = 0
        _PLAIN_DWORDS[player] = _ACTIVE_PACKED_DWORDS[player]
        _SAVED_FIELD_COUNT[player] = 0
        _SAVED_VALUE_CURSOR[player] = 0
        _PAYLOAD_DWORDS[player] = _ACTIVE_PACKED_DWORDS[player]
        _set_flag(player, SCND_CODEC_RAW)
        _ACC[player] = 0
        _BEST_LEN[player] = _ACTIVE_PACKED_DWORDS[player]
        _BEST_CODEC[player] = SCND_CODEC_RAW
        _BEST_PARAMS[player] = 0
        _PROFILE_ZERO_RUNS[player] = 0
        _PROFILE_NONZERO_DWORDS[player] = 0
        _PROFILE_PREV_IS_ZERO[player] = 0
        _PROFILE_FIRST_DWORD[player] = 0
        _PROFILE_ALL_SAME[player] = 1
        _PROFILE_VARINT_BYTES[player] = 0
        if ep.EUDIf()(is_save.AtLeast(1)):
            _MODE[player] = 1
            _NONCE[player] = ep.f_dwrand()
            _LAST_CODEC[player] = SCND_CODEC_RAW
            _LAST_RAW_DWORDS[player] = _ACTIVE_PACKED_DWORDS[player]
            _LAST_PACKED_DWORDS[player] = _ACTIVE_PACKED_DWORDS[player]
            _LAST_RATIO_PCT[player] = 100
        if ep.EUDElse()():
            _NONCE[player] = 0
        ep.EUDEndIf()
        ret << 1
    if ep.EUDElse()():
        _RESULT[player] = RESULT_BUSY
    ep.EUDEndIf()
    return ret


def _rotr32_expr(value, bits: int):
    bits = int(bits) & 31
    if bits == 0:
        return value
    return ep.f_bitor(ep.f_bitrshift(value, bits), ep.f_bitlshift(value, 32 - bits))


def _reveal_obfuscated_key(record: _ObfuscatedKeyRecord):
    tmp = ep.EUDVariable()
    salt = ep.EUDVariable()
    m2 = ep.EUDVariable()
    m1 = ep.EUDVariable()
    m0 = ep.EUDVariable()
    key = ep.EUDVariable()

    tmp << record.c3
    tmp << ep.f_bitxor(tmp, _rotl32_const(record.w1, 13))
    tmp << _rotr32_expr(tmp, record.r3)
    salt << record.a3
    salt << ep.f_bitxor(salt, _rotl32_const(record.w1, 13))
    m2 << ep.f_bitxor(tmp, salt)

    tmp << record.c2
    tmp << ep.f_bitxor(tmp, record.w1)
    tmp << _rotr32_expr(tmp, record.r2)
    salt << record.a2
    salt << ep.f_bitxor(salt, record.w1)
    tmp -= salt
    m1 << ep.f_bitxor(tmp, m2)

    tmp << record.c1
    tmp << ep.f_bitxor(tmp, _rotl32_const(record.w0, 9))
    tmp << _rotr32_expr(tmp, record.r1)
    salt << record.a1
    salt << ep.f_bitxor(salt, _rotl32_const(record.w0, 9))
    tmp -= salt
    m0 << ep.f_bitxor(tmp, m1)

    tmp << record.c0
    tmp << ep.f_bitxor(tmp, record.w0)
    tmp << _rotr32_expr(tmp, record.r0)
    salt << record.a0
    salt << ep.f_bitxor(salt, record.w0)
    tmp -= salt
    key << ep.f_bitxor(tmp, m0)
    return key


def _reveal_context_keys(context: _ObfuscatedContext):
    revealed = [ep.EUDVariable(), ep.EUDVariable(), ep.EUDVariable(), ep.EUDVariable()]
    for record in context.records:
        revealed[record.key_index] << _reveal_obfuscated_key(record)
    return revealed[0], revealed[1], revealed[2], revealed[3]


def _install_context_with_schema_hash(player, map_id: str, save_id: int, maker_key: str, schema_hash: int) -> None:
    key0, key1, key2, key3 = _reveal_context_keys(
        _obfuscate_context_keys(
            map_id,
            save_id,
            maker_key,
            int(schema_hash),
            _SCOPE_PLAYER,
        )
    )
    name_hash = _player_name_hash(player)
    _KEY0[player] = ep.f_bitxor(key0, name_hash)
    _KEY1[player] = ep.f_bitxor(key1, _rotl32(name_hash, 7))
    _KEY2[player] = ep.f_bitxor(key2, ep.f_mul(name_hash, 0x9E3779B1))
    _KEY3[player] = ep.f_bitxor(key3, _rotl32(name_hash, 17))


def _install_context(player, map_id: str, save_id: int, maker_key: str) -> None:
    _install_context_with_schema_hash(
        player,
        map_id,
        save_id,
        maker_key,
        _context_schema_hash(_SCOPE_PLAYER),
    )


def _install_global_context(player, map_id: str, save_id: int, maker_key: str) -> None:
    key0, key1, key2, key3 = _reveal_context_keys(
        _obfuscate_context_keys(
            map_id,
            save_id,
            maker_key,
            _context_schema_hash(_SCOPE_GLOBAL),
            _SCOPE_GLOBAL,
        )
    )
    _KEY0[player] = key0
    _KEY1[player] = key1
    _KEY2[player] = key2
    _KEY3[player] = key3


def _clear_context_keys(player) -> None:
    _KEY0[player] = 0
    _KEY1[player] = 0
    _KEY2[player] = 0
    _KEY3[player] = 0


def _set_active_schema(player, scope: int) -> None:
    _ACTIVE_SCOPE[player] = scope
    if scope == _SCOPE_GLOBAL:
        _ACTIVE_TOTAL_BITS[player] = _GLOBAL_TOTAL_BITS
        _ACTIVE_PACKED_DWORDS[player] = _GLOBAL_PACKED_DWORDS
        _ACTIVE_SCHEMA_HASH[player] = _context_schema_hash(_SCOPE_GLOBAL)
    else:
        _ACTIVE_TOTAL_BITS[player] = _PLAYER_TOTAL_BITS
        if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
            _ACTIVE_PACKED_DWORDS[player] = _PLAYER_COMPAT_BITS_MAX_DWORDS
        elif _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
            _ACTIVE_PACKED_DWORDS[player] = _PLAYER_COMPAT_KEY_BITS_MAX_DWORDS
        else:
            _ACTIVE_PACKED_DWORDS[player] = _PLAYER_PACKED_DWORDS
        _ACTIVE_SCHEMA_HASH[player] = _context_schema_hash(_SCOPE_PLAYER)


def _set_active_schema_to_canonical_player(player) -> None:
    _ACTIVE_SCOPE[player] = _SCOPE_PLAYER
    _ACTIVE_TOTAL_BITS[player] = _PLAYER_TOTAL_BITS
    _ACTIVE_PACKED_DWORDS[player] = _PLAYER_PACKED_DWORDS
    _ACTIVE_SCHEMA_HASH[player] = _context_schema_hash(_SCOPE_PLAYER)


def begin_save(player_value, map_id: str, save_id: int, maker_key: str):
    """Start making a chat save code for player_value."""
    if not _FLAT:
        raise RuntimeError("SCND has no bindings.")
    started = ep.EUDVariable()
    started << 0
    flag = ep.EUDVariable()
    flag << 1
    if isinstance(player_value, int):
        _set_active_schema(player_value, _SCOPE_PLAYER)
        _install_context(player_value, map_id, save_id, maker_key)
        started << _start_player(player_value, flag)
        return started
    _set_active_schema(player_value, _SCOPE_PLAYER)
    _install_context(player_value, map_id, save_id, maker_key)
    started << _start_player(player_value, flag)
    return started


def begin_save_global(player_value, map_id: str, save_id: int, maker_key: str):
    """Start making a shared/global save code that is not nickname-bound."""
    raise RuntimeError("SCND global save is not supported. Load global values from the launcher/sheet.")


def begin_load(player_value, map_id: str, save_id: int, maker_key: str):
    """Start loading from the accumulated input buffer for player_value."""
    if not _FLAT:
        raise RuntimeError("SCND has no bindings.")
    started = ep.EUDVariable()
    started << 0
    flag = ep.EUDVariable()
    flag << 0
    if isinstance(player_value, int):
        if ep.EUDIf()(_reserve_load_for(player_value).AtLeast(1)):
            _set_active_schema(player_value, _SCOPE_PLAYER)
            _install_context(player_value, map_id, save_id, maker_key)
            started << _start_player(player_value, flag)
            if ep.EUDIf()(started.Exactly(0)):
                _release_load_if_owned(player_value)
            ep.EUDEndIf()
        if ep.EUDElse()():
            _RESULT[player_value] = RESULT_BUSY
        ep.EUDEndIf()
        return started
    if ep.EUDIf()(_reserve_load_for(player_value).AtLeast(1)):
        _set_active_schema(player_value, _SCOPE_PLAYER)
        _install_context(player_value, map_id, save_id, maker_key)
        started << _start_player(player_value, flag)
        if ep.EUDIf()(started.Exactly(0)):
            _release_load_if_owned(player_value)
        ep.EUDEndIf()
    if ep.EUDElse()():
        _RESULT[player_value] = RESULT_BUSY
    ep.EUDEndIf()
    return started


def begin_load_hangul8192(player_value, map_id: str, save_id: int, maker_key: str):
    """Start loading from the accumulated stable Hangul input buffer."""
    if not _FLAT:
        raise RuntimeError("SCND has no bindings.")
    started = ep.EUDVariable()
    started << 0
    flag = ep.EUDVariable()
    flag << 0

    def _body(player):
        if ep.EUDIf()(_reserve_load_for(player).AtLeast(1)):
            _set_active_schema(player, _SCOPE_PLAYER)
            _install_context(player, map_id, save_id, maker_key)
            started << _start_player(player, flag)
            if ep.EUDIf()(started.AtLeast(1)):
                _MODE[player] = 2
            if ep.EUDElse()():
                _release_load_if_owned(player)
            ep.EUDEndIf()
        if ep.EUDElse()():
            _RESULT[player] = RESULT_BUSY
        ep.EUDEndIf()
        return started

    if isinstance(player_value, int):
        return _body(player_value)
    return _body(player_value)


def begin_load_hangul8192_global(player_value, map_id: str, save_id: int, maker_key: str):
    """Start loading a shared/global Hangul packet that is not nickname-bound."""
    if not _HAS_GLOBAL_BINDINGS:
        raise RuntimeError("SCND has no global bindings.")
    started = ep.EUDVariable()
    started << 0
    flag = ep.EUDVariable()
    flag << 0

    def _body(player):
        if ep.EUDIf()(_reserve_load_for(player).AtLeast(1)):
            _set_active_schema(player, _SCOPE_GLOBAL)
            _install_global_context(player, map_id, save_id, maker_key)
            started << _start_player(player, flag)
            if ep.EUDIf()(started.AtLeast(1)):
                _MODE[player] = 2
            if ep.EUDElse()():
                _release_load_if_owned(player)
            ep.EUDEndIf()
        if ep.EUDElse()():
            _RESULT[player] = RESULT_BUSY
        ep.EUDEndIf()
        return started

    if isinstance(player_value, int):
        return _body(player_value)
    return _body(player_value)


def clear_input(player_value):
    if isinstance(player_value, int):
        _APP_LOAD_QUEUED[player_value] = 0
        _INPUT_LEN[player_value] = 0
        _HANGUL_INPUT_LEN[player_value] = 0
        _MESSAGE[player_value] = MESSAGE_INPUT_CLEARED
        return
    _APP_LOAD_QUEUED[player_value] = 0
    _INPUT_LEN[player_value] = 0
    _HANGUL_INPUT_LEN[player_value] = 0
    _MESSAGE[player_value] = MESSAGE_INPUT_CLEARED


def clear_hangul8192_input(player_value):
    if isinstance(player_value, int):
        _APP_LOAD_QUEUED[player_value] = 0
        _HANGUL_INPUT_LEN[player_value] = 0
        _MESSAGE[player_value] = MESSAGE_INPUT_CLEARED
        return
    _APP_LOAD_QUEUED[player_value] = 0
    _HANGUL_INPUT_LEN[player_value] = 0
    _MESSAGE[player_value] = MESSAGE_INPUT_CLEARED


@ep.EUDFunc
def _append_hangul8192_value_body(player, value):
    dst = ep.EUDVariable()
    appended = ep.EUDVariable()
    dst << _HANGUL_INPUT_LEN[player]
    appended << 0
    if ep.EUDIf()([dst < SCND_MAX_HANGUL_CHARS - 1, value < SCND_HANGUL_BASE]):
        ep.f_dwwrite_epd(_hinput_epd(player) + dst, value)
        _HANGUL_INPUT_LEN[player] = dst + 1
        _MESSAGE[player] = MESSAGE_INPUT_APPENDED
        appended << 1
    ep.EUDEndIf()
    return appended


def append_hangul8192_value(player_value, value):
    """Append one stable Hangul value, usually codepoint - 0xAC00."""
    if isinstance(player_value, int):
        return _append_hangul8192_value_body(player_value, value)
    return _append_hangul8192_value_body(player_value, value)


def hangul8192_input_length(player_value):
    if isinstance(player_value, int):
        return _HANGUL_INPUT_LEN[player_value]
    return _HANGUL_INPUT_LEN[player_value]


def hangul8192_input_ready(player_value):
    def _body(player):
        ready = ep.EUDVariable()
        target_dwords = ep.EUDVariable()
        target_chars = ep.EUDVariable()
        ready << 0
        if ep.EUDIf()(_HANGUL_INPUT_LEN[player].AtLeast(1)):
            target_dwords << _read_hangul8192_header_dwords(player)
            if ep.EUDIf()([
                target_dwords >= SCND_HEADER_DWORDS + SCND_TAG_DWORDS,
                target_dwords <= SCND_HEADER_DWORDS + SCND_MAX_DWORDS + SCND_TAG_DWORDS,
            ]):
                target_chars << _hangul8192_len_from_bytes(ep.f_mul(target_dwords, 4))
                if ep.EUDIf()(_HANGUL_INPUT_LEN[player] >= target_chars + 1):
                    ready << 1
                ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()
        return ready

    if isinstance(player_value, int):
        return _body(player_value)
    return _body(player_value)


def _hangul8192_expected_total_chars(player):
    total = ep.EUDVariable()
    target_dwords = ep.EUDVariable()
    target_chars = ep.EUDVariable()
    total << 0
    if ep.EUDIf()(_HANGUL_INPUT_LEN[player].AtLeast(1)):
        target_dwords << _read_hangul8192_header_dwords(player)
        if ep.EUDIf()([
            target_dwords >= SCND_HEADER_DWORDS + SCND_TAG_DWORDS,
            target_dwords <= SCND_HEADER_DWORDS + SCND_MAX_DWORDS + SCND_TAG_DWORDS,
        ]):
            target_chars << _hangul8192_len_from_bytes(ep.f_mul(target_dwords, 4))
            total << target_chars + 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    return total


def _hangul8192_packet_total_chars_at(player, start_index):
    total = ep.EUDVariable()
    target_dwords = ep.EUDVariable()
    target_chars = ep.EUDVariable()
    total << 0
    if ep.EUDIf()(_HANGUL_INPUT_LEN[player] > start_index):
        target_dwords << ep.f_dwread_epd(_hinput_epd(player) + start_index)
        if ep.EUDIf()([
            target_dwords >= SCND_HEADER_DWORDS + SCND_TAG_DWORDS,
            target_dwords <= SCND_HEADER_DWORDS + SCND_MAX_DWORDS + SCND_TAG_DWORDS,
        ]):
            target_chars << _hangul8192_len_from_bytes(ep.f_mul(target_dwords, 4))
            total << target_chars + 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    return total


def _hangul8192_packet_ready_at(player, start_index):
    ready = ep.EUDVariable()
    packet_chars = ep.EUDVariable()
    ready << 0
    packet_chars << _hangul8192_packet_total_chars_at(player, start_index)
    if ep.EUDIf()([packet_chars.AtLeast(1), _HANGUL_INPUT_LEN[player] >= start_index + packet_chars]):
        ready << 1
    ep.EUDEndIf()
    return ready


def _hangul8192_combined_input_ready(player):
    ready = ep.EUDVariable()
    first_chars = ep.EUDVariable()
    ready << 0
    if not _HAS_GLOBAL_BINDINGS:
        ready << hangul8192_input_ready(player)
        return ready

    first_chars << _hangul8192_packet_total_chars_at(player, 0)
    if ep.EUDIf()([first_chars.AtLeast(1), _HANGUL_INPUT_LEN[player] > first_chars]):
        if ep.EUDIf()(_hangul8192_packet_ready_at(player, first_chars).AtLeast(1)):
            ready << 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    return ready


def _hangul8192_accept_total_chars(player):
    total = ep.EUDVariable()
    first_chars = ep.EUDVariable()
    second_chars = ep.EUDVariable()
    total << 0
    first_chars << _hangul8192_packet_total_chars_at(player, 0)

    if not _HAS_GLOBAL_BINDINGS:
        total << first_chars
        return total

    if ep.EUDIf()(first_chars.AtLeast(1)):
        if ep.EUDIf()(_HANGUL_INPUT_LEN[player] < first_chars):
            total << first_chars
        if ep.EUDElseIf()(_HANGUL_INPUT_LEN[player].Exactly(first_chars)):
            total << first_chars + 1
        if ep.EUDElse()():
            second_chars << _hangul8192_packet_total_chars_at(player, first_chars)
            if ep.EUDIf()(second_chars.AtLeast(1)):
                total << first_chars + second_chars
            if ep.EUDElse()():
                total << first_chars + 1
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()
    return total


def _hangul8192_has_tail_after_current(player):
    has_tail = ep.EUDVariable()
    packet_chars = ep.EUDVariable()
    has_tail << 0
    packet_chars << _hangul8192_expected_total_chars(player)
    if ep.EUDIf()([packet_chars.AtLeast(1), _HANGUL_INPUT_LEN[player] > packet_chars]):
        has_tail << 1
    ep.EUDEndIf()
    return has_tail


@ep.EUDFunc
def _shift_hangul8192_tail_body(player):
    packet_chars = ep.EUDVariable()
    total_len = ep.EUDVariable()
    src = ep.EUDVariable()
    dst = ep.EUDVariable()
    packet_chars << _hangul8192_expected_total_chars(player)
    total_len << _HANGUL_INPUT_LEN[player]
    if ep.EUDIf()([packet_chars.AtLeast(1), total_len > packet_chars]):
        src << packet_chars
        dst << 0
        if ep.EUDWhile()(src < total_len):
            ep.f_dwwrite_epd(_hinput_epd(player) + dst, ep.f_dwread_epd(_hinput_epd(player) + src))
            src += 1
            dst += 1
        ep.EUDEndWhile()
        _HANGUL_INPUT_LEN[player] = dst
    if ep.EUDElse()():
        _HANGUL_INPUT_LEN[player] = 0
    ep.EUDEndIf()
    return _HANGUL_INPUT_LEN[player]


def _shift_hangul8192_tail(player):
    return _shift_hangul8192_tail_body(player)


@ep.EUDFunc
def _append_input_body(player, ptr, length, skip):
    reader = ep.EUDByteReader()
    reader.seekoffset(ptr + skip)
    i = ep.EUDVariable()
    dst = ep.EUDVariable()
    ch = ep.EUDVariable()
    i << skip
    dst << _INPUT_LEN[player]
    if ep.EUDWhile()([i < length, dst < SCND_MAX_CODE_CHARS - 1]):
        ch << reader.readbyte()
        if ep.EUDIfNot()(ch.Exactly(ord(" "))):
            ep.f_bwrite(_input_addr(player, dst), ch)
            dst += 1
        ep.EUDEndIf()
        i += 1
    ep.EUDEndWhile()
    _INPUT_LEN[player] = dst
    ep.f_bwrite(_input_addr(player, dst), 0)
    _MESSAGE[player] = MESSAGE_INPUT_APPENDED
    return dst


@ep.EUDFunc
def _append_input_cstring_body(player, ptr, max_length, skip):
    reader = ep.EUDByteReader()
    reader.seekoffset(ptr + skip)
    i = ep.EUDVariable()
    dst = ep.EUDVariable()
    ch = ep.EUDVariable()
    i << skip
    dst << _INPUT_LEN[player]
    if ep.EUDWhile()([i < max_length, dst < SCND_MAX_CODE_CHARS - 1]):
        ch << reader.readbyte()
        ep.EUDBreakIf(ch.Exactly(0))
        if ep.EUDIfNot()(ch.Exactly(ord(" "))):
            ep.f_bwrite(_input_addr(player, dst), ch)
            dst += 1
        ep.EUDEndIf()
        i += 1
    ep.EUDEndWhile()
    _INPUT_LEN[player] = dst
    ep.f_bwrite(_input_addr(player, dst), 0)
    _MESSAGE[player] = MESSAGE_INPUT_APPENDED
    return dst


def append_input_from_chat(player_value, ptr, length, prefix_len=0):
    """Append code text from chat memory.  Spaces are ignored."""
    if isinstance(player_value, int):
        return _append_input_body(player_value, ptr, length, prefix_len)
    return _append_input_body(player_value, ptr, length, prefix_len)


def append_input_from_cstring(player_value, ptr, max_length=218, prefix_len=0):
    """Append code text from a null-terminated memory string. Spaces are ignored."""
    if isinstance(player_value, int):
        return _append_input_cstring_body(player_value, ptr, max_length, prefix_len)
    return _append_input_cstring_body(player_value, ptr, max_length, prefix_len)


@ep.EUDFunc
def _tick_player(player):
    _tick_sync_player(player)
    state = _STATE[player]

    if ep.EUDIf()(state.Exactly(_PHASE_FAILED)):
        _app_abort_load_runtime(player)
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_IDLE)):
        _app_try_start_queued_load(player)
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_SYNC_COPY)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < SCND_HEADER_DWORDS + _SYNC_PLAIN_DWORDS):
                _copy_sync_receive_to_work_one(player, _INDEX[player])
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(SCND_HEADER_DWORDS + _SYNC_PLAIN_DWORDS)):
            if ep.EUDIf()(_validate_sync_plain_header(player).AtLeast(1)):
                _STATE[player] = _PHASE_UNPACK
                _INDEX[player] = 0
            if ep.EUDElseIf()(_validate_input_sync_header(player).AtLeast(1)):
                _STATE[player] = _PHASE_INPUT_SYNC_COPY
                _INDEX[player] = 0
                _HANGUL_INPUT_LEN[player] = 0
            if ep.EUDElse()():
                _STATE[player] = _PHASE_FAILED
                _RESULT[player] = RESULT_BAD_CODE
                _MESSAGE[player] = MESSAGE_LOAD_FAILED
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_INPUT_SYNC_COPY)):
        input_count = ep.EUDVariable()
        input_count << ep.f_dwread_epd(_work_epd(player) + 1)
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < input_count):
                _input_sync_copy_step(player)
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player] >= input_count):
            _HANGUL_INPUT_LEN[player] = input_count
            _STATE[player] = _PHASE_INPUT_SYNC_LOAD
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_INPUT_SYNC_LOAD)):
        if ep.EUDIf()(_hangul8192_combined_input_ready(player).AtLeast(1)):
            _STATE[player] = _PHASE_IDLE
            _begin_load_from_ready_input(player)
        if _HAS_GLOBAL_BINDINGS:
            if ep.EUDElseIf()(hangul8192_input_ready(player).AtLeast(1)):
                _fail_load_now(player, RESULT_GLOBAL_REQUIRED)
        if ep.EUDElse()():
            _fail_load_now(player, RESULT_BAD_CODE)
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_CLEAR_WORK)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < SCND_HEADER_DWORDS + _ACTIVE_PACKED_DWORDS[player] + SCND_TAG_DWORDS):
                ep.f_dwwrite_epd(_work_epd(player) + _INDEX[player], 0)
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(SCND_HEADER_DWORDS + _ACTIVE_PACKED_DWORDS[player] + SCND_TAG_DWORDS)):
            _INDEX[player] = 0
            if ep.EUDIf()(_MODE[player].Exactly(1)):
                _STATE[player] = _PHASE_PACK
            if ep.EUDElseIf()(_MODE[player].Exactly(2)):
                _STATE[player] = _PHASE_HANGUL_DECODE_CLEAR
            if ep.EUDElse()():
                _STATE[player] = _PHASE_DECODE_CLEAR
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_PACK)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < len(_FLAT)):
                if _APP_COMPAT_MODE != SCND_COMPAT_EXACT:
                    _pack_compat_step(player)
                else:
                    _pack_step(player)
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(len(_FLAT))):
            if _APP_COMPAT_MODE != SCND_COMPAT_EXACT:
                _PLAIN_DWORDS[player] = ep.f_div(_SUBINDEX[player] + 3, 4)[0]
            else:
                _PLAIN_DWORDS[player] = _ACTIVE_PACKED_DWORDS[player]
            _STATE[player] = _PHASE_PROFILE
            _INDEX[player] = 0
            _reset_profile(player)
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_PROFILE)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _PLAIN_DWORDS[player]):
                _profile_one_dword(player)
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_PLAIN_DWORDS[player])):
            _finish_profile(player)
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_COMPRESS)):
        if ep.EUDIf()(_BEST_CODEC[player].Exactly(SCND_CODEC_VARINT)):
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()(_INDEX[player] < _PLAIN_DWORDS[player]):
                    _compress_varint_one_dword(player)
                ep.EUDEndIf()
            if ep.EUDIf()(_INDEX[player].AtLeast(_PLAIN_DWORDS[player])):
                _finish_varint(player)
                _BEST_LEN[player] = _PAYLOAD_DWORDS[player]
                _BEST_PARAMS[player] = _flag_params(_FLAGS[player])
                _record_compress_stats(player)
                _STATE[player] = _PHASE_BEST_COMMIT
                _INDEX[player] = 0
            ep.EUDEndIf()
        if ep.EUDElse()():
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()(_INDEX[player] < _PLAIN_DWORDS[player]):
                    _compress_one_dword(player)
                ep.EUDEndIf()
            if ep.EUDIf()(_INDEX[player].AtLeast(_PLAIN_DWORDS[player])):
                _finish_trial(player)
                _STATE[player] = _PHASE_BEST_COMMIT
                _INDEX[player] = 0
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_BEST_COMMIT)):
        _PAYLOAD_DWORDS[player] = _BEST_LEN[player]
        if ep.EUDIf()(_BEST_CODEC[player].Exactly(SCND_CODEC_ZERO_RLE)):
            _set_flag(player, SCND_CODEC_ZERO_RLE, 0)
            _STATE[player] = _PHASE_COPY_COMPRESSED
            _INDEX[player] = 0
        if ep.EUDElseIf()(_BEST_CODEC[player].Exactly(SCND_CODEC_VARINT)):
            _set_flag(player, SCND_CODEC_VARINT, _BEST_PARAMS[player])
            _STATE[player] = _PHASE_COPY_COMPRESSED
            _INDEX[player] = 0
        if ep.EUDElse()():
            _set_flag(player, SCND_CODEC_RAW, 0)
            _STATE[player] = _PHASE_WRITE_HEADER
            _INDEX[player] = 0
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_COPY_COMPRESSED)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _PAYLOAD_DWORDS[player]):
                _copy_temp_to_payload_one(player, _INDEX[player])
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_PAYLOAD_DWORDS[player])):
            _STATE[player] = _PHASE_WRITE_HEADER
            _INDEX[player] = 0
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_WRITE_HEADER)):
        _write_header(player)
        _init_mac(player)
        _STATE[player] = _PHASE_CRYPT
        _INDEX[player] = 0
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_CRYPT)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _PAYLOAD_DWORDS[player]):
                _crypt_one_dword(player, _INDEX[player])
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_PAYLOAD_DWORDS[player])):
            ep.f_dwwrite_epd(_work_epd(player) + SCND_DATA_OFFSET + _PAYLOAD_DWORDS[player], _MAC0[player])
            ep.f_dwwrite_epd(_work_epd(player) + SCND_DATA_OFFSET + _PAYLOAD_DWORDS[player] + 1, _MAC1[player])
            _STATE[player] = _PHASE_ENCODE_CLEAR
            _INDEX[player] = 0
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_ENCODE_CLEAR)):
        ep.f_bwrite(_code_addr(player, 0), 0)
        _STATE[player] = _PHASE_ENCODE
        _INDEX[player] = 0
        _SUBINDEX[player] = 0
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_ENCODE)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < (SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS) * 4):
                _encode_step(player)
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast((SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS) * 4)):
            _CODE_LEN[player] = _SUBINDEX[player]
            ep.f_bwrite(_code_addr(player, _CODE_LEN[player]), 0)
            _STATE[player] = _PHASE_SAVE_DONE
            _RESULT[player] = RESULT_OK
            _MESSAGE[player] = MESSAGE_SAVE_DONE
            _app_abort_load_runtime(player)
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_HANGUL_DECODE_CLEAR)):
        target_dwords = ep.EUDVariable()
        target_bytes = ep.EUDVariable()
        target_chars = ep.EUDVariable()
        target_dwords << _read_hangul8192_header_dwords(player)
        target_bytes << ep.f_mul(target_dwords, 4)
        target_chars << _hangul8192_len_from_bytes(target_bytes)
        _HANGUL_INPUT_TARGET_BYTES[player] = target_bytes
        _HANGUL_INPUT_TARGET_CHARS[player] = target_chars
        _INDEX[player] = 1
        _DECODE_OUT[player] = 0
        _ACC[player] = 0
        _ACC_BITS[player] = 0
        if ep.EUDIf()([
            _HANGUL_INPUT_LEN[player] >= target_chars + 1,
            target_dwords >= SCND_HEADER_DWORDS + SCND_TAG_DWORDS,
            target_dwords <= SCND_HEADER_DWORDS + SCND_MAX_DWORDS + SCND_TAG_DWORDS,
        ]):
            _STATE[player] = _PHASE_HANGUL_DECODE
        if ep.EUDElse()():
            _STATE[player] = _PHASE_FAILED
            _RESULT[player] = RESULT_BAD_CODE
            _MESSAGE[player] = MESSAGE_LOAD_FAILED
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_HANGUL_DECODE)):
        target_end = _HANGUL_INPUT_TARGET_CHARS[player] + 1
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < target_end):
                _hangul8192_decode_step(player)
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(target_end)):
            _STATE[player] = _PHASE_VALIDATE
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_DECODE_CLEAR)):
        _STATE[player] = _PHASE_DECODE
        _INDEX[player] = 0
        _DECODE_OUT[player] = 0
        _ACC[player] = 0
        _ACC_BITS[player] = 0
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_DECODE)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _INPUT_LEN[player]):
                _decode_step(player)
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_INPUT_LEN[player])):
            _STATE[player] = _PHASE_VALIDATE
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_VALIDATE)):
        new_user_status = ep.EUDVariable()
        new_user_status << _new_user_packet_status(player)
        if ep.EUDIf()(new_user_status.Exactly(SCND_NEW_USER_PACKET_OK)):
            _finish_new_user_load(player)
        if ep.EUDElseIf()(new_user_status.Exactly(SCND_NEW_USER_PACKET_BAD)):
            _STATE[player] = _PHASE_FAILED
            _RESULT[player] = RESULT_BAD_CODE
            _MESSAGE[player] = MESSAGE_LOAD_FAILED
        if ep.EUDElse()():
            if ep.EUDIf()(_validate_loaded_header(player).AtLeast(1)):
                _STATE[player] = _PHASE_VALIDATE_MAC
                _INDEX[player] = 0
            if ep.EUDElse()():
                _STATE[player] = _PHASE_FAILED
                _MESSAGE[player] = MESSAGE_LOAD_FAILED
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_VALIDATE_MAC)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _PAYLOAD_DWORDS[player]):
                _mac_loaded_one_dword(player, _INDEX[player])
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_PAYLOAD_DWORDS[player])):
            if ep.EUDIf()(_validate_loaded_tag(player).AtLeast(1)):
                _STATE[player] = _PHASE_LOAD_CRYPT
                _INDEX[player] = 0
            if ep.EUDElse()():
                _STATE[player] = _PHASE_FAILED
                _MESSAGE[player] = MESSAGE_LOAD_FAILED
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_LOAD_CRYPT)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _PAYLOAD_DWORDS[player]):
                _decrypt_one_dword(player, _INDEX[player])
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_PAYLOAD_DWORDS[player])):
            if ep.EUDIf()(_flag_codec(_FLAGS[player]).Exactly(SCND_CODEC_ZERO_RLE)):
                _STATE[player] = _PHASE_DECOMPRESS
                _INDEX[player] = 0
                _SUBINDEX[player] = 0
                _ACC[player] = 0
            if ep.EUDElseIf()(_flag_codec(_FLAGS[player]).Exactly(SCND_CODEC_VARINT)):
                # VARINT decoder reads bytes from work, writes dwords to temp.
                # _INDEX = byte cursor; _SUBINDEX = output dword cursor.
                _STATE[player] = _PHASE_DECOMPRESS
                _INDEX[player] = 0
                _SUBINDEX[player] = 0
                _ACC[player] = 0
            if ep.EUDElseIf()(_flag_codec(_FLAGS[player]).Exactly(SCND_CODEC_FILL)):
                _STATE[player] = _PHASE_FILL_EXPAND
                _INDEX[player] = 0
            if ep.EUDElse()():
                _set_load_payload_ready(player)
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_FILL_EXPAND)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _PLAIN_DWORDS[player]):
                _expand_fill_one_dword(player, _INDEX[player])
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_PLAIN_DWORDS[player])):
            _set_load_payload_ready(player)
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_DECOMPRESS)):
        if ep.EUDIf()(_flag_codec(_FLAGS[player]).Exactly(SCND_CODEC_VARINT)):
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()(_SUBINDEX[player] < _PLAIN_DWORDS[player]):
                    _decompress_varint_one_dword(player)
                ep.EUDEndIf()
            if ep.EUDIf()(_SUBINDEX[player].AtLeast(_PLAIN_DWORDS[player])):
                _STATE[player] = _PHASE_COPY_DECOMPRESSED
                _INDEX[player] = 0
            ep.EUDEndIf()
        if ep.EUDElse()():
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()(_SUBINDEX[player] < _PLAIN_DWORDS[player]):
                    _decompress_one_step(player)
                ep.EUDEndIf()
            if ep.EUDIf()(_SUBINDEX[player].AtLeast(_PLAIN_DWORDS[player])):
                _STATE[player] = _PHASE_COPY_DECOMPRESSED
                _INDEX[player] = 0
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_COPY_DECOMPRESSED)):
        for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
            if ep.EUDIf()(_INDEX[player] < _PLAIN_DWORDS[player]):
                _copy_temp_to_payload_one(player, _INDEX[player])
                _INDEX[player] += 1
            ep.EUDEndIf()
        if ep.EUDIf()(_INDEX[player].AtLeast(_PLAIN_DWORDS[player])):
            _set_load_payload_ready(player)
        ep.EUDEndIf()
    ep.EUDEndIf()

    if _APP_COMPAT_MODE != SCND_COMPAT_EXACT:
        if ep.EUDIf()(state.Exactly(_PHASE_COMPAT_DEFAULTS)):
            plain_bytes = ep.EUDVariable()
            plain_bytes << ep.f_mul(_PLAIN_DWORDS[player], 4)
            if _APP_COMPAT_MODE == SCND_COMPAT_BITS:
                if ep.EUDIf()(_SAVED_FIELD_COUNT[player] > _PLAYER_FIELD_COUNT):
                    _fail_bad_compat_payload(player)
                ep.EUDEndIf()
                if ep.EUDIf()(4 + _SAVED_FIELD_COUNT[player] > plain_bytes):
                    _fail_bad_compat_payload(player)
                ep.EUDEndIf()
            elif _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
                if ep.EUDIf()(4 + ep.f_mul(_SAVED_FIELD_COUNT[player], 5) > plain_bytes):
                    _fail_bad_compat_payload(player)
                ep.EUDEndIf()
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()([_STATE[player].Exactly(_PHASE_COMPAT_DEFAULTS), _INDEX[player] < _PLAYER_PACKED_DWORDS]):
                    _compat_copy_default_dword_to_temp(player)
                ep.EUDEndIf()
            if ep.EUDIf()([_STATE[player].Exactly(_PHASE_COMPAT_DEFAULTS), _INDEX[player].AtLeast(_PLAYER_PACKED_DWORDS)]):
                _STATE[player] = _PHASE_COMPAT_CONVERT
                _INDEX[player] = 0
            ep.EUDEndIf()
        ep.EUDEndIf()

        if ep.EUDIf()(state.Exactly(_PHASE_COMPAT_CONVERT)):
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()([_STATE[player].Exactly(_PHASE_COMPAT_CONVERT), _INDEX[player] < _SAVED_FIELD_COUNT[player]]):
                    if _APP_COMPAT_MODE == SCND_COMPAT_KEY_BITS:
                        _compat_convert_key_bits_step(player)
                    else:
                        _compat_convert_bits_step(player)
                ep.EUDEndIf()
            if ep.EUDIf()([_STATE[player].Exactly(_PHASE_COMPAT_CONVERT), _INDEX[player].AtLeast(_SAVED_FIELD_COUNT[player])]):
                _STATE[player] = _PHASE_COMPAT_COPY
                _INDEX[player] = 0
                _set_active_schema_to_canonical_player(player)
                _PLAIN_DWORDS[player] = _PLAYER_PACKED_DWORDS
            ep.EUDEndIf()
        ep.EUDEndIf()

        if ep.EUDIf()(state.Exactly(_PHASE_COMPAT_COPY)):
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()(_INDEX[player] < _PLAYER_PACKED_DWORDS):
                    _compat_copy_temp_to_work_one(player)
                ep.EUDEndIf()
            if ep.EUDIf()(_INDEX[player].AtLeast(_PLAYER_PACKED_DWORDS)):
                if _SYNC_ENABLED:
                    _STATE[player] = _PHASE_SYNC_START
                else:
                    _STATE[player] = _PHASE_UNPACK
                _INDEX[player] = 0
            ep.EUDEndIf()
        ep.EUDEndIf()

    if ep.EUDIf()(state.Exactly(_PHASE_UNPACK)):
        if _HAS_GLOBAL_BINDINGS:
            if ep.EUDIf()(_ACTIVE_SCOPE[player].Exactly(_SCOPE_GLOBAL)):
                if ep.EUDIf()(_global_pending_available_for(player).AtLeast(1)):
                    if ep.EUDIf()(_INDEX[player].Exactly(0)):
                        _reserve_global_pending_for(player)
                    ep.EUDEndIf()
                    for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                        if ep.EUDIf()([_STATE[player].Exactly(_PHASE_UNPACK), _INDEX[player] < _GLOBAL_PACKED_DWORDS]):
                            _copy_global_plain_to_pending_one(player)
                        ep.EUDEndIf()
                    if ep.EUDIf()([_STATE[player].Exactly(_PHASE_UNPACK), _INDEX[player].AtLeast(_GLOBAL_PACKED_DWORDS)]):
                        if ep.EUDIf()(_APP_HANGUL_LOAD_TAIL[player].Exactly(0)):
                            _commit_pending_global_values(player)
                        ep.EUDEndIf()
                        if ep.EUDIf()(_STATE[player].Exactly(_PHASE_UNPACK)):
                            _STATE[player] = _PHASE_LOAD_DONE
                            _RESULT[player] = RESULT_OK
                            _MESSAGE[player] = MESSAGE_LOAD_DONE
                            _app_close_load_gate(player)
                        ep.EUDEndIf()
                    ep.EUDEndIf()
                if ep.EUDElse()():
                    _STATE[player] = _PHASE_IDLE
                    _RESULT[player] = RESULT_NONE
                    _MESSAGE[player] = MESSAGE_NONE
                    _INDEX[player] = 0
                    _release_load_if_owned(player)
                    _app_queue_load_runtime(player)
                ep.EUDEndIf()
            if ep.EUDElse()():
                if ep.EUDIf()(_global_pending_owned_by(player).AtLeast(1)):
                    _commit_pending_global_values(player)
                ep.EUDEndIf()
                for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                    if ep.EUDIf()([_STATE[player].Exactly(_PHASE_UNPACK), _INDEX[player] < len(_FLAT)]):
                        _unpack_step(player)
                    ep.EUDEndIf()
                if ep.EUDIf()([_STATE[player].Exactly(_PHASE_UNPACK), _INDEX[player].AtLeast(len(_FLAT))]):
                    _STATE[player] = _PHASE_LOAD_DONE
                    _RESULT[player] = RESULT_OK
                    _MESSAGE[player] = MESSAGE_LOAD_DONE
                    _app_close_load_gate(player)
                ep.EUDEndIf()
            ep.EUDEndIf()
        else:
            for _ in ep.EUDLoopRange(SCND_WORK_PER_TICK):
                if ep.EUDIf()([_STATE[player].Exactly(_PHASE_UNPACK), _INDEX[player] < len(_FLAT)]):
                    _unpack_step(player)
                ep.EUDEndIf()
            if ep.EUDIf()([_STATE[player].Exactly(_PHASE_UNPACK), _INDEX[player].AtLeast(len(_FLAT))]):
                _STATE[player] = _PHASE_LOAD_DONE
                _RESULT[player] = RESULT_OK
                _MESSAGE[player] = MESSAGE_LOAD_DONE
                _app_close_load_gate(player)
            ep.EUDEndIf()
    ep.EUDEndIf()


@ep.EUDFunc
def tick_for_player(player_value):
    """Advance pending SCND jobs for one player slot."""
    _tick_player(player_value)
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_SAVE_DONE)):
        _clear_context_keys(player_value)
    ep.EUDEndIf()
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_LOAD_DONE)):
        _clear_context_keys(player_value)
    ep.EUDEndIf()
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_FAILED)):
        _clear_context_keys(player_value)
    ep.EUDEndIf()


@ep.EUDFunc
def tick():
    """Advance pending SCND jobs for all slots.

    Large schemas compile and run faster when creators call tick_for_player(p)
    only for the local/human slot that can own the current save-code job.
    """
    for player in range(PLAYERS):
        tick_for_player(player)


@ep.EUDFunc
def _processing_busy(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()([_STATE[player_value].AtLeast(1), _STATE[player_value].AtMost(_PHASE_ENCODE)]):
        result << 1
    ep.EUDEndIf()
    if ep.EUDIf()([_STATE[player_value].AtLeast(_PHASE_INPUT_SYNC_COPY), _STATE[player_value].AtMost(_PHASE_INPUT_SYNC_LOAD)]):
        result << 1
    ep.EUDEndIf()
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_SYNC_COPY)):
        result << 1
    ep.EUDEndIf()
    if ep.EUDIf()([_STATE[player_value].AtLeast(_PHASE_DECODE_CLEAR), _STATE[player_value].AtMost(_PHASE_UNPACK)]):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def busy(player_value):
    result = ep.EUDVariable()
    result << _processing_busy(player_value)
    if ep.EUDIf()(_APP_LOAD_QUEUED[player_value].Exactly(1)):
        result << 1
    ep.EUDEndIf()
    if ep.EUDIf()(_APP_HANGUL_INPUT_ACTIVE[player_value].Exactly(1)):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def done(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_SAVE_DONE)):
        result << 1
    ep.EUDEndIf()
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_LOAD_DONE)):
        if ep.EUDIf()(_APP_HANGUL_LOAD_TAIL[player_value].Exactly(0)):
            result << 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_FAILED)):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def save_done(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_SAVE_DONE)):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def load_done(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_LOAD_DONE)):
        if ep.EUDIf()(_APP_HANGUL_LOAD_TAIL[player_value].Exactly(0)):
            result << 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def failed(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_FAILED)):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def save_failed(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()([_STATE[player_value].Exactly(_PHASE_FAILED), _MODE[player_value].Exactly(1)]):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def load_failed(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_STATE[player_value].Exactly(_PHASE_FAILED)):
        if ep.EUDIfNot()(_MODE[player_value].Exactly(1)):
            result << 1
        ep.EUDEndIf()
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def ok(player_value):
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_RESULT[player_value].Exactly(RESULT_OK)):
        result << 1
    ep.EUDEndIf()
    return result


def is_new_user(player_value):
    if isinstance(player_value, int):
        return _APP_IS_NEW_USER[player_value]
    return _APP_IS_NEW_USER[player_value]


def get_result(player_value):
    if isinstance(player_value, int):
        return _RESULT[player_value]
    return _RESULT[player_value]


def get_last_message(player_value):
    if isinstance(player_value, int):
        return _MESSAGE[player_value]
    return _MESSAGE[player_value]


def get_result_for_player(player_value):
    """Return result for a fixed or runtime player slot without dynamic lookup.

    Use this in maker-facing runtime player loops when the player value comes
    from EUDLoopPlayer/CurrentPlayer style runtime state.
    """
    if not hasattr(player_value, "Exactly"):
        return get_result(player_value)

    result = ep.EUDVariable()
    result << RESULT_NONE
    for player in range(PLAYERS):
        if ep.EUDIf()(player_value.Exactly(player)):
            result << get_result(player)
        ep.EUDEndIf()
    return result


def get_last_message_for_player(player_value):
    """Return last message for a fixed or runtime player slot safely."""
    if not hasattr(player_value, "Exactly"):
        return get_last_message(player_value)

    message = ep.EUDVariable()
    message << MESSAGE_NONE
    for player in range(PLAYERS):
        if ep.EUDIf()(player_value.Exactly(player)):
            message << get_last_message(player)
        ep.EUDEndIf()
    return message


@ep.EUDFunc
def reset_message(player_value):
    if isinstance(player_value, int):
        _MESSAGE[player_value] = MESSAGE_NONE
        return
    _MESSAGE[player_value] = MESSAGE_NONE


@ep.EUDFunc
def reset_result(player_value):
    if isinstance(player_value, int):
        _RESULT[player_value] = RESULT_NONE
        _MESSAGE[player_value] = MESSAGE_NONE
        _APP_IS_NEW_USER[player_value] = 0
        return
    _RESULT[player_value] = RESULT_NONE
    _MESSAGE[player_value] = MESSAGE_NONE
    _APP_IS_NEW_USER[player_value] = 0


@ep.EUDFunc
def reset_done(player_value):
    if isinstance(player_value, int):
        if ep.EUDIf()(done(player_value).AtLeast(1)):
            _APP_LOAD_QUEUED[player_value] = 0
            _release_load_if_owned(player_value)
            _release_global_pending_if_owned(player_value)
            _STATE[player_value] = _PHASE_IDLE
            _RESULT[player_value] = RESULT_NONE
            _MESSAGE[player_value] = MESSAGE_NONE
            _APP_IS_NEW_USER[player_value] = 0
        ep.EUDEndIf()
        return
    if ep.EUDIf()(done(player_value).AtLeast(1)):
        _APP_LOAD_QUEUED[player_value] = 0
        _release_load_if_owned(player_value)
        _release_global_pending_if_owned(player_value)
        _STATE[player_value] = _PHASE_IDLE
        _RESULT[player_value] = RESULT_NONE
        _MESSAGE[player_value] = MESSAGE_NONE
        _APP_IS_NEW_USER[player_value] = 0
    ep.EUDEndIf()


@ep.EUDFunc
def reset_job(player_value):
    if isinstance(player_value, int):
        _APP_LOAD_QUEUED[player_value] = 0
        _APP_CODE_BUILD_ACTIVE[player_value] = 0
        _APP_CODE_VIEW_TIMER[player_value] = 0
        _APP_CODE_VIEW_LOCAL_CLOSED[player_value] = 0
        _release_load_if_owned(player_value)
        _release_global_pending_if_owned(player_value)
        _STATE[player_value] = _PHASE_IDLE
        _RESULT[player_value] = RESULT_NONE
        _MESSAGE[player_value] = MESSAGE_NONE
        _APP_IS_NEW_USER[player_value] = 0
        return
    _APP_LOAD_QUEUED[player_value] = 0
    _APP_CODE_BUILD_ACTIVE[player_value] = 0
    _APP_CODE_VIEW_TIMER[player_value] = 0
    _APP_CODE_VIEW_LOCAL_CLOSED[player_value] = 0
    _release_load_if_owned(player_value)
    _release_global_pending_if_owned(player_value)
    _STATE[player_value] = _PHASE_IDLE
    _RESULT[player_value] = RESULT_NONE
    _MESSAGE[player_value] = MESSAGE_NONE
    _APP_IS_NEW_USER[player_value] = 0


@ep.EUDFunc
def code_length(player_value):
    if isinstance(player_value, int):
        return _CODE_LEN[player_value]
    return _CODE_LEN[player_value]


@ep.EUDFunc
def input_length(player_value):
    if isinstance(player_value, int):
        return _INPUT_LEN[player_value]
    return _INPUT_LEN[player_value]


def last_compress_stats(player_value):
    """Return (codec_id, raw_dwords, payload_dwords, ratio_pct).

    The values are valid after a save finishes and remain until the next save
    starts. Load jobs do not clear the previous save's compression stats.
    """
    if isinstance(player_value, int):
        return (
            _LAST_CODEC[player_value],
            _LAST_RAW_DWORDS[player_value],
            _LAST_PACKED_DWORDS[player_value],
            _LAST_RATIO_PCT[player_value],
        )
    return (
        _LAST_CODEC[player_value],
        _LAST_RAW_DWORDS[player_value],
        _LAST_PACKED_DWORDS[player_value],
        _LAST_RATIO_PCT[player_value],
    )


def unicode_code_length(player_value):
    """Return the visible base-32768 char count for the current binary code."""
    def _body(player):
        total_bytes = ep.EUDVariable()
        total_bytes << ep.f_mul(SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS, 4)
        return _unicode32768_len_from_bytes(total_bytes)

    if isinstance(player_value, int):
        return _body(player_value)
    return _body(player_value)


def hangul8192_code_length(player_value):
    """Return visible Hangul syllables for the keyboard-friendly stable code."""
    def _body(player):
        total_bytes = ep.EUDVariable()
        total_bytes << ep.f_mul(SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS, 4)
        return _hangul8192_len_from_bytes(total_bytes) + 1

    if isinstance(player_value, int):
        return _body(player_value)
    return _body(player_value)


def reset_unicode_print(player_value):
    def _body(player):
        _HANGUL_PRINT_HEADER_DONE[player] = 0
        _HANGUL_PRINT_BYTE_INDEX[player] = 0
        _HANGUL_PRINT_DATA_CHARS[player] = 0
        _HANGUL_PRINT_TOTAL_BYTES[player] = 0
        _HANGUL_PRINT_TOTAL_CHARS[player] = 0
        _HANGUL_PRINT_ACC[player] = 0
        _HANGUL_PRINT_ACC_BITS[player] = 0
        _HANGUL_PRINT_DIGIT0[player] = 0
        _HANGUL_PRINT_DIGIT1[player] = 0
        _HANGUL_PRINT_DIGIT2[player] = 0

    if isinstance(player_value, int):
        _body(player_value)
        return
    _body(player_value)


def reset_hangul8192_print(player_value):
    return reset_unicode_print(player_value)


def _app_code_view_append_byte(player, value):
    if ep.EUDIf()(_APP_CODE_VIEW_LINE_INDEX[player] < _APP_CODE_VIEW_LINES):
        if ep.EUDIf()(_APP_CODE_VIEW_LINE_LEN[player] < _APP_CODE_VIEW_LINE_BYTES - 1):
            line_index = _APP_CODE_VIEW_LINE_INDEX[player]
            line_len = _APP_CODE_VIEW_LINE_LEN[player]
            ep.f_bwrite(_app_code_view_line_addr(player, line_index, line_len), value)
            _APP_CODE_VIEW_LINE_LEN[player] = line_len + 1
            ep.f_bwrite(_app_code_view_line_addr(player, line_index, line_len + 1), 0)
        ep.EUDEndIf()
    ep.EUDEndIf()


def _app_code_view_append_literal(player, text):
    for byte in str(text).encode("utf-8"):
        _app_code_view_append_byte(player, byte)


def _app_code_view_append_hangul_char(player, value):
    if ep.EUDIf()(_APP_CODE_VIEW_LINE_INDEX[player] < _APP_CODE_VIEW_LINES):
        if ep.EUDIf()(_APP_CODE_VIEW_LINE_LEN[player] < _APP_CODE_VIEW_LINE_BYTES - 4):
            cp = ep.EUDVariable()
            line_index = _APP_CODE_VIEW_LINE_INDEX[player]
            line_len = _APP_CODE_VIEW_LINE_LEN[player]
            cp << SCND_HANGUL_BASE_CP + value
            ep.f_bwrite(
                _app_code_view_line_addr(player, line_index, line_len),
                0xE0 + ep.f_bitrshift(cp, 12),
            )
            ep.f_bwrite(
                _app_code_view_line_addr(player, line_index, line_len + 1),
                0x80 + ep.f_bitand(ep.f_bitrshift(cp, 6), 0x3F),
            )
            ep.f_bwrite(
                _app_code_view_line_addr(player, line_index, line_len + 2),
                0x80 + ep.f_bitand(cp, 0x3F),
            )
            _APP_CODE_VIEW_LINE_LEN[player] = line_len + 3
            ep.f_bwrite(_app_code_view_line_addr(player, line_index, line_len + 3), 0)
        ep.EUDEndIf()
    ep.EUDEndIf()


def _app_code_view_finish_line(player):
    if ep.EUDIf()(_APP_CODE_VIEW_LINE_INDEX[player] < _APP_CODE_VIEW_LINES):
        ep.f_bwrite(
            _app_code_view_line_addr(
                player,
                _APP_CODE_VIEW_LINE_INDEX[player],
                _APP_CODE_VIEW_LINE_LEN[player],
            ),
            0,
        )
        _APP_CODE_VIEW_LINE_INDEX[player] += 1
        _APP_CODE_VIEW_LINE_LEN[player] = 0
        if ep.EUDIf()(_APP_CODE_VIEW_LINE_INDEX[player] < _APP_CODE_VIEW_LINES):
            ep.f_bwrite(
                _app_code_view_line_addr(player, _APP_CODE_VIEW_LINE_INDEX[player], 0),
                0,
            )
        ep.EUDEndIf()
    ep.EUDEndIf()


def _begin_app_code_view_text(player, header: str = SCND_SAVE_DATA_HEADER):
    reset_hangul8192_print(player)
    _APP_CODE_VIEW_LINE_INDEX[player] = 0
    _APP_CODE_VIEW_LINE_LEN[player] = 0
    for line_index in range(_APP_CODE_VIEW_LINES):
        ep.f_bwrite(_app_code_view_line_addr(player, line_index, 0), 0)
    if header:
        _app_code_view_append_literal(player, SCND_SAVE_DATA_COLOR + str(header))
        _app_code_view_finish_line(player)


def _append_app_code_view_padding(player):
    for _ in ep.EUDLoopRange(_APP_CODE_VIEW_LINES):
        if ep.EUDIf()(_APP_CODE_VIEW_LINE_INDEX[player] < _APP_CODE_VIEW_LINES):
            _app_code_view_append_literal(player, "\x04 ")
            _app_code_view_finish_line(player)
        ep.EUDEndIf()


def _append_next_hangul8192_view_line(player_value, prefix=SCND_SAVE_DATA_COLOR, chars_per_line=25):
    chars_per_line = int(chars_per_line)
    if chars_per_line < 1:
        chars_per_line = 1
    if chars_per_line > 96:
        chars_per_line = 96

    def _body(player):
        appended = ep.EUDVariable()
        total_dwords = ep.EUDVariable()
        total_bytes = ep.EUDVariable()
        total_chars = ep.EUDVariable()
        byte_index = ep.EUDVariable()
        data_chars = ep.EUDVariable()
        acc = ep.EUDVariable()
        acc_bits = ep.EUDVariable()
        line_chars = ep.EUDVariable()
        digit_index = ep.EUDVariable()
        digit0 = ep.EUDVariable()
        digit1 = ep.EUDVariable()
        digit2 = ep.EUDVariable()
        b0 = ep.EUDVariable()
        b1 = ep.EUDVariable()
        b2 = ep.EUDVariable()
        b3 = ep.EUDVariable()
        b4 = ep.EUDVariable()
        q0 = ep.EUDVariable()
        q1 = ep.EUDVariable()
        q2 = ep.EUDVariable()
        q3 = ep.EUDVariable()
        q4 = ep.EUDVariable()
        appended << 0
        line_chars << 0

        if ep.EUDIf()(_HANGUL_PRINT_TOTAL_BYTES[player].Exactly(0)):
            total_dwords << SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS
            total_bytes << ep.f_mul(total_dwords, 4)
            total_chars << _hangul8192_len_from_bytes(total_bytes)
            _HANGUL_PRINT_TOTAL_BYTES[player] = total_bytes
            _HANGUL_PRINT_TOTAL_CHARS[player] = total_chars
            _HANGUL_PRINT_BYTE_INDEX[player] = 0
            _HANGUL_PRINT_DATA_CHARS[player] = 0
            _HANGUL_PRINT_ACC[player] = 0
            _HANGUL_PRINT_ACC_BITS[player] = 0
            _HANGUL_PRINT_DIGIT0[player] = 0
            _HANGUL_PRINT_DIGIT1[player] = 0
            _HANGUL_PRINT_DIGIT2[player] = 0
            _HANGUL_PRINT_HEADER_DONE[player] = 0
        ep.EUDEndIf()

        total_bytes << _HANGUL_PRINT_TOTAL_BYTES[player]
        total_chars << _HANGUL_PRINT_TOTAL_CHARS[player]
        byte_index << _HANGUL_PRINT_BYTE_INDEX[player]
        data_chars << _HANGUL_PRINT_DATA_CHARS[player]
        acc << _HANGUL_PRINT_ACC[player]
        acc_bits << _HANGUL_PRINT_ACC_BITS[player]
        digit_index << acc_bits

        if ep.EUDIf()([_HANGUL_PRINT_HEADER_DONE[player].Exactly(0), line_chars < chars_per_line]):
            _app_code_view_append_literal(player, prefix)
            total_dwords << SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS
            _app_code_view_append_hangul_char(player, total_dwords)
            _HANGUL_PRINT_HEADER_DONE[player] = 1
            line_chars += 1
            appended << 1
        if ep.EUDElseIf()(data_chars < total_chars):
            _app_code_view_append_literal(player, prefix)
            appended << 1
        ep.EUDEndIf()

        if ep.EUDIf()(appended.AtLeast(1)):
            for _ in ep.EUDLoopRange(chars_per_line):
                if ep.EUDIf()([line_chars < chars_per_line, data_chars < total_chars]):
                    if ep.EUDIf()(digit_index.Exactly(0)):
                        b0 << 0
                        b1 << 0
                        b2 << 0
                        b3 << 0
                        b4 << 0
                        if ep.EUDIf()(byte_index < total_bytes):
                            b0 << ep.f_bread(_work_addr(player, byte_index))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 1 < total_bytes):
                            b1 << ep.f_bread(_work_addr(player, byte_index + 1))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 2 < total_bytes):
                            b2 << ep.f_bread(_work_addr(player, byte_index + 2))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 3 < total_bytes):
                            b3 << ep.f_bread(_work_addr(player, byte_index + 3))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 4 < total_bytes):
                            b4 << ep.f_bread(_work_addr(player, byte_index + 4))
                        ep.EUDEndIf()

                        q0, q1, q2, q3, q4, digit0 = _divmod_hangul_base_bytes5(b0, b1, b2, b3, b4)
                        q0, q1, q2, q3, q4, digit1 = _divmod_hangul_base_bytes5(q0, q1, q2, q3, q4)
                        q0, q1, q2, q3, q4, digit2 = _divmod_hangul_base_bytes5(q0, q1, q2, q3, q4)
                        _HANGUL_PRINT_DIGIT0[player] = digit0
                        _HANGUL_PRINT_DIGIT1[player] = digit1
                        _HANGUL_PRINT_DIGIT2[player] = digit2
                        byte_index += SCND_HANGUL_GROUP_BYTES
                    ep.EUDEndIf()

                    digit0 << _HANGUL_PRINT_DIGIT0[player]
                    digit1 << _HANGUL_PRINT_DIGIT1[player]
                    digit2 << _HANGUL_PRINT_DIGIT2[player]
                    if ep.EUDIf()(digit_index.Exactly(0)):
                        _app_code_view_append_hangul_char(player, digit0)
                    if ep.EUDElseIf()(digit_index.Exactly(1)):
                        _app_code_view_append_hangul_char(player, digit1)
                    if ep.EUDElse()():
                        _app_code_view_append_hangul_char(player, digit2)
                    ep.EUDEndIf()
                    digit_index += 1
                    if ep.EUDIf()(digit_index >= SCND_HANGUL_GROUP_CHARS):
                        digit_index << 0
                    ep.EUDEndIf()
                    data_chars += 1
                    line_chars += 1
                ep.EUDEndIf()
            _app_code_view_finish_line(player)
            _HANGUL_PRINT_BYTE_INDEX[player] = byte_index
            _HANGUL_PRINT_DATA_CHARS[player] = data_chars
            _HANGUL_PRINT_ACC[player] = acc
            _HANGUL_PRINT_ACC_BITS[player] = digit_index
        ep.EUDEndIf()

        return appended

    if isinstance(player_value, int):
        return _body(player_value)
    result = ep.EUDVariable()
    result << _body(player_value)
    return result


def begin_save_data_view(buffer, player_value, header: str = SCND_SAVE_DATA_HEADER):
    """Reset Hangul print state and optionally write a SCNDgram save-data title row.

    The default leaves all visible rows for code data.  Pass a non-empty header
    only when capacity is less important than a readable title row.
    """
    reset_hangul8192_print(player_value)
    buffer.insert(0)
    if header:
        buffer.append(SCND_SAVE_DATA_COLOR + str(header) + "\n")


def append_save_data_padding(
    buffer,
    player_value,
    chars_per_line=SCND_SAVE_DATA_CHARS_PER_LINE,
    visible_lines=SCND_SAVE_DATA_VISIBLE_LINES,
    header_lines=SCND_SAVE_DATA_HEADER_LINES,
):
    """Append visible blank rows so chat cannot occupy the save-data block."""
    chars_per_line = max(1, int(chars_per_line))
    visible_lines = max(1, int(visible_lines))
    header_lines = max(0, int(header_lines))

    def _body(player):
        hangul_lines, _unused = ep.f_div(
            hangul8192_code_length(player) + chars_per_line - 1,
            chars_per_line,
        )
        used_lines = ep.EUDVariable()
        blank_lines = ep.EUDVariable()
        used_lines << hangul_lines + header_lines
        blank_lines << visible_lines
        blank_lines -= used_lines

        if ep.EUDIf()(used_lines < visible_lines):
            buffer.append("\n")
            for i in range(visible_lines):
                if ep.EUDIf()(blank_lines > i):
                    buffer.append("\x04 ")
                    if ep.EUDIf()(blank_lines > i + 1):
                        buffer.append("\n")
                    ep.EUDEndIf()
                ep.EUDEndIf()
        ep.EUDEndIf()

    if isinstance(player_value, int):
        return _body(player_value)
    return _body(player_value)


def append_save_data_view_step(
    buffer,
    player_value,
    chars_per_line=SCND_SAVE_DATA_CHARS_PER_LINE,
    lines_per_tick=4,
    visible_lines=SCND_SAVE_DATA_VISIBLE_LINES,
):
    """Append a few Hangul code rows and finish with stable padding.

    Returns 1 when the full save-data block is complete for this player, else 0.
    """
    chars_per_line = max(1, int(chars_per_line))
    lines_per_tick = max(1, int(lines_per_tick))
    visible_lines = max(1, int(visible_lines))

    def _body(player):
        completed = ep.EUDVariable()
        completed << 0
        for _ in ep.EUDLoopRange(lines_per_tick):
            if ep.EUDIf()(completed.Exactly(0)):
                appended = append_next_hangul8192_chunk(
                    buffer,
                    player,
                    SCND_SAVE_DATA_COLOR,
                    chars_per_line,
                    final_newline=False,
                )
                if ep.EUDIf()(appended.Exactly(0)):
                    # Keep SAVE_DONE observable until maker code calls reset_done().
                    append_save_data_padding(buffer, player, chars_per_line, visible_lines)
                    completed << 1
                ep.EUDEndIf()
            ep.EUDEndIf()
        return completed

    if isinstance(player_value, int):
        return _body(player_value)
    return _body(player_value)


def append_next_hangul8192_chunk(buffer, player_value, prefix="!H ", chars_per_line=25, final_newline=True):
    """Append one keyboard-friendly full-Hangul chunk to a StringBuffer.

    The first Hangul syllable stores total packet dwords.  The remaining
    syllables are the encrypted SCND binary packet encoded as 5 bytes -> 3
    base11172 Hangul digits.  This matches the SCAcorn template DB range.
    """
    chars_per_line = int(chars_per_line)
    if chars_per_line < 1:
        chars_per_line = 1
    if chars_per_line > 96:
        chars_per_line = 96

    def _body(player):
        appended = ep.EUDVariable()
        total_dwords = ep.EUDVariable()
        total_bytes = ep.EUDVariable()
        total_chars = ep.EUDVariable()
        byte_index = ep.EUDVariable()
        data_chars = ep.EUDVariable()
        acc = ep.EUDVariable()
        acc_bits = ep.EUDVariable()
        line_chars = ep.EUDVariable()
        digit_index = ep.EUDVariable()
        digit0 = ep.EUDVariable()
        digit1 = ep.EUDVariable()
        digit2 = ep.EUDVariable()
        b0 = ep.EUDVariable()
        b1 = ep.EUDVariable()
        b2 = ep.EUDVariable()
        b3 = ep.EUDVariable()
        b4 = ep.EUDVariable()
        q0 = ep.EUDVariable()
        q1 = ep.EUDVariable()
        q2 = ep.EUDVariable()
        q3 = ep.EUDVariable()
        q4 = ep.EUDVariable()
        appended << 0
        line_chars << 0

        if ep.EUDIf()(_HANGUL_PRINT_TOTAL_BYTES[player].Exactly(0)):
            total_dwords << SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS
            total_bytes << ep.f_mul(total_dwords, 4)
            total_chars << _hangul8192_len_from_bytes(total_bytes)
            _HANGUL_PRINT_TOTAL_BYTES[player] = total_bytes
            _HANGUL_PRINT_TOTAL_CHARS[player] = total_chars
            _HANGUL_PRINT_BYTE_INDEX[player] = 0
            _HANGUL_PRINT_DATA_CHARS[player] = 0
            _HANGUL_PRINT_ACC[player] = 0
            _HANGUL_PRINT_ACC_BITS[player] = 0
            _HANGUL_PRINT_DIGIT0[player] = 0
            _HANGUL_PRINT_DIGIT1[player] = 0
            _HANGUL_PRINT_DIGIT2[player] = 0
            _HANGUL_PRINT_HEADER_DONE[player] = 0
        ep.EUDEndIf()

        total_bytes << _HANGUL_PRINT_TOTAL_BYTES[player]
        total_chars << _HANGUL_PRINT_TOTAL_CHARS[player]
        byte_index << _HANGUL_PRINT_BYTE_INDEX[player]
        data_chars << _HANGUL_PRINT_DATA_CHARS[player]
        acc << _HANGUL_PRINT_ACC[player]
        acc_bits << _HANGUL_PRINT_ACC_BITS[player]
        digit_index << acc_bits

        if ep.EUDIf()([_HANGUL_PRINT_HEADER_DONE[player].Exactly(0), line_chars < chars_per_line]):
            buffer.append(prefix)
            total_dwords << SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS
            _write_hangul8192_char(total_dwords)
            buffer.append(ep.ptr2s(_HANGUL_CHAR))
            _HANGUL_PRINT_HEADER_DONE[player] = 1
            line_chars += 1
            appended << 1
        if ep.EUDElseIf()(data_chars < total_chars):
            buffer.append(prefix)
            appended << 1
        ep.EUDEndIf()

        if ep.EUDIf()(appended.AtLeast(1)):
            for _ in ep.EUDLoopRange(chars_per_line):
                if ep.EUDIf()([line_chars < chars_per_line, data_chars < total_chars]):
                    if ep.EUDIf()(digit_index.Exactly(0)):
                        b0 << 0
                        b1 << 0
                        b2 << 0
                        b3 << 0
                        b4 << 0
                        if ep.EUDIf()(byte_index < total_bytes):
                            b0 << ep.f_bread(_work_addr(player, byte_index))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 1 < total_bytes):
                            b1 << ep.f_bread(_work_addr(player, byte_index + 1))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 2 < total_bytes):
                            b2 << ep.f_bread(_work_addr(player, byte_index + 2))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 3 < total_bytes):
                            b3 << ep.f_bread(_work_addr(player, byte_index + 3))
                        ep.EUDEndIf()
                        if ep.EUDIf()(byte_index + 4 < total_bytes):
                            b4 << ep.f_bread(_work_addr(player, byte_index + 4))
                        ep.EUDEndIf()

                        q0, q1, q2, q3, q4, digit0 = _divmod_hangul_base_bytes5(b0, b1, b2, b3, b4)
                        q0, q1, q2, q3, q4, digit1 = _divmod_hangul_base_bytes5(q0, q1, q2, q3, q4)
                        q0, q1, q2, q3, q4, digit2 = _divmod_hangul_base_bytes5(q0, q1, q2, q3, q4)
                        _HANGUL_PRINT_DIGIT0[player] = digit0
                        _HANGUL_PRINT_DIGIT1[player] = digit1
                        _HANGUL_PRINT_DIGIT2[player] = digit2
                        byte_index += SCND_HANGUL_GROUP_BYTES
                    ep.EUDEndIf()

                    digit0 << _HANGUL_PRINT_DIGIT0[player]
                    digit1 << _HANGUL_PRINT_DIGIT1[player]
                    digit2 << _HANGUL_PRINT_DIGIT2[player]
                    if ep.EUDIf()(digit_index.Exactly(0)):
                        _write_hangul8192_char(digit0)
                    if ep.EUDElseIf()(digit_index.Exactly(1)):
                        _write_hangul8192_char(digit1)
                    if ep.EUDElse()():
                        _write_hangul8192_char(digit2)
                    ep.EUDEndIf()
                    buffer.append(ep.ptr2s(_HANGUL_CHAR))
                    digit_index += 1
                    if ep.EUDIf()(digit_index >= SCND_HANGUL_GROUP_CHARS):
                        digit_index << 0
                    ep.EUDEndIf()
                    data_chars += 1
                    line_chars += 1
                ep.EUDEndIf()
            if final_newline:
                buffer.append("\n")
            else:
                if ep.EUDIf()(data_chars < total_chars):
                    buffer.append("\n")
                ep.EUDEndIf()
            _HANGUL_PRINT_BYTE_INDEX[player] = byte_index
            _HANGUL_PRINT_DATA_CHARS[player] = data_chars
            _HANGUL_PRINT_ACC[player] = acc
            _HANGUL_PRINT_ACC_BITS[player] = digit_index
        ep.EUDEndIf()

        return appended

    if isinstance(player_value, int):
        return _body(player_value)
    result = ep.EUDVariable()
    result << _body(player_value)
    return result


def _append_app_code_view_step_body(player):
    completed = ep.EUDVariable()
    completed << 0
    for _ in ep.EUDLoopRange(_APP_CODE_VIEW_BUILD_LINES):
        if ep.EUDIf()(completed.Exactly(0)):
            appended = _append_next_hangul8192_view_line(
                player,
                SCND_SAVE_DATA_COLOR,
                _APP_CODE_VIEW_CHARS,
            )
            if ep.EUDIf()(appended.Exactly(0)):
                # Keep SAVE_DONE observable until maker code calls reset_done().
                _append_app_code_view_padding(player)
                completed << 1
            ep.EUDEndIf()
        ep.EUDEndIf()
    return completed


def append_next_unicode_chunk(buffer, player_value, prefix="!", chars_per_line=26):
    """Append one compact base-32768 visual chunk to a StringBuffer.

    This is a display codec for shorter-looking save codes.  It encodes the
    encrypted SCND binary packet directly as 15-bit Hangul/CJK characters.  The
    current load path still uses Base64url unless a map wires a matching Unicode
    input decoder.
    """
    chars_per_line = int(chars_per_line)
    if chars_per_line < 1:
        chars_per_line = 1
    if chars_per_line > 26:
        chars_per_line = 26

    def _body(player):
        appended = ep.EUDVariable()
        total_bytes = ep.EUDVariable()
        total_chars = ep.EUDVariable()
        byte_index = ep.EUDVariable()
        data_chars = ep.EUDVariable()
        acc = ep.EUDVariable()
        acc_bits = ep.EUDVariable()
        appended << 0

        if ep.EUDIf()(_HANGUL_PRINT_TOTAL_BYTES[player].Exactly(0)):
            total_bytes << ep.f_mul(SCND_HEADER_DWORDS + _PAYLOAD_DWORDS[player] + SCND_TAG_DWORDS, 4)
            total_chars << _unicode32768_len_from_bytes(total_bytes)
            _HANGUL_PRINT_TOTAL_BYTES[player] = total_bytes
            _HANGUL_PRINT_TOTAL_CHARS[player] = total_chars
            _HANGUL_PRINT_BYTE_INDEX[player] = 0
            _HANGUL_PRINT_DATA_CHARS[player] = 0
            _HANGUL_PRINT_ACC[player] = 0
            _HANGUL_PRINT_ACC_BITS[player] = 0
        ep.EUDEndIf()

        total_bytes << _HANGUL_PRINT_TOTAL_BYTES[player]
        total_chars << _HANGUL_PRINT_TOTAL_CHARS[player]
        byte_index << _HANGUL_PRINT_BYTE_INDEX[player]
        data_chars << _HANGUL_PRINT_DATA_CHARS[player]
        acc << _HANGUL_PRINT_ACC[player]
        acc_bits << _HANGUL_PRINT_ACC_BITS[player]

        if ep.EUDIf()(data_chars < total_chars):
            buffer.append(prefix)
            for _ in ep.EUDLoopRange(chars_per_line):
                if ep.EUDIf()(data_chars < total_chars):
                    for _ in ep.EUDLoopRange(2):
                        if ep.EUDIf()([acc_bits < SCND_UNICODE_BASE_BITS, byte_index < total_bytes]):
                            next_byte = ep.f_bread(_work_addr(player, byte_index))
                            acc += ep.f_bitlshift(next_byte, acc_bits)
                            acc_bits += 8
                            byte_index += 1
                        ep.EUDEndIf()

                    _write_unicode32768_char(ep.f_bitand(acc, SCND_UNICODE_BASE - 1))
                    buffer.append(ep.ptr2s(_HANGUL_CHAR))
                    acc << ep.f_bitrshift(acc, SCND_UNICODE_BASE_BITS)
                    if ep.EUDIf()(acc_bits >= SCND_UNICODE_BASE_BITS):
                        acc_bits -= SCND_UNICODE_BASE_BITS
                    if ep.EUDElse()():
                        acc_bits << 0
                    ep.EUDEndIf()
                    data_chars += 1
                ep.EUDEndIf()
            buffer.append("\n")
            _HANGUL_PRINT_BYTE_INDEX[player] = byte_index
            _HANGUL_PRINT_DATA_CHARS[player] = data_chars
            _HANGUL_PRINT_ACC[player] = acc
            _HANGUL_PRINT_ACC_BITS[player] = acc_bits
            appended << 1
        ep.EUDEndIf()

        return appended

    if isinstance(player_value, int):
        return _body(player_value)
    result = ep.EUDVariable()
    result << _body(player_value)
    return result


def print_next_chunk(player_value, prefix="!L ", chars_per_line=SCND_PRINT_CHARS):
    """Print one code chunk.  Returns 1 when a chunk was printed."""
    # Prefix is emitted as a separate line prefix to avoid copying large buffers.
    # eudplib's f_eprintln accepts multiple pieces, but this helper temporarily
    # null-terminates the chunk and prints the chunk pointer.
    chars_per_line = int(chars_per_line)
    if chars_per_line < 16:
        chars_per_line = 16
    if chars_per_line > 180:
        chars_per_line = 180

    def _body(player):
        start = _PRINT_INDEX[player]
        remaining = ep.EUDVariable()
        count = ep.EUDVariable()
        old = ep.EUDVariable()
        printed = ep.EUDVariable()
        printed << 0
        if ep.EUDIf()(start < _CODE_LEN[player]):
            remaining << _CODE_LEN[player] - start
            count << remaining
            if ep.EUDIf()(count >= chars_per_line):
                count << chars_per_line
            ep.EUDEndIf()
            old << ep.f_bread(_code_addr(player, start + count))
            ep.f_bwrite(_code_addr(player, start + count), 0)
            _app_shared_eprintln(player, prefix, ep.ptr2s(_code_addr(player, start)))
            ep.f_bwrite(_code_addr(player, start + count), old)
            _PRINT_INDEX[player] = start + count
            printed << 1
        ep.EUDEndIf()
        return printed

    result = ep.EUDVariable()
    result << 0
    if isinstance(player_value, int):
        return _body(player_value)
    result << _body(player_value)
    return result


def append_next_chunk(buffer, player_value, prefix="!L ", chars_per_line=SCND_PRINT_CHARS):
    """Append one code chunk to a StringBuffer. Returns 1 when appended."""
    chars_per_line = int(chars_per_line)
    if chars_per_line < 16:
        chars_per_line = 16
    if chars_per_line > 180:
        chars_per_line = 180

    def _body(player):
        start = _PRINT_INDEX[player]
        remaining = ep.EUDVariable()
        count = ep.EUDVariable()
        old = ep.EUDVariable()
        appended = ep.EUDVariable()
        appended << 0
        if ep.EUDIf()(start < _CODE_LEN[player]):
            remaining << _CODE_LEN[player] - start
            count << remaining
            if ep.EUDIf()(count >= chars_per_line):
                count << chars_per_line
            ep.EUDEndIf()
            old << ep.f_bread(_code_addr(player, start + count))
            ep.f_bwrite(_code_addr(player, start + count), 0)
            buffer.append(prefix, ep.ptr2s(_code_addr(player, start)), "\n")
            ep.f_bwrite(_code_addr(player, start + count), old)
            _PRINT_INDEX[player] = start + count
            appended << 1
        ep.EUDEndIf()
        return appended

    result = ep.EUDVariable()
    result << 0
    if isinstance(player_value, int):
        return _body(player_value)
    result << _body(player_value)
    return result


# region: compact maker runtime ----------------------------------------------

def configure(
    map_id: str,
    save_id=0,
    maker_key=None,
    *,
    chat_result: int = SCND_DEFAULT_CHAT_RESULT,
    chat_ptr: int = SCND_DEFAULT_CHAT_PTR,
    chat_len: int = SCND_DEFAULT_CHAT_LEN,
    chat_pattern: int = SCND_DEFAULT_CHAT_PATTERN,
    chat_code_append: int = SCND_CHAT_CODE_APPEND,
    chat_clear: int = SCND_CHAT_CLEAR,
    chat_load_done: int = SCND_CHAT_LOAD_DONE,
    chat_save: int = SCND_CHAT_SAVE,
    chat_load: int = SCND_CHAT_LOAD,
    code_view_ticks: int = SCND_SAVE_DATA_VIEW_TICKS,
    chars_per_line: int = SCND_SAVE_DATA_CHARS_PER_LINE,
    build_lines_per_tick: int = SCND_SAVE_DATA_BUILD_LINES_PER_TICK,
    visible_lines: int = SCND_SAVE_DATA_VISIBLE_LINES,
    display_line: int = 0,
    code_view_buffer_size: int = 14000,
    darken_code_view: bool = True,
    code_view_dark_brightness: int = SCND_CODE_VIEW_DARK_BRIGHTNESS,
    code_view_normal_brightness: int = SCND_CODE_VIEW_NORMAL_BRIGHTNESS,
    enable_hangul_keys: bool = True,
    msqcloader=None,
    auto_msqcloader: bool = True,
    require_msqcloader: bool = False,
    compat_mode: int = SCND_COMPAT_EXACT,
):
    """Configure SCND's maker-friendly save/load runtime."""
    global _APP_CONFIGURED, _APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY
    global _APP_COMPAT_MODE
    global _APP_CHAT_RESULT, _APP_CHAT_PTR, _APP_CHAT_LEN, _APP_CHAT_PATTERN
    global _APP_CHAT_CODE_APPEND, _APP_CHAT_CLEAR, _APP_CHAT_LOAD_DONE
    global _APP_CHAT_SAVE, _APP_CHAT_LOAD
    global _APP_CODE_VIEW_TICKS, _APP_CODE_VIEW_CHARS, _APP_CODE_VIEW_BUILD_LINES
    global _APP_CODE_VIEW_LINES, _APP_ENABLE_HANGUL_KEYS, _APP_DISPLAY_LINE
    global _APP_CODE_VIEW, _APP_CODE_VIEW_LINE_BYTES, _APP_CODE_VIEW_DESCRIPTOR
    global _APP_DARKEN_CODE_VIEW, _APP_CODE_VIEW_DARK_BRIGHTNESS
    global _APP_CODE_VIEW_NORMAL_BRIGHTNESS
    global _APP_NEW_USER_CODE_WORDS

    # EPScript callers can use scnd.configure(MAP_ID, SAVE_KEY, 1) because
    # keyword-only arguments are awkward in some editor/plugin call paths.
    if maker_key is not None and isinstance(save_id, str) and isinstance(maker_key, int):
        compat_mode = int(maker_key)
        maker_key = save_id
        save_id = 0

    if maker_key is None:
        if isinstance(save_id, str):
            maker_key = save_id
        else:
            raise RuntimeError("Call scnd.configure(map_id, maker_key).")
    elif int(save_id) != 0:
        raise RuntimeError("SCND save_id is fixed to 0.")

    _APP_MAP_ID = str(map_id)
    _APP_SAVE_ID = 0
    _APP_MAKER_KEY = str(maker_key)
    _APP_COMPAT_MODE = int(compat_mode)
    _APP_NEW_USER_CODE_WORDS = None
    if _APP_COMPAT_MODE < SCND_COMPAT_EXACT or _APP_COMPAT_MODE > SCND_COMPAT_MAX:
        raise RuntimeError("SCND compat_mode must be 0, 1, or 2.")
    if _FLAT:
        _rebuild_schema()
    _APP_CHAT_RESULT = int(chat_result)
    _APP_CHAT_PTR = int(chat_ptr)
    _APP_CHAT_LEN = int(chat_len)
    _APP_CHAT_PATTERN = int(chat_pattern)
    _APP_CHAT_CODE_APPEND = int(chat_code_append)
    _APP_CHAT_CLEAR = int(chat_clear)
    _APP_CHAT_LOAD_DONE = int(chat_load_done)
    _APP_CHAT_SAVE = int(chat_save)
    _APP_CHAT_LOAD = int(chat_load)
    _APP_CODE_VIEW_TICKS = int(code_view_ticks)
    _APP_CODE_VIEW_CHARS = int(chars_per_line)
    _APP_CODE_VIEW_BUILD_LINES = int(build_lines_per_tick)
    _APP_CODE_VIEW_LINES = int(visible_lines)
    _APP_CODE_VIEW_LINE_BYTES = max(16, int(chars_per_line) * 3 + 8)
    _APP_DISPLAY_LINE = int(display_line)
    _APP_DARKEN_CODE_VIEW = bool(darken_code_view)
    _APP_CODE_VIEW_DARK_BRIGHTNESS = max(1, min(31, int(code_view_dark_brightness)))
    _APP_CODE_VIEW_NORMAL_BRIGHTNESS = max(1, min(31, int(code_view_normal_brightness)))
    _APP_ENABLE_HANGUL_KEYS = bool(enable_hangul_keys)
    if _APP_CODE_VIEW is None:
        required_view_bytes = PLAYERS * _APP_CODE_VIEW_LINES * _APP_CODE_VIEW_LINE_BYTES
        _APP_CODE_VIEW = ep.Db(max(int(code_view_buffer_size), required_view_bytes))
    _ensure_code_view_descriptor()
    if msqcloader is not None:
        use_msqcloader(msqcloader)
    elif auto_msqcloader or require_msqcloader:
        auto_use_msqcloader(required=bool(require_msqcloader))
    _APP_CONFIGURED = True


setup = configure


def _encrypt_map_id(map_id: str, maker_key: str) -> str:
    map_id = str(map_id or "")
    maker_key = str(maker_key or "")
    if not map_id or not maker_key:
        return ""
    return hmac.new(maker_key.encode("utf-8"), map_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _new_user_code_words(map_id: str, maker_key: str) -> tuple[int, ...]:
    map_id_text = str(map_id or "")
    key_text = str(maker_key or "")
    if not map_id_text or not key_text:
        return ()
    material = "|".join(
        [
            map_id_text,
            str(int(_APP_SAVE_ID)),
            "%08x" % int(_PLAYER_SCHEMA_HASH),
            str(int(_PLAYER_TOTAL_BITS)),
            str(int(_PLAYER_PACKED_DWORDS)),
            str(int(_PLAYER_FIELD_COUNT)),
            str(int(_APP_COMPAT_MODE)),
            str(int(SCND_VERSION)),
        ]
    )
    digest = hmac.new(
        key_text.encode("utf-8"),
        (SCND_NEW_USER_DOMAIN + "|" + material).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return (
        SCND_NEW_USER_MAGIC,
        SCND_NEW_USER_MARKER,
        SCND_NEW_USER_VERSION,
        int(_PLAYER_SCHEMA_HASH) & 0xFFFFFFFF,
        int.from_bytes(digest[0:4], "little"),
        int.from_bytes(digest[4:8], "little"),
        int.from_bytes(digest[8:12], "little"),
        int.from_bytes(digest[12:16], "little"),
    )


def _active_new_user_code_words() -> tuple[int, ...]:
    global _APP_NEW_USER_CODE_WORDS
    if _APP_NEW_USER_CODE_WORDS is None:
        _APP_NEW_USER_CODE_WORDS = _new_user_code_words(_APP_MAP_ID, _APP_MAKER_KEY)
    return _APP_NEW_USER_CODE_WORDS


def _encode_hangul11172_dwords(dwords: tuple[int, ...]) -> str:
    if not dwords:
        return ""
    if len(dwords) >= SCND_HANGUL_BASE:
        raise RuntimeError("SCND Hangul packet dword count is too large.")
    payload = bytearray()
    for word in dwords:
        payload.extend((int(word) & 0xFFFFFFFF).to_bytes(4, "little"))
    chars = [chr(SCND_HANGUL_BASE_CP + len(dwords))]
    for offset in range(0, len(payload), SCND_HANGUL_GROUP_BYTES):
        chunk = payload[offset:offset + SCND_HANGUL_GROUP_BYTES]
        value = int.from_bytes(chunk, "little")
        for _ in range(SCND_HANGUL_GROUP_CHARS):
            digit = value % SCND_HANGUL_BASE
            chars.append(chr(SCND_HANGUL_BASE_CP + digit))
            value //= SCND_HANGUL_BASE
    return "".join(chars)


def _is_png_or_gif_bytes(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"GIF87a")
        or data.startswith(b"GIF89a")
    )


def _looks_like_base64_image(value: str) -> bool:
    if len(value) < 32 or len(value) % 4 != 0:
        return False
    if any(ch.isspace() for ch in value):
        return False
    return all(ch.isalnum() or ch in "+/=" for ch in value)


def _validate_map_image_value(image: str) -> str:
    text = str(image or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith("data:image/"):
        comma = text.find(",")
        if comma < 0:
            raise RuntimeError("SCND map image data URI must be base64 PNG or GIF.")
        header = text[:comma].lower()
        if not (header.startswith("data:image/png;") or header.startswith("data:image/gif;")):
            raise RuntimeError("SCND map image only supports PNG or GIF.")
        try:
            decoded = base64.b64decode(text[comma + 1 :], validate=True)
        except Exception as exc:
            raise RuntimeError("SCND map image data URI is not valid base64.") from exc
        if not _is_png_or_gif_bytes(decoded):
            raise RuntimeError("SCND map image base64 data is not a PNG or GIF.")
        return text

    if _looks_like_base64_image(text):
        try:
            decoded = base64.b64decode(text, validate=True)
        except Exception as exc:
            raise RuntimeError("SCND map image base64 data is invalid.") from exc
        if not _is_png_or_gif_bytes(decoded):
            raise RuntimeError("SCND map image base64 data is not a PNG or GIF.")
        return text

    path_without_query = text.split("?", 1)[0].split("#", 1)[0]
    ext = os.path.splitext(path_without_query)[1].lower()
    if ext not in (".png", ".gif"):
        raise RuntimeError("SCND map image only supports .png or .gif files.")

    if os.path.isfile(text):
        with open(text, "rb") as handle:
            if not _is_png_or_gif_bytes(handle.read(8)):
                raise RuntimeError("SCND map image file is not a PNG or GIF.")

    return text


def _map_image_mpq_path_for(image: str) -> str:
    ext = os.path.splitext(str(image or "").split("?", 1)[0].split("#", 1)[0])[1].lower()
    if ext == ".gif":
        return "1.gif"
    return "1.png"


def _prepare_map_image_manifest_value(image: str) -> str:
    text = _validate_map_image_value(image)
    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith("data:image/") or _looks_like_base64_image(text):
        return text

    if "://" in text:
        return text

    if not os.path.isfile(text):
        return text

    with open(text, "rb") as handle:
        image_bytes = handle.read()
    if not _is_png_or_gif_bytes(image_bytes):
        raise RuntimeError("SCND map image file is not a PNG or GIF.")

    image_mpq_path = _map_image_mpq_path_for(text)
    ep.MPQAddFile(image_mpq_path, image_bytes)
    return image_mpq_path


def set_map_info(display_name: str = "", image: str = ""):
    """Set optional launcher-facing map presentation metadata.

    display_name is the readable map name shown to launcher users.
    image can be a .png/.gif URL or path, data:image/png|gif base64 string, or
    raw base64 PNG/GIF image data. Local PNG/GIF files are embedded into the
    map MPQ and read by the launcher from the distributed map.
    """
    global _APP_MAP_DISPLAY_NAME, _APP_MAP_IMAGE
    _APP_MAP_DISPLAY_NAME = str(display_name or "")
    _APP_MAP_IMAGE = _validate_map_image_value(image)


set_map_presentation = set_map_info


def write_mpq_manifest(
    map_id: str = "",
    map_name: str = "",
    sheet_id: str = "",
    app_script_id: str = "",
    legacy_feature_flag: int = 0,
    *,
    mpq_path: str = SCND_MANIFEST_MPQ_PATH,
    maker_key=None,
    save_app_script_setup_path: str | None = "SCND_SERVER_PROPERTY_TEMPLATE.gs",
    private_spreadsheet_id: str = "",
    save_sheet_name: str = "SaveLog",
    display_name: str = "",
    image: str = "",
):
    """Write SCND launcher metadata into the output map MPQ.

    Call this once at compile time.  sheet_id is only the spreadsheet ID, not a
    full URL.
    """
    _require_configured()
    map_id_text = str(map_id or "")
    if not map_id_text:
        raise RuntimeError("SCND MAP_ID is required for the launcher manifest.")
    if map_id_text != _APP_MAP_ID:
        raise RuntimeError("SCND MAP_ID mismatch: scnd.configure() and scnd.write_mpq_manifest() must use the same MAP_ID.")
    if not _FLAT:
        raise RuntimeError("Call scnd.write_mpq_manifest after registering SCND bindings.")

    global _APP_NEW_USER_CODE_WORDS
    key = _APP_MAKER_KEY if maker_key is None else str(maker_key)
    map_id_key = _encrypt_map_id(map_id_text, key)
    _APP_NEW_USER_CODE_WORDS = _new_user_code_words(map_id_text, key)
    new_user_code = _encode_hangul11172_dwords(_APP_NEW_USER_CODE_WORDS)
    doc = {
        "scnd_manifest_version": 1,
        "map_id_key": map_id_key,
        "new_user_code": new_user_code,
        "map_name": str(map_name or ""),
        "map_display_name": str(display_name or _APP_MAP_DISPLAY_NAME or ""),
        "map_image": _prepare_map_image_manifest_value(image or _APP_MAP_IMAGE or ""),
        "sheet_id": str(sheet_id or ""),
        "app_script_id": str(app_script_id or ""),
        "compat_mode": int(_APP_COMPAT_MODE),
        "has_global_bindings": bool(_HAS_GLOBAL_BINDINGS),
        "player_total_bits": int(_PLAYER_TOTAL_BITS),
        "global_total_bits": int(_GLOBAL_TOTAL_BITS),
    }
    payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ep.MPQAddFile(str(mpq_path), payload)
    if save_app_script_setup_path and str(app_script_id or "").strip():
        setup_path = write_save_app_script_setup(
            map_id_text,
            key,
            map_id_key=map_id_key,
            path=str(save_app_script_setup_path),
            private_spreadsheet_id=private_spreadsheet_id,
            save_sheet_name=save_sheet_name,
        )
        doc["save_app_script_setup_path"] = setup_path
        print("[SCND] Save Apps Script property template written:", setup_path)
    return doc


def _require_configured():
    if not _APP_CONFIGURED:
        raise RuntimeError("Call scnd.configure(map_id, maker_key) first.")


def _ensure_msqcloader_runtime():
    init_msqcloader(required=False)


def set_launcher_input_probe_only(enabled: int = 1):
    """Debug mode: receive launcher packets but do not append/decode them."""
    global _APP_INPUT_PROBE_ONLY
    _APP_INPUT_PROBE_ONLY = bool(enabled)


@ep.EUDFunc
def _app_reset_launcher_input_state(player):
    _APP_HANGUL_INPUT_ACTIVE[player] = 0
    _APP_PACKET_SEQ_READY[player] = 0
    _APP_PACKET_LAST_SEQ[player] = 0
    _APP_PACKET_EXPECTED_SEQ[player] = 0
    _APP_PACKET_ARM_DELAY[player] = 0
    _APP_MSQC_STALE_TICKS[player] = 0


@ep.EUDFunc
def _app_close_load_gate(player):
    _APP_LOAD_ENABLED[player] = 0
    _app_reset_launcher_input_state(player)


@ep.EUDFunc
def _app_open_load_gate(player):
    if ep.EUDIf()([
        _APP_LOAD_QUEUED[player].Exactly(0),
        _STATE[player].Exactly(_PHASE_IDLE),
        _APP_HANGUL_LOAD_TAIL[player].Exactly(0),
    ]):
        if ep.EUDIf()(_APP_LOAD_ENABLED[player].Exactly(0)):
            clear_hangul8192_input(player)
            _app_reset_launcher_input_state(player)
            _APP_PACKET_PROBE_COUNT[player] = 0
            _APP_STATUS_TIMER[player] = 0
            _app_shared_eprintln(player, "\x07SCND\x04 load enabled. Send code with SCNDgram.")
        ep.EUDEndIf()
        _APP_LOAD_ENABLED[player] = 1
    ep.EUDEndIf()


@ep.EUDFunc
def _app_abort_load_runtime(player):
    _app_close_load_gate(player)
    _APP_LOAD_QUEUED[player] = 0
    _APP_HANGUL_LOAD_TAIL[player] = 0
    _APP_HANGUL_INPUT_ACTIVE[player] = 0
    _APP_PACKET_PROBE_COUNT[player] = 0
    _release_load_if_owned(player)
    _release_global_pending_if_owned(player)
    _INPUT_LEN[player] = 0
    _HANGUL_INPUT_LEN[player] = 0


@ep.EUDFunc
def _app_queue_load_runtime(player):
    if ep.EUDIf()(_APP_LOAD_QUEUED[player].Exactly(0)):
        _app_shared_eprintln(player, "\x07SCND\x04 load queued. Waiting for another load to finish.")
    ep.EUDEndIf()
    _APP_LOAD_QUEUED[player] = 1
    _app_close_load_gate(player)
    _APP_HANGUL_INPUT_ACTIVE[player] = 0
    _APP_STATUS_TIMER[player] = 0


@ep.EUDFunc
def _app_disable_load_runtime(player):
    if ep.EUDIf()(_APP_LOAD_ENABLED[player].Exactly(1)):
        _APP_STATUS_TIMER[player] = 0
        _app_shared_eprintln(player, "\x08SCND\x04 load disabled.")
    ep.EUDEndIf()
    _app_abort_load_runtime(player)


def save(player_value):
    """Start a save job with the configured context."""
    _require_configured()
    _ensure_msqcloader_runtime()

    def _body(player):
        if ep.EUDIf()([busy(player).Exactly(0), _APP_CODE_BUILD_ACTIVE[player].Exactly(0)]):
            reset_done(player)
            _app_abort_load_runtime(player)
            started = begin_save(player, _APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY)
            if ep.EUDIf()(started.AtLeast(1)):
                _APP_STATUS_TIMER[player] = 0
                _app_shared_eprintln(player, "\x07SCND\x04 save requested.")
            ep.EUDEndIf()
        ep.EUDEndIf()

    return _body(player_value)


def save_global(player_value):
    """Start a shared/global save job with the configured context."""
    _require_configured()
    _ensure_msqcloader_runtime()

    def _body(player):
        if ep.EUDIf()([busy(player).Exactly(0), _APP_CODE_BUILD_ACTIVE[player].Exactly(0)]):
            reset_done(player)
            _app_abort_load_runtime(player)
            started = begin_save_global(player, _APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY)
            if ep.EUDIf()(started.AtLeast(1)):
                _APP_STATUS_TIMER[player] = 0
                _app_shared_eprintln(player, "\x07SCND\x04 global save requested.")
            ep.EUDEndIf()
        ep.EUDEndIf()

    return _body(player_value)


@ep.EUDFunc
def _begin_load_from_ready_input(player):
    started = ep.EUDVariable()
    resume_player_tail = ep.EUDVariable()
    started << 0
    resume_player_tail << _APP_HANGUL_LOAD_TAIL[player]
    if ep.EUDIf()(_reserve_load_for(player).AtLeast(1)):
        if _HAS_GLOBAL_BINDINGS:
            if ep.EUDIf()(resume_player_tail.Exactly(1)):
                if ep.EUDIf()(hangul8192_input_ready(player).Exactly(1)):
                    _APP_HANGUL_LOAD_TAIL[player] = 0
                    started << begin_load_hangul8192(player, _APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY)
                ep.EUDEndIf()
            if ep.EUDElseIf()(_hangul8192_combined_input_ready(player).Exactly(1)):
                if ep.EUDIf()(_reserve_global_pending_for(player).AtLeast(1)):
                    _APP_HANGUL_LOAD_TAIL[player] = 1
                    started << begin_load_hangul8192_global(player, _APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY)
                if ep.EUDElse()():
                    _release_load_if_owned(player)
                    _app_queue_load_runtime(player)
                ep.EUDEndIf()
            if ep.EUDElseIf()(hangul8192_input_ready(player).Exactly(1)):
                _APP_HANGUL_LOAD_TAIL[player] = 0
                _fail_load_now(player, RESULT_GLOBAL_REQUIRED)
            if ep.EUDElse()():
                _APP_HANGUL_LOAD_TAIL[player] = 0
                _fail_load_now(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
            _APP_HANGUL_INPUT_ACTIVE[player] = 0
        else:
            if ep.EUDIf()(_hangul8192_has_tail_after_current(player).AtLeast(1)):
                _APP_HANGUL_LOAD_TAIL[player] = 0
                _fail_load_now(player, RESULT_UNEXPECTED_GLOBAL)
                _APP_HANGUL_INPUT_ACTIVE[player] = 0
            if ep.EUDElseIf()(hangul8192_input_ready(player).Exactly(1)):
                started << begin_load_hangul8192(player, _APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY)
                _APP_HANGUL_INPUT_ACTIVE[player] = 0
            if ep.EUDElseIf()(_INPUT_LEN[player].AtLeast(1)):
                _APP_HANGUL_LOAD_TAIL[player] = 0
                started << begin_load(player, _APP_MAP_ID, _APP_SAVE_ID, _APP_MAKER_KEY)
            ep.EUDEndIf()
    if ep.EUDElse()():
        _app_queue_load_runtime(player)
    ep.EUDEndIf()
    if ep.EUDIf()(started.AtLeast(1)):
        _app_close_load_gate(player)
        _APP_STATUS_TIMER[player] = 0
        _app_shared_eprintln(player, "\x07SCND\x04 load requested.")
    ep.EUDEndIf()
    return started


@ep.EUDFunc
def _app_try_start_queued_load(player):
    if ep.EUDIf()([
        _APP_LOAD_QUEUED[player].Exactly(1),
        _processing_busy(player).Exactly(0),
        _APP_CODE_BUILD_ACTIVE[player].Exactly(0),
        _load_available_for(player).AtLeast(1),
    ]):
        if _HAS_GLOBAL_BINDINGS:
            if ep.EUDIf()(_hangul8192_combined_input_ready(player).Exactly(1)):
                if ep.EUDIf()(_global_pending_available_for(player).AtLeast(1)):
                    _APP_LOAD_QUEUED[player] = 0
                    _begin_load_from_ready_input(player)
                ep.EUDEndIf()
            if ep.EUDElseIf()(hangul8192_input_ready(player).Exactly(1)):
                _APP_LOAD_QUEUED[player] = 0
                _APP_HANGUL_LOAD_TAIL[player] = 0
                _fail_load_now(player, RESULT_GLOBAL_REQUIRED)
            if ep.EUDElse()():
                _APP_LOAD_QUEUED[player] = 0
                _APP_HANGUL_LOAD_TAIL[player] = 0
                _fail_load_now(player, RESULT_BAD_CODE)
            ep.EUDEndIf()
        else:
            _APP_LOAD_QUEUED[player] = 0
            _begin_load_from_ready_input(player)
    ep.EUDEndIf()


def load(player_value):
    """Start a load job from Hangul direct input or legacy `!L` input."""
    _require_configured()
    _ensure_msqcloader_runtime()

    def _body(player):
        reset_done(player)
        if ep.EUDIf()([
            _APP_LOAD_ENABLED[player].Exactly(0),
            _APP_LOAD_QUEUED[player].Exactly(0),
            _APP_HANGUL_LOAD_TAIL[player].Exactly(0),
        ]):
            _app_shared_eprintln(player, "\x08SCND\x04 load is not enabled yet.")
        if ep.EUDElseIf()([
            _APP_LOAD_QUEUED[player].Exactly(0),
            _processing_busy(player).Exactly(0),
            _APP_CODE_BUILD_ACTIVE[player].Exactly(0),
        ]):
            _begin_load_from_ready_input(player)
        ep.EUDEndIf()

    return _body(player_value)


def _load_enabled_value(enabled):
    if isinstance(enabled, bool):
        return 1 if enabled else 0
    if isinstance(enabled, int):
        return 1 if enabled else 0
    return enabled


def set_load_enabled(player_value, enabled: int = 1):
    """Allow or block SCND load input for one player.

    SCNDgram packet input only starts a load when this value is 1.
    The runtime automatically switches it back to 0 after load completion.
    """
    value = _load_enabled_value(enabled)
    if isinstance(value, int):
        if value:
            return _app_open_load_gate(player_value)
        return _app_disable_load_runtime(player_value)

    if ep.EUDIf()(value.AtLeast(1)):
        _app_open_load_gate(player_value)
    if ep.EUDElse()():
        _app_disable_load_runtime(player_value)
    ep.EUDEndIf()


def enable_load(player_value):
    return _app_open_load_gate(player_value)


def disable_load(player_value):
    return _app_disable_load_runtime(player_value)


def set_all_load_enabled(enabled: int = 1):
    value = _load_enabled_value(enabled)
    for player in range(PLAYERS):
        if isinstance(value, int):
            if value:
                _app_open_load_gate(player)
            else:
                _app_disable_load_runtime(player)
        else:
            if ep.EUDIf()(value.AtLeast(1)):
                _app_open_load_gate(player)
            if ep.EUDElse()():
                _app_disable_load_runtime(player)
            ep.EUDEndIf()


def load_enabled(player_value):
    return _APP_LOAD_ENABLED[player_value]


def clear_runtime_input(player_value):
    clear_input(player_value)
    clear_hangul8192_input(player_value)
    _app_abort_load_runtime(player_value)


def append_load_line_from_chat(player_value, prefix_len: int = 3):
    """Append a maker-approved chat line to the Base64 load buffer."""
    ptr = ep.f_dwread(_APP_CHAT_PTR)
    length = ep.f_dwread(_APP_CHAT_LEN)
    return append_input_from_chat(player_value, ptr, length, int(prefix_len))


@ep.EUDFunc
def handle_chat(player_value):
    """Handle configured chatEvent memory commands."""
    _require_configured()

    def _body(player):
        if ep.EUDIf()(ep.Memory(_APP_CHAT_RESULT, ep.Exactly, _APP_CHAT_SAVE)):
            save(player)
            ep.DoActions(ep.SetMemory(_APP_CHAT_RESULT, ep.SetTo, 0))
        ep.EUDEndIf()

        if ep.EUDIf()(ep.Memory(_APP_CHAT_RESULT, ep.Exactly, _APP_CHAT_CLEAR)):
            clear_runtime_input(player)
            ep.DoActions(ep.SetMemory(_APP_CHAT_RESULT, ep.SetTo, 0))
        ep.EUDEndIf()

        if ep.EUDIf()(ep.Memory(_APP_CHAT_PATTERN, ep.Exactly, _APP_CHAT_CODE_APPEND)):
            _app_shared_eprintln(player, "\x08SCND\x04 chat load input is disabled. Use SCNDgram packet send.")
            ep.DoActions(ep.SetMemory(_APP_CHAT_PATTERN, ep.SetTo, 0))
        ep.EUDEndIf()

        if ep.EUDIf()(ep.Memory(_APP_CHAT_RESULT, ep.Exactly, _APP_CHAT_LOAD_DONE)):
            load(player)
            ep.DoActions(ep.SetMemory(_APP_CHAT_RESULT, ep.SetTo, 0))
        ep.EUDEndIf()

        if ep.EUDIf()(ep.Memory(_APP_CHAT_RESULT, ep.Exactly, _APP_CHAT_LOAD)):
            load(player)
            ep.DoActions(ep.SetMemory(_APP_CHAT_RESULT, ep.SetTo, 0))
        ep.EUDEndIf()

    return _body(player_value)


def _app_read_key_state(vk):
    _APP_KEY_READER.seekoffset(_APP_KEY_STATE_ADDR + int(vk))
    return ep.f_bitand(_APP_KEY_READER.readbyte(), 1)


def _app_set_msqc_transfer(active):
    SCNDInputMSQCIsTransfer << active


def _app_set_msqc_send(channel, value, parity):
    _SCND_INPUT_MSQC_SENDS[int(channel) - 1] << _msqc_word_to_pos(value, parity)


def _app_clear_msqc_send_channels():
    _app_set_msqc_transfer(0)
    for channel in range(1, _MSQC_SCND_INPUT_CHANNEL_COUNT + 1):
        _app_set_msqc_send(channel, 0, 0)


def _app_read_msqc_receive_raw(channel, player):
    receive = _SCND_INPUT_MSQC_RECEIVES[int(channel) - 1]
    return receive[player]


def _app_read_msqc_receive(channel, player):
    receive = _SCND_INPUT_MSQC_RECEIVES[int(channel) - 1]
    return _msqc_pos_to_word(receive[player])


def _app_clear_msqc_receive(channel, player):
    receive = _SCND_INPUT_MSQC_RECEIVES[int(channel) - 1]
    receive[player] = -1


@ep.EUDFunc
def _app_msqc_ch1_known_parity(parity):
    known = ep.EUDVariable()
    known << 0
    if ep.EUDIf()(parity.Exactly(_MSQC_SCND_INPUT_START)):
        known << 1
    if ep.EUDElseIf()(parity.Exactly(_MSQC_SCND_INPUT_FIRST)):
        known << 1
    if ep.EUDElseIf()(parity.Exactly(_MSQC_SCND_INPUT_COMMIT)):
        known << 1
    if ep.EUDElseIf()(parity.Exactly(_MSQC_SCND_INPUT_CANCEL)):
        known << 1
    if ep.EUDElseIf()(parity.Exactly(_MSQC_SCND_INPUT_CODE_CLOSE)):
        known << 1
    ep.EUDEndIf()
    return known


def _app_send_launcher_input_sync(player):
    _app_scan_keys(player)
    if ep.EUDIf()([
        _APP_CODE_VIEW_TIMER[player] > 0,
        _APP_CODE_VIEW_LOCAL_CLOSED[player].Exactly(0),
        _app_key_cur(player, _APP_KEY_ESCAPE_SLOT).Exactly(1),
        _app_key_prev(player, _APP_KEY_ESCAPE_SLOT).Exactly(0),
    ]):
        _app_set_msqc_transfer(1)
        _app_set_msqc_send(1, _MSQC_SCND_INPUT_MAGIC, _MSQC_SCND_INPUT_CODE_CLOSE)
        _app_set_msqc_send(2, 0, 0)
        _app_set_msqc_send(3, 0, 0)
    if ep.EUDElseIf()(_STATE[player].Exactly(_PHASE_IDLE)):
        if ep.EUDIf()([
            _app_key_cur(player, _APP_KEY_GATE_SLOT).Exactly(1),
            _app_key_cur(player, _APP_KEY_STROBE_SLOT).Exactly(1),
        ]):
            first_value = _app_read_launcher_packet_value(player)
            second_value = _app_read_launcher_packet_second_value(player)
            packet_seq = _app_read_launcher_packet_seq(player)
            _app_set_msqc_transfer(1)
            _app_set_msqc_send(1, first_value, _MSQC_SCND_INPUT_FIRST)
            _app_set_msqc_send(2, second_value, _MSQC_SCND_INPUT_SECOND)
            _app_set_msqc_send(3, packet_seq, _MSQC_SCND_INPUT_SEQ)
        if ep.EUDElseIf()([
            _app_key_cur(player, _APP_KEY_GATE_SLOT).Exactly(1),
            _app_key_cur(player, _APP_KEY_COMMIT_SLOT).Exactly(1),
            _app_key_prev(player, _APP_KEY_COMMIT_SLOT).Exactly(0),
        ]):
            _app_set_msqc_transfer(1)
            _app_set_msqc_send(1, _MSQC_SCND_INPUT_MAGIC, _MSQC_SCND_INPUT_COMMIT)
            _app_set_msqc_send(2, 0, 0)
            _app_set_msqc_send(3, 0, 0)
        if ep.EUDElseIf()([
            _app_key_cur(player, _APP_KEY_GATE_SLOT).Exactly(1),
            _app_key_cur(player, _APP_KEY_CANCEL_SLOT).Exactly(1),
            _app_key_prev(player, _APP_KEY_CANCEL_SLOT).Exactly(0),
        ]):
            _app_set_msqc_transfer(1)
            _app_set_msqc_send(1, _MSQC_SCND_INPUT_MAGIC, _MSQC_SCND_INPUT_CANCEL)
            _app_set_msqc_send(2, 0, 0)
            _app_set_msqc_send(3, 0, 0)
        if ep.EUDElse()():
            _app_clear_msqc_send_channels()
        ep.EUDEndIf()
    if ep.EUDElse()():
        _app_clear_msqc_send_channels()
    ep.EUDEndIf()
    _app_save_keys(player)


def _app_process_launcher_input_sync(player):
    raw1 = ep.EUDVariable()
    word1 = ep.EUDVariable()
    word2 = ep.EUDVariable()
    word3 = ep.EUDVariable()
    parity1 = ep.EUDVariable()
    parity2 = ep.EUDVariable()
    parity3 = ep.EUDVariable()
    value1 = ep.EUDVariable()
    value2 = ep.EUDVariable()
    value3 = ep.EUDVariable()
    handled = ep.EUDVariable()

    raw1 << _app_read_msqc_receive_raw(1, player)
    word1 << _app_read_msqc_receive(1, player)
    word2 << _app_read_msqc_receive(2, player)
    word3 << _app_read_msqc_receive(3, player)
    parity1 << _msqc_word_parity(word1)
    parity2 << _msqc_word_parity(word2)
    parity3 << _msqc_word_parity(word3)
    value1 << _msqc_word_value(word1)
    value2 << _msqc_word_value(word2)
    value3 << _msqc_word_value(word3)
    handled << 0

    if ep.EUDIf()([parity1.Exactly(_MSQC_SCND_INPUT_CODE_CLOSE), value1.Exactly(_MSQC_SCND_INPUT_MAGIC)]):
        handled << 1
        _APP_CODE_VIEW_TIMER[player] = 0
        _APP_CODE_VIEW_LOCAL_CLOSED[player] = 1
        _app_clear_msqc_receive(1, player)
        _app_clear_msqc_receive(2, player)
        _app_clear_msqc_receive(3, player)
    ep.EUDEndIf()

    if ep.EUDIf()(_STATE[player].Exactly(_PHASE_IDLE)):
        if ep.EUDIf()([parity1.Exactly(_MSQC_SCND_INPUT_START), value1.Exactly(_MSQC_SCND_INPUT_MAGIC)]):
            handled << 1
            if ep.EUDIf()(_APP_LOAD_ENABLED[player].Exactly(1)):
                if ep.EUDIf()(_HANGUL_INPUT_LEN[player].Exactly(0)):
                    start_hangul_input(player)
                    _APP_STATUS_TIMER[player] = 0
                    _app_shared_eprintln(player, "\x07SCND\x04 load input started.")
                ep.EUDEndIf()
            if ep.EUDElse()():
                _app_shared_eprintln(player, "\x08SCND\x04 load is not enabled now.")
            ep.EUDEndIf()
            _app_clear_msqc_receive(1, player)
            _app_clear_msqc_receive(2, player)
            _app_clear_msqc_receive(3, player)
        ep.EUDEndIf()

        if ep.EUDIf()([
            parity1.Exactly(_MSQC_SCND_INPUT_FIRST),
            parity2.Exactly(_MSQC_SCND_INPUT_SECOND),
            parity3.Exactly(_MSQC_SCND_INPUT_SEQ),
        ]):
            handled << 1
            if _APP_INPUT_PROBE_ONLY:
                if ep.EUDIf()(_APP_LOAD_ENABLED[player].Exactly(1)):
                    _APP_PACKET_PROBE_COUNT[player] += 1
                    _APP_STATUS_TIMER[player] = 0
                ep.EUDEndIf()
            else:
                if ep.EUDIf()([
                    _APP_LOAD_ENABLED[player].Exactly(1),
                    _APP_HANGUL_INPUT_ACTIVE[player].Exactly(0),
                ]):
                    start_hangul_input(player)
                ep.EUDEndIf()
                if ep.EUDIf()(_APP_HANGUL_INPUT_ACTIVE[player].Exactly(1)):
                    if ep.EUDIf()(_app_accept_launcher_packet(player, value3, value1, value2).AtLeast(1)):
                        _APP_STATUS_TIMER[player] = 0
                        _app_try_auto_load_hangul(player)
                    ep.EUDEndIf()
                ep.EUDEndIf()
            _app_clear_msqc_receive(1, player)
            _app_clear_msqc_receive(2, player)
            _app_clear_msqc_receive(3, player)
        ep.EUDEndIf()

        if ep.EUDIf()([parity1.Exactly(_MSQC_SCND_INPUT_COMMIT), value1.Exactly(_MSQC_SCND_INPUT_MAGIC)]):
            handled << 1
            if _HAS_GLOBAL_BINDINGS:
                if ep.EUDIf()(_hangul8192_combined_input_ready(player).Exactly(1)):
                    load(player)
                    _APP_HANGUL_INPUT_ACTIVE[player] = 0
                    _APP_PACKET_ARM_DELAY[player] = 0
                if ep.EUDElseIf()(hangul8192_input_ready(player).Exactly(1)):
                    load(player)
                    _APP_HANGUL_INPUT_ACTIVE[player] = 0
                    _APP_PACKET_ARM_DELAY[player] = 0
                ep.EUDEndIf()
            else:
                if ep.EUDIf()(_hangul8192_has_tail_after_current(player).AtLeast(1)):
                    load(player)
                    _APP_HANGUL_INPUT_ACTIVE[player] = 0
                    _APP_PACKET_ARM_DELAY[player] = 0
                if ep.EUDElseIf()(hangul8192_input_ready(player).Exactly(1)):
                    load(player)
                    _APP_HANGUL_INPUT_ACTIVE[player] = 0
                    _APP_PACKET_ARM_DELAY[player] = 0
                ep.EUDEndIf()
            _app_clear_msqc_receive(1, player)
            _app_clear_msqc_receive(2, player)
            _app_clear_msqc_receive(3, player)
        ep.EUDEndIf()

        if ep.EUDIf()([parity1.Exactly(_MSQC_SCND_INPUT_CANCEL), value1.Exactly(_MSQC_SCND_INPUT_MAGIC)]):
            handled << 1
            clear_hangul8192_input(player)
            _app_reset_launcher_input_state(player)
            _app_clear_msqc_receive(1, player)
            _app_clear_msqc_receive(2, player)
            _app_clear_msqc_receive(3, player)
        ep.EUDEndIf()
    ep.EUDEndIf()

    if ep.EUDIf()(handled.AtLeast(1)):
        _APP_MSQC_STALE_TICKS[player] = 0
    ep.EUDEndIf()

    if ep.EUDIf()(handled.Exactly(0)):
        if ep.EUDIfNot()(raw1.Exactly(-1)):
            if ep.EUDIfNot()(raw1.Exactly(0)):
                if ep.EUDIf()(_app_msqc_ch1_known_parity(parity1).AtLeast(1)):
                    _APP_MSQC_STALE_TICKS[player] += 1
                    if ep.EUDIf()(_APP_MSQC_STALE_TICKS[player].AtLeast(_MSQC_SCND_INPUT_STALE_LIMIT)):
                        _APP_MSQC_STALE_TICKS[player] = 0
                        _app_clear_msqc_receive(1, player)
                        _app_clear_msqc_receive(2, player)
                        _app_clear_msqc_receive(3, player)
                    ep.EUDEndIf()
                if ep.EUDElse()():
                    _APP_MSQC_STALE_TICKS[player] = 0
                    _app_clear_msqc_receive(1, player)
                    _app_clear_msqc_receive(2, player)
                    _app_clear_msqc_receive(3, player)
                ep.EUDEndIf()
            ep.EUDEndIf()
        ep.EUDEndIf()
    ep.EUDEndIf()


def _app_key_slot(player, key):
    return player * _APP_KEY_COUNT + int(key)


def _app_key_cur(player, key):
    return _APP_KEY_CUR[_app_key_slot(player, key)]


def _app_key_prev(player, key):
    return _APP_KEY_PREV[_app_key_slot(player, key)]


def _app_scan_keys(player):
    for key, vk in enumerate(_APP_PACKET_FIRST_VKS):
        _APP_KEY_CUR[_app_key_slot(player, key)] = _app_read_key_state(vk)
    for key, vk in enumerate(_APP_PACKET_SECOND_VKS):
        _APP_KEY_CUR[_app_key_slot(player, 14 + key)] = _app_read_key_state(vk)
    for key, vk in enumerate(_APP_PACKET_SEQ_VKS):
        _APP_KEY_CUR[_app_key_slot(player, 28 + key)] = _app_read_key_state(vk)
    _APP_KEY_CUR[_app_key_slot(player, _APP_KEY_GATE_SLOT)] = _app_read_key_state(_APP_VK_SCND_GATE)
    _APP_KEY_CUR[_app_key_slot(player, _APP_KEY_STROBE_SLOT)] = _app_read_key_state(_APP_VK_SCND_STROBE)
    _APP_KEY_CUR[_app_key_slot(player, _APP_KEY_COMMIT_SLOT)] = _app_read_key_state(_APP_VK_COMMIT)
    _APP_KEY_CUR[_app_key_slot(player, _APP_KEY_CANCEL_SLOT)] = _app_read_key_state(_APP_VK_CANCEL)
    _APP_KEY_CUR[_app_key_slot(player, _APP_KEY_ESCAPE_SLOT)] = _app_read_key_state(_APP_VK_ESCAPE)


def _app_save_keys(player):
    for key in range(_APP_KEY_COUNT):
        _APP_KEY_PREV[_app_key_slot(player, key)] = _APP_KEY_CUR[_app_key_slot(player, key)]


def _app_read_launcher_packet_value(player):
    value = ep.EUDVariable()
    value << 0
    for bit in range(14):
        if ep.EUDIf()(_app_key_cur(player, bit).Exactly(1)):
            value += 1 << bit
        ep.EUDEndIf()
    return value


def _app_read_launcher_packet_second_value(player):
    value = ep.EUDVariable()
    value << 0
    for bit in range(14):
        if ep.EUDIf()(_app_key_cur(player, 14 + bit).Exactly(1)):
            value += 1 << bit
        ep.EUDEndIf()
    return value


def _app_read_launcher_packet_seq(player):
    seq = ep.EUDVariable()
    seq << 0
    for bit in range(8):
        if ep.EUDIf()(_app_key_cur(player, 28 + bit).Exactly(1)):
            seq += 1 << bit
        ep.EUDEndIf()
    return seq


def _app_append_hangul_value(player, value):
    if ep.EUDIf()(value < SCND_HANGUL_BASE):
        append_hangul8192_value(player, value)
    if ep.EUDElse()():
        clear_hangul8192_input(player)
        _APP_HANGUL_INPUT_ACTIVE[player] = 0
        _APP_PACKET_ARM_DELAY[player] = 0
    ep.EUDEndIf()


def _app_append_hangul_pair(player, first_value, second_value):
    expected_chars = ep.EUDVariable()
    _app_append_hangul_value(player, first_value)
    expected_chars << _hangul8192_accept_total_chars(player)
    if ep.EUDIf()([
        expected_chars.AtLeast(1),
        _HANGUL_INPUT_LEN[player] < expected_chars,
    ]):
        _app_append_hangul_value(player, second_value)
    ep.EUDEndIf()


def _app_accept_launcher_packet(player, packet_seq, first_value, second_value):
    accepted = ep.EUDVariable()
    accepted << 0

    if ep.EUDIf()(_APP_PACKET_SEQ_READY[player].Exactly(0)):
        if ep.EUDIf()(packet_seq.Exactly(0)):
            _APP_PACKET_SEQ_READY[player] = 1
            _APP_PACKET_LAST_SEQ[player] = packet_seq
            _APP_PACKET_EXPECTED_SEQ[player] = 1
            _app_append_hangul_pair(player, first_value, second_value)
            accepted << 1
        if ep.EUDElse()():
            accepted << 0
        ep.EUDEndIf()
    if ep.EUDElse()():
        if ep.EUDIf()(packet_seq.Exactly(_APP_PACKET_LAST_SEQ[player])):
            accepted << 0
        if ep.EUDElseIf()(packet_seq.Exactly(_APP_PACKET_EXPECTED_SEQ[player])):
            _APP_PACKET_LAST_SEQ[player] = packet_seq
            if ep.EUDIf()(_APP_PACKET_EXPECTED_SEQ[player].Exactly(255)):
                _APP_PACKET_EXPECTED_SEQ[player] = 0
            if ep.EUDElse()():
                _APP_PACKET_EXPECTED_SEQ[player] += 1
            ep.EUDEndIf()
            _app_append_hangul_pair(player, first_value, second_value)
            accepted << 1
        if ep.EUDElse()():
            _app_shared_eprintln(
                player,
                "\x08SCND\x04 load input packet dropped. resend please. expected=",
                _APP_PACKET_EXPECTED_SEQ[player],
                " got=",
                packet_seq,
            )
            clear_hangul8192_input(player)
            _APP_HANGUL_INPUT_ACTIVE[player] = 0
            _APP_PACKET_SEQ_READY[player] = 0
            _APP_PACKET_LAST_SEQ[player] = 0
            _APP_PACKET_EXPECTED_SEQ[player] = 0
            _APP_PACKET_ARM_DELAY[player] = 0
        ep.EUDEndIf()
    ep.EUDEndIf()

    return accepted


def _app_try_auto_load_hangul(player):
    if ep.EUDIf()([
        _APP_HANGUL_INPUT_ACTIVE[player].Exactly(1),
        _hangul8192_combined_input_ready(player).Exactly(1),
    ]):
        _APP_STATUS_TIMER[player] = 0
        _app_shared_eprintln(player, "\x07SCND\x04 load input complete. decoding starts.")
        load(player)
        _APP_HANGUL_INPUT_ACTIVE[player] = 0
        _APP_PACKET_ARM_DELAY[player] = 0
    ep.EUDEndIf()


def _app_update_launcher_input_local(player):
    _app_send_launcher_input_sync(player)


@ep.EUDFunc
def _start_hangul_input_body(player):
    clear_hangul8192_input(player)
    _APP_HANGUL_INPUT_ACTIVE[player] = 1
    _APP_PACKET_SEQ_READY[player] = 0
    _APP_PACKET_LAST_SEQ[player] = 0
    _APP_PACKET_EXPECTED_SEQ[player] = 0
    _APP_PACKET_ARM_DELAY[player] = 3


def start_hangul_input(player_value):
    return _start_hangul_input_body(player_value)


@ep.EUDFunc
def update_hangul_input(player_value):
    if not _APP_ENABLE_HANGUL_KEYS:
        return
    _ensure_msqcloader_runtime()

    def _body(player):
        _app_process_launcher_input_sync(player)
        if ep.EUDIf()(ep.IsUserCP()):
            _app_update_launcher_input_local(player)
        ep.EUDEndIf()

    return _body(player_value)


@ep.EUDFunc
def _app_try_start_code_view(player):
    if ep.EUDIf()(_APP_CODE_BUILD_ACTIVE[player].Exactly(0)):
        _APP_CODE_VIEW_TIMER[player] = 0
        _APP_CODE_VIEW_LOCAL_CLOSED[player] = 0
        _begin_app_code_view_text(player)
        _APP_CODE_BUILD_ACTIVE[player] = 1
    ep.EUDEndIf()


@ep.EUDFunc
def handle_messages(player_value):
    def _body(player):
        msg = ep.EUDVariable()
        msg << _MESSAGE[player]

        if ep.EUDIf()(msg.Exactly(MESSAGE_SAVE_DONE)):
            reset_message(player)
            _APP_STATUS_TIMER[player] = 0
            _app_abort_load_runtime(player)
            _app_shared_eprintln(player, "\x07SCND\x04 save done.")
            _app_try_start_code_view(player)
        ep.EUDEndIf()

        if ep.EUDIf()(msg.Exactly(MESSAGE_SAVE_FAILED)):
            reset_message(player)
            _APP_STATUS_TIMER[player] = 0
            _app_abort_load_runtime(player)
            _app_shared_eprintln(player, "\x08SCND\x04 save failed.")
            _MESSAGE[player] = MESSAGE_NONE
        ep.EUDEndIf()

        if ep.EUDIf()(msg.Exactly(MESSAGE_LOAD_DONE)):
            reset_message(player)
            _APP_STATUS_TIMER[player] = 0
            if ep.EUDIf()(_APP_HANGUL_LOAD_TAIL[player].Exactly(1)):
                _APP_HANGUL_LOAD_TAIL[player] = 0
                _shift_hangul8192_tail(player)
                if ep.EUDIf()(hangul8192_input_ready(player).Exactly(1)):
                    _STATE[player] = _PHASE_IDLE
                    _RESULT[player] = RESULT_NONE
                    _MESSAGE[player] = MESSAGE_NONE
                    _APP_HANGUL_LOAD_TAIL[player] = 1
                    _app_shared_eprintln(player, "\x07SCND\x04 processing next load data.")
                    load(player)
                if ep.EUDElse()():
                    _app_abort_load_runtime(player)
                    _STATE[player] = _PHASE_FAILED
                    _RESULT[player] = RESULT_BAD_CODE
                    _app_shared_eprintln(player, "\x08SCND\x04 invalid remaining load data.")
                    _MESSAGE[player] = MESSAGE_NONE
                ep.EUDEndIf()
            if ep.EUDElse()():
                _app_abort_load_runtime(player)
                _app_shared_eprintln(player, "\x07SCND\x04 load done.")
                _MESSAGE[player] = MESSAGE_NONE
            ep.EUDEndIf()
        ep.EUDEndIf()

        if ep.EUDIf()(msg.Exactly(MESSAGE_LOAD_FAILED)):
            reset_message(player)
            _APP_STATUS_TIMER[player] = 0
            _app_abort_load_runtime(player)
            result = ep.EUDVariable()
            result << _RESULT[player]
            if ep.EUDIf()(result.Exactly(RESULT_GLOBAL_REQUIRED)):
                _app_shared_eprintln(player, "\x08SCND\x04 global data required. Send global + player data together.")
            if ep.EUDElseIf()(result.Exactly(RESULT_GLOBAL_MISMATCH)):
                _app_shared_eprintln(player, "\x08SCND\x04 global data mismatch.")
            if ep.EUDElseIf()(result.Exactly(RESULT_UNEXPECTED_GLOBAL)):
                _app_shared_eprintln(player, "\x08SCND\x04 unexpected global data.")
            if ep.EUDElseIf()(result.Exactly(RESULT_BUSY)):
                _app_shared_eprintln(player, "\x08SCND\x04 another load is still running. Please try again shortly.")
            if ep.EUDElseIf()(result.Exactly(RESULT_BAD_SCHEMA)):
                _app_shared_eprintln(player, "\x08SCND\x04 load failed: schema mismatch.")
            if ep.EUDElseIf()(result.Exactly(RESULT_BAD_TAG)):
                _app_shared_eprintln(player, "\x08SCND\x04 load failed: key or player name mismatch.")
            if ep.EUDElseIf()(result.Exactly(RESULT_BAD_CODE)):
                _app_shared_eprintln(player, "\x08SCND\x04 load failed: bad code payload.")
            if ep.EUDElseIf()(result.Exactly(RESULT_TOO_LARGE)):
                _app_shared_eprintln(player, "\x08SCND\x04 load failed: code is too large.")
            if ep.EUDElse()():
                _app_shared_eprintln(player, "\x08SCND\x04 load failed.")
            ep.EUDEndIf()
            _MESSAGE[player] = MESSAGE_NONE
        ep.EUDEndIf()

    return _body(player_value)


def _hangul_input_visible_count(player):
    return _HANGUL_INPUT_LEN[player]


def _hangul_input_target_count(player):
    target = ep.EUDVariable()
    target << 0
    if ep.EUDIf()(_HANGUL_INPUT_LEN[player].AtLeast(1)):
        target << _hangul8192_accept_total_chars(player)
    ep.EUDEndIf()
    return target


@ep.EUDFunc
def print_runtime_status(player_value):
    def _body(player):
        if ep.EUDIf()(_APP_STATUS_TIMER[player] > 0):
            _APP_STATUS_TIMER[player] -= 1
        if ep.EUDElseIf()(_APP_PACKET_PROBE_COUNT[player] > 0):
            ep.f_eprintln("\x04SCND packet probe...", _APP_PACKET_PROBE_COUNT[player])
            _APP_STATUS_TIMER[player] = SCND_STATUS_PRINT_INTERVAL
        if ep.EUDElseIf()(_APP_HANGUL_INPUT_ACTIVE[player].Exactly(1)):
            input_count = _hangul_input_visible_count(player)
            input_target = _hangul_input_target_count(player)
            if ep.EUDIf()(input_target.AtLeast(1)):
                ep.f_eprintln("\x04SCND load input...", input_count, "/", input_target)
            if ep.EUDElse()():
                ep.f_eprintln("\x04SCND load input...", input_count)
            ep.EUDEndIf()
            _APP_STATUS_TIMER[player] = SCND_STATUS_PRINT_INTERVAL
        if ep.EUDElseIf()(_APP_LOAD_QUEUED[player].Exactly(1)):
            ep.f_eprintln("\x04SCND load waiting...")
            _APP_STATUS_TIMER[player] = SCND_STATUS_PRINT_INTERVAL
        if ep.EUDElseIf()(busy(player).AtLeast(1)):
            if ep.EUDIf()(_MODE[player].Exactly(1)):
                ep.f_eprintln("\x04SCND saving...", _STATE[player])
            if ep.EUDElse()():
                ep.f_eprintln("\x04SCND loading...", _STATE[player])
            ep.EUDEndIf()
            _APP_STATUS_TIMER[player] = SCND_STATUS_PRINT_INTERVAL
        ep.EUDEndIf()

    return _body(player_value)


@ep.EUDFunc
def build_code_view(player_value):
    def _body(player):
        if ep.EUDIf()(_APP_CODE_BUILD_ACTIVE[player].Exactly(1)):
            completed = _append_app_code_view_step_body(player)
            if ep.EUDIf()(completed.Exactly(1)):
                _APP_CODE_BUILD_ACTIVE[player] = 0
                _APP_CODE_VIEW_LOCAL_CLOSED[player] = 0
                _APP_CODE_VIEW_TIMER[player] = _APP_CODE_VIEW_TICKS
            ep.EUDEndIf()
        ep.EUDEndIf()

    return _body(player_value)


def _code_view_dark_on(player):
    if not _APP_DARKEN_CODE_VIEW:
        return
    ep.f_bwrite(SCND_SCREEN_BRIGHTNESS_ADDR, _APP_CODE_VIEW_DARK_BRIGHTNESS)


def _code_view_dark_off(player):
    if not _APP_DARKEN_CODE_VIEW:
        return
    ep.f_bwrite(SCND_SCREEN_BRIGHTNESS_ADDR, _APP_CODE_VIEW_NORMAL_BRIGHTNESS)


@ep.EUDFunc
def display_code_view(player_value):
    def _body(player):
        if ep.EUDIf()(_APP_CODE_VIEW_TIMER[player] > 0):
            if ep.EUDIf()(ep.IsUserCP()):
                if ep.EUDIf()(_APP_CODE_VIEW_LOCAL_CLOSED[player].Exactly(0)):
                    _code_view_dark_on(player)
                    for line_index in range(_APP_CODE_VIEW_LINES):
                        ep.f_printAt(
                            _APP_DISPLAY_LINE + line_index,
                            "{}",
                            ep.ptr2s(_app_code_view_line_addr(player, line_index)),
                        )
                if ep.EUDElse()():
                    _code_view_dark_off(player)
                ep.EUDEndIf()
            ep.EUDEndIf()
            _APP_CODE_VIEW_TIMER[player] -= 1
            if ep.EUDIf()(_APP_CODE_VIEW_TIMER[player].Exactly(0)):
                _APP_CODE_VIEW_LOCAL_CLOSED[player] = 0
                if ep.EUDIf()(ep.IsUserCP()):
                    _code_view_dark_off(player)
                ep.EUDEndIf()
            ep.EUDEndIf()
        if ep.EUDElseIf()(_APP_CODE_VIEW_LOCAL_CLOSED[player].Exactly(1)):
            _APP_CODE_VIEW_LOCAL_CLOSED[player] = 0
            if ep.EUDIf()(ep.IsUserCP()):
                _code_view_dark_off(player)
            ep.EUDEndIf()
        ep.EUDEndIf()

    return _body(player_value)


@ep.EUDFunc
def save_code_showing(player_value):
    """Return 1 while the generated save code is visible or being prepared."""
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_APP_CODE_BUILD_ACTIVE[player_value].Exactly(1)):
        result << 1
    ep.EUDEndIf()
    if ep.EUDIf()(_APP_CODE_VIEW_TIMER[player_value] > 0):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def _app_idle_launcher_input_probe(player_value):
    """Let the local launcher key bridge feed MSQC without running full SCND."""
    if not _APP_ENABLE_HANGUL_KEYS:
        return
    if ep.EUDIf()([
        ep.IsUserCP(),
        _STATE[player_value].Exactly(_PHASE_IDLE),
        _APP_LOAD_ENABLED[player_value].Exactly(1),
    ]):
        should_update = ep.EUDVariable()
        should_update << 0
        if ep.EUDIf()(_app_read_key_state(_APP_VK_SCND_GATE).Exactly(1)):
            should_update << 1
        if ep.EUDElseIf()(SCNDInputMSQCIsTransfer.AtLeast(1)):
            should_update << 1
        ep.EUDEndIf()
        if ep.EUDIf()(should_update.AtLeast(1)):
            _app_update_launcher_input_local(player_value)
        ep.EUDEndIf()
    ep.EUDEndIf()


def _scnd_debug_inc(counter, player):
    if SCND_DEBUG_PROFILING:
        counter[player] += 1


@ep.EUDFunc
def _player_needs_tick(player_value):
    """Return 1 when the full SCND runtime has active work for this player."""
    result = ep.EUDVariable()
    result << 0
    if ep.EUDIf()(_STATE[player_value].AtLeast(1)):
        result << 1
    if ep.EUDElseIf()(_MESSAGE[player_value].AtLeast(1)):
        result << 1
    if ep.EUDElseIf()(_APP_HANGUL_INPUT_ACTIVE[player_value].Exactly(1)):
        result << 1
    if ep.EUDElseIf()(_APP_STATUS_TIMER[player_value].AtLeast(1)):
        result << 1
    if ep.EUDElseIf()(_APP_CODE_BUILD_ACTIVE[player_value].Exactly(1)):
        result << 1
    if ep.EUDElseIf()(_APP_CODE_VIEW_TIMER[player_value].AtLeast(1)):
        result << 1
    if ep.EUDElseIf()(_APP_PACKET_PROBE_COUNT[player_value].AtLeast(1)):
        result << 1
    if ep.EUDElseIf()(_APP_HANGUL_LOAD_TAIL[player_value].Exactly(1)):
        result << 1
    if ep.EUDElseIf()(_APP_LOAD_QUEUED[player_value].Exactly(1)):
        result << 1
    ep.EUDEndIf()
    return result


@ep.EUDFunc
def _player_msqc_has_signal(player_value):
    """Cheap idle wake probe for SCNDgram/MSQC channel 1."""
    result = ep.EUDVariable()
    raw = ep.EUDVariable()
    result << 0
    if _APP_ENABLE_HANGUL_KEYS:
        raw << _app_read_msqc_receive_raw(1, player_value)
        if ep.EUDIfNot()(raw.Exactly(-1)):
            if ep.EUDIfNot()(raw.Exactly(0)):
                result << 1
            ep.EUDEndIf()
        ep.EUDEndIf()
    return result


@ep.EUDFunc
def update_player(player_value):
    """Advance the complete maker-facing SCND runtime for one player.

    This includes background save/load jobs, SCNDgram launcher input, status
    messages, and save-code display.  Makers only decide when to call
    save/enable_load/disable_load.
    """
    _ensure_msqcloader_runtime()
    tick_for_player(player_value)
    update_hangul_input(player_value)
    handle_messages(player_value)
    print_runtime_status(player_value)
    build_code_view(player_value)
    display_code_view(player_value)


@ep.EUDFunc
def run_player(player_value):
    """Run all SCND background work for one player."""
    if not SCND_IDLE_GATE_ENABLED:
        update_player(player_value)
        return

    _ensure_msqcloader_runtime()
    if ep.EUDIf()(_player_needs_tick(player_value).AtLeast(1)):
        _scnd_debug_inc(_SCND_DEBUG_FULL_HITS, player_value)
        update_player(player_value)
    if ep.EUDElse()():
        if ep.EUDIf()(_player_msqc_has_signal(player_value).AtLeast(1)):
            _scnd_debug_inc(_SCND_DEBUG_WAKE_HITS, player_value)
            update_player(player_value)
        if ep.EUDElse()():
            _app_idle_launcher_input_probe(player_value)
            _scnd_debug_inc(_SCND_DEBUG_IDLE_HITS, player_value)
        ep.EUDEndIf()
    ep.EUDEndIf()


@ep.EUDFunc
def run_player_auto(player_value):
    """Backward-compatible alias for run_player."""
    run_player(player_value)


def run_human_players():
    for player in ep.EUDLoopPlayer("Human"):
        ep.f_setcurpl(player)
        run_player(player)


def run_human_players_auto():
    for player in ep.EUDLoopPlayer("Human"):
        ep.f_setcurpl(player)
        run_player_auto(player)


# endregion: compact maker runtime -------------------------------------------


def reset_print(player_value):
    if isinstance(player_value, int):
        _PRINT_INDEX[player_value] = 0
        return
    _PRINT_INDEX[player_value] = 0


f_clear_bindings = clear_bindings
f_bind_player_uint = bind_player_uint
f_bind_player_uint_bytes = bind_player_uint_bytes
f_bind_player_bool = bind_player_bool
f_bind_player_array = bind_player_array
f_bind_player_array_bytes = bind_player_array_bytes
f_bind_player_array_bits = bind_player_array_bits
f_bind_player_bool_array = bind_player_bool_array
f_bind_player_value = bind_player_value
f_bind_player_value_bytes = bind_player_value_bytes
f_bind_player_single = bind_player_single
f_bind_player_single_bytes = bind_player_single_bytes
f_bind_player_bool_value = bind_player_bool_value
f_bind_global_value = bind_global_value
f_bind_global_uint = bind_global_uint
f_bind_global_array = bind_global_array
f_bind_global_array_bits = bind_global_array_bits
f_reset_global_lock = reset_global_lock
f_bind_player_schema = bind_player_schema
f_bind_schema = bind_player_schema
f_schema_bit_budget = schema_bit_budget
f_player_field_schema = player_field_schema
f_global_field_schema = global_field_schema
f_write_save_app_script_setup = write_save_app_script_setup
f_set_map_info = set_map_info
f_set_map_presentation = set_map_presentation
f_write_mpq_manifest = write_mpq_manifest
f_use_msqcloader = use_msqcloader
f_auto_use_msqcloader = auto_use_msqcloader
f_init_msqcloader = init_msqcloader
f_set_launcher_input_probe_only = set_launcher_input_probe_only
f_begin_save = begin_save
f_begin_save_global = begin_save_global
f_begin_load = begin_load
f_begin_load_hangul8192 = begin_load_hangul8192
f_begin_load_hangul8192_global = begin_load_hangul8192_global
f_tick_for_player = tick_for_player
f_tick = tick
f_busy = busy
f_done = done
f_save_done = save_done
f_load_done = load_done
f_failed = failed
f_save_failed = save_failed
f_load_failed = load_failed
f_ok = ok
f_is_new_user = is_new_user
f_get_result = get_result
f_get_last_message = get_last_message
f_get_result_for_player = get_result_for_player
f_get_last_message_for_player = get_last_message_for_player
f_reset_message = reset_message
f_reset_result = reset_result
f_reset_done = reset_done
f_reset_job = reset_job
f_code_length = code_length
f_input_length = input_length
f_last_compress_stats = last_compress_stats
f_unicode_code_length = unicode_code_length
f_hangul8192_code_length = hangul8192_code_length
f_clear_input = clear_input
f_clear_hangul8192_input = clear_hangul8192_input
f_append_input_from_chat = append_input_from_chat
f_append_input_from_cstring = append_input_from_cstring
f_append_hangul8192_value = append_hangul8192_value
f_hangul8192_input_length = hangul8192_input_length
f_hangul8192_input_ready = hangul8192_input_ready
f_print_next_chunk = print_next_chunk
f_append_next_chunk = append_next_chunk
f_append_next_unicode_chunk = append_next_unicode_chunk
f_append_next_hangul8192_chunk = append_next_hangul8192_chunk
f_begin_save_data_view = begin_save_data_view
f_append_save_data_padding = append_save_data_padding
f_append_save_data_view_step = append_save_data_view_step
f_configure = configure
f_setup = configure
f_save = save
f_save_global = save_global
f_load = load
f_set_load_enabled = set_load_enabled
f_enable_load = enable_load
f_disable_load = disable_load
f_set_all_load_enabled = set_all_load_enabled
f_load_enabled = load_enabled
f_clear_runtime_input = clear_runtime_input
f_handle_chat = handle_chat
f_start_hangul_input = start_hangul_input
f_update_hangul_input = update_hangul_input
f_handle_messages = handle_messages
f_print_runtime_status = print_runtime_status
f_build_code_view = build_code_view
f_display_code_view = display_code_view
f_save_code_showing = save_code_showing
f_update_player = update_player
f_run_player = run_player
f_run_player_auto = run_player_auto
f_run_human_players = run_human_players
f_run_human_players_auto = run_human_players_auto
f_append_load_line_from_chat = append_load_line_from_chat
f_reset_print = reset_print
f_reset_unicode_print = reset_unicode_print
f_reset_hangul8192_print = reset_hangul8192_print
