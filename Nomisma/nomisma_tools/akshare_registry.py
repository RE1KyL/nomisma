"""Reviewed source metadata, not a dynamic function-name dispatch mechanism."""
from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType

from dataset_store import DatasetError


class SourceStatus(str, Enum):
    VERIFIED = 'verified'
    DISABLED_CANDIDATE = 'disabled_candidate'
    PLANNED = 'planned'


@dataclass(frozen=True)
class Source:
    provider: str
    interface_name: str
    function_name: str
    status: SourceStatus
    supported_symbols: tuple[str, ...]
    output_schema: tuple[tuple[str, str, str], ...]
    package_version: str
    verified_at: str | None
    verification_status: str
    notes: str


SINA_ID = 'cn_index_hs300_sina_daily'
SINA_COLUMNS = ('date', 'open', 'high', 'low', 'close', 'volume')
STANDARD_COLUMN_MAPPING = MappingProxyType({name: name for name in SINA_COLUMNS})
SOURCES = MappingProxyType({
    SINA_ID: Source('sina', SINA_ID, 'stock_zh_index_daily', SourceStatus.VERIFIED,
                   ('sh000300',),
                   (('date','date','not_applicable'),('open','number','index_points'),
                    ('high','number','index_points'),('low','number','index_points'),
                    ('close','number','index_points'),('volume','number','unknown')),
                   '1.20.18','2026-11-01T11:00:08.908975+00:00','phase2a0_live_passed',
                   'Full history only; filter dates locally. Volume unit remains unverified.'),
    'cn_equity_eastmoney_history': Source('eastmoney','cn_equity_eastmoney_history','stock_zh_a_hist',
        SourceStatus.DISABLED_CANDIDATE,(),(), '1.20.18',None,'live_failed_push2_rate_limit',
        'Disabled candidate. No execution adapter or automatic fallback.'),
    'cn_equity_eastmoney_profile': Source('eastmoney','cn_equity_eastmoney_profile','stock_individual_info_em',
        SourceStatus.DISABLED_CANDIDATE,(),(), '1.20.18',None,'live_failed_push2_rate_limit',
        'Disabled candidate. No execution adapter or automatic fallback.'),
})


def source_catalog() -> list[dict]:
    return [asdict(source) for source in SOURCES.values()]


def verified_source(interface_name: str) -> Source:
    if not isinstance(interface_name,str) or interface_name not in SOURCES:
        raise DatasetError('SOURCE_NOT_ALLOWED', 'Source is unregistered; no function will be called')
    source = SOURCES[interface_name]
    if source.status != SourceStatus.VERIFIED:
        raise DatasetError('SOURCE_DISABLED', 'Source has not passed live verification')
    if interface_name != SINA_ID:
        raise DatasetError('SOURCE_NOT_ALLOWED', 'Only the reviewed Sina HS300 adapter is implemented')
    return source

