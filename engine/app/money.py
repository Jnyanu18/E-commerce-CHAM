from decimal import ROUND_HALF_UP, Decimal


def rupees_to_paise(value: str | int | float | Decimal) -> int:
    """Convert rupees to integer paise. Strings/Decimals avoid float error; floats go via str."""
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paise_to_rupees_str(paise: int) -> str:
    return f"{Decimal(paise) / 100:,.2f}"
