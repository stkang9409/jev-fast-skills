"""POSIX SharedMemory Request-Scoped Engine (Internal Shared RAM Layer for Jev Skills)."""

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
    Used internally by fast-context (AST cache), fast-browser (handoff sessions),
    and prompt-engineer (before/after audit delta tracking).
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
                        self._write_index({"values": {}, "bin_entries": {}, "next_bin_offset": INDEX_MAX_SIZE})

    @contextmanager
    def _lock(self):
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

    def _compact(self, idx: Dict[str, Any]):
        """Compact live slices in the 15MB data region so active keys survive wrap-around."""
        write_cursor = INDEX_MAX_SIZE
        # Collect all active slice descriptors
        live_entries = []
        for k, meta in list(idx.get("values", {}).items()):
            if isinstance(meta, dict) and "__shm_slice__" in meta:
                live_entries.append(("values", k, meta))
        for k, meta in list(idx.get("bin_entries", {}).items()):
            if isinstance(meta, dict) and "offset" in meta:
                live_entries.append(("bin_entries", k, meta))

        live_entries.sort(key=lambda item: item[2]["offset"])
        for bucket, k, meta in live_entries:
            old_off = meta["offset"]
            length = meta["len"]
            if old_off != write_cursor:
                payload = bytes(self.shm.buf[old_off : old_off + length])
                self.shm.buf[write_cursor : write_cursor + length] = payload
                meta["offset"] = write_cursor
            write_cursor += length
        idx["next_bin_offset"] = write_cursor

    def _alloc_slice(self, idx: Dict[str, Any], existing_meta: Optional[Dict[str, int]], data_len: int) -> int:
        if existing_meta and isinstance(existing_meta, dict) and existing_meta.get("len", 0) >= data_len:
            return existing_meta["offset"]
        if data_len > (self.size - INDEX_MAX_SIZE):
            raise ValueError(f"Data ({data_len} bytes) exceeds buffer capacity ({self.size - INDEX_MAX_SIZE} bytes)")
        offset = idx.get("next_bin_offset", INDEX_MAX_SIZE)
        if offset + data_len > self.size:
            self._compact(idx)
            offset = idx.get("next_bin_offset", INDEX_MAX_SIZE)
            if offset + data_len > self.size:
                # Still full after compaction: reset data region
                offset = INDEX_MAX_SIZE
                idx["bin_entries"] = {}
                idx["values"] = {}
        idx["next_bin_offset"] = offset + data_len
        return offset

    def set(self, key: str, value: Any):
        raw_bytes = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        data_len = len(raw_bytes)
        with self._lock():
            idx = self._read_index()
            if key in idx["bin_entries"]:
                del idx["bin_entries"][key]
            existing = idx["values"].get(key)
            offset = self._alloc_slice(
                idx,
                existing if isinstance(existing, dict) and "__shm_slice__" in existing else None,
                data_len,
            )
            self.shm.buf[offset : offset + data_len] = raw_bytes
            idx["values"][key] = {"__shm_slice__": 1, "offset": offset, "len": data_len}
            self._write_index(idx)

    def get(self, key: str, default: Any = None) -> Any:
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

    def delete(self, key: str):
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

    def close(self):
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None

    @classmethod
    def teardown_all(cls, name: str = DEFAULT_SHM_NAME):
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
