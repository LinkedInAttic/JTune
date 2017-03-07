import datetime
import pytest

from decimal import Decimal

from jtune.jtune import GCRecord


def test_gcrecord_parnew():
    """Verify parsing of a parnew GC record."""

    parnew = (datetime.datetime(2017, 2, 2, 15, 16, 2, 890000),
              ['2017-02-02T15:16:02.890-0800: 394.312: [GC (Allocation Failure) 394.312: [ParNew\n',
                  'Desired survivor size 1310720 bytes, new threshold 15 (max 15)\n',
                  '- age   1:      82152 bytes,      82152 total\n',
                  '- age   2:      31640 bytes,     113792 total\n',
                  '- age   3:       2656 bytes,     116448 total\n',
                  '- age   5:      27480 bytes,     143928 total\n',
                  '- age   6:        848 bytes,     144776 total\n',
                  '- age   7:        248 bytes,     145024 total\n',
                  '- age   8:       1112 bytes,     146136 total\n',
                  '- age   9:       2424 bytes,     148560 total\n',
                  '- age  11:        160 bytes,     148720 total\n',
                  '- age  12:      11672 bytes,     160392 total\n',
                  '- age  13:        224 bytes,     160616 total\n',
                  '- age  14:         64 bytes,     160680 total\n',
                  '- age  15:      37752 bytes,     198432 total\n',
                  ': 5285K->229K(7680K), 0.0048416 secs] 54518K->49466K(202240K), 0.0049963 secs] [Times: user=0.03 sys=0.00, real=0.01 secs] \n'
               ])

    r = GCRecord(parnew)

    assert not r.is_cms_gc
    assert r.valid_record
    assert len(r.ages) == 15
    assert r.young_gc_time == Decimal('0.0048416')
    assert r.jvm_running_time == 394.31200000000001


def test_gcrecord_stwgc():
    """Validate parsing of a CMS remark stop the world event."""

    cms = (datetime.datetime(2017, 2, 3, 14, 11, 34, 967000), ['2017-02-03T14:11:34.967-0800: 8.389: [GC (CMS Final Remark) [YG occupancy: 1786 K (7680 K)]8.389: [Rescan (parallel) , 0.0008489 secs]8.390: [weak refs processing, 0.0000172 secs]8.390: [class unloading, 0.0143227 secs]8.404: [scrub symbol table, 0.0043012 secs]8.409: [scrub string table, 0.0006371 secs][1 CMS-remark: 31483K(194560K)] 33270K(202240K), 0.0225082 secs] [Times: user=0.06 sys=0.00, real=0.03 secs] \n'])
    r = GCRecord(cms)

    assert not r.is_cms_gc
    assert r.is_stw_gc
    assert r.valid_record
    assert r.stw_time == 0.022508199999999999
    assert r.gc_type == 'CMS-STW'
