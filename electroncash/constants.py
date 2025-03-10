from typing import List, Mapping, Sequence

PROJECT_NAME: str = "Electrum ABC"
PROJECT_NAME_NO_SPACES = "ElectrumABC"
SCRIPT_NAME: str = "electrum-abc"
REPOSITORY_OWNER: str = "Bitcoin-ABC"
REPOSITORY_NAME: str = "ElectrumABC"
REPOSITORY_URL: str = f"https://github.com/{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
RELEASES_JSON_URL: str = (
    f"https://raw.github.com/{REPOSITORY_OWNER}/"
    f"{REPOSITORY_NAME}/master/contrib/update_checker"
    f"/releases.json"
)

POSIX_DATA_DIR: str = ".electrum-abc"
"""This is the name of the directory where the wallets, recent server lists
and some other things are saved.
"""

PORTABLE_DATA_DIR: str = "electrum_abc_data"
"""This is the name of the dir where the wallets, recent server lists and some
other things are saved when running the portable .exe file.
This directory is saved in the local directory containing the exe.
"""

CURRENCY = "eCash"


class Unit:
    def __init__(self, ticker: str, decimals: int, old_name: str = ""):
        self.ticker = ticker
        self.decimals = decimals
        self.old_name = old_name
        """old_unit can be specified to show additional information in the
        unit selection menu."""

    @property
    def name_for_selection_menu(self):
        return self.ticker if not self.old_name else f"{self.ticker} ({self.old_name})"

    def __str__(self):
        return self.ticker


SAT = Unit("sats", 0)
XEC = Unit("XEC", 2, "bits")
MegaXEC = Unit("MegaXEC", 8)

BASE_UNITS: Sequence[Unit] = [SAT, XEC, MegaXEC]
"""List of units"""

BASE_UNITS_BY_DECIMALS: Mapping[int, str] = {
    SAT.decimals: SAT.ticker,
    XEC.decimals: XEC.ticker,
    MegaXEC.decimals: MegaXEC.ticker,
}
"""Dict of units indexed by number of decimals"""

CASHADDR_PREFIX: str = "ecash"
CASHADDR_PREFIX_BCH: str = "bitcoincash"
CASHADDR_TESTNET_PREFIX = "ectest"
CASHADDR_TESTNET_PREFIX_BCH = "bchtest"

WHITELISTED_PREFIXES: List[str] = [CASHADDR_PREFIX, CASHADDR_PREFIX_BCH]

WHITELISTED_TESTNET_PREFIXES: List[str] = [
    CASHADDR_TESTNET_PREFIX,
    CASHADDR_TESTNET_PREFIX_BCH,
]

DUST_THRESHOLD: int = 546
"""
Change < dust threshold is added to the tx fee.
The unit is satoshis.
"""
