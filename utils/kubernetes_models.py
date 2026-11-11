"""
Strict validation of plain dicts (camelCase, as in a Kubernetes YAML manifest)
against the models of the official Kubernetes Python client.

The client models only check required fields, and its deserializer silently
drops unknown fields: here every field is checked (unknown fields, types,
required fields) and every error is reported with its path.
"""

import datetime
import difflib
import re
from typing import Any

from kubernetes import client
from kubernetes.client import models

_LIST_RE = re.compile(r"^list\[(.+)\]$")
_DICT_RE = re.compile(r"^dict\(([^,]+), (.+)\)$")
_REQUIRED_RE = re.compile(r"Invalid value for `(\w+)`, must not be `None`")
# Stands for a missing required field while looking for the other ones
_PLACEHOLDER = object()

# Groups whose resources are modeled by the client (custom resources whose
# kind collides with a built-in one are not validated)
_BUILTIN_GROUPS = {
    "",
    "admissionregistration.k8s.io",
    "apiextensions.k8s.io",
    "apps",
    "autoscaling",
    "batch",
    "certificates.k8s.io",
    "coordination.k8s.io",
    "discovery.k8s.io",
    "networking.k8s.io",
    "node.k8s.io",
    "policy",
    "rbac.authorization.k8s.io",
    "scheduling.k8s.io",
    "storage.k8s.io",
}

# Validation is client-side only: no API server is involved
_CONFIGURATION = client.Configuration()
_CONFIGURATION.client_side_validation = True


class KubernetesValidationError(ValueError):
    """Errors found in an object, one string per error."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def _type_name(value: Any) -> str:
    return type(value).__name__


class _Converter:
    """Converts values into client models, collecting the errors."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, path: str, msg: str) -> None:
        """Records an error at `path`."""
        self.errors.append(f"{path}: {msg}" if path else msg)

    def convert(self, value: Any, type_name: str, path: str) -> Any:
        """Converts `value`, of OpenAPI type `type_name` (e.g. list[V1Container])."""
        if value is None:
            return None

        if m := _LIST_RE.match(type_name):
            if not isinstance(value, list):
                self.error(path, f"expected a list, got {_type_name(value)}")
                return None
            return [
                self.convert(item, m.group(1), _join(path, i))
                for i, item in enumerate(value)
            ]

        if m := _DICT_RE.match(type_name):
            if not isinstance(value, dict):
                self.error(path, f"expected a mapping, got {_type_name(value)}")
                return None
            return {
                str(k): self.convert(v, m.group(2), _join(path, k))
                for k, v in value.items()
            }

        if type_name == "str":
            # Numbers are accepted where a string is expected (e.g. quantities
            # `cpu: 1`, label values `version: 2`), as kubectl does for YAML
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                self.error(path, f"expected a string, got {_type_name(value)}")
                return None
            return str(value)

        if type_name == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                self.error(path, f"expected an integer, got {_type_name(value)}")
                return None
            return value

        if type_name == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                self.error(path, f"expected a number, got {_type_name(value)}")
                return None
            return float(value)

        if type_name == "bool":
            if not isinstance(value, bool):
                self.error(path, f"expected a boolean, got {_type_name(value)}")
                return None
            return value

        if type_name in ("datetime", "date"):
            if isinstance(value, (datetime.date, str)):
                return value
            self.error(path, f"expected a {type_name}, got {_type_name(value)}")
            return None

        if type_name == "object":
            # Free-form (IntOrString, RawExtension, ...)
            return value

        return self.convert_model(value, type_name, path)

    def convert_model(self, value: Any, type_name: str, path: str) -> Any:
        """Converts a mapping into the client model `type_name`."""
        klass = getattr(models, type_name, None)
        if klass is None:
            # Unknown to this client version: kept as is
            return value
        if not isinstance(value, dict):
            self.error(
                path, f"expected a mapping ({type_name}), got {_type_name(value)}"
            )
            return None

        # attribute_map: python name -> JSON name
        json_to_attr = {j: a for a, j in klass.attribute_map.items()}
        errors_before = len(self.errors)
        kwargs = {}
        # Fields given but invalid (already reported, not "required" again)
        invalid = set()
        for key, item in value.items():
            attr = json_to_attr.get(key)
            if attr is None:
                hint = difflib.get_close_matches(str(key), json_to_attr, n=1)
                suffix = f" (did you mean '{hint[0]}'?)" if hint else ""
                self.error(_join(path, key), f"unknown field{suffix}")
                continue
            kwargs[attr] = self.convert(
                item, klass.openapi_types[attr], _join(path, key)
            )
            if kwargs[attr] is None and item is not None:
                invalid.add(attr)

        # The model constructor stops at the first missing required field:
        # each one is replaced by a placeholder to find the next one
        while True:
            try:
                instance = klass(**kwargs, local_vars_configuration=_CONFIGURATION)
                break
            except ValueError as e:
                m = _REQUIRED_RE.search(str(e))
                if not m or kwargs.get(m.group(1)) is _PLACEHOLDER:
                    self.error(path, str(e))
                    return None
                if m.group(1) not in invalid:
                    field = klass.attribute_map.get(m.group(1), m.group(1))
                    self.error(_join(path, field), "field required")
                kwargs[m.group(1)] = _PLACEHOLDER

        return instance if len(self.errors) == errors_before else None


def to_model(data: Any, type_name: str) -> Any:
    """
    Converts `data` (a camelCase dict) into an instance of the client model
    `type_name` (e.g. "V1PodSpec"). Raises KubernetesValidationError with
    every error found.
    """
    converter = _Converter()
    result = converter.convert(data, type_name, "")
    if converter.errors:
        raise KubernetesValidationError(converter.errors)
    return result


def model_name_for(api_version: str, kind: str) -> str | None:
    """
    Client model of a built-in resource, e.g.
    ("apps/v1", "Deployment") -> "V1Deployment"; None if not modeled.
    """
    group, _, version = api_version.rpartition("/")
    if group not in _BUILTIN_GROUPS:
        return None
    version = f"{version[:1].upper()}{version[1:]}"
    # Core models whose name collides with another group's are prefixed
    # (e.g. core/v1 Event -> CoreV1Event, while V1Event is events.k8s.io)
    candidates = [f"Core{version}{kind}"] if group == "" else []
    candidates.append(f"{version}{kind}")
    return next((n for n in candidates if hasattr(models, n)), None)
