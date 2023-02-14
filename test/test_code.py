import os
import pytest
import shutil
import subprocess


ROOT_DIR = os.path.realpath(f'{__file__}/../..')


@pytest.mark.skipif(not shutil.which('flake8'), reason='flake8 is not installed')
def test_flake8():
    subprocess.check_call(['flake8'], cwd=ROOT_DIR)
