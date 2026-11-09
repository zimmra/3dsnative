// Dual-screen adaptation of nxnative's CAMetalLayer front end. Game images stay
// in Metal textures; UIKit supplies layout, touch and keyboard input.
#import <UIKit/UIKit.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#include <TargetConditionals.h>
#include <math.h>
#include <stdlib.h>
#include "runtime.h"
#if defined(N3DS_REQUIRE_NATIVE_DUO) && !__has_include(<UIKit/UIHingeInteraction.h>)
#error Native Duo builds require the iOS 27.1 hinge SDK
#endif
#import "DmLayout.h"
#import "N3DSGeometry.h"
#import <AVFAudio/AVFAudio.h>
void n3ds_audio_session(void);

@interface N3DSMetalLayer : CAMetalLayer
@property(atomic) BOOL presented;
@property(nonatomic, copy) dispatch_block_t firstFrame;
@end
@implementation N3DSMetalLayer
- (id<CAMetalDrawable>)nextDrawable {
    id<CAMetalDrawable> drawable = [super nextDrawable];
    if (drawable && !self.presented) {
        __weak N3DSMetalLayer *weak = self;
#if TARGET_OS_SIMULATOR
        // Simulator Metal omits drawable presentation callbacks. This only
        // dismisses the loading text; screenshots verify the rendered frame.
        self.presented = YES;
        dispatch_async(dispatch_get_main_queue(), ^{ if (weak.firstFrame) weak.firstFrame(); });
#else
        [drawable addPresentedHandler:^(id<MTLDrawable> frame) {
            N3DSMetalLayer *layer = weak;
            if (!layer || layer.presented) return;
            layer.presented = YES;
            dispatch_async(dispatch_get_main_queue(), ^{ if (layer.firstFrame) layer.firstFrame(); });
        }];
#endif
    }
    return drawable;
}
@end

@interface DmGameView : UIView
@property(nonatomic, copy) dispatch_block_t geometryChanged;
@end
@implementation DmGameView
+ (Class)layerClass { return N3DSMetalLayer.class; }
- (instancetype)initWithFrame:(CGRect)frame {
    if ((self = [super initWithFrame:frame])) {
        [self registerForTraitChanges:@[UITraitDisplayScale.class] withHandler:^(id<UITraitEnvironment> environment, UITraitCollection *previous) {
            [(UIView *)environment setNeedsLayout];
        }];
        CAMetalLayer *layer = (CAMetalLayer *)self.layer;
        layer.device = MTLCreateSystemDefaultDevice();
        layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
        layer.framebufferOnly = NO; layer.opaque = YES;
        self.backgroundColor = UIColor.blackColor;
    }
    return self;
}
- (void)didMoveToWindow { [super didMoveToWindow]; [self setNeedsLayout]; }
- (void)layoutSubviews {
    [super layoutSubviews];
    CAMetalLayer *layer = (CAMetalLayer *)self.layer;
    UIScreen *screen = self.window.windowScene.screen;
    CGFloat scale = screen ? screen.nativeScale : MAX(self.traitCollection.displayScale, 1);
    CGSize pixels = CGSizeMake(round(self.bounds.size.width * scale), round(self.bounds.size.height * scale));
    BOOL changed = !CGSizeEqualToSize(layer.drawableSize, pixels) || layer.contentsScale != scale;
    layer.contentsScale = scale; layer.drawableSize = pixels;
    if (changed && self.geometryChanged) self.geometryChanged();
}
@end

@interface N3DSRootView : UIView
@end
@implementation N3DSRootView
+ (BOOL)requiresConstraintBasedLayout { return YES; }
- (void)updateConstraints {
#if __has_include(<UIKit/UIHingeInteraction.h>)
    if (@available(iOS 27.1, *)) {
        [self reservedRegionsOfKind:UIViewReservedRegionKind.divisionRegionKind options:UIViewReservedRegionQueryOptionsIncludeInactive];
        [self setNeedsLayout];
    }
#endif
    [super updateConstraints];
}
@end

