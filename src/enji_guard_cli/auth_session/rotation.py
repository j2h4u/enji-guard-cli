"""Durable cookie-rotation journal operations.

All operations delegate to the existing fsync/atomic implementation so the
on-disk schema and crash-recovery semantics remain byte-for-byte compatible.
"""

from enji_guard_cli.auth_session.store import (
    PendingRefreshRotation,
    consume_pending_rotation,
    load_pending_rotation,
    mark_pending_rotation_requested,
    mark_pending_rotation_rotated,
    pending_rotation_path,
    record_pending_rotation_error,
    reserve_pending_rotation,
)

__all__ = [
    "PendingRefreshRotation",
    "consume_pending_rotation",
    "load_pending_rotation",
    "mark_pending_rotation_requested",
    "mark_pending_rotation_rotated",
    "pending_rotation_path",
    "record_pending_rotation_error",
    "reserve_pending_rotation",
]
