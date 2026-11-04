"""Exercise the actual portable screen-layout function, including fold regions."""
import ctypes as c
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class Rect(c.Structure):
    _fields_ = [('x',c.c_double),('y',c.c_double),('w',c.c_double),('h',c.c_double)]
class Geometry(c.Structure):
    _fields_ = [('top',Rect),('bottom',Rect),('controls',Rect),('landscape_options',c.c_int),('control_unit',c.c_double),('bottom_menu',c.c_int)]
class Point(c.Structure):
    _fields_ = [('x',c.c_double),('y',c.c_double)]

class Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        header = Path(__file__).resolve().parents[1]/'app/ios/Sources/N3DSGeometry.h'
        (root/'check.c').write_text(f'#include "{header}"\nN3DSGeometry layout(N3DSRect b,N3DSRect d,int p,int s,int f) {{ return n3ds_geometry(b,d,p,s,f); }}\nN3DSPoint anchor(N3DSRect b,int g) {{ return n3ds_control_anchor(b,g); }}\nN3DSPoint position(N3DSRect b,int g,double u,int m) {{ return n3ds_control_position(b,g,u,m); }}\ndouble grab(double u,int g) {{ return n3ds_control_grab(u,g); }}\n')
        env = os.environ.copy()
        env.pop('IPHONEOS_DEPLOYMENT_TARGET', None); env.pop('SDKROOT', None)
        subprocess.run(['cc','-shared','-fPIC',str(root/'check.c'),'-o',str(root/'layout.so'),'-lm'],check=True,env=env)
        cls.lib = c.CDLL(str(root/'layout.so'))
        cls.lib.layout.argtypes = [Rect,Rect,c.c_int,c.c_int,c.c_int]
        cls.lib.layout.restype = Geometry
        cls.lib.anchor.argtypes = [Rect,c.c_int]
        cls.lib.anchor.restype = Point
        cls.lib.position.argtypes = [Rect,c.c_int,c.c_double,c.c_int]
        cls.lib.position.restype = Point
        cls.lib.grab.argtypes = [c.c_double,c.c_int]
        cls.lib.grab.restype = c.c_double
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def layout(self, b, display=0, flip=0, partial=0, fold=None):
        return self.lib.layout(Rect(*b),Rect(*(fold or (0,0,0,0))),partial,display,flip)
    def test_options_are_only_regular_landscape(self):
        self.assertTrue(self.layout((0,0,900,600)).landscape_options)
        self.assertFalse(self.layout((0,0,390,844)).landscape_options)
        self.assertFalse(self.layout((0,0,900,600),partial=1).landscape_options)
    def test_portrait_and_tabletop_ignore_landscape_preferences(self):
        for b, partial, fold in [((0,0,390,844),0,None), ((0,0,800,1000),1,(0,490,800,20))]:
            expected = self.layout(b,partial=partial,fold=fold)
            for display in range(4):
                actual = self.layout(b,display,1,partial,fold)
                self.assertEqual([(r.x,r.y,r.w,r.h) for r in (actual.top,actual.bottom,actual.controls)], [(r.x,r.y,r.w,r.h) for r in (expected.top,expected.bottom,expected.controls)])
    def test_single_screen_modes_and_flip(self):
        b=(0,0,1000,700)
        self.assertEqual(self.layout(b,1).bottom.w,0)
        self.assertEqual(self.layout(b,2).top.w,0)
        first, flipped = self.layout(b,3),self.layout(b,3,1)
        self.assertGreater(first.top.w,flipped.top.w)
        self.assertLess(first.bottom.w,flipped.bottom.w)
    def test_screens_preserve_aspect_and_stay_above_controls(self):
        for w,h in [(390,844),(844,390),(800,1000),(1000,800),(460,700)]:
            for display in range(4):
                layout=self.layout((10,20,w,h),display)
                for screen,ratio in [(layout.top,400/240),(layout.bottom,320/240)]:
                    if not screen.w: continue
                    self.assertAlmostEqual(screen.w/screen.h,ratio)
                    self.assertGreaterEqual(screen.x,10)
                    self.assertLessEqual(screen.x+screen.w,10+w+.001)
                    if layout.bottom_menu:
                        if screen.y >= layout.controls.y:
                            self.assertLessEqual(screen.y+screen.h,layout.controls.y+layout.controls.h-64+.001)
                        else:
                            self.assertLessEqual(screen.y+screen.h,layout.controls.y+.001)
                    else:
                        self.assertLessEqual(screen.y+screen.h,layout.controls.y+.001)
    def test_inactive_fold_keeps_screens_on_their_panels(self):
        layout=self.layout((0,0,800,1000),partial=1,fold=(0,500,800,0))
        self.assertLessEqual(layout.top.y+layout.top.h,500)
        self.assertGreaterEqual(layout.bottom.y,500)
    def test_face_buttons_are_rightmost_and_cstick_is_inward(self):
        for width in (320,390,800,1000):
            stick,dpad,face,cstick = [self.lib.anchor(Rect(0,0,width,260),i) for i in range(4)]
            self.assertLess(stick.x,dpad.x)
            self.assertLess(dpad.x,cstick.x)
            self.assertGreater(cstick.x,width/2)
            self.assertLess(cstick.x,face.x)
            self.assertGreater(cstick.y,face.y)

    def test_portrait_matches_book_panels_and_uses_bottom_menu(self):
        portrait=self.layout((0,0,800,1000))
        book=self.layout((0,0,800,1000),partial=1,fold=(0,500,800,0))
        for field in ('top','bottom','controls'):
            a,b=getattr(portrait,field),getattr(book,field)
            self.assertEqual((a.x,a.y,a.w,a.h),(b.x,b.y,b.w,b.h))
        self.assertTrue(portrait.bottom_menu)
        self.assertGreater(book.bottom.h,238) # previous book-mode lower-screen height
    def test_control_scale_is_portrait_baseline_across_inner_layouts(self):
        portrait=self.layout((0,0,800,1000))
        book=self.layout((0,0,800,1000),partial=1,fold=(0,490,800,20))
        for display in range(4):
            self.assertAlmostEqual(self.layout((0,0,1000,800),display).control_unit,portrait.control_unit)
        self.assertAlmostEqual(book.control_unit,portrait.control_unit)
    def test_vertical_flip_keeps_large_screen_above_small_screen(self):
        normal,flipped=self.layout((0,0,1000,800),3),self.layout((0,0,1000,800),3,1)
        self.assertLess(normal.top.y,normal.bottom.y)
        self.assertLess(flipped.bottom.y,flipped.top.y)
        self.assertTrue(normal.bottom_menu and flipped.bottom_menu)

    def test_small_display_excludes_control_touch_targets(self):
        for b in [(0,0,800,1000),(0,0,1000,800),(0,0,390,844)]:
            layout=self.layout(b,3)
            screen=layout.bottom
            self.assertGreater(screen.w,0)
            for group in range(4):
                center=self.lib.position(layout.controls,group,layout.control_unit,layout.bottom_menu)
                radius=self.lib.grab(layout.control_unit,group)
                x=min(max(center.x,screen.x),screen.x+screen.w)
                y=min(max(center.y,screen.y),screen.y+screen.h)
                self.assertGreaterEqual(((center.x-x)**2+(center.y-y)**2)**.5,radius)
    def test_flat_portrait_honors_inactive_division(self):
        flat=self.layout((0,0,800,1000),fold=(0,490,800,20))
        bent=self.layout((0,0,800,1000),partial=1,fold=(0,490,800,20))
        for field in ('top','bottom','controls'):
            a,b=getattr(flat,field),getattr(bent,field)
            self.assertEqual((a.x,a.y,a.w,a.h),(b.x,b.y,b.w,b.h))
