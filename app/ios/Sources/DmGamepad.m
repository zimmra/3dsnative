// Controls adapted from nxnative; the runtime merges touch and physical input independently.

#import <UIKit/UIKit.h>
#import <GameController/GameController.h>
#import <CoreMotion/CoreMotion.h>
#import <CoreText/CoreText.h>
#include <pthread.h>
#include <stdatomic.h>
#include "runtime.h"
#import "DmLayout.h"
#import "N3DSGeometry.h"

enum { B_A = 0, B_B, B_X, B_Y, B_LS, B_RS, B_L, B_R, B_ZL, B_ZR, B_PLUS, B_MINUS, B_LEFT, B_UP, B_RIGHT, B_DOWN, B_N };
enum { C_NONE = 0, C_STICK, C_DPAD, C_FACE, C_BTN, C_GAME };


static const CGFloat
    K_ZL_X = 44, K_ZL_Y = 40, K_ZL_R = 26,
    K_L_X = 44, K_L_Y = 108, K_L_R = 24,
    K_DROP_TIP = 1.45,
    K_CORNER_IN = 0.5,
    K_FACE_X = 81, K_PAD_Y = 82,
    K_FACE_GAP = 50, K_FACE_R = 27,
    K_PILL_DX = 62, K_PILL_Y = 22, K_PILL_W = 64, K_PILL_H = 32,
    K_MENU_R = 15,
    K_STICK_X = 81, K_STICK_R = 77, K_CSTICK_R = 60, K_KNOB_R = 30,
    K_STICK_GRAB = 1.4,
    K_FULL = 0.8,
    K_DEAD = 0.1,

    K_LOW_X = 231, K_LOW_Y = 64,
    K_DPAD_R = 48, K_ARM_W = 17, K_ARM_IN = 11,
    K_DPAD_GRAB = 1.3,
    K_CLICK_X = 102, K_CLICK_R = 17;
static const int k_btns[] = {B_ZL, B_L, B_ZR, B_R, B_MINUS, B_PLUS};
static NSString *const k_layout_key = @"DmPadLayout";
static int dm_opmode(void) { return 0; }


@interface DmGamepad : UIView
- (void)arrange:(DmLayout)layout;
- (BOOL)menuTakes:(CGPoint)p;
- (NSDictionary *)diagnostics;
@end


@interface DmMenuButton : UIButton
@end
@implementation DmMenuButton
- (BOOL)pointInside:(CGPoint)p withEvent:(UIEvent *)e { DmGamepad *pad = (DmGamepad *)self.superview; return [pad menuTakes:[self convertPoint:p toView:pad]]; }
@end
static __weak DmGamepad *g_view;
static void pads_start(void);

@implementation DmGamepad {
    NSMapTable<UITouch *, NSNumber *> *_owner;
    UITouch *_stick[2]; CGPoint _stickNow[2];
    CGPoint _rCentre;
    uint64_t _faceBits, _dpadBits, _btnBits;
    NSMapTable<UITouch *, NSNumber *> *_faceOf;
    CAShapeLayer *_key[B_N]; CATextLayer *_label[B_N];
    CAShapeLayer *_ring, *_knob, *_cRing, *_cKnob;
    DmMenuButton *_menu;
    UIImpactFeedbackGenerator *_touchFeedback;
    unsigned _hapticSticks;
    BOOL _stickShown;
    BOOL _full, _tap;
    CALayer *_controls;
    BOOL _padOff;
    DmLayout _layout;
    UITouch *_game[16];
}

static UIColor *k_idle, *k_on, *k_line, *k_ringFill, *k_text, *k_textOn;
static CAShapeLayer *shape(CALayer *parent) {
    CAShapeLayer *l = [CAShapeLayer layer];
    l.fillColor = k_idle.CGColor; l.strokeColor = k_line.CGColor; l.lineWidth = 1.5;
    l.shadowColor = UIColor.blackColor.CGColor; l.shadowOpacity = 0.2; l.shadowRadius = 3; l.shadowOffset = CGSizeMake(0, 1);
    [parent addSublayer:l]; return l;
}
static CATextLayer *label(CALayer *parent, NSString *s) {
    CATextLayer *t = [CATextLayer layer];
    t.string = s; t.alignmentMode = kCAAlignmentCenter; t.foregroundColor = k_text.CGColor;
    [parent addSublayer:t]; return t;
}
static BOOL fullOnly(int b) { return b == B_LS || b == B_RS || b >= B_LEFT; }