@interface N3DSRootVC : UIViewController
@property(nonatomic, strong) DmGameView *top;
@property(nonatomic, strong) DmGameView *bottom;
@property(nonatomic, strong) UILabel *status;
@property(nonatomic, strong) UIButton *flip;
@property(nonatomic) BOOL partiallyOpen;
@property(nonatomic, copy) NSString *layoutLog;
@property(nonatomic, strong) UILabel *fps;
@property(nonatomic, strong) UIView *gamepad;
@property(nonatomic, strong) NSDictionary *manifest;
@property(nonatomic, strong) NSString *resources;
@property(nonatomic) BOOL started;
@property(nonatomic) BOOL geometryQueued;
@property(nonatomic) uint32_t keys;
@property(nonatomic) uint32_t automated;
@property(nonatomic, strong) UITouch *screenTouch;
- (BOOL)inputURL:(NSURL *)url;
@end

static void runtimeStatus(void *context, enum n3ds_stage stage, uint64_t done, uint64_t total, const char *message) {
    N3DSRootVC *controller = (__bridge N3DSRootVC *)context;
    NSString *text = message ? [NSString stringWithUTF8String:message] : @"";
    dispatch_async(dispatch_get_main_queue(), ^{
        if (stage == N3DS_FAILED) controller.status.text = @"Could not start the game.\nShare Documents/log.txt for diagnostics.";
        else if (stage != N3DS_RUNNING) controller.status.text = text;
    });
}

@implementation N3DSRootVC
- (void)loadView { self.view = [N3DSRootView new]; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskAll; }
- (BOOL)prefersHomeIndicatorAutoHidden { return YES; }
- (UIRectEdge)preferredScreenEdgesDeferringSystemGestures { return UIRectEdgeAll; }
- (void)settingsChanged:(NSNotification *)notification { [self.view setNeedsLayout]; }
#if __has_include(<UIKit/UIHingeInteraction.h>)
- (void)hingeChanged:(UIHinge *)hinge API_AVAILABLE(ios(27.1)) {
    self.partiallyOpen = hinge.status == UIHingeStatusPartiallyOpen;
    [self.view setNeedsUpdateConstraints]; [self.view setNeedsLayout];
}
#endif
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.blackColor;
#if __has_include(<UIKit/UIHingeInteraction.h>)
    if (@available(iOS 27.1, *)) {
        __weak N3DSRootVC *weak = self;
        [self.view addInteraction:[[UIHingeInteraction alloc] initWithUpdateHandler:^(UIHingeInteraction *interaction, UIHingeInteractionUpdate *update) {
            [weak hingeChanged:update.hinge];
        }]];
    }
