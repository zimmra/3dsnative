"""Synthetic icon-format checks; no game artwork is committed."""
from pathlib import Path
import struct
import sys
import unittest
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app/tools'))
from game_icon import icon_rgb, png

class Icon(unittest.TestCase):
    def test_rgb565_morton_tiles_and_png_dimensions(self):
        smdh=bytearray(0x36c0); smdh[:4]=b'SMDH'
        for y in range(48):
            for x in range(48):
                morton=sum(((x>>b)&1)<<(b*2)|((y>>b)&1)<<(b*2+1) for b in range(3))
                offset=0x24c0+2*(((y//8)*6+x//8)*64+morton)
                struct.pack_into('<H',smdh,offset,((x%32)<<11)|((y%64)<<5)|31)
        rgb=icon_rgb(bytes(smdh))
        for x,y in [(0,0),(7,8),(8,7),(47,47)]:
            offset=(y*48+x)*3
            self.assertEqual(rgb[offset:offset+3],bytes([((x%32)<<3)|((x%32)>>2),(y<<2)|(y>>4),255]))
        data=png(rgb,180)
        self.assertEqual(struct.unpack_from('>II',data,16),(180,180))
        position=8; compressed=b''
        while position < len(data):
            length=struct.unpack_from('>I',data,position)[0]
            if data[position+4:position+8]==b'IDAT': compressed+=data[position+8:position+8+length]
            position+=12+length
        self.assertEqual(len(zlib.decompress(compressed)),180*(180*3+1))
