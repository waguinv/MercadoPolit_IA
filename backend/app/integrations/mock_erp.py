"""Adaptador simulado do ERP: dados em memória, sem rede, determinístico."""
from datetime import UTC, datetime
from decimal import Decimal

from app.integrations.erp_port import (
    ErpNotFoundError,
    ErpProduct,
    ErpStockBalance,
    ErpStockLocation,
    ErpUnit,
)

DEFAULT_UNITS = [
    ErpUnit("UN", "Unidade", False),
    ErpUnit("KG", "Quilograma", True),
    ErpUnit("CX", "Caixa", False),
]
DEFAULT_PRODUCTS = [
    ErpProduct("1001", "Arroz Tipo 1 5kg", "UN", sku="ARZ5", barcode="7890000000011"),
    ErpProduct("1002", "Feijão Carioca 1kg", "UN", sku="FEJ1", barcode="7890000000028"),
    ErpProduct("2001", "Banana Prata", "KG", sku="BAN", barcode=None),
    ErpProduct("2002", "Farinha de Trigo 25kg", "UN", sku="FAR25", barcode="7890000000035"),
    ErpProduct("3001", "Refrigerante Cola 2L (caixa c/6)", "CX", sku="COLA6", barcode="7890000000042"),
]
DEFAULT_BALANCES = {
    ("1001", None): Decimal("120"),
    ("1001", "DEP"): Decimal("100"),
    ("1002", None): Decimal("80"),
    ("2001", None): Decimal("35.500"),
}


DEFAULT_LOCATIONS = [
    ErpStockLocation("DEP", "Depósito"),
    ErpStockLocation("LOJA", "Loja"),
]


class MockErpAdapter:
    def __init__(
        self,
        units: list[ErpUnit] | None = None,
        products: list[ErpProduct] | None = None,
        balances: dict[tuple[str, str | None], Decimal] | None = None,
    ) -> None:
        self.units = list(DEFAULT_UNITS if units is None else units)
        self.products = list(DEFAULT_PRODUCTS if products is None else products)
        self.balances = dict(DEFAULT_BALANCES if balances is None else balances)

    def check_connection(self) -> None:
        return None

    def list_stock_locations(self) -> list[ErpStockLocation]:
        return list(DEFAULT_LOCATIONS)

    def list_units(self) -> list[ErpUnit]:
        return list(self.units)

    def list_products(self) -> list[ErpProduct]:
        return list(self.products)

    def get_stock_balance(
        self, product_external_id: str, location_code: str | None = None
    ) -> ErpStockBalance:
        product = next((p for p in self.products if p.external_id == product_external_id), None)
        if product is None:
            raise ErpNotFoundError(f"Produto {product_external_id} não existe no ERP.")
        qty = self.balances.get((product_external_id, location_code), Decimal("0"))
        return ErpStockBalance(
            product_external_id=product_external_id,
            location_code=location_code,
            quantity=qty,
            as_of=datetime.now(UTC),
        )
