import torch

from functools import lru_cache

from ...cplx import Cplx


class CplxParameter(torch.nn.ParameterDict):
    """Torch-friendly container for complex-valued parameter."""
    def __init__(self, cplx):
        if not isinstance(cplx, Cplx):
            raise TypeError(f"""`{type(self).__name__}` accepts only """
                            f"""Cplx tensors.""")

        super().__init__({
            "real": torch.nn.Parameter(cplx.real),
            "imag": torch.nn.Parameter(cplx.imag),
        })

        # save reference to the underlying cplx data
        self._cplx = cplx

    def _load_from_state_dict(self, state_dict, prefix, local_metadata,
                              strict, missing_keys, unexpected_keys,
                              error_msgs):

        missing, unexpected = [], []
        super()._load_from_state_dict(state_dict, prefix, local_metadata,
                                      strict, missing, unexpected, error_msgs)

        if len(missing) == 2:
            # By desgin of `__init__` we get here if the last call to
            # `_load_from_state_dict` did not perform anything (only might
            # possibly have found some unexpected keys), did not register any
            # errors and could not change the state of the model. Hence, it
            # is safe to call it once more below.

            # So, we could not load either `.real` or `.imag`. Thus all parts
            # are missing, and we must try to look if the dict contains a
            # parameter that is being promoted from real to complex tensor.
            real, dot, _ = prefix.rpartition(".")
            if real not in state_dict:
                # indicate that the parameter as a whole is missing (not
                # just parts)
                missing = [real]

            else:
                # state dict has a key that matches the name of this
                # parameter dict, so we upcast from real to complex.
                par, missing = state_dict[real], []

                # recursively call on a special state_dict to promote R to C
                # NO missing or unexpected items are possible by design, only
                # shape mismatch or general exceptions are now possible.
                self._load_from_state_dict(
                    {f"{real}.real": par, f"{real}.imag": torch.zeros_like(par)},
                    prefix, local_metadata, strict, [], [], error_msgs)

        elif len(missing) == 1:
            # Although loaded either `.real` or `.imag`, the state_dict
            # contains only one of ".real" or ".imag", so the other part is
            # missing. Therefore append to `error_msg`.
            error_msgs.append(f"Complex parameter requires both `.imag`"
                              f" and `.imag` parts. Missing `{missing[0]}`.")

        if strict and unexpected:
            error_msgs.append(f"Complex parameter disallows redundant key(s)"
                              f" in state_dict: {unexpected}.")

        unexpected_keys.extend(unexpected)
        missing_keys.extend(missing)

    def extra_repr(self):
        return repr(tuple(self._cplx.shape))[1:-1]

    @property
    def data(self):
        return self._cplx


class CplxParameterAccessor():
    """Cosmetic complex parameter accessor.

    Details
    -------
    This works both for the default `forward()` inherited from Linear,
    and for what the user expects to see when they request weight from
    the layer (masked zero values).

    Warning
    -------
    This hacky property works only because torch.nn.Module implements
    its own special attribute access mechanism via `__getattr__`. This
    is why `SparseWeightMixin` in .masked couldn't work with 'weight'
    as a read-only @property.
    """
    def __getattr__(self, name):
        # default attr lookup straight to parent's __getattr__
        attr = super().__getattr__(name)
        if not isinstance(attr, CplxParameter):  # automatically handles None
            return attr

        # Cplx() is a light weight container for mutable real-imag parts.
        #  Can we cache this? What if creating `Cplx` is costly?
        return Cplx(attr.real, attr.imag)


class BaseRealToCplx(torch.nn.Module):
    pass


class BaseCplxToReal(torch.nn.Module):
    pass


class _CplxToCplxMeta(type):
    """Meta class for bracketed creation of componentwise operations."""
    @lru_cache(maxsize=None)
    def __getitem__(self, Base):
        # make sure that base is not an instance, and that no
        #  nested wrapping takes place.
        assert isinstance(Base, type) and issubclass(Base, torch.nn.Module)
        if issubclass(Base, (CplxToCplx, BaseRealToCplx)):
            return Base

        if Base is torch.nn.Module:
            return CplxToCplx

        class template(Base, CplxToCplx):
            def forward(self, input):
                """Apply to real and imaginary parts independently."""
                return input.apply(super().forward)

        name = "Cplx" + Base.__name__
        template.__name__ = template.__qualname__ = name
        return template


class CplxToCplx(CplxParameterAccessor, torch.nn.Module,
                 metaclass=_CplxToCplxMeta):
    pass


def is_from_cplx(module):
    if isinstance(module, (CplxToCplx, BaseCplxToReal)):
        return True

    if isinstance(module, torch.nn.Sequential):
        return is_from_cplx(module[0])

    if isinstance(module, type):
        return issubclass(module, (CplxToCplx, BaseCplxToReal))

    return False


def is_to_cplx(module):
    if isinstance(module, (CplxToCplx, BaseRealToCplx)):
        return True

    if isinstance(module, torch.nn.Sequential):
        return is_to_cplx(module[-1])

    if isinstance(module, type):
        return issubclass(module, (CplxToCplx, BaseRealToCplx))

    return False


def is_cplx_to_cplx(module):
    return is_from_cplx(module) and is_to_cplx(module)
