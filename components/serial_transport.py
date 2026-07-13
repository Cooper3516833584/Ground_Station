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

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._serial is not None

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
        with self._lock:
            if self._serial is not serial_obj:
                raise RuntimeError("serial link is not connected")
            serial_obj.write(data)
            serial_obj.flush()

    def _open_serial(self, serial_module):
        serial_obj = serial_module.Serial()
        serial_obj.port = self._port
        serial_obj.baudrate = self._baudrate
        serial_obj.bytesize = serial_module.EIGHTBITS
        serial_obj.parity = serial_module.PARITY_NONE
        serial_obj.stopbits = serial_module.STOPBITS_ONE
        serial_obj.timeout = 0.1
        serial_obj.write_timeout = 0.5
        serial_obj.dsrdtr = False
        serial_obj.rtscts = False
        serial_obj.dtr = False
        serial_obj.rts = False
        serial_obj.open()
        serial_obj.setDTR(False)
        serial_obj.setRTS(False)
        return serial_obj

    def _run(self) -> None:
        try:
            import serial
        except ImportError as exc:
            if self._on_disconnected:
                self._on_disconnected(exc)
            return

        while not self._stop.is_set():
            try:
                serial_obj = self._open_serial(serial)
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
            with self._lock:
                if self._serial is not serial_obj:
                    return
                in_wait = serial_obj.in_waiting
                data = serial_obj.read(in_wait) if in_wait else b""
            if data:
                self._on_bytes(data)
            else:
                self._stop.wait(0.005)
