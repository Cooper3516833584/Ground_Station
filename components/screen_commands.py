from __future__ import annotations


class ScreenCommandDetector:
    """Detect configured UTF-8 tokens, including tokens split across serial reads."""

    def __init__(self, tokens: tuple[str, ...]):
        if not tokens:
            raise ValueError("at least one screen token is required")
        self._tokens = {
            token.encode("utf-8").upper(): token
            for token in tokens
        }
        self._max_token_bytes = max(len(token) for token in self._tokens)
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[str]:
        if not data:
            return []
        self._buffer.extend(data.upper())
        detected = []
        while self._buffer:
            matches = []
            for encoded, original in self._tokens.items():
                index = self._buffer.find(encoded)
                if index >= 0:
                    matches.append((index, -len(encoded), encoded, original))
            if not matches:
                keep = self._max_token_bytes - 1
                if len(self._buffer) > keep:
                    if keep:
                        del self._buffer[:-keep]
                    else:
                        self._buffer.clear()
                break
            index, _, encoded, original = min(matches)
            del self._buffer[: index + len(encoded)]
            detected.append(original)
        return detected
