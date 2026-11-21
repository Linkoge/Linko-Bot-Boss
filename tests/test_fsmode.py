"""Owner-only chmods keep Main's named ACL entry on an instance's database and data directory (fsmode.private)."""
import os
import shutil
import subprocess

import pytest

from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.fsmode import private

OTHER_UID = '65534'  # a named entry for another user, like Main's u:codex:rw- on an instance database


def acl(path):
    output = subprocess.run(['getfacl', '-cpn', '--', str(path)], capture_output=True, text=True, check=True,
                            timeout=10).stdout
    return {line.split('#')[0].strip() for line in output.splitlines() if line.strip()}


def setfacl(path, spec):
    subprocess.run(['setfacl', '-m', spec, '--', str(path)], check=True, capture_output=True, timeout=10)


@pytest.fixture
def acls(tmp_path):
    if not (shutil.which('setfacl') and shutil.which('getfacl')):
        pytest.skip('setfacl/getfacl are not installed')
    probe = tmp_path / 'probe'
    probe.touch()
    if subprocess.run(['setfacl', '-m', f'u:{OTHER_UID}:r', str(probe)], capture_output=True, timeout=10).returncode:
        pytest.skip('the test filesystem does not support POSIX ACLs')
    return tmp_path


def test_plain_file_gets_a_plain_private_mode(tmp_path):
    path = tmp_path / 'file'
    path.touch()
    path.chmod(0o666)
    private(path, 0o600)
    assert path.stat().st_mode & 0o777 == 0o600


def test_database_and_data_dir_keep_named_entries_and_mask_but_close_group_and_other(acls):
    data = acls / 'instance'
    data.mkdir()
    db_path = data / 'manager.sqlite3'
    Database(db_path)
    os.chmod(data, 0o755)
    db_path.chmod(0o664)
    setfacl(data, f'u:{OTHER_UID}:rx')
    setfacl(db_path, f'u:{OTHER_UID}:rw')
    # What the instance does at every start: Settings.prepare() on its data dir, then Database().
    Settings(data_dir=data, project_roots=(acls,)).prepare()
    Database(db_path)
    assert acl(db_path) == {'user::rw-', f'user:{OTHER_UID}:rw-', 'group::---', 'mask::rw-', 'other::---'}
    assert acl(data) == {'user::rwx', f'user:{OTHER_UID}:r-x', 'group::---', 'mask::r-x', 'other::---'}
    # A plain chmod is what used to strip the grant: the mask becomes --- and the named entry is void.
    db_path.chmod(0o600)
    assert 'mask::---' in acl(db_path)
