from decimal import Decimal as D
from src import pmus_aws_supervisor as sup
from src import pmus_q1_executor_v4 as q1
from src import pmus_settlement_auditor as settle

class PositionOnlyDecimal:
    def request(self, method, path, body=None, base=q1.API):
        if path == '/v1/orders/open': return {'orders': []}
        if path == '/v1/portfolio/positions':
            return {'positions': {'m': {'netPositionDecimal': '1.0000'}}}
        raise AssertionError((method,path))

def test_supervisor_blocks_net_position_decimal_only():
    reason=sup.account_block_reason(PositionOnlyDecimal())
    assert reason and reason.startswith('nonzero_positions:')
    assert 'm=1.0000' in reason

def test_v4_reads_net_position_decimal_only():
    assert q1._position_for_slug(PositionOnlyDecimal(), 'm') == D('1.0000')

class Activities:
    def __init__(self): self.paths=[]
    def request(self, method, path, body=None, base=q1.API):
        self.paths.append(path)
        assert path == '/v1/portfolio/activities'
        return {'activities': [
          {'type':'ACTIVITY_TYPE_TRADE','trade':{'marketSlug':'other'}},
          {'type':'ACTIVITY_TYPE_TRADE','trade':{'marketSlug':'m'}},
          {'type':'ACTIVITY_TYPE_POSITION_RESOLUTION','positionResolution':{'marketSlug':'m'}},
        ]}

def test_settlement_activity_auth_path_has_no_query_and_filters_locally():
    t=Activities()
    rows=settle._activities(t,'m','ACTIVITY_TYPE_TRADE')
    assert len(rows)==1 and rows[0]['trade']['marketSlug']=='m'
    assert t.paths == ['/v1/portfolio/activities']