#endif
    self.top = [DmGameView new]; self.bottom = [DmGameView new];
    [self.view addSubview:self.top]; [self.view addSubview:self.bottom];
    self.gamepad = dm_gamepad_view(self.view.bounds);
    [self.view addSubview:self.gamepad];
    self.flip = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.flip setImage:[UIImage systemImageNamed:@"arrow.up.arrow.down"] forState:UIControlStateNormal];
    self.flip.tintColor = UIColor.whiteColor; self.flip.accessibilityLabel = @"Flip large and small screens";
    [self.flip addAction:[UIAction actionWithHandler:^(UIAction *action) {
        NSUserDefaults *settings = NSUserDefaults.standardUserDefaults;
        [settings setBool:![settings boolForKey:@"FlipLargeScreen"] forKey:@"FlipLargeScreen"];
        [NSNotificationCenter.defaultCenter postNotificationName:@"N3DSSettingsChanged" object:nil];
    }] forControlEvents:UIControlEventTouchUpInside];
    [self.view addSubview:self.flip];
    self.bottom.multipleTouchEnabled = YES;
    self.status = [UILabel new]; self.status.numberOfLines = 0;
    self.status.textColor = UIColor.whiteColor; self.status.textAlignment = NSTextAlignmentCenter;
    self.status.font = [UIFont preferredFontForTextStyle:UIFontTextStyleBody];
    self.status.adjustsFontForContentSizeCategory = YES;
    self.status.text = @"Starting game";
    [self.view addSubview:self.status];
    self.fps = [UILabel new]; self.fps.textColor = UIColor.whiteColor;
    self.fps.font = [UIFont monospacedDigitSystemFontOfSize:12 weight:UIFontWeightMedium];
    self.fps.backgroundColor = [UIColor colorWithWhite:0 alpha:.7];
    [self.view addSubview:self.fps];
    __weak N3DSRootVC *weak = self;
    [NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *timer) {
        N3DSRootVC *controller = weak; if (!controller) { [timer invalidate]; return; }
        controller.fps.hidden = ![NSUserDefaults.standardUserDefaults boolForKey:@"ShowFPS"];
        n3ds_perf stats = n3ds_get_perf();
        controller.fps.text = [NSString stringWithFormat:@" %.1f FPS · %.0f%% ", stats.game_fps, stats.emulation_speed * 100];
    }];
    ((N3DSMetalLayer *)self.top.layer).firstFrame = ^{ weak.status.hidden = YES; };
    self.top.geometryChanged = self.bottom.geometryChanged = ^{
        N3DSRootVC *controller = weak;
        if (!controller || controller.geometryQueued) return;
        controller.geometryQueued = YES;
        dispatch_async(dispatch_get_main_queue(), ^{ controller.geometryQueued = NO; [controller publishGeometry]; });
    };
    self.resources = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"Game"];
    NSData *data = [NSData dataWithContentsOfFile:[self.resources stringByAppendingPathComponent:@"game.json"]];
    self.manifest = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (!self.manifest) self.status.text = @"Game files are missing.\nRebuild the IPA with your backup.";
    NSNotificationCenter *notifications = NSNotificationCenter.defaultCenter;
    [notifications addObserver:self selector:@selector(settingsChanged:) name:@"N3DSSettingsChanged" object:nil];
    [notifications addObserverForName:AVAudioSessionInterruptionNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *notification) {
        BOOL interrupted = [notification.userInfo[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue] == AVAudioSessionInterruptionTypeBegan;
        if (!interrupted) n3ds_audio_session();
        n3ds_pause(interrupted);
    }];
    [notifications addObserver:self selector:@selector(background:) name:UIApplicationWillResignActiveNotification object:nil];
    [notifications addObserver:self selector:@selector(foreground:) name:UIApplicationDidBecomeActiveNotification object:nil];
#if TARGET_OS_SIMULATOR
    // Explicit local test interface; entirely absent from device builds.
    // File commands avoid the Simulator's external-URL confirmation dialog.
    if (getenv("N3DS_AUTOMATION")) {
        NSString *documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
        NSString *input = [documents stringByAppendingPathComponent:@"simulator-input.json"];
        [NSFileManager.defaultManager removeItemAtPath:input error:nil];
        __block NSString *previous;
        [NSTimer scheduledTimerWithTimeInterval:.05 repeats:YES block:^(NSTimer *timer) {
            N3DSRootVC *controller = weak; if (!controller) { [timer invalidate]; return; }
            NSData *data = [NSData dataWithContentsOfFile:input];
            NSDictionary *command = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
            NSString *sequence = command[@"sequence"];
            if (![sequence isKindOfClass:NSString.class] || [sequence isEqual:previous]) return;
            previous = sequence;
            if (command[@"display"]) {
                [NSUserDefaults.standardUserDefaults setInteger:[command[@"display"] integerValue] forKey:@"LandscapeDisplay"];
                [controller.view setNeedsLayout];
            }
            if (command[@"orientation"]) {
                BOOL landscape=[command[@"orientation"] isEqual:@"landscape"];
                UIWindowSceneGeometryPreferencesIOS *preferences=[[UIWindowSceneGeometryPreferencesIOS alloc] initWithInterfaceOrientations:landscape?UIInterfaceOrientationMaskLandscapeLeft:UIInterfaceOrientationMaskPortrait];
                [controller.view.window.windowScene requestGeometryUpdateWithPreferences:preferences errorHandler:^(NSError *error) { NSLog(@"Simulator rotation: %@",error); }];
            }
            if (command[@"speed"]) n3ds_set_speed([command[@"speed"] unsignedIntValue]);
            n3ds_input(N3DS_INPUT_AUTOMATION, [command[@"buttons"] unsignedIntValue], 0, 0, 0, 0);
            if (command[@"touch"]) {
                NSArray *touch = command[@"touch"];
                if (touch.count == 3) n3ds_touch([touch[0] boolValue], [touch[1] floatValue], [touch[2] floatValue]);
            }
            NSDictionary *state = @{@"sequence":sequence, @"gamepad":dm_gamepad_diagnostics(),
                                    @"top":NSStringFromCGRect(controller.top.frame), @"bottom":NSStringFromCGRect(controller.bottom.frame)};
            NSData *json = [NSJSONSerialization dataWithJSONObject:state options:NSJSONWritingPrettyPrinted error:nil];
            [json writeToFile:[documents stringByAppendingPathComponent:@"simulator-state.json"] atomically:YES];
        }];
    }
