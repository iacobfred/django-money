import warnings
from types import MappingProxyType
from typing import Optional, Union
from babel.numbers import get_currency_precision

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db.models import F
from django.utils import translation
from django.utils.deconstruct import deconstructible
from django.utils.html import avoid_wrapping, conditional_escape
from django.utils.safestring import mark_safe
import moneyed.l10n
from moneyed import Currency, Money as DefaultMoney

from .settings import DECIMAL_PLACES, MONEY_FORMAT


__all__ = ["Money", "Currency"]


@deconstructible
class Money(DefaultMoney):
    """
    Extends functionality of Money with Django-related features.
    """

    use_l10n: "Optional[bool]" = None

    def __init__(
        self,
        amount: "object",
        currency: "Optional[Union[Currency, str]]" = None,
        format_options: "Optional[dict]" = None,
        decimal_places: "Optional[int]" = None,
        **kwargs,
    ):
        self.decimal_places = decimal_places if decimal_places is not None else DECIMAL_PLACES
        self.format_options = MappingProxyType(format_options) if format_options is not None else None
        super().__init__(amount, currency, **kwargs)

    def _copy_attributes(self, source, target):
        """Copy attributes to the new `Money` instance.

        This class stores extra bits of information about string formatting that the parent class doesn't have.
        The problem is that the parent class creates new instances of `Money` without in some of its methods and
        it does so without knowing about `django-money`-level attributes.
        For this reason, when this class uses some methods of the parent class that have this behavior, the resulting
        instances lose those attribute values.

        When it comes to what number of decimal places to choose, we take the maximum number.
        """
        selection = [
            getattr(candidate, "decimal_places", None)
            for candidate in (self, source)
            if getattr(candidate, "decimal_places", None) is not None
        ]
        if selection:
            target.decimal_places = max(selection)

    def __add__(self, other):
        if isinstance(other, F):
            return other.__radd__(self)
        other = maybe_convert(other, self.currency)
        result = super().__add__(other)
        self._copy_attributes(other, result)
        return result

    def __sub__(self, other):
        if isinstance(other, F):
            return other.__rsub__(self)
        other = maybe_convert(other, self.currency)
        result = super().__sub__(other)
        self._copy_attributes(other, result)
        return result

    def __mul__(self, other):
        if isinstance(other, F):
            return other.__rmul__(self)
        result = super().__mul__(other)
        self._copy_attributes(other, result)
        return result

    def __truediv__(self, other):
        if isinstance(other, F):
            return other.__rtruediv__(self)
        result = super().__truediv__(other)
        if isinstance(result, self.__class__):
            self._copy_attributes(other, result)
        return result

    def __rtruediv__(self, other):
        # Backported from py-moneyed, non released bug-fix
        # https://github.com/py-moneyed/py-moneyed/blob/c518745dd9d7902781409daec1a05699799474dd/moneyed/classes.py#L217-L218
        raise TypeError("Cannot divide non-Money by a Money instance.")

    @property
    def is_localized(self):
        if self.use_l10n is None:
            # This definitely raises a warning in Django 4 - we want to ignore RemovedInDjango50Warning
            # However, we cannot ignore this specific warning class as it doesn't exist in older
            # Django versions
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                setting = getattr(settings, "USE_L10N", True)
            return setting
        return self.use_l10n

    def __str__(self):
        format_options = {
            **MONEY_FORMAT,
            **(self.format_options or {}),
        }
        locale = get_current_locale()
        if locale:
            format_options["locale"] = locale
        return moneyed.l10n.format_money(self, **format_options)

    def __html__(self):
        return mark_safe(avoid_wrapping(conditional_escape(str(self))))

    def __round__(self, n=None):
        amount = round(self.amount, n)
        new = self.__class__(amount, self.currency)
        self._copy_attributes(self, new)
        return new

    def round(self, ndigits=0):
        new = super().round(ndigits)
        self._copy_attributes(self, new)
        return new

    def __pos__(self):
        new = super().__pos__()
        self._copy_attributes(self, new)
        return new

    def __neg__(self):
        new = super().__neg__()
        self._copy_attributes(self, new)
        return new

    def __abs__(self):
        new = super().__abs__()
        self._copy_attributes(self, new)
        return new

    def __rmod__(self, other):
        new = super().__rmod__(other)
        self._copy_attributes(self, new)
        return new

    # DefaultMoney sets those synonym functions
    # we overwrite the 'targets' so the wrong synonyms are called
    # Example: we overwrite __add__; __radd__ calls __add__ on DefaultMoney...
    __radd__ = __add__
    __rmul__ = __mul__
    
    def quantize(self, exp=None, rounding=None) -> 'Money':
        """Return a copy of the object with its amount quantized.

        If `exp` is given the resulting exponent will match that of `exp`.

        Otherwise the resulting exponent will be set to the correct exponent
        of the currency if it's known and to default (two decimal places)
        otherwise.
        """
        if rounding is None:
            rounding = ROUND_HALF_UP
        if exp is None:
            digits = get_currency_precision(self.currency)
            exp = Decimal('0.1') ** digits
        else:
            exp = Decimal(exp)
        return Money(
            self.amount.quantize(exp, rounding=rounding), self.currency)


def get_current_locale():
    return translation.to_locale(
        translation.get_language()
        # get_language can return None starting from Django 1.8
        or settings.LANGUAGE_CODE
    )


def maybe_convert(value, currency):
    """
    Converts other Money instances to the local currency if `AUTO_CONVERT_MONEY` is set to True.
    """
    if getattr(settings, "AUTO_CONVERT_MONEY", False) and value.currency != currency:
        from .contrib.exchange.models import convert_money

        return convert_money(value, currency)
    return value
