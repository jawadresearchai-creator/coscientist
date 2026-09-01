from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]

def joined(name):
    doc=yaml.safe_load((ROOT/'.github/workflows'/name).read_text())
    jobs=doc.get('jobs',{})
    steps=[]
    for j in jobs.values(): steps.extend(j.get('steps',[]))
    return '\n'.join((s.get('name','')+'\n'+str(s.get('run','') or '')) for s in steps)

def test_analysis_uses_manifest_driven_gms_fetch_not_flat_folder_fetch():
    j=joined('analysis.yml')
    assert 'coscientist lake-sync' in j
    assert 'coscientist gms-fetch' in j
    assert 'coscientist fetch --manifest' not in j

def test_analysis_verifies_code_before_gms_outcome_fetch():
    j=joined('analysis.yml')
    assert j.index('coscientist analysis-verify') < j.index('coscientist gms-fetch')

def test_cycle_refreshes_catalog_before_deterministic_stages():
    j=joined('cycle.yml')
    assert j.index('coscientist lake-sync') < j.index('Run the deterministic stages')

def test_doctor_checks_both_github_and_lake_catalog():
    j=joined('doctor.yml')
    assert 'coscientist github-status' in j
    assert 'coscientist lake-sync' in j
    assert 'coscientist lake-doctor' in j