#endif
}
- (BOOL)canBecomeFirstResponder { return YES; }
- (BOOL)prefersStatusBarHidden { return YES; }
- (void)viewDidAppear:(BOOL)animated { [super viewDidAppear:animated]; [self becomeFirstResponder]; }
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGRect bounds = self.view.bounds;
    // A game has no navigation/status chrome. Camera-side safe insets must not
    // shrink the entire picture and input viewport into an arbitrary column.
    CGRect safe = bounds;
    safe.size.height = MAX(0,safe.size.height-4);
    N3DSRect division = {0};
#if __has_include(<UIKit/UIHingeInteraction.h>)
    if (@available(iOS 27.1, *)) {
        for (UIViewReservedRegion *region in [self.view reservedRegionsOfKind:UIViewReservedRegionKind.divisionRegionKind options:UIViewReservedRegionQueryOptionsIncludeInactive]) {
            CGRect raw = region.frame;
            CGFloat x = MAX(CGRectGetMinX(bounds), CGRectGetMinX(raw)), y = MAX(CGRectGetMinY(bounds), CGRectGetMinY(raw));
            CGFloat right = MIN(CGRectGetMaxX(bounds), CGRectGetMaxX(raw)), bottom = MIN(CGRectGetMaxY(bounds), CGRectGetMaxY(raw));
            // Preserve zero-height inactive fold regions, as in ns2ios-shell.
            CGRect frame = right > x && bottom >= y ? CGRectMake(x,y,right-x,bottom-y) : CGRectNull;
            if (!CGRectIsNull(frame) && frame.size.width > frame.size.height && frame.size.width >= bounds.size.width*.9) {
                division = (N3DSRect){frame.origin.x,frame.origin.y,frame.size.width,frame.size.height}; break;
            }
        }
    }
