import qbet.calculations as calculations


def test_calculation_package_exports_public_api() -> None:
    namespace: dict[str, object] = {}

    exec("from qbet.calculations import *", namespace)

    for name in (
        "ArbitrageOffer",
        "DutchingAllocation",
        "DutchingInput",
        "DutchingOffer",
        "DutchingResult",
        "DutchingTargetMode",
        "TwoWayArbitrageInput",
        "TwoWayArbitrageResult",
        "calculate_dutching",
        "calculate_two_way_arbitrage",
        "FreeBetInput",
        "FreeBetResult",
        "FreeBetStakeReturn",
        "calculate_free_bet",
    ):
        assert name in calculations.__all__
        assert namespace[name] is getattr(calculations, name)