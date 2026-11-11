from importlib.metadata import version


def test_package_is_installed_with_a_version():
    assert version("wills-agentic-tools")