- (instancetype)initWithFrame:(CGRect)f {
    if ((self = [super initWithFrame:f])) {
        _layout = (DmLayout){CGRectZero, self.bounds, YES, NO};
        [self registerForTraitChanges:@[UITraitDisplayScale.class] withHandler:^(id<UITraitEnvironment> environment, UITraitCollection *previous) {
            [(UIView *)environment setNeedsLayout];
        }];
        self.multipleTouchEnabled = YES; self.opaque = NO; self.backgroundColor = UIColor.clearColor;
        const char *feedback = getenv("DM_TOUCH_HAPTICS");
        if (!feedback || atoi(feedback)) {
            _touchFeedback = [[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleLight];
            [_touchFeedback prepare];
        }
        _owner = [NSMapTable strongToStrongObjectsMapTable]; _faceOf = [NSMapTable strongToStrongObjectsMapTable];
        static dispatch_once_t once;
        dispatch_once(&once, ^{

            k_idle = [UIColor colorWithWhite:1 alpha:0.14]; k_on = [UIColor colorWithWhite:1 alpha:0.45]; k_line = [UIColor colorWithWhite:1 alpha:0.38];
            k_ringFill = [UIColor colorWithWhite:1 alpha:0.06]; k_text = [UIColor colorWithWhite:1 alpha:0.6]; k_textOn = [UIColor colorWithWhite:0 alpha:0.7];
        });
        _controls = [CALayer layer]; [self.layer addSublayer:_controls];
        _ring = shape(_controls); _ring.fillColor = k_ringFill.CGColor;
        _knob = shape(_controls);
        _cRing = shape(_controls); _cKnob = shape(_controls);
        CAShapeLayer *in = [CAShapeLayer layer]; in.fillColor = nil; in.strokeColor = k_line.CGColor; in.lineWidth = 1.5;
        [_knob addSublayer:in];
        static NSString *const names[B_N] = {@"A", @"B", @"X", @"Y", nil, nil, @"L", @"R", @"ZL", @"ZR", @"Start", @"Select"};
        for (int b = 0; b < B_N; b++) {
            _key[b] = shape(_controls);
            if (names[b]) _label[b] = label(_key[b], names[b]);
        }
        _menu = [DmMenuButton buttonWithType:UIButtonTypeCustom];
        _menu.backgroundColor = k_idle; _menu.layer.borderColor = k_line.CGColor; _menu.layer.borderWidth = 1.5;
        _menu.layer.shadowColor = UIColor.blackColor.CGColor; _menu.layer.shadowOpacity = 0.2; _menu.layer.shadowRadius = 3; _menu.layer.shadowOffset = CGSizeMake(0, 1);
        _menu.tintColor = k_text; _menu.accessibilityLabel = @"Menu";
        __weak DmGamepad *weakSelf = self;
        _menu.menu = [UIMenu menuWithChildren:@[[UIDeferredMenuElement elementWithUncachedProvider:^(void (^completion)(NSArray<UIMenuElement *> *)) {
            DmGamepad *pad = weakSelf;
            completion(pad ? [pad gameMenu].children : @[]);
        }]]];
        _menu.showsMenuAsPrimaryAction = YES;
        [_menu addAction:[UIAction actionWithHandler:^(UIAction *a) { (void)a; [weakSelf reset]; }] forControlEvents:UIControlEventMenuActionTriggered];
        [self addSubview:_menu];
        NSNumber *saved = [NSUserDefaults.standardUserDefaults objectForKey:k_layout_key];
        int l = saved ? (int)saved.integerValue : 1;
        if (l == 2 && dm_opmode() != 0) l = 0;
        _full = l == 1; _tap = l == 2;
        [self applyLayout];
        NSNotificationCenter *nc = NSNotificationCenter.defaultCenter;
        [nc addObserver:self selector:@selector(padsChanged:) name:GCControllerDidConnectNotification object:nil];
        [nc addObserver:self selector:@selector(padsChanged:) name:GCControllerDidDisconnectNotification object:nil];
        pads_start();
        [self padsChanged:nil];

    }
    return self;
}
- (void)applyLayout {
    [CATransaction begin]; [CATransaction setDisableActions:YES];
    for (int b = 0; b < B_N; b++) _key[b].hidden = _tap || b == B_LS || b == B_RS || (fullOnly(b) && !_full);
    _ring.hidden = _knob.hidden = _tap;
    _cRing.hidden = _cKnob.hidden = _tap || !_full;
    [CATransaction commit];
}
- (int)padLayout { return _tap ? 2 : _full ? 1 : 0; }
- (void)setPadLayout:(int)l {
    if (l == 2 && dm_opmode() != 0) l = 0;
    [self reset];
    _full = l == 1; _tap = l == 2;
    [NSUserDefaults.standardUserDefaults setObject:@(l) forKey:k_layout_key];
    [self applyLayout]; [self setNeedsLayout];
}
- (void)opmodeChanged {
    if (!_tap || dm_opmode() == 0) return;
    [self reset]; _tap = NO; _full = NO; [self applyLayout]; [self setNeedsLayout];
}
- (void)setTouchFeedback:(BOOL)on {
    if (!on) { _touchFeedback = nil; return; }
    if (!_touchFeedback) { _touchFeedback = [[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleLight]; [_touchFeedback prepare]; }
}


- (void)arrange:(DmLayout)layout {
    if (CGRectEqualToRect(_layout.controls, layout.controls) && CGRectEqualToRect(_layout.game, layout.game) && _layout.separated == layout.separated && _layout.topSafeOnly == layout.topSafeOnly) return;
    [self reset];
    _layout = layout;
    [self setNeedsLayout];
}
- (CGRect)safe { return CGRectIntersection(_layout.controls, UIEdgeInsetsInsetRect(self.bounds, dm_game_safe_insets(self.bounds, self.safeAreaInsets, _layout.topSafeOnly))); }
- (CGRect)corner { if (_layout.separated) return self.safe; UIEdgeInsets i = self.safeAreaInsets; i.left *= K_CORNER_IN; i.right *= K_CORNER_IN; return UIEdgeInsetsInsetRect(self.bounds, i); }
- (CGFloat)u { if (_layout.separated) { CGRect s = self.safe; return 1.25 * MIN(MIN(s.size.height / 430.0, s.size.width / 700.0), 1.3); } return 1.25 * MIN(self.bounds.size.height / 430.0, 1.3); }
static CGPoint side(CGRect s, int right, CGFloat dx, CGFloat y) { return CGPointMake(right ? CGRectGetMaxX(s) - dx : CGRectGetMinX(s) + dx, y); }
- (BOOL)portraitControls { return _layout.separated && self.bounds.size.height > self.bounds.size.width; }
- (CGPoint)portraitThumbCenter:(BOOL)right {
    CGRect s = self.safe;
    return CGPointMake(CGRectGetMinX(s) + s.size.width * (right ? 0.72 : 0.28),
                       CGRectGetMinY(s) + s.size.height * 0.58);
}
- (CGPoint)anchor:(int)group margin:(CGFloat)margin {
    CGRect b = self.safe;
    N3DSPoint point = n3ds_control_anchor((N3DSRect){b.origin.x,b.origin.y,b.size.width,b.size.height}, group);
    return CGPointMake(fmin(fmax(point.x, CGRectGetMinX(b)+margin), CGRectGetMaxX(b)-margin),
                       fmin(fmax(point.y, CGRectGetMinY(b)+44+margin), CGRectGetMaxY(b)-margin));
}
- (CGPoint)faceCenter { return [self anchor:2 margin:self.faceGap+self.faceHitR]; }
- (CGFloat)faceGap { return MAX(32.5, K_FACE_GAP * self.u); }
- (CGFloat)faceR { return MAX(21.25, K_FACE_R * self.u); }
- (CGFloat)faceHitR { return MAX(self.faceR * 1.25, _layout.separated ? 22 : 0); }
- (CGPoint)facePos:(int)b { CGPoint c = self.faceCenter; CGFloat g = self.faceGap;
    switch (b) { case B_A: return CGPointMake(c.x + g, c.y); case B_B: return CGPointMake(c.x, c.y + g); case B_X: return CGPointMake(c.x, c.y - g); default: return CGPointMake(c.x - g, c.y); } }
- (CGPoint)stickHome { return [self anchor:0 margin:[self stickR:0]+6]; }
- (CGPoint)cstickHome { return [self anchor:3 margin:[self stickR:1]+6]; }
- (CGPoint)stickCentre:(int)i { return i ? self.cstickHome : self.stickHome; }
- (CGFloat)stickR:(int)i { return i ? MAX(27.5, K_CSTICK_R * self.u) : MAX(40, K_STICK_R * self.u); }
- (BOOL)inRightArea:(CGPoint)p { return hypot(p.x - self.cstickHome.x, p.y - self.cstickHome.y) <= MAX([self stickR:1], 22); }
- (CGPoint)dpadCenter { return [self anchor:1 margin:self.dpadR+6]; }
- (CGFloat)dpadR { return MAX(27.5, K_DPAD_R * self.u); }
static BOOL isDrop(int b) { return b == B_ZL || b == B_L || b == B_ZR || b == B_R; }
static BOOL isClick(int b) { return b == B_LS || b == B_RS; }
- (CGPoint)dropCenter:(int)b { CGRect s = self.safe; CGFloat u = self.u; BOOL z = b == B_ZL || b == B_ZR;
    if (_layout.separated) return side(s, b == B_ZR || b == B_R, z ? 82 : 30, CGRectGetMinY(s) + 24);
    CGFloat edge = _layout.separated ? 22 : 0;
    return side(s, b == B_ZR || b == B_R, MAX((z ? K_ZL_X : K_L_X) * u, edge), CGRectGetMinY(s) + MAX((z ? K_ZL_Y : K_L_Y) * u, edge)); }
- (CGFloat)dropR:(int)b { return MAX(17, (b == B_ZL || b == B_ZR ? K_ZL_R : K_L_R) * self.u); }
- (CGPoint)clickCenter:(int)b { CGRect s = self.safe; CGFloat u = self.u; CGFloat edge = _layout.separated ? 22 : 0;
    return side(s, b == B_RS, MAX(K_CLICK_X * u, edge), CGRectGetMinY(s) + MAX(K_ZL_Y * u, edge)); }
- (CGRect)pillRect:(int)b { CGRect s = self.safe; CGFloat u = self.u, w = MAX(K_PILL_W * u, 54), h = MAX(K_PILL_H * u, 28);
    CGPoint c = CGPointMake(CGRectGetMidX(s) + (b == B_PLUS ? K_PILL_DX : -K_PILL_DX) * MAX(u, .85), CGRectGetMinY(s) + MAX(K_PILL_Y * u, 22));
    return CGRectMake(c.x - w / 2, c.y - h / 2, w, h); }
- (CGPoint)menuCenter { CGRect s = self.safe; return CGPointMake(CGRectGetMidX(s), CGRectGetMinY(s) + MAX(K_PILL_Y * self.u, 22)); }

static CGFloat dist(CGPoint a, CGPoint b) { return hypot(a.x - b.x, a.y - b.y); }
- (CGFloat)buttonDistance:(int)b point:(CGPoint)p {
    CGFloat u = self.u;
    if (isDrop(b)) return dist(p, [self dropCenter:b]) / MAX([self dropR:b] * 1.35, _layout.separated ? 22 : 0);
    if (isClick(b)) return dist(p, [self clickCenter:b]) / MAX(K_CLICK_R * u * 1.35, _layout.separated ? 22 : 0);
    CGRect r = CGRectInset([self pillRect:b], -8 * u, -8 * u);
    if (!_layout.separated && !CGRectContainsPoint(r, p)) return CGFLOAT_MAX;
    CGFloat w = MAX(r.size.width, 44), h = MAX(r.size.height, 44);
    return MAX(fabs(p.x - CGRectGetMidX(r)) / (w / 2), fabs(p.y - CGRectGetMidY(r)) / (h / 2));
}
- (int)btnAt:(CGPoint)p {
    int best = -1; CGFloat distance = 1;
    for (int i = 0; i < (int)(sizeof k_btns / sizeof *k_btns); i++) { int b = k_btns[i];
        if (fullOnly(b) && !_full) continue;
        CGFloat d = [self buttonDistance:b point:p];
        if (d <= 1 && (best < 0 || d < distance)) {
            best = b; distance = d;
            if (!_layout.separated) return b;
        }
    }
    return best;
}
- (CGFloat)menuDistance:(CGPoint)p { return dist(p, self.menuCenter) / MAX(K_MENU_R * self.u * 1.6, 22); }
- (BOOL)menuTakes:(CGPoint)p {
    CGFloat d = [self menuDistance:p]; if (d > 1) return NO;
    if (_tap || _padOff || (_layout.separated && !CGRectContainsPoint(_layout.controls, p))) return YES;
    int hit = [self btnAt:p];
    return hit < 0 || d < [self buttonDistance:hit point:p];
}
- (uint64_t)faceAt:(CGPoint)p {
    uint64_t m = 0; CGFloat r = self.faceHitR;
    for (int b = B_A; b <= B_Y; b++) if (dist(p, [self facePos:b]) <= r) m |= 1ull << b;
    return m;
}
- (uint64_t)dpadAt:(CGPoint)p {
    CGPoint c = self.dpadCenter; CGFloat dx = p.x - c.x, dy = p.y - c.y;
    if (hypot(dx, dy) < self.dpadR * 0.25) return 0;
    double a = atan2(-dy, dx) * 180 / M_PI; uint64_t m = 0;
    if (a > -67.5 && a < 67.5) m |= 1ull << B_RIGHT;
    if (a > 22.5 && a < 157.5) m |= 1ull << B_UP;
    if (a > 112.5 || a < -112.5) m |= 1ull << B_LEFT;
    if (a < -22.5 && a > -157.5) m |= 1ull << B_DOWN;
    return m;
}
- (CGPoint)knobOffset:(int)i {
    if (!_stick[i]) return CGPointZero;
    CGPoint h = [self stickCentre:i]; CGFloat dx = _stickNow[i].x - h.x, dy = _stickNow[i].y - h.y, d = hypot(dx, dy), R = [self stickR:i];
    if (d > R) { dx *= R / d; dy *= R / d; }
    return CGPointMake(dx, dy);
}


- (void)recompute {
    uint64_t face = 0; for (UITouch *t in _faceOf) face |= [_faceOf objectForKey:t].unsignedLongLongValue;
    uint64_t btn = 0, dpad = 0;
    for (UITouch *t in _owner) { uint64_t v = [_owner objectForKey:t].unsignedLongLongValue; int c = (int)(v & 0xff);
        if (c == C_BTN) btn |= 1ull << (v >> 8);
        else if (c == C_DPAD) dpad |= [self dpadAt:[t locationInView:self]]; }
    int32_t ax[4] = {0};
    for (int i = 0; i < 2; i++) {
        CGPoint o = [self knobOffset:i]; CGFloat R = [self stickR:i], d = hypot(o.x, o.y), dz = R * K_DEAD;
        if (d <= dz) continue;
        CGFloat m = fmin((d - dz) / (R * K_FULL - dz), 1) / d;
        ax[i * 2] = (int32_t)lrint(o.x * m * 32767); ax[i * 2 + 1] = (int32_t)lrint(-o.y * m * 32767);
    }
    BOOL changed = face != _faceBits || dpad != _dpadBits || btn != _btnBits;
    uint64_t pressed = (face | dpad | btn) & ~(_faceBits | _dpadBits | _btnBits);
    unsigned gripping = (_stick[0] ? 1u : 0) | (_stick[1] ? 2u : 0);
    if (pressed || (gripping & ~_hapticSticks)) {
        [_touchFeedback impactOccurredWithIntensity:0.6]; [_touchFeedback prepare];
    }
    _hapticSticks = gripping;
    _faceBits = face; _dpadBits = dpad; _btnBits = btn;
    n3ds_input(N3DS_INPUT_TOUCH, (uint32_t)(face | dpad | btn), ax[0], ax[1], ax[2], ax[3]);
    BOOL stick = _stick[0] != nil || _stick[1] != nil;
    if (changed || stick || _stickShown) [self refresh];
    _stickShown = stick;
}

- (void)pushGame {
    for (int i = 0; i < 16; i++) if (_game[i]) {
        CGPoint p = dm_game_touch_point(_layout.game, [_game[i] locationInView:self]);
        n3ds_touch(true, p.x, p.y); return;
    }
    n3ds_touch(false, 0, 0);
}
- (BOOL)gameBegin:(UITouch *)t {
    if (CGRectIsEmpty(_layout.game) || !CGRectContainsPoint(_layout.game, [t locationInView:self])) return NO;
    for (int i = 0; i < 16; i++) if (!_game[i]) { _game[i] = t; [_owner setObject:@(C_GAME | (uint64_t)i << 8) forKey:t]; return YES; }
    return NO;
}
- (void)touchesBegan:(NSSet<UITouch *> *)ts withEvent:(UIEvent *)e {
    BOOL game = NO;
    for (UITouch *t in ts) {
        if (_padOff) { game |= [self gameBegin:t]; continue; }
        CGPoint p = [t locationInView:self];
        if (_layout.separated && !CGRectContainsPoint(_layout.controls, p)) { game |= [self gameBegin:t]; continue; }
        int hit = _tap ? -1 : [self btnAt:p];
        if (hit >= 0) { [_owner setObject:@(C_BTN | (uint64_t)hit << 8) forKey:t]; continue; }
        if (_tap) { game |= [self gameBegin:t]; continue; }

        CGFloat best = 1; int kind = C_NONE, idx = 0;
        CGFloat f = dist(p, self.faceCenter) / (self.faceGap + MAX(self.faceR * 1.6, self.faceHitR)); if (f <= best) { best = f; kind = C_FACE; }
        if (_full) { CGFloat d = dist(p, self.dpadCenter) / MAX(self.dpadR * K_DPAD_GRAB, _layout.separated ? 22 : 0); if (d < best) { best = d; kind = C_DPAD; } }
        if (!_stick[0]) { CGFloat s = dist(p, self.stickHome) / MAX([self stickR:0] * K_STICK_GRAB, _layout.separated ? 22 : 0); if (s < best) { best = s; kind = C_STICK; } }
        if (kind == C_NONE && _full && !_stick[1] && [self inRightArea:p]) { kind = C_STICK; idx = 1; _rCentre = p; }
        if (kind == C_FACE) { [_owner setObject:@(C_FACE) forKey:t]; [_faceOf setObject:@([self faceAt:p]) forKey:t]; }
        else if (kind == C_DPAD) [_owner setObject:@(C_DPAD) forKey:t];
        else if (kind == C_STICK) { [_owner setObject:@(C_STICK | (uint64_t)idx << 8) forKey:t]; _stick[idx] = t; _stickNow[idx] = p; }
        else game |= [self gameBegin:t];
    }
    if (game) [self pushGame];
    [self recompute];
}
- (void)touchesMoved:(NSSet<UITouch *> *)ts withEvent:(UIEvent *)e {
    BOOL game = NO;
    for (UITouch *t in ts) {
        uint64_t v = [_owner objectForKey:t].unsignedLongLongValue; int c = (int)(v & 0xff); CGPoint p = [t locationInView:self];
        if (c == C_FACE) [_faceOf setObject:@([self faceAt:p]) forKey:t];
        else if (c == C_STICK) { _stickNow[v >> 8] = p;
        }
        else if (c == C_GAME) game = YES;
    }
    if (game) [self pushGame];
    [self recompute];
}
- (void)endTouches:(NSSet<UITouch *> *)ts tapped:(BOOL)ended {
    BOOL game = NO;
    for (UITouch *t in ts) {
        uint64_t v = [_owner objectForKey:t].unsignedLongLongValue;
        if ((v & 0xff) == C_GAME && (v >> 8) < 16) { _game[v >> 8] = nil; game = YES; }
        [_owner removeObjectForKey:t]; [_faceOf removeObjectForKey:t]; for (int i = 0; i < 2; i++) if (t == _stick[i]) _stick[i] = nil;
    }
    if (game) [self pushGame];
    [self recompute];
}
- (void)touchesEnded:(NSSet<UITouch *> *)ts withEvent:(UIEvent *)e { [self endTouches:ts tapped:YES]; }
- (void)touchesCancelled:(NSSet<UITouch *> *)ts withEvent:(UIEvent *)e { [self endTouches:ts tapped:NO]; }
- (void)reset { [_owner removeAllObjects]; [_faceOf removeAllObjects]; _stick[0] = _stick[1] = nil; for (int i = 0; i < 16; i++) _game[i] = nil; [self pushGame]; [self recompute]; }


static CGPathRef circlePath(CGPoint c, CGFloat r) { return CGPathCreateWithEllipseInRect(CGRectMake(c.x - r, c.y - r, 2 * r, 2 * r), NULL); }
static CGPathRef dropPath(CGPoint c, CGFloat r, CGFloat tipAngle, CGFloat round) {
    CGFloat d = r * K_DROP_TIP, th = acos(r / d);
    CGPoint tip = CGPointMake(c.x + d * cos(tipAngle), c.y + d * sin(tipAngle));
    CGPoint p1 = CGPointMake(c.x + r * cos(tipAngle + th), c.y + r * sin(tipAngle + th)), p2 = CGPointMake(c.x + r * cos(tipAngle - th), c.y + r * sin(tipAngle - th));
    CGMutablePathRef p = CGPathCreateMutable();
    CGPathMoveToPoint(p, NULL, p2.x, p2.y);
    CGPathAddArcToPoint(p, NULL, tip.x, tip.y, p1.x, p1.y, round);
    CGPathAddArc(p, NULL, c.x, c.y, r, tipAngle + th, tipAngle - th + 2 * M_PI, false);
    CGPathCloseSubpath(p); return p;
}
static CGPathRef armPath(CGPoint c, CGFloat R, CGFloat w, CGFloat in, CGFloat angle, CGFloat round) {
    CGPoint v[5] = {{-w, -R}, {w, -R}, {w, -(in + w)}, {0, -in}, {-w, -(in + w)}};
    CGFloat cs = cos(angle), sn = sin(angle);
    for (int i = 0; i < 5; i++) v[i] = CGPointMake(c.x + v[i].x * cs - v[i].y * sn, c.y + v[i].x * sn + v[i].y * cs);
    CGMutablePathRef p = CGPathCreateMutable();
    CGPathMoveToPoint(p, NULL, (v[4].x + v[0].x) / 2, (v[4].y + v[0].y) / 2);
    for (int i = 0; i < 5; i++) CGPathAddArcToPoint(p, NULL, v[i].x, v[i].y, v[(i + 1) % 5].x, v[(i + 1) % 5].y, round);
    CGPathCloseSubpath(p); return p;
}
static void setPath(CAShapeLayer *l, CGPathRef p) { l.path = p; l.shadowPath = p; CGPathRelease(p); }
static void placeLabel(CATextLayer *t, CGPoint c, CGFloat fs) {
    CTFontRef f = CTFontCreateUIFontForLanguage(kCTFontUIFontEmphasizedSystem, fs, NULL);
    t.font = f; t.fontSize = fs;
    CGFloat h = CTFontGetAscent(f) + CTFontGetDescent(f) + CTFontGetLeading(f), w = MAX(64, fs * 3);
    t.frame = CGRectMake(c.x - w / 2, c.y - h / 2, w, h);
    CFRelease(f);
}

- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat u = self.u;
    [CATransaction begin]; [CATransaction setDisableActions:YES];
    CGFloat scale = MAX(self.traitCollection.displayScale, 1);
    for (int b = 0; b < B_N; b++) if (_label[b] && _label[b].contentsScale != scale) {
        _label[b].contentsScale = scale; [_label[b] setNeedsDisplay];
    }
    _controls.frame = self.layer.bounds;
    for (int b = B_A; b <= B_Y; b++) { CGPoint c = [self facePos:b]; setPath(_key[b], circlePath(c, self.faceR)); placeLabel(_label[b], c, MAX(14, 20 * u)); }
    for (int i = 0; i < (int)(sizeof k_btns / sizeof *k_btns); i++) { int b = k_btns[i];
        if (isDrop(b)) {
            BOOL right = b == B_ZR || b == B_R; CGPoint c = [self dropCenter:b];
            setPath(_key[b], dropPath(c, [self dropR:b], right ? -M_PI_4 : -3 * M_PI_4, 4 * u)); placeLabel(_label[b], c, MAX(12, 16 * u));
        } else if (isClick(b)) {
            CGPoint c = [self clickCenter:b]; setPath(_key[b], circlePath(c, K_CLICK_R * u)); placeLabel(_label[b], c, 13 * u);
        } else {
            CGRect rc = [self pillRect:b];
            setPath(_key[b], CGPathCreateWithRoundedRect(rc, rc.size.height / 2, rc.size.height / 2, NULL)); placeLabel(_label[b], CGPointMake(CGRectGetMidX(rc), CGRectGetMidY(rc)), MAX(13, 18 * u));
        }
    }
    static const struct { int b; CGFloat angle; } arms[] = {{B_UP, 0}, {B_RIGHT, M_PI_2}, {B_DOWN, M_PI}, {B_LEFT, -M_PI_2}};
    for (int i = 0; i < 4; i++) setPath(_key[arms[i].b], armPath(self.dpadCenter, self.dpadR, K_ARM_W * u, K_ARM_IN * u, arms[i].angle, 6 * u));
    CGFloat mr = MAX(K_MENU_R * u, 22); CGPoint mc = self.menuCenter;
    _menu.frame = CGRectMake(mc.x - mr, mc.y - mr, 2 * mr, 2 * mr); _menu.layer.cornerRadius = mr;
    _menu.layer.shadowPath = [UIBezierPath bezierPathWithOvalInRect:_menu.bounds].CGPath;
    [_menu setImage:[UIImage systemImageNamed:@"gearshape.fill" withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:MAX(14, 13 * u) weight:UIImageSymbolWeightSemibold]] forState:UIControlStateNormal];
    setPath(_ring, circlePath(self.stickHome, [self stickR:0]));
    setPath(_cRing, circlePath(self.cstickHome, [self stickR:1]));
    CGFloat k = K_KNOB_R * u; _knob.bounds = CGRectMake(0, 0, 2 * k, 2 * k);
    setPath(_knob, circlePath(CGPointMake(k, k), k));
    CAShapeLayer *in = (CAShapeLayer *)_knob.sublayers.firstObject; CGPathRef ip = circlePath(CGPointMake(k, k), k * 0.62);
    in.frame = _knob.bounds; in.path = ip; CGPathRelease(ip);
    [CATransaction commit];
    [self refresh];
}
- (void)safeAreaInsetsDidChange { [super safeAreaInsetsDidChange]; [self setNeedsLayout]; }
- (void)refresh {
    uint64_t all = _faceBits | _dpadBits | _btnBits;
    [CATransaction begin]; [CATransaction setDisableActions:YES];
    for (int b = 0; b < B_N; b++) { BOOL on = (all >> b) & 1;
        _key[b].fillColor = (on ? k_on : k_idle).CGColor; _label[b].foregroundColor = (on ? k_textOn : k_text).CGColor; }
    CGPoint h = self.stickHome, o = [self knobOffset:0];
    CGPoint ch = self.cstickHome, co = [self knobOffset:1];
    setPath(_cKnob, circlePath(CGPointMake(ch.x + co.x, ch.y + co.y), [self stickR:1] * (9.0/22.0)));
    _knob.position = CGPointMake(h.x + o.x, h.y + o.y); _knob.fillColor = (_stick[0] ? k_on : k_idle).CGColor;
    [CATransaction commit];
}


