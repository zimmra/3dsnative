#pragma once
#import <UIKit/UIKit.h>
#include <TargetConditionals.h>
typedef struct { CGRect game, controls; BOOL separated, topSafeOnly; } DmLayout;
static inline UIEdgeInsets dm_game_safe_insets(CGRect b, UIEdgeInsets i, BOOL duo) { return i; }
static inline CGPoint dm_game_touch_point(CGRect game, CGPoint p) {
    return CGPointMake(fmin(fmax((p.x-game.origin.x)/game.size.width, 0), .999),
                       fmin(fmax((p.y-game.origin.y)/game.size.height, 0), .999));
}
UIView *dm_gamepad_view(CGRect frame);
void dm_gamepad_arrange(UIView *view, DmLayout layout);
void dm_gamepad_reset(void);
#if TARGET_OS_SIMULATOR
NSDictionary *dm_gamepad_diagnostics(void);
#endif