#endif
    NSUserDefaults *settings = NSUserDefaults.standardUserDefaults;
    N3DSDisplay display = (N3DSDisplay)[settings integerForKey:@"LandscapeDisplay"];
    N3DSGeometry geometry = n3ds_geometry((N3DSRect){safe.origin.x,safe.origin.y,safe.size.width,safe.size.height}, division,
        self.partiallyOpen, display, [settings boolForKey:@"FlipLargeScreen"]);
    [settings setBool:geometry.landscape_options forKey:@"LandscapeOptionsAvailable"];
    CGRect top = CGRectMake(geometry.top.x,geometry.top.y,geometry.top.w,geometry.top.h);
    CGRect bottom = CGRectMake(geometry.bottom.x,geometry.bottom.y,geometry.bottom.w,geometry.bottom.h);
    CGRect controls = CGRectMake(geometry.controls.x,geometry.controls.y,geometry.controls.w,geometry.controls.h);
    BOOL topOmitted = CGRectIsEmpty(top), bottomOmitted = CGRectIsEmpty(bottom);
    // Hidden screens keep a valid offscreen drawable for the emulator's two swapchains.
    if (topOmitted) top = CGRectMake(-402,-242,400,240);
    if (bottomOmitted) bottom = CGRectMake(-322,-242,320,240);
    self.flip.hidden = !geometry.landscape_options || display != N3DS_VERTICAL;
    self.flip.frame = CGRectMake(geometry.bottom_menu==N3DS_MENU_OUTER_RIGHT ? CGRectGetMinX(controls)+8 : CGRectGetMidX(controls)+104, geometry.bottom_menu ? CGRectGetMaxY(controls)-48 : CGRectGetMinY(controls)+54,44,44);
    NSString *line = [NSString stringWithFormat:@"bounds %@ safe %@ fold %@ partial=%d top %@ bottom %@ controls %@", NSStringFromCGRect(bounds), NSStringFromCGRect(safe), NSStringFromCGRect(CGRectMake(division.x,division.y,division.w,division.h)), self.partiallyOpen, NSStringFromCGRect(top), NSStringFromCGRect(bottom), NSStringFromCGRect(controls)];
    if (![line isEqual:self.layoutLog]) { self.layoutLog = line; NSLog(@"3dsnative Duo layout: %@", line); }
    BOOL changed = !CGRectEqualToRect(self.top.frame, top) || !CGRectEqualToRect(self.bottom.frame, bottom);
    if (changed) { self.screenTouch = nil; n3ds_touch(false, 0, 0); }
    self.top.frame = top; self.bottom.frame = bottom;
    self.gamepad.frame = self.view.bounds;
    dm_gamepad_arrange(self.gamepad, (DmLayout){bottomOmitted ? CGRectZero : bottom, controls, YES, NO, geometry.control_unit, geometry.bottom_menu});
    [self.top layoutIfNeeded]; [self.bottom layoutIfNeeded];
    self.status.frame = CGRectInset(safe, 24, 24);
    self.fps.frame = CGRectMake(safe.origin.x + 8, safe.origin.y + 8, 150, 24);
    [self publishGeometry];
}
- (void)publishGeometry {
    CAMetalLayer *top = (CAMetalLayer *)self.top.layer, *bottom = (CAMetalLayer *)self.bottom.layer;
    if (!top.drawableSize.width || !top.drawableSize.height || !bottom.drawableSize.width || !bottom.drawableSize.height) return;
    n3ds_resize(top.drawableSize.width, top.drawableSize.height, bottom.drawableSize.width, bottom.drawableSize.height);
    if (self.started || !self.manifest) return;
    self.started = YES;
    NSString *game = [self.resources stringByAppendingPathComponent:self.manifest[@"game"]];
    NSString *update = self.manifest[@"update"] ? [self.resources stringByAppendingPathComponent:self.manifest[@"update"]] : nil;
    NSString *identity = self.manifest[@"update_sha256"];
    NSString *documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    n3ds_boot_config config = {0};
    config.title_id = strtoull([self.manifest[@"title_id"] UTF8String], NULL, 16);
    config.resolution_scale = MAX(1, [NSUserDefaults.standardUserDefaults integerForKey:@"ResolutionScale"]);
    config.top = (n3ds_surface){(__bridge void *)top, top.drawableSize.width, top.drawableSize.height};
    config.bottom = (n3ds_surface){(__bridge void *)bottom, bottom.drawableSize.width, bottom.drawableSize.height};
    config.callback = runtimeStatus; config.context = (__bridge void *)self;
    // NSString's filesystem representation may use autoreleased storage. Obtain
    // it inside the boot thread's pool, which remains alive until runtime exits.
    NSThread *thread = [[NSThread alloc] initWithBlock:^{
        @autoreleasepool {
            n3ds_boot_config boot = config;
            boot.game_path = game.fileSystemRepresentation; boot.update_path = update.fileSystemRepresentation;
            boot.update_sha256 = identity.UTF8String; boot.documents_path = documents.fileSystemRepresentation;
            (void)self;
            n3ds_run(&boot);
        }
    }];
    thread.stackSize = 4 * 1024 * 1024; thread.name = @"3dsnative runtime"; [thread start];
}
- (void)background:(NSNotification *)notification {
    dm_gamepad_reset();
    self.screenTouch = nil; n3ds_touch(false, 0, 0);
    self.keys = self.automated = 0;
    n3ds_input(N3DS_INPUT_KEYBOARD, 0, 0, 0, 0, 0);
    n3ds_input(N3DS_INPUT_AUTOMATION, 0, 0, 0, 0, 0);
    n3ds_pause(true);
}
- (void)foreground:(NSNotification *)notification { n3ds_audio_session(); n3ds_pause(false); [self becomeFirstResponder]; }
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    for (UITouch *touch in touches) {
        if (!self.screenTouch && CGRectContainsPoint(self.bottom.frame, [touch locationInView:self.view])) self.screenTouch = touch;
    }
    [self sendTouch];
}
- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { [self sendTouch]; }
- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    if ([touches containsObject:self.screenTouch]) self.screenTouch = nil;
    [self sendTouch];
}
- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { [self touchesEnded:touches withEvent:event]; }
- (void)sendTouch {
    if (!self.screenTouch) { n3ds_touch(false, 0, 0); return; }
    CGPoint p = [self.screenTouch locationInView:self.bottom];
    n3ds_touch(true, fmin(fmax(p.x / self.bottom.bounds.size.width, 0), .999),
                      fmin(fmax(p.y / self.bottom.bounds.size.height, 0), .999));
}
- (uint32_t)keyMask:(NSString *)key {
    NSDictionary *map = @{@"z":@(N3DS_A), @"x":@(N3DS_B), @"c":@(N3DS_X), @"v":@(N3DS_Y),
                         @"q":@(N3DS_L), @"e":@(N3DS_R), @"\r":@(N3DS_START), @" ":@(N3DS_SELECT),
                         @"w":@(N3DS_UP), @"a":@(N3DS_LEFT), @"s":@(N3DS_DOWN), @"d":@(N3DS_RIGHT),
                         UIKeyInputUpArrow:@(N3DS_UP), UIKeyInputDownArrow:@(N3DS_DOWN),
                         UIKeyInputLeftArrow:@(N3DS_LEFT), UIKeyInputRightArrow:@(N3DS_RIGHT)};
    return [map[key.lowercaseString] unsignedIntValue];
}
- (void)pressesBegan:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event {
    for (UIPress *press in presses) if (press.key) self.keys |= [self keyMask:press.key.charactersIgnoringModifiers];
    n3ds_input(N3DS_INPUT_KEYBOARD, self.keys, 0, 0, 0, 0);
}
- (void)pressesEnded:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event {
    for (UIPress *press in presses) if (press.key) self.keys &= ~[self keyMask:press.key.charactersIgnoringModifiers];
    n3ds_input(N3DS_INPUT_KEYBOARD, self.keys, 0, 0, 0, 0);
}
- (void)pressesCancelled:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event { [self pressesEnded:presses withEvent:event]; }
- (BOOL)inputURL:(NSURL *)url {
#if TARGET_OS_SIMULATOR
    if (![url.scheme isEqualToString:@"n3dsnative"] || ![url.host isEqualToString:@"button"]) return NO;
    NSMutableDictionary *query = [NSMutableDictionary new];
    for (NSURLQueryItem *item in [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO].queryItems) if (item.value) query[item.name] = item.value;
    uint32_t mask = [query[@"mask"] unsignedIntValue];
    if ([query[@"pressed"] boolValue]) self.automated |= mask; else self.automated &= ~mask;
    n3ds_input(N3DS_INPUT_AUTOMATION, self.automated, 0, 0, 0, 0);
    return YES;
#else
    return NO;
#endif
}
@end

UIViewController *front_ios_root(void) { return [N3DSRootVC new]; }
BOOL front_ios_input(UIViewController *controller, NSURL *url) { return [(N3DSRootVC *)controller inputURL:url]; }
