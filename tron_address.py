"""Small dependency-free TRON Base58Check address codec."""
from __future__ import annotations

import hashlib

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {char: index for index, char in enumerate(_ALPHABET)}


def _b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        if char not in _INDEX:
            raise ValueError("invalid base58 address")
        number = number * 58 + _INDEX[char]
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + raw


def _b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = _ALPHABET[remainder] + encoded
    zeros = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * zeros + (encoded or ("" if zeros else "1"))


def tron_payload(value: str) -> bytes:
    value = str(value or "").strip()
    if value.startswith("0x"):
        value = value[2:]
    if len(value) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in value):
        payload = bytes.fromhex("41" + value)
    elif len(value) == 42 and value[:2].lower() == "41" and all(
            ch in "0123456789abcdefABCDEF" for ch in value):
        payload = bytes.fromhex(value)
    else:
        decoded = _b58decode(value)
        if len(decoded) != 25:
            raise ValueError("invalid TRON address length")
        payload, checksum = decoded[:21], decoded[21:]
        if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
            raise ValueError("invalid TRON address checksum")
        if payload[0] != 0x41:
            raise ValueError("invalid TRON address prefix")
    return payload


def tron_to_base58(value: str) -> str:
    payload = tron_payload(value)
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return _b58encode(payload + checksum)


def tron_to_hex(value: str, *, prefix: bool = False) -> str:
    value = tron_payload(value).hex()
    return ("0x" if prefix else "") + (value[2:] if not prefix else value)
