"""Exceptions raised while validating a resolver request."""


class InputError(ValueError):
    """The JSON request does not satisfy the resolver contract."""
