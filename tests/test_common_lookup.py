import pytest

from common import lookup


def test_common_lookup_module_exports_lookup_helpers():
    assert callable(lookup.lookup)
    assert callable(lookup.lookup_prefix)
    assert callable(lookup.lookup_by_name)
    assert callable(lookup.lookup_by_grid)


def test_build_index_requires_en_dat_file(tmp_path):
    missing_en = tmp_path / "EN.dat"
    missing_idx = tmp_path / "EN.idx"

    with pytest.raises(FileNotFoundError):
        lookup.build_index(en_path=str(missing_en), index_path=str(missing_idx))
