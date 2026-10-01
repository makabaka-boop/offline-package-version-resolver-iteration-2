import json

import pytest

from resolver import InputError, parse_request


def valid_payload(**overrides):
    payload = {
        "packages": {
            "a": {
                "1": {"dependencies": {"b": [1, 2]}},
                "2": {},
            },
            "b": {"1": {"dependencies": {}}, "2": {"dependencies": {}}},
        },
        "root": {"a": [1, 2]},
        "installed": {"a": 1},
    }
    payload.update(overrides)
    return payload


def test_accepts_strict_request():
    catalog = parse_request(json.dumps(valid_payload()))
    assert catalog.ordered_names == ("a", "b")
    assert catalog.installed == {"a": 1}


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        "[]",
        "null",
        '{"packages": {}, "root": {}, "installed": {}}',
    ],
)
def test_reject_malformed_documents(document):
    with pytest.raises(InputError):
        parse_request(document)


def test_reject_unknown_top_level_key():
    payload = valid_payload()
    payload["unexpected"] = True
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_too_many_packages():
    packages = {f"p{i}": {"1": {}} for i in range(11)}
    payload = valid_payload(packages=packages, root={"p0": [1, 1]}, installed={})
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_too_many_versions():
    payload = valid_payload(packages={"a": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}}})
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_non_integer_or_non_positive_version_key():
    for bad_key in ["0", "-1", "1.0", "v1"]:
        payload = valid_payload(packages={"a": {bad_key: {}}}, root={}, installed={})
        with pytest.raises(InputError):
            parse_request(json.dumps(payload))


def test_reject_non_ascii_package_name():
    payload = valid_payload(
        packages={"ä": {"1": {}}},
        root={},
        installed={},
    )
    with pytest.raises(InputError):
        parse_request(json.dumps(payload, ensure_ascii=False))


def test_reject_malformed_interval():
    payload = valid_payload(root={"a": [2, 1]})
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_zero_interval_bound():
    payload = valid_payload(root={"a": [0, 1]})
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_boolean_as_version_number():
    payload = valid_payload(installed={"a": True})
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_dependency_outside_catalog():
    payload = valid_payload(
        packages={"a": {"1": {"dependencies": {"missing": [1, 1]}}}},
        root={"a": [1, 1]},
        installed={},
    )
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_root_outside_catalog():
    payload = valid_payload(root={"missing": [1, 1]})
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_installed_package_outside_catalog():
    payload = valid_payload(installed={"missing": 1})
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_installed_version_need_not_still_exist_in_catalog():
    # Version 1 for installed a may be stale; current catalog only has 2/3.
    catalog = parse_request(json.dumps(valid_payload(installed={"a": 1, "b": 9})))
    assert catalog.installed == {"a": 1, "b": 9}


def test_reject_duplicate_json_object_keys():
    raw = """
    {
      "packages": {"a": {"1": {}, "1": {}}},
      "root": {},
      "installed": {}
    }
    """
    with pytest.raises(InputError):
        parse_request(raw)


def test_conflicts_default_to_empty_when_omitted():
    catalog = parse_request(json.dumps(valid_payload()))
    assert catalog.conflicts == ()


def test_accepts_conflicts():
    payload = valid_payload(conflicts=[[["a", 1], ["b", 2]]])
    catalog = parse_request(json.dumps(payload))
    assert catalog.conflicts == ((("a", 1), ("b", 2)),)


def test_accepts_empty_conflicts_list():
    catalog = parse_request(json.dumps(valid_payload(conflicts=[])))
    assert catalog.conflicts == ()


def test_reject_conflicts_that_are_not_a_list():
    for bad in [{"a": 1}, "a", 1, True]:
        with pytest.raises(InputError):
            parse_request(json.dumps(valid_payload(conflicts=bad)))


@pytest.mark.parametrize(
    "conflicts",
    [
        [["a", 1, "b", 2]],          # flat four-element entry
        [[["a", 1]]],                # single endpoint
        [[["a", 1], ["b", 2], ["b", 1]]],  # three endpoints
        [[["a", 1], "b"]],           # endpoint is not a pair
        [[["a", 1], ["b"]]],         # endpoint missing its version
        [[["a", 1, "x"], ["b", 2]]],  # endpoint with extra element
    ],
)
def test_reject_malformed_conflict_structure(conflicts):
    with pytest.raises(InputError):
        parse_request(json.dumps(valid_payload(conflicts=conflicts)))


def test_reject_conflict_with_unknown_package():
    payload = valid_payload(conflicts=[[["a", 1], ["missing", 1]]])
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_conflict_with_unknown_version():
    payload = valid_payload(conflicts=[[["a", 9], ["b", 1]]])
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_conflict_between_same_package():
    for entry in [[["a", 1], ["a", 2]], [["a", 1], ["a", 1]]]:
        payload = valid_payload(conflicts=[entry])
        with pytest.raises(InputError):
            parse_request(json.dumps(payload))


def test_reject_reversed_duplicate_conflict():
    payload = valid_payload(conflicts=[[["a", 1], ["b", 2]], [["b", 2], ["a", 1]]])
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_exact_duplicate_conflict():
    payload = valid_payload(conflicts=[[["a", 1], ["b", 2]], [["a", 1], ["b", 2]]])
    with pytest.raises(InputError):
        parse_request(json.dumps(payload))


def test_reject_conflict_with_non_positive_or_non_integer_version():
    for bad_version in [0, -1, True, "1", 1.5]:
        payload = valid_payload(conflicts=[[["a", bad_version], ["b", 1]]])
        with pytest.raises(InputError):
            parse_request(json.dumps(payload))


def test_reject_conflict_with_invalid_package_name():
    for bad_name in ["", "has space", 1]:
        payload = valid_payload(conflicts=[[[bad_name, 1], ["b", 1]]])
        with pytest.raises(InputError):
            parse_request(json.dumps(payload))
