from __future__ import annotations

import threading
import time
from typing import Callable


class SerialTransport:
    def __init__(
        self,
        *,
        port: str,
        baudrate: int = 9600,
        on_bytes: Callable[[bytes], None],
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[Exception | None], None] | None = None,
        reconnect_seconds: float = 1.0,
    ):
        self._port = port
        self._baudrate = baudrate
        self._on_bytes = on_bytes
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._reconnect_seconds = reconnect_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._serial = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hc14-serial", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            serial_obj = self._serial
            self._serial = None
        if serial_obj is not None:
            try:
                serial_obj.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def write(self, data: bytes) -> None:
        with self._lock:
            serial_obj = self._serial
        if serial_obj is None:
            raise RuntimeError("serial link is not connected")
        serial_obj.write(data)
        serial_obj.flush()

    def _run(self) -> None:
        try:
            import serial
        except ImportError as exc:
            if self._on_disconnected:
                self._on_disconnected(exc)
            return

        while not self._stop.is_set():
            try:
                serial_obj = serial.Serial(
                    port=self._port,
                    baudrate=self._baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.1,
                    write_timeout=0.5,
                    dsrdtr=False,
                    rtscts=False,
                )
                serial_obj.dtr = False
                serial_obj.rts = False
                with self._lock:
                    self._serial = serial_obj
                if self._on_connected:
                    self._on_connected()
                self._read_loop(serial_obj)
            except Exception as exc:
                if self._on_disconnected:
                    self._on_disconnected(exc)
            finally:
                with self._lock:
                    serial_obj = self._serial
                    self._serial = None
                if serial_obj is not None:
                    try:
                        serial_obj.close()
                    except Exception:
                        pass
            if not self._stop.is_set():
                time.sleep(self._reconnect_seconds)

    def _read_loop(self, serial_obj) -> None:
        while not self._stop.is_set():
            data = serial_obj.read(256)
            if data:
                self._on_bytes(data)