static GCController *held[8];
static int32_t axis(float v) { return (int32_t)(fmaxf(-1.f, fminf(1.f, v)) * 32767); }
static void read_pad(GCExtendedGamepad *g) {
    unsigned p = 0; while (p < 8 && held[p] != g.controller) p++; if (p == 8) return;
    NSString *category = g.controller.productCategory;
    BOOL nintendo = [category isEqualToString:@"Switch Pro Controller"] || [category containsString:@"Joy-Con"];
    uint32_t m = 0;
    if ((nintendo ? g.buttonA : g.buttonB).isPressed) m |= N3DS_A;
    if ((nintendo ? g.buttonB : g.buttonA).isPressed) m |= N3DS_B;
    if ((nintendo ? g.buttonX : g.buttonY).isPressed) m |= N3DS_X;
    if ((nintendo ? g.buttonY : g.buttonX).isPressed) m |= N3DS_Y;
    #define K(btn, bit) if (btn.isPressed) m |= bit
    K(g.leftShoulder, N3DS_L); K(g.rightShoulder, N3DS_R);
    K(g.leftTrigger, N3DS_ZL); K(g.rightTrigger, N3DS_ZR);
    K(g.buttonMenu, N3DS_START); K(g.buttonOptions, N3DS_SELECT);
    K(g.dpad.left, N3DS_LEFT); K(g.dpad.right, N3DS_RIGHT);
    K(g.dpad.up, N3DS_UP); K(g.dpad.down, N3DS_DOWN);
    #undef K
    n3ds_input(N3DS_INPUT_CONTROLLER_BASE + p, m, axis(g.leftThumbstick.xAxis.value), axis(g.leftThumbstick.yAxis.value),
               axis(g.rightThumbstick.xAxis.value), axis(g.rightThumbstick.yAxis.value));
}
static void pads_start(void) {}
- (void)padsChanged:(NSNotification *)note {
    NSArray *now = GCController.controllers;
    GCController *gone = [note.name isEqual:GCControllerDidDisconnectNotification] ? note.object : nil;
    for (unsigned p = 0; p < 8; p++) if (held[p] && (held[p] == gone || ![now containsObject:held[p]])) {
        held[p].extendedGamepad.valueChangedHandler = nil; held[p] = nil;
        n3ds_input(N3DS_INPUT_CONTROLLER_BASE + p, 0, 0, 0, 0, 0);
    }
    for (GCController *c in now) if (c != gone && c.extendedGamepad) {
        unsigned p = 0; while (p < 8 && held[p] != c) p++;
        if (p == 8) { p = 0; while (p < 8 && held[p]) p++; }
        if (p == 8) continue;
        held[p] = c;
        c.extendedGamepad.valueChangedHandler = ^(GCExtendedGamepad *g, GCControllerElement *element) { read_pad(g); };
        read_pad(c.extendedGamepad);
    }
    BOOL any = NO; for (unsigned p = 0; p < 8; p++) any |= held[p] != nil;
#if TARGET_OS_SIMULATOR
    NSLog(@"3dsnative controller state: connected=%lu, hiding touch controls=%d", (unsigned long)now.count, any);
#endif
    BOOL hide = any && [NSUserDefaults.standardUserDefaults boolForKey:@"HideTouchWithController"];
    dispatch_async(dispatch_get_main_queue(), ^{ self->_padOff = hide; self->_controls.hidden = hide; [self reset]; });
}
- (UIMenu *)gameMenu {
    __weak DmGamepad *weak = self;
    NSMutableArray *scales = [NSMutableArray new];
    for (unsigned scale = 1; scale <= 3; scale++) {
        UIAction *action = [UIAction actionWithTitle:[NSString stringWithFormat:@"%u× resolution", scale] image:nil identifier:nil handler:^(UIAction *a) {
            n3ds_resolution(scale); [NSUserDefaults.standardUserDefaults setInteger:scale forKey:@"ResolutionScale"];
        }];
        action.state = MAX(1, [NSUserDefaults.standardUserDefaults integerForKey:@"ResolutionScale"]) == scale ? UIMenuElementStateOn : UIMenuElementStateOff;
        [scales addObject:action];
    }
    UIAction *controls = [UIAction actionWithTitle:@"Show or hide touch controls" image:[UIImage systemImageNamed:@"gamecontroller"] identifier:nil handler:^(UIAction *a) {
        DmGamepad *pad = weak; if (pad) [pad setPadLayout:pad->_tap ? 1 : 2];
    }];
    UIAction *automatic = [UIAction actionWithTitle:@"Hide controls with a controller" image:nil identifier:nil handler:^(UIAction *a) {
        NSUserDefaults *settings = NSUserDefaults.standardUserDefaults;
        [settings setBool:![settings boolForKey:@"HideTouchWithController"] forKey:@"HideTouchWithController"];
        [weak padsChanged:nil];
    }];
    automatic.state = [NSUserDefaults.standardUserDefaults boolForKey:@"HideTouchWithController"] ? UIMenuElementStateOn : UIMenuElementStateOff;
    UIAction *fps = [UIAction actionWithTitle:@"Show FPS" image:nil identifier:nil handler:^(UIAction *a) {
        NSUserDefaults *settings = NSUserDefaults.standardUserDefaults;
        [settings setBool:![settings boolForKey:@"ShowFPS"] forKey:@"ShowFPS"];
        [NSNotificationCenter.defaultCenter postNotificationName:@"N3DSSettingsChanged" object:nil];
    }];
    fps.state = [NSUserDefaults.standardUserDefaults boolForKey:@"ShowFPS"] ? UIMenuElementStateOn : UIMenuElementStateOff;
    NSMutableArray *items = [NSMutableArray arrayWithArray:@[[UIMenu menuWithTitle:@"Resolution" children:scales], controls, automatic, fps]];
    if ([NSUserDefaults.standardUserDefaults boolForKey:@"LandscapeOptionsAvailable"]) {
        NSMutableArray *layouts = [NSMutableArray new];
        NSArray *names = @[@"50/50 split", @"Top screen only", @"Bottom screen only", @"Large/small vertical"];
        for (NSUInteger i = 0; i < names.count; i++) {
            UIAction *choice = [UIAction actionWithTitle:names[i] image:nil identifier:nil handler:^(UIAction *action) {
                [NSUserDefaults.standardUserDefaults setInteger:i forKey:@"LandscapeDisplay"];
                [NSNotificationCenter.defaultCenter postNotificationName:@"N3DSSettingsChanged" object:nil];
            }];
            choice.state = [NSUserDefaults.standardUserDefaults integerForKey:@"LandscapeDisplay"] == i ? UIMenuElementStateOn : UIMenuElementStateOff;
            [layouts addObject:choice];
        }
        [items insertObject:[UIMenu menuWithTitle:@"Landscape display" children:layouts] atIndex:0];
    }
    return [UIMenu menuWithChildren:items];
}
- (NSDictionary *)diagnostics {
    return @{@"physical_controller": @(_padOff), @"touch_only": @(_tap),
             @"controls_hidden": @(_controls.hidden), @"controls": NSStringFromCGRect(_layout.controls),
             @"bounds": NSStringFromCGRect(self.bounds), @"face_path": @(_key[B_A].path != NULL)};
}
@end
UIView *dm_gamepad_view(CGRect frame) { DmGamepad *v = [[DmGamepad alloc] initWithFrame:frame]; g_view = v; return v; }
void dm_gamepad_arrange(UIView *view, DmLayout layout) { [(DmGamepad *)view arrange:layout]; }
void dm_gamepad_reset(void) { [g_view reset]; }
#if TARGET_OS_SIMULATOR
NSDictionary *dm_gamepad_diagnostics(void) { return [g_view diagnostics] ?: @{}; }
#endif
