import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from coscientist.drive import DriveCredentials, CredentialError
from coscientist.gms_lake import (
    AnalysisRoute, Availability, GMSCatalog, GMSLakeError, build_catalog,
    fetch_frozen_from_gms, query_plan, read_manifest,
)
from coscientist.freeze import FreezeManifest
from coscientist.lake import LakeCatalog
from coscientist.models import Candidate, Construct, Necessity
from coscientist.registry import SourceRegistry
from coscientist.gates import g3_access


def make_manifest(path: Path, rows):
    con = sqlite3.connect(path)
    con.execute('''CREATE TABLE files (
        source_id TEXT, domain TEXT, dataset_id TEXT, remote_path TEXT,
        sha256 TEXT, bytes INTEGER, status TEXT, concepts TEXT,
        granularity TEXT, coverage_start TEXT, coverage_end TEXT,
        last_checked TEXT, last_changed TEXT
    )''')
    con.executemany('INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    con.commit(); con.close()


def test_rclone_json_token_is_accepted():
    env = {
        'GDRIVE_CLIENT_ID': 'cid', 'GDRIVE_CLIENT_SECRET': 'secret',
        'GDRIVE_TOKEN': json.dumps({'access_token':'a','refresh_token':'REFRESH','expiry':'x'})
    }
    assert DriveCredentials.from_env(env).refresh_token == 'REFRESH'


def test_rclone_json_without_refresh_token_fails():
    env = {'GDRIVE_CLIENT_ID':'cid','GDRIVE_CLIENT_SECRET':'s','GDRIVE_TOKEN':'{"access_token":"a"}'}
    with pytest.raises(CredentialError):
        DriveCredentials.from_env(env)


def test_sqlite_manifest_builds_available_direct_object(tmp_path):
    p = tmp_path/'m.sqlite'
    make_manifest(p, [('SEC','01_FIN','companyfacts','01_FIN/SEC/companyfacts.zip','a'*64,1000,'OK','filings,accounting','firm-year','2010','2024','x','y')])
    items = read_manifest(str(p), repo_sha='deadbeef')
    assert len(items) == 1
    d=items[0]
    assert d.availability == Availability.AVAILABLE.value
    assert d.analysis_route == AnalysisRoute.DIRECT_FETCH.value
    assert d.direct_fetch is True
    assert d.lake_repo_sha == 'deadbeef'


def test_openalex_is_query_layer_required_even_when_available(tmp_path):
    p=tmp_path/'m.sqlite'
    make_manifest(p,[('OPENALEX_SNAPSHOT','09_SCIENCE','works','09_SCIENCE/OPENALEX_SNAPSHOT/works/part.parquet','b'*64,10_000,'OK','bibliometrics','work','2020','2026','x','y')])
    d=read_manifest(str(p))[0]
    assert d.availability == 'AVAILABLE'
    assert d.analysis_route == 'QUERY_LAYER_REQUIRED'
    assert d.direct_fetch is False


def test_large_object_is_query_layer_required(tmp_path):
    p=tmp_path/'m.sqlite'
    make_manifest(p,[('BIG','D','x','D/BIG/x.parquet','c'*64,5000,'OK','x','row','2020','2024','x','y')])
    d=read_manifest(str(p), direct_fetch_limit=1000)[0]
    assert d.analysis_route == 'QUERY_LAYER_REQUIRED'


def test_curated_path_is_preferred_by_query_plan(tmp_path):
    p=tmp_path/'m.sqlite'
    make_manifest(p,[
        ('SRC','D','raw','D/SRC/raw.csv','a'*64,100,'OK','tax','firm-year','2010','2024','x','y'),
        ('SRC','D','curated','02_CURATED/D/SRC/tax_extract.parquet','b'*64,100,'OK','tax','firm-year','2010','2024','x','y'),
    ])
    c=build_catalog([str(p)])
    plan=query_plan(c,concepts=['tax'],columns=['firm','year'],filters=['year<=2024'])
    assert plan['candidates'][0]['analysis_route']=='CURATED_QUERY'
    assert 'whole lake' in plan['policy']


def test_catalog_roundtrip_is_legacy_lake_compatible(tmp_path):
    p=tmp_path/'m.sqlite'; out=tmp_path/'catalog.json'
    make_manifest(p,[('SRC','D','x','D/SRC/x.csv','a'*64,100,'OK','tax','firm-year','2010','2024','x','y')])
    c=build_catalog([str(p)],lake_repo='a/b',lake_repo_sha='123')
    c.save(out)
    lc=LakeCatalog.load(out)
    assert len(lc.datasets)==1
    ds=next(iter(lc.datasets.values()))
    assert ds.remote_path=='D/SRC/x.csv'
    assert ds.availability=='AVAILABLE'


def test_query_required_lake_route_defers_g3(tmp_path):
    p=tmp_path/'m.sqlite'; out=tmp_path/'catalog.json'
    make_manifest(p,[('OPENALEX_SNAPSHOT','D','works','D/OPENALEX_SNAPSHOT/works.parquet','a'*64,100,'OK','bibliometrics','work','2010','2026','x','y')])
    build_catalog([str(p)]).save(out)
    lc=LakeCatalog.load(out)
    c=Candidate(id='C',title='x',question='q',design='d',constructs=[
        Construct(name='papers',role='outcome',concepts=['bibliometrics'],necessity=Necessity.ESSENTIAL)
    ])
    r=g3_access(c,SourceRegistry({}),lc)
    assert r.verdict.value=='DEFER'
    assert 'query/curation' in r.reason


def test_fetch_refuses_query_required_object(tmp_path):
    p=tmp_path/'m.sqlite'
    make_manifest(p,[('OPENALEX_SNAPSHOT','D','works','D/OPENALEX_SNAPSHOT/works.parquet','a'*64,100,'OK','bibliometrics','work','2010','2026','x','y')])
    cat=build_catalog([str(p)])
    fm=FreezeManifest(candidate_id='C',question='q',estimand='e',design='d',sample_definition='s',treatment='t',outcome='o',dataset_hashes={'D/OPENALEX_SNAPSHOT/works.parquet':'a'*64})
    class NeverDrive:
        def download_path(self,*a,**k): raise AssertionError('must not download')
    with pytest.raises(GMSLakeError,match='direct fetch is forbidden'):
        fetch_frozen_from_gms(NeverDrive(),'root',fm,cat,str(tmp_path/'data'))


def test_failed_manifest_row_is_not_available(tmp_path):
    p=tmp_path/'m.sqlite'
    make_manifest(p,[('SRC','D','x','D/SRC/x','a'*64,100,'FAILED','x','row','2010','2020','x','y')])
    d=read_manifest(str(p))[0]
    assert d.availability=='UNAVAILABLE'
