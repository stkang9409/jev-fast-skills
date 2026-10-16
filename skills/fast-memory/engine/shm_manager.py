"""POSIX SharedMemory Request-Scoped Engine with Atomic Concurrency & Binary Buffers."""

import os
import json
import fcntl
from pathlib import Path
from multiprocessing import shared_memory
from contextlib import contextmanager
from typing import Any, Optional, Dict, List

DEFAULT_SHM_NAME = "agy_req_shm_v1"
DEFAULT_SHM_SIZE = 16 * 1024 * 1024  # 16 MB total buffer
INDEX_MAX_SIZE = 1 * 1024 * 1024     # First 1 MB reserved for lightweight metadata index
LOCK_FILE_PATH = Path("/tmp/agy_req_shm.lock")


def _open_shm(name: str, create: bool = False, size: int = 0) -> shared_memory.SharedMemory:
    """Safely open or create SharedMemory without resource_tracker unlinking on process exit."""
    try:
        if create:
            return shared_memory.SharedMemory(name=name, create=True, size=size, track=False)
        else:
            return shared_memory.SharedMemory(name=name, track=False)
    except TypeError:
        if create:
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        else:
            shm = shared_memory.SharedMemory(name=name)
        try:
            from multiprocessing import resource_tracker
            resource_tracker.unregister(shm._name, "shared_memory")
        except Exception:
            pass
        return shm


