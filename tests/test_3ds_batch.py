"""Choose decrypted updates without treating encrypted duplicates as extra games."""
from pathlib import Path
import unittest
from scripts import build_ipas_from_3ds as batch
from three_ds import Dump,NCCH,DumpError

class Batch(unittest.TestCase):
    def dump(self,title,version,decrypted):
        return Dump(Path('synthetic'), 'cia' if version is not None else 'cci',title,version,(),(NCCH(0,512,title,decrypted,''),))
    def test_decrypted_update_wins_over_encrypted_duplicate(self):
        base=self.dump(0x0004000000000100,None,True)
        title=base.title_id|0x0000000E00000000
        encrypted=self.dump(title,0,False); plain=self.dump(title,0,True)
        self.assertIs(batch.select_update(base,[encrypted,plain]),plain)
    def test_newer_encrypted_update_is_not_silently_downgraded(self):
        base=self.dump(0x0004000000000100,None,True)
        title=base.title_id|0x0000000E00000000
        with self.assertRaises(DumpError):
            batch.select_update(base,[self.dump(title,2,False),self.dump(title,1,True)])
