# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code

import cloud_manager  # noqa: F401


def test_cloud_manager_module_importable():
    """Verify cloud_manager module is importable after CloudHealthChecker removal."""
    # CloudHealthChecker was removed in v1.3.33
    # cloud_manager module now only contains documentation about the removal
    assert hasattr(cloud_manager, "logger")
