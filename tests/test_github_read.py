import io, json
from coscientist.github import GitHubReadClient

class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self,*a): return False

def test_readonly_github_status_reads_head_and_runs():
    def opener(req,timeout=None):
        u=req.full_url
        if '/actions/runs' in u:
            return Resp(json.dumps({'workflow_runs':[{'id':1,'name':'ingest','status':'completed','conclusion':'success','head_sha':'abc'}]}).encode())
        if '/commits/main' in u:
            return Resp(json.dumps({'sha':'abc123'}).encode())
        return Resp(json.dumps({'default_branch':'main'}).encode())
    s=GitHubReadClient('owner/repo',opener=opener).status()
    assert s.head_sha=='abc123'
    assert s.latest_runs[0]['conclusion']=='success'