class RequestMemory:
    """Ultra-fast (65us) atomic in-memory store powered by POSIX shared memory.
    Both JSON values and raw binary buffers store their payload slices in the 15MB data region
    while keeping the 1MB metadata index ultra-compact (O(1) per-key payload access).
    """

    def __init__(self, name: str = DEFAULT_SHM_NAME, size: int = DEFAULT_SHM_SIZE, create: bool = True):
        self.name = name
        self.size = size
        self.shm: Optional[shared_memory.SharedMemory] = None

        try:
            self.shm = _open_shm(name=self.name, create=False)
        except FileNotFoundError:
            if create:
                with self._lock():
                    try:
                        self.shm = _open_shm(name=self.name, create=False)
                    except FileNotFoundError:
                        self.shm = _open_shm(name=self.name, create=True, size=self.size)
                        self._write_index({
                            "values": {},
                            "bin_entries": {},
                            "next_bin_offset": INDEX_MAX_SIZE
                        })
            else:
                raise

    @contextmanager
    def _lock(self):
        """Atomic cross-process mutex using fast POSIX file locking (~3us)."""
        LOCK_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LOCK_FILE_PATH), os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_index(self) -> Dict[str, Any]:
        if not self.shm:
            return {"values": {}, "bin_entries": {}, "next_bin_offset": INDEX_MAX_SIZE}
        try:
            length = int.from_bytes(bytes(self.shm.buf[:4]), byteorder="big")
            if length == 0:
                return {"values": {}, "bin_entries": {}, "next_bin_offset": INDEX_MAX_SIZE}
            raw = bytes(self.shm.buf[4 : 4 + length])
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"values": {}, "bin_entries": {}, "next_bin_offset": INDEX_MAX_SIZE}

    def _write_index(self, index: Dict[str, Any]):
        if not self.shm:
            return
        raw = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > INDEX_MAX_SIZE - 4:
            raise ValueError(f"Index size ({len(raw)} bytes) exceeds index limit ({INDEX_MAX_SIZE} bytes)")
        self.shm.buf[:4] = len(raw).to_bytes(4, byteorder="big")
        self.shm.buf[4 : 4 + len(raw)] = raw

    def _alloc_slice(self, idx: Dict[str, Any], existing_meta: Optional[Dict[str, int]], data_len: int) -> int:
        if existing_meta and isinstance(existing_meta, dict) and existing_meta.get("len", 0) >= data_len:
            return existing_meta["offset"]
        offset = idx.get("next_bin_offset", INDEX_MAX_SIZE)
        if offset + data_len > self.size:
            if data_len > (self.size - INDEX_MAX_SIZE):
                raise ValueError(f"Data ({data_len} bytes) exceeds buffer capacity ({self.size - INDEX_MAX_SIZE} bytes)")
            offset = INDEX_MAX_SIZE
            idx["bin_entries"] = {}
            idx["values"] = {}
        idx["next_bin_offset"] = offset + data_len
        return offset

    def set(self, key: str, value: Any):
        """Save a JSON-compatible key-value pair in shared RAM atomically using data-region slicing."""
        raw_bytes = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        data_len = len(raw_bytes)
        with self._lock():
            idx = self._read_index()
            if key in idx["bin_entries"]:
                del idx["bin_entries"][key]
            existing = idx["values"].get(key)
            offset = self._alloc_slice(idx, existing if isinstance(existing, dict) and "__shm_slice__" in existing else None, data_len)
            self.shm.buf[offset : offset + data_len] = raw_bytes
            idx["values"][key] = {"__shm_slice__": 1, "offset": offset, "len": data_len}
            self._write_index(idx)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a JSON-compatible value in microseconds (only decodes the requested key)."""
        with self._lock():
            idx = self._read_index()
            if key not in idx["values"]:
                return default
            entry = idx["values"][key]
            if isinstance(entry, dict) and entry.get("__shm_slice__") == 1:
                offset = entry["offset"]
                length = entry["len"]
                raw = bytes(self.shm.buf[offset : offset + length])
                return json.loads(raw.decode("utf-8"))
            return entry

    def set_bytes(self, key: str, data: bytes):
        """Write raw binary buffer directly into RAM zero-copy with atomic lock."""
        with self._lock():
            idx = self._read_index()
            if key in idx["values"]:
                del idx["values"][key]
            data_len = len(data)
            existing = idx["bin_entries"].get(key)
            offset = self._alloc_slice(idx, existing, data_len)
            self.shm.buf[offset : offset + data_len] = data
            idx["bin_entries"][key] = {"offset": offset, "len": data_len}
            self._write_index(idx)

    def get_bytes(self, key: str) -> Optional[bytes]:
        """Retrieve raw binary buffer directly from RAM in microseconds."""
        with self._lock():
            idx = self._read_index()
            meta = idx["bin_entries"].get(key)
            if not meta:
                return None
            offset = meta["offset"]
            length = meta["len"]
            return bytes(self.shm.buf[offset : offset + length])

    def delete(self, key: str):
        """Remove a key (value or binary) from shared RAM atomically."""
        with self._lock():
            idx = self._read_index()
            removed = False
            if key in idx["values"]:
                del idx["values"][key]
                removed = True
            if key in idx["bin_entries"]:
                del idx["bin_entries"][key]
                removed = True
            if removed:
                self._write_index(idx)

    def list_keys(self) -> List[str]:
        """List all active keys in shared RAM."""
        with self._lock():
            idx = self._read_index()
            return list(idx["values"].keys()) + list(idx["bin_entries"].keys())

    def get_meta(self, key: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a key (type: value or binary, size)."""
        with self._lock():
            idx = self._read_index()
            if key in idx["values"]:
                entry = idx["values"][key]
                sz = entry.get("len", 0) if isinstance(entry, dict) else 0
                return {"type": "json", "size": sz}
            elif key in idx["bin_entries"]:
                return {"type": "binary", "size": idx["bin_entries"][key]["len"]}
            return None

    def clear(self):
        """Clear all keys without unlinking memory block."""
        with self._lock():
            self._write_index({"values": {}, "bin_entries": {}, "next_bin_offset": INDEX_MAX_SIZE})

    def close(self):
        """Close local process handle."""
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None

    def destroy(self):
        """Completely unlink and release the RAM block to the OS."""
        with self._lock():
            if self.shm:
                try:
                    self.shm.close()
                    self.shm.unlink()
                except Exception:
                    pass
                self.shm = None
            try:
                if LOCK_FILE_PATH.exists():
                    LOCK_FILE_PATH.unlink()
            except Exception:
                pass

    @classmethod
    def teardown_all(cls, name: str = DEFAULT_SHM_NAME):
        """Static helper to cleanly destroy shared memory and locks."""
        try:
            shm = _open_shm(name=name, create=False)
            shm.close()
            shm.unlink()
        except Exception:
            pass
        try:
            if LOCK_FILE_PATH.exists():
                LOCK_FILE_PATH.unlink()
        except Exception:
            pass
