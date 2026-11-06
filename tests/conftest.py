import pytest


@pytest.fixture
def directory_link():
    def create(link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            if getattr(error, "winerror", None) != 1314:
                raise
            import _winapi

            _winapi.CreateJunction(str(target), str(link))

    return create
