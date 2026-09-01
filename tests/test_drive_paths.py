from coscientist.drive import DriveClient, DriveCredentials, DriveError, FOLDER_MIME
import pytest

class FakeDrive(DriveClient):
    def __init__(self, tree):
        super().__init__(DriveCredentials('a','b','c'))
        self.tree=tree
    def list_folder(self,folder_id):
        return self.tree.get(folder_id,[])

def test_resolve_path_walks_only_named_segments():
    tree={'root':[{'id':'d1','name':'01_RAW_IMMUTABLE','mimeType':FOLDER_MIME}],
          'd1':[{'id':'d2','name':'SEC','mimeType':FOLDER_MIME}],
          'd2':[{'id':'f','name':'x.zip','mimeType':'application/zip'}]}
    assert FakeDrive(tree).resolve_path('root','01_RAW_IMMUTABLE/SEC/x.zip')['id']=='f'

def test_resolve_path_rejects_ambiguous_segment():
    tree={'root':[{'id':'a','name':'SEC','mimeType':FOLDER_MIME},{'id':'b','name':'SEC','mimeType':FOLDER_MIME}]}
    with pytest.raises(DriveError,match='ambiguous'):
        FakeDrive(tree).resolve_path('root','SEC/x')
