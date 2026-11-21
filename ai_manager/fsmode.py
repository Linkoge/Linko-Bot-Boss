"""Owner-only permissions that keep deliberate POSIX ACL grants.

A plain chmod on a file with an extended ACL rewrites the ACL mask from the group bits: chmod 600 makes the mask
`---` and silently voids every named entry (for example Main's `u:codex:rw-` on an instance database, which must
survive the instance's own restarts). `private` sets the owner bits and clears the owning group and other entries as
chmod would, but keeps named user/group entries and the mask exactly as they are. Without an extended ACL it is a plain
chmod, so nothing is loosened for anyone else."""
import errno
import os
import struct

ACCESS = 'system.posix_acl_access'
VERSION = 2
USER_OBJ, USER, GROUP_OBJ, GROUP, MASK, OTHER = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
ENTRY = struct.Struct('<HHI')


def acl_entries(path):
    """The access ACL as [(tag, perm, id)], or None when the file has no extended ACL (or the system has none)."""
    try:
        raw = os.getxattr(path, ACCESS)
    except (AttributeError, OSError) as error:
        if isinstance(error, AttributeError) or error.errno in {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}:
            return None
        raise
    if len(raw) < 4 or struct.unpack_from('<I', raw)[0] != VERSION or (len(raw) - 4) % ENTRY.size:
        return None
    entries = [ENTRY.unpack_from(raw, offset) for offset in range(4, len(raw), ENTRY.size)]
    return entries if any(tag == MASK for tag, _, _ in entries) else None


def private(path, mode):
    """chmod(path, mode) for owner-only modes (0o600, 0o700) that keeps named ACL entries and the mask."""
    entries = acl_entries(path)
    if entries is None:
        os.chmod(path, mode)
        return
    # Writing the ACL also sets the mode bits: owner and other from their entries, the group bits from the kept mask.
    perms = {USER_OBJ: (mode >> 6) & 7, GROUP_OBJ: (mode >> 3) & 7, OTHER: mode & 7}
    updated = [(tag, perms.get(tag, perm), ident) for tag, perm, ident in entries]
    if updated != entries:
        os.setxattr(path, ACCESS, struct.pack('<I', VERSION) + b''.join(ENTRY.pack(*e) for e in updated))
